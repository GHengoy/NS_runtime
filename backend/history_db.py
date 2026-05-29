"""
backend/history_db.py — SQLite 기반 히스토리 메타데이터 인덱서
=============================================================

[역할]
    defect/borderline 이미지 파일의 메타데이터를 SQLite에 인덱싱합니다.
    매 API 요청마다 디렉토리를 전체 스캔하는 대신 DB를 쿼리해
    24시간 가동 시에도 일정한 응답 속도를 유지합니다.

[동작]
    - 서버 시작 시 백그라운드 스레드에서 전체 디렉토리를 1회 스캔 (full rebuild)
    - 이후 10초마다 현재 시간대 폴더(YYYY-MM-DD/HH)만 증분 스캔
    - DB 손상 시 파일 삭제 후 서버 재시작하면 자동 재구축

[DB 위치]
    {첫 번째 save_root}/history_index.db
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple


# ── 파일명 파싱 ───────────────────────────────────────────────────────────

def _parse_filename(filename: str) -> Optional[Dict]:
    """파일명에서 메타데이터를 추출합니다.
    형식: {class}_{conf}_{YYYYMMDD}_{HHMMSS}_{ms}.jpg
    예시: AI_0.81_20260226_085616_171.jpg
    """
    if filename.endswith("_mark.jpg") or filename.endswith(".txt"):
        return None
    if not filename.endswith(".jpg"):
        return None

    base = filename[:-4]
    parts = base.split("_")
    if len(parts) < 5:
        return None
    try:
        ms = parts[-1]
        hhmmss = parts[-2]
        yyyymmdd = parts[-3]
        conf = float(parts[-4])
        class_name = "_".join(parts[:-4])
        ts = datetime.strptime(f"{yyyymmdd}_{hhmmss}", "%Y%m%d_%H%M%S")
        return {
            "class_name": class_name,
            "confidence": conf,
            "timestamp": ts.isoformat(),
            "ms": ms,
        }
    except (ValueError, IndexError):
        return None


# ── HistoryDB 클래스 ──────────────────────────────────────────────────────

class HistoryDB:
    """SQLite 기반 히스토리 메타데이터 인덱서."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._ready = threading.Event()  # full scan 완료 시 set
        self._stop_event = threading.Event()
        self._trigger_event = threading.Event()  # 즉시 증분 스캔 트리거
        self._thread: Optional[threading.Thread] = None
        self._save_roots: List[str] = []
        self._init_db()

    # ── DB 초기화 ─────────────────────────────────────────────────────

    def _init_db(self):
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id             TEXT PRIMARY KEY,
                    category       TEXT NOT NULL,
                    line_name      TEXT NOT NULL,
                    class_name     TEXT NOT NULL,
                    confidence     REAL NOT NULL,
                    timestamp      TEXT NOT NULL,
                    date           TEXT NOT NULL,
                    hour           TEXT NOT NULL,
                    filename       TEXT NOT NULL,
                    dir_path       TEXT NOT NULL,
                    detector_type  TEXT NOT NULL DEFAULT ''
                )
            """)
            # Migration: add detector_type column to existing databases
            try:
                conn.execute("ALTER TABLE history ADD COLUMN detector_type TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # column already exists
            conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON history (date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_line ON history (line_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_class ON history (class_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_category ON history (category)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON history (timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_detector ON history (detector_type)")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    # ── 백그라운드 인덱서 시작/종료 ──────────────────────────────────

    def start(self, save_roots: List[str]):
        """백그라운드 인덱서 스레드를 시작합니다."""
        self._save_roots = save_roots
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._indexer_loop,
            name="history-indexer",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._trigger_event.set()  # unblock waiting thread
        if self._thread:
            self._thread.join(timeout=5)

    def trigger_scan(self):
        """불량 저장 직후 호출 — 증분 스캔을 즉시 깨웁니다."""
        self._trigger_event.set()

    def wait_ready(self, timeout: float = 30.0) -> bool:
        """초기 인덱싱 완료를 대기합니다."""
        return self._ready.wait(timeout=timeout)

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    # ── 인덱서 루프 ──────────────────────────────────────────────────

    def _indexer_loop(self):
        """백그라운드: 최초 전체 스캔 → 이후 10초마다 증분 스캔."""
        try:
            t0 = time.time()
            count = self._full_scan()
            elapsed = time.time() - t0
            print(f"[HistoryDB] Full scan done: {count} records indexed ({elapsed:.1f}s)")
        except Exception as e:
            print(f"[HistoryDB] Full scan error: {e}")
        finally:
            self._ready.set()

        # 증분 스캔 루프 — 10초마다 또는 trigger_scan() 호출 시 즉시 실행
        while not self._stop_event.is_set():
            self._trigger_event.wait(timeout=10.0)
            self._trigger_event.clear()
            if self._stop_event.is_set():
                break
            try:
                self._incremental_scan()
            except Exception as e:
                print(f"[HistoryDB] Incremental scan error: {e}")

    def _full_scan(self) -> int:
        """전체 디렉토리를 스캔해 DB에 INSERT OR IGNORE, 이후 orphan 레코드 정리."""
        count = 0
        conn = self._connect()
        try:
            for root in self._save_roots:
                detector_type = os.path.basename(root)
                for category in ("defect", "borderline"):
                    cat_dir = os.path.join(root, category)
                    if not os.path.isdir(cat_dir):
                        continue
                    for line_name in os.listdir(cat_dir):
                        line_dir = os.path.join(cat_dir, line_name)
                        if not os.path.isdir(line_dir):
                            continue
                        for cls in os.listdir(line_dir):
                            cls_dir = os.path.join(line_dir, cls)
                            if not os.path.isdir(cls_dir):
                                continue
                            for date_folder in os.listdir(cls_dir):
                                date_dir = os.path.join(cls_dir, date_folder)
                                if not os.path.isdir(date_dir):
                                    continue
                                for hour_folder in os.listdir(date_dir):
                                    hour_dir = os.path.join(date_dir, hour_folder)
                                    if not os.path.isdir(hour_dir):
                                        continue
                                    count += self._index_directory(
                                        conn, category, line_name, cls,
                                        date_folder, hour_folder, hour_dir,
                                        detector_type,
                                    )
            conn.commit()

            # 실제 파일이 없는 orphan 레코드 제거
            rows = conn.execute("SELECT id, dir_path, filename FROM history").fetchall()
            orphan_ids = [
                r["id"] for r in rows
                if not os.path.isfile(os.path.join(r["dir_path"], r["filename"]))
            ]
            if orphan_ids:
                conn.executemany("DELETE FROM history WHERE id = ?", [(rid,) for rid in orphan_ids])
                conn.commit()
                print(f"[HistoryDB] Purged {len(orphan_ids)} orphaned records")
        finally:
            conn.close()
        return count

    def _incremental_scan(self):
        """현재 시간대 폴더만 증분 스캔합니다."""
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        hour_str = now.strftime("%H")
        # 이전 시간대도 스캔 (분 경계에서 누락 방지)
        prev_hour = f"{max(0, int(hour_str) - 1):02d}"

        conn = self._connect()
        try:
            for root in self._save_roots:
                detector_type = os.path.basename(root)
                for category in ("defect", "borderline"):
                    cat_dir = os.path.join(root, category)
                    if not os.path.isdir(cat_dir):
                        continue
                    for line_name in os.listdir(cat_dir):
                        line_dir = os.path.join(cat_dir, line_name)
                        if not os.path.isdir(line_dir):
                            continue
                        for cls in os.listdir(line_dir):
                            cls_dir = os.path.join(line_dir, cls)
                            if not os.path.isdir(cls_dir):
                                continue
                            for h in (hour_str, prev_hour):
                                hour_dir = os.path.join(cls_dir, date_str, h)
                                if os.path.isdir(hour_dir):
                                    self._index_directory(
                                        conn, category, line_name, cls,
                                        date_str, h, hour_dir,
                                        detector_type,
                                    )
            conn.commit()
        finally:
            conn.close()

    def _index_directory(
        self, conn: sqlite3.Connection,
        category: str, line_name: str, class_name: str,
        date_str: str, hour_str: str, dir_path: str,
        detector_type: str = '',
    ) -> int:
        """특정 시간 폴더의 파일을 인덱싱합니다."""
        count = 0
        try:
            files = os.listdir(dir_path)
        except OSError:
            return 0
        for fname in files:
            meta = _parse_filename(fname)
            if meta is None:
                continue
            record_id = f"{category}/{line_name}/{class_name}/{date_str}/{hour_str}/{fname}"
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO history
                       (id, category, line_name, class_name, confidence,
                        timestamp, date, hour, filename, dir_path, detector_type)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (record_id, category, line_name, meta["class_name"],
                     meta["confidence"], meta["timestamp"],
                     date_str, hour_str, fname, dir_path, detector_type),
                )
                count += 1
            except sqlite3.IntegrityError:
                pass
        return count

    # ── 쿼리 API ─────────────────────────────────────────────────────

    def query_history(
        self,
        category: str = "all",
        line: Optional[str] = None,
        class_name: Optional[str] = None,
        date: Optional[str] = None,
        detector_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 60,
        sort: str = "newest",
    ) -> Dict:
        """히스토리 레코드를 쿼리합니다."""
        conditions = []
        params = []

        if category != "all":
            conditions.append("category = ?")
            params.append(category)
        if line:
            conditions.append("line_name = ?")
            params.append(line)
        if class_name:
            conditions.append("class_name = ?")
            params.append(class_name)
        if date:
            conditions.append("date = ?")
            params.append(date)
        if detector_type:
            conditions.append("detector_type = ?")
            params.append(detector_type)

        where = " WHERE " + " AND ".join(conditions) if conditions else ""

        # 정렬
        order_map = {
            "newest": "timestamp DESC",
            "oldest": "timestamp ASC",
            "confidence_high": "confidence DESC",
            "confidence_low": "confidence ASC",
        }
        order = order_map.get(sort, "timestamp DESC")

        conn = self._connect()
        try:
            # 총 개수
            row = conn.execute(f"SELECT COUNT(*) as cnt FROM history{where}", params).fetchone()
            total = row["cnt"]

            # 페이지네이션
            offset = (page - 1) * page_size
            rows = conn.execute(
                f"SELECT * FROM history{where} ORDER BY {order} LIMIT ? OFFSET ?",
                params + [page_size, offset],
            ).fetchall()
        finally:
            conn.close()

        records = []
        for r in rows:
            fpath = os.path.join(r["dir_path"], r["filename"])
            mark_path = os.path.join(r["dir_path"], r["filename"][:-4] + "_mark.jpg")
            records.append({
                "id": r["id"],
                "category": r["category"],
                "line_name": r["line_name"],
                "class_name": r["class_name"],
                "confidence": r["confidence"],
                "timestamp": r["timestamp"],
                "date": r["date"],
                "detector_type": r["detector_type"],
                "image_url": f"/api/history/image?path={fpath}",
                "mark_url": (
                    f"/api/history/image?path={mark_path}"
                    if os.path.exists(mark_path) else None
                ),
            })

        return {
            "records": records,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if total > 0 else 0,
        }

    def query_filters(self) -> Dict:
        """히스토리 필터 UI용 라인명/클래스명/날짜/디텍터 타입 목록을 반환합니다."""
        conn = self._connect()
        try:
            lines = [r[0] for r in conn.execute(
                "SELECT DISTINCT line_name FROM history ORDER BY line_name"
            ).fetchall()]
            classes = [r[0] for r in conn.execute(
                "SELECT DISTINCT class_name FROM history ORDER BY class_name"
            ).fetchall()]
            dates = [r[0] for r in conn.execute(
                "SELECT DISTINCT date FROM history ORDER BY date DESC"
            ).fetchall()]
            detector_types = [r[0] for r in conn.execute(
                "SELECT DISTINCT detector_type FROM history WHERE detector_type != '' ORDER BY detector_type"
            ).fetchall()]
        finally:
            conn.close()

        return {
            "lines": lines,
            "classes": classes,
            "dates": dates,
            "detector_types": detector_types,
        }

    def delete_before_date(self, cutoff_date: str):
        """특정 날짜 이전 레코드를 DB에서 삭제합니다. (cleanup 연동)"""
        conn = self._connect()
        try:
            conn.execute("DELETE FROM history WHERE date < ?", (cutoff_date,))
            conn.commit()
        finally:
            conn.close()

    def delete_records_by_path(self, path: str) -> int:
        """파일 또는 폴더 경로에 해당하는 DB 레코드를 삭제합니다."""
        norm = os.path.normpath(path)
        conn = self._connect()
        try:
            cur = conn.execute(
                """DELETE FROM history
                   WHERE (dir_path || '/' || filename) = ?
                      OR dir_path = ?
                      OR dir_path LIKE ?""",
                (norm, norm, norm + "/%"),
            )
            deleted = cur.rowcount
            conn.commit()
            return deleted
        finally:
            conn.close()
