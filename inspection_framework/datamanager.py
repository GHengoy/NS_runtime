"""
datamanager.py — 이미지 및 감지 결과 데이터 백업 모듈
========================================================

[역할]
    불량(defect) / 경계(borderline) 이미지와 감지 결과 텍스트 파일을
    라인명 · 클래스명 · 날짜 · 시간 별 폴더 구조로 자동 저장합니다.

[저장 폴더 구조]
    {save_root}/
    ├── defect/{line_name}/{class_name}/YYYY-MM-DD/HH/
    │   ├── AI_0.93_20260224_163000_123.jpg       ← 원본
    │   ├── AI_0.93_20260224_163000_123_mark.jpg  ← 어노테이션
    │   └── AI_0.93_20260224_163000_123.txt       ← 감지 좌표
    └── borderline/{line_name}/{class_name}/YYYY-MM-DD/HH/
        ├── AI_0.61_20260224_163000_456.jpg
        ├── AI_0.61_20260224_163000_456_mark.jpg
        └── AI_0.61_20260224_163000_456.txt
"""

import os
import shutil
import cv2
import numpy as np
from datetime import datetime, timedelta
from typing import List

# 타입 힌팅 (순환 임포트 방지)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from detector import DetectionResult


class DataManager:
    """
    이미지 & 감지 결과 파일 저장 관리 클래스.

    사용법 예시
    -----------
    dm = DataManager(save_root='/mnt/IMG/line_A')

    # 불량 저장
    dm.save_defect(image=frame, annotated=annotated,
                   detections=results, line_name='4-7-pouch')

    # 경계(borderline) 저장
    dm.save_borderline(image=frame, annotated=annotated,
                       detections=results, line_name='4-7-pouch')
    """

    def __init__(self, save_root: str, **_kwargs):
        """
        Parameters
        ----------
        save_root : 이미지를 저장할 최상위 디렉토리 경로
        **_kwargs : 하위 호환용 (max_preview, save_normal 등 무시)
        """
        self.save_root = save_root
        self.last_saved: tuple | None = None  # (image_path, mark_path, category, detections)

    # ------------------------------------------------------------------
    # 공개 메서드 (Public Methods)
    # ------------------------------------------------------------------

    def save_defect(
        self,
        image: np.ndarray,
        annotated: np.ndarray,
        detections: "List[DetectionResult]",
        line_name: str = "line",
    ):
        """
        불량 이미지와 감지 결과를 저장합니다.

        Parameters
        ----------
        image      : 원본 BGR 이미지
        annotated  : 박스가 그려진 BGR 이미지
        detections : YoloDetector.detect() 의 반환값
        line_name  : 라인 이름 (폴더 경로에 사용)
        """
        return self._save("defect", image, annotated, detections, line_name)

    def save_borderline(
        self,
        image: np.ndarray,
        annotated: np.ndarray,
        detections: "List[DetectionResult]",
        line_name: str = "line",
    ):
        """
        경계(borderline) 이미지와 감지 결과를 저장합니다.
        save_thresholds 이상이지만 class_thresholds 미만인 감지.

        Parameters
        ----------
        image      : 원본 BGR 이미지
        annotated  : 박스가 그려진 BGR 이미지
        detections : 저장 대상 감지 결과
        line_name  : 라인 이름 (폴더 경로에 사용)
        """
        return self._save("borderline", image, annotated, detections, line_name)

    def save_normal(self, image: np.ndarray, line_name: str = "line"):
        """하위 호환용 no-op. 정상 이미지 저장은 비활성."""
        pass

    def cleanup_old_data(self, retention_days: int):
        """
        보관 기간이 지난 날짜 폴더(YYYY-MM-DD)를 삭제합니다.

        Parameters
        ----------
        retention_days : 보관 일수. 0이면 삭제하지 않음.
        """
        if retention_days <= 0:
            return

        cutoff = datetime.now() - timedelta(days=retention_days)
        deleted = 0

        for category in ("defect", "borderline"):
            cat_dir = os.path.join(self.save_root, category)
            if not os.path.isdir(cat_dir):
                continue

            # {cat_dir}/{line_name}/{class_name}/YYYY-MM-DD/
            for line_name in os.listdir(cat_dir):
                line_dir = os.path.join(cat_dir, line_name)
                if not os.path.isdir(line_dir):
                    continue
                for class_name in os.listdir(line_dir):
                    class_dir = os.path.join(line_dir, class_name)
                    if not os.path.isdir(class_dir):
                        continue
                    for date_folder in os.listdir(class_dir):
                        date_path = os.path.join(class_dir, date_folder)
                        if not os.path.isdir(date_path):
                            continue
                        try:
                            folder_date = datetime.strptime(date_folder, "%Y-%m-%d")
                        except ValueError:
                            continue
                        if folder_date < cutoff:
                            shutil.rmtree(date_path)
                            deleted += 1

                    # 날짜 폴더 삭제 후 class_name 폴더가 비었으면 정리
                    if os.path.isdir(class_dir) and not os.listdir(class_dir):
                        os.rmdir(class_dir)

                # line_name 폴더가 비었으면 정리
                if os.path.isdir(line_dir) and not os.listdir(line_dir):
                    os.rmdir(line_dir)

        if deleted > 0:
            print(f"[DataManager] 🗑️ {deleted}개 오래된 날짜 폴더 삭제 (보관: {retention_days}일)")

    # ------------------------------------------------------------------
    # 내부 메서드 (Internal Methods)
    # ------------------------------------------------------------------

    def _save(
        self,
        category: str,
        image: np.ndarray,
        annotated: np.ndarray,
        detections: "List[DetectionResult]",
        line_name: str,
    ):
        """defect / borderline 공통 저장 로직. (image_path, mark_path) 반환."""
        class_name = self._get_class_name(detections)
        save_dir = self._get_dated_dir(category, line_name, class_name)
        filename = self._make_filename(detections)

        image_path = os.path.join(save_dir, filename + ".jpg")
        mark_path = os.path.join(save_dir, filename + "_mark.jpg")
        cv2.imwrite(image_path, image)
        cv2.imwrite(mark_path, annotated)
        self._save_label_txt(os.path.join(save_dir, filename + ".txt"), detections)

        self.last_saved = (image_path, mark_path, category, detections)
        print(f"[DataManager] 💾 {category} 저장: {line_name}/{class_name}/{filename}")
        return image_path, mark_path

    def _get_dated_dir(self, category: str, line_name: str, class_name: str) -> str:
        """라인명 · 클래스명 · 날짜 · 시각 폴더를 생성하고 경로를 반환합니다."""
        now = datetime.now()
        dated = os.path.join(
            self.save_root, category,
            line_name, class_name,
            now.strftime("%Y-%m-%d"),
            now.strftime("%H"),
        )
        os.makedirs(dated, exist_ok=True)
        return dated

    @staticmethod
    def _get_class_name(detections: "List[DetectionResult]") -> str:
        """감지 결과에서 대표 클래스명을 추출합니다."""
        if detections:
            best = max(detections, key=lambda d: d.confidence)
            return best.label
        return "unknown"

    @staticmethod
    def _make_filename(detections: "List[DetectionResult]") -> str:
        """
        저장 파일명을 생성합니다.
        형식: {class}_{conf}_{YYYYMMDD}_{HHMMSS}_{ms}
        예시: AI_0.93_20260224_163001_123
        """
        now = datetime.now()
        ts = now.strftime("%Y%m%d_%H%M%S") + f"_{now.microsecond // 1000:03d}"
        if detections:
            best = max(detections, key=lambda d: d.confidence)
            return f"{best.label}_{best.confidence:.2f}_{ts}"
        return f"unknown_{ts}"

    @staticmethod
    def _save_label_txt(path: str, detections: "List[DetectionResult]"):
        """절대 픽셀 좌표 형식으로 라벨 텍스트 파일을 저장합니다.
        형식: {label} {x1} {y1} {x2} {y2} {confidence:.4f} [recognized_text]
        주의: YOLO 정규화 좌표(0~1)가 아닌 픽셀 절대 좌표입니다."""
        try:
            with open(path, "w") as f:
                for det in detections:
                    x1, y1, x2, y2 = det.bbox_xyxy
                    line = f"{det.label} {x1} {y1} {x2} {y2} {det.confidence:.4f}"
                    if getattr(det, 'recognized_text', None):
                        line += f" {det.recognized_text}"
                    f.write(line + "\n")
        except Exception as e:
            print(f"[DataManager] 라벨 파일 저장 실패: {e}")
