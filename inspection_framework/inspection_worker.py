"""
inspection_worker.py — 백그라운드 검사 워커
============================================

[역할]
    카메라 1대의 검사 루프를 백그라운드 스레드에서 실행합니다.
    start() / stop() 으로 비동기 제어하고,
    frame_queue 로 JPEG 프레임을 외부에 노출합니다.

[왜 이렇게 바꿨나? (inspection_runtime.py 와의 차이)]
    기존 inspection_runtime.py 는 run() 이 blocking(멈춤) 이었습니다.
    카메라 6대를 동시에 띄우거나, FastAPI WebSocket 에서
    프레임을 받아가려면 run() 이 백그라운드에서 돌아야 합니다.

    InspectionWorker 는:
        - start()  → 백그라운드 스레드 시작 (즉시 반환)
        - stop()   → 루프 종료 요청 (즉시 반환, 루프는 곧 멈춤)
        - status   → 현재 상태 문자열 반환 ("running" / "stopped" / "error")
        - stats    → FPS, 총 불량 수 등 딕셔너리 반환
        - frame_queue → (JPEG bytes) 큐. 외부에서 꺼내 WebSocket 전송 가능.

[나중에 FastAPI 에서 이렇게 씁니다]
    worker = InspectionWorker(config)
    worker.start()

    # WebSocket 핸들러에서:
    while True:
        jpeg_bytes = await asyncio.get_event_loop().run_in_executor(
            None, worker.frame_queue.get
        )
        await websocket.send_bytes(jpeg_bytes)

    worker.stop()
"""

import cv2
import logging
import time
import threading
import traceback
from datetime import date, datetime
from queue import Queue, Full
from typing import Callable, Optional

_logger = logging.getLogger(__name__)

from config import InspectionConfig
# 나머지 모듈은 _build_modules() 에서 lazy import
# (pypylon / ultralytics 없는 환경에서도 서버가 정상 기동되도록)


# ──────────────────────────────────────────────────────────────────────
#  워커 상태 상수
# ──────────────────────────────────────────────────────────────────────
STATUS_STOPPED      = "stopped"        # 실행 전 또는 정상 종료
STATUS_INITIALIZING = "initializing"   # 모듈 초기화 진행 중
STATUS_RUNNING      = "running"        # 검사 루프 실행 중
STATUS_ERROR        = "error"          # 예외 발생으로 종료


class InspectionWorker:
    """
    카메라 1대 = 검사 워커 1개.

    사용법 예시
    -----------
    config = InspectionConfig.from_json("configs/4-7-pouch-C.json")
    worker = InspectionWorker(config)

    worker.start()          # 백그라운드에서 검사 시작 (즉시 반환)
    print(worker.status)    # "running"
    print(worker.stats)     # {"fps": 12.3, "defect_count": 5, ...}

    # JPEG 프레임 꺼내기 (WebSocket / OpenCV 창에 사용)
    jpeg = worker.frame_queue.get(timeout=1.0)

    worker.stop()           # 검사 중지 요청 (즉시 반환)
    worker.join()           # 스레드가 완전히 끝날 때까지 대기
    """

    def __init__(self, config: InspectionConfig, frame_queue_size: int = 2,
                 process_fn=None, on_save_callback: Optional[Callable] = None):
        """
        Parameters
        ----------
        config           : InspectionConfig 인스턴스 (모든 설정 포함)
        frame_queue_size : frame_queue 최대 크기.
                           작을수록 메모리 적게 씀 (기본 2 = 항상 최신 2프레임 유지).
        process_fn       : 이미지 획득 후 처리 함수 (run_local.py 에서 주입).
                           시그니처: process_fn(frame, cropped, *, detector, rejecter,
                                                data_manager, config) -> (annotated, is_defect)
                           None 이면 기본 동작 (AI 감지 → 리젝트 → 저장) 을 사용합니다.
        on_save_callback : 이미지 저장 후 호출되는 콜백 (S3 업로드 등).
                           시그니처: on_save_callback(category, save_dir, filename, line_name)
                           None 이면 아무 것도 하지 않습니다.
        """
        self.config = config

        # ── 외부에서 읽을 수 있는 상태 값들 ──────────────────────────
        self.frame_queue: Queue = Queue(maxsize=frame_queue_size)
        """
        JPEG bytes 가 들어오는 큐.
        - 외부(FastAPI WebSocket, OpenCV 창 등)에서 get() 으로 꺼냅니다.
        - maxsize 초과 시 오래된 프레임을 버리고 최신 프레임만 유지합니다.
        """

        # ── 내부 상태 ─────────────────────────────────────────────────
        self._status: str = STATUS_STOPPED
        self._stop_event = threading.Event()   # stop() 호출 → set()
        self._thread: Optional[threading.Thread] = None

        # 통계 (stats 프로퍼티로 외부 노출)
        self._fps: float = 0.0
        self._defect_count: int = 0
        self._total_count: int = 0
        self._last_error: str = ""
        self._reset_date: date = date.today()  # 카운터가 리셋된 날짜

        # 초기화 단계 추적 (stats 프로퍼티로 외부 노출)
        self._init_stage: str = ""       # 현재 초기화 단계명 (camera, model 등)
        self._init_total: int = 0        # 총 초기화 단계 수
        self._init_current: int = 0      # 현재 진행 중인 단계 번호

        # 커스텀 처리 함수 (run_local.py에서 주입)
        self._process_fn = process_fn

        # 이미지 저장 후 콜백 (S3 업로드 등)
        self._on_save_callback = on_save_callback

        # 각 모듈 인스턴스 (start() 시 생성)
        self._camera = None
        self._detector = None   # BaseDetector subclass instance
        self._rejecter = None
        self._data_manager = None

        # 시간 기반 리젝트 딜레이 (프레임 기반 대신 순수 시간 사용)
        self._time_based_reject: bool = False
        self._reject_delay_sec: float = 0.0
        self._pending_rejects: list = []          # Continuous 바 표시용 타임스탬프
        self._pending_rejects_lock = threading.Lock()
        self._cont_valve_on = False               # Continuous 모드: 현재 valve ON 상태

        # 비동기 감지 (PaddleOCR / CNN 등 느린 디텍터용)
        self._async_detect = False              # True: detection runs in bg thread
        self._async_lock = threading.Lock()
        self._async_input = None                # (frame, cropped) latest unprocessed
        self._async_event = threading.Event()
        self._async_result_lock = threading.Lock()
        self._async_annotated = None
        self._async_is_defect = False
        self._async_defect_meta = None

        # 원본 해상도 추적 (스트리밍 축소 전 실제 카메라 해상도)
        self._orig_frame_size: tuple | None = None

    # ──────────────────────────────────────────────────────────────────
    # 공개 프로퍼티 (Read-Only)
    # ──────────────────────────────────────────────────────────────────

    @property
    def status(self) -> str:
        """현재 워커 상태. "running" / "stopped" / "error" 중 하나."""
        return self._status

    @property
    def stats(self) -> dict:
        """
        현재 통계를 딕셔너리로 반환합니다.
        FastAPI 가 /stats 엔드포인트로 그대로 JSON 응답할 수 있습니다.

        반환 예시
        ---------
        {
            "line_name":    "4-7-pouch-C",
            "status":       "running",
            "fps":          12.3,
            "total_count":  1500,
            "defect_count": 23,
            "defect_rate":  "1.53%",
            "last_error":   ""
        }
        """
        defect_rate = (
            f"{self._defect_count / self._total_count * 100:.2f}%"
            if self._total_count > 0 else "0.00%"
        )
        return {
            "line_name":    self.config.line_name,
            "project_name": getattr(self.config, 'project_name', '') or self.config.line_name,
            "status":       self._status,
            "fps":          round(self._fps, 1),
            "total_count":  self._total_count,
            "defect_count": self._defect_count,
            "defect_rate":  defect_rate,
            "last_error":   self._last_error,
            "reset_date":   self._reset_date.isoformat(),
            **(self._build_valve_bar_meta()
               if getattr(self.config, 'collection_mode', 'auto') == 'continuous'
               else {
                   "reject_window_size": len(self._rejecter.window_state) if self._rejecter else 0,
                   "reject_window_marks": ([i for i, v in enumerate(self._rejecter.window_state) if v == 1]
                                           if self._rejecter else []),
               }),
            # 초기화 진행 상태
            "init_stage":   self._init_stage,
            "init_current": self._init_current,
            "init_total":   self._init_total,
            # 원본 카메라 해상도 (스트리밍 축소 전)
            "orig_frame_size": list(self._orig_frame_size) if self._orig_frame_size else None,
        }

    # ──────────────────────────────────────────────────────────────────
    # 공개 메서드 (Public Methods)
    # ──────────────────────────────────────────────────────────────────

    def start(self):
        """
        백그라운드 스레드에서 검사 루프를 시작합니다.
        이 메서드는 즉시 반환됩니다 (non-blocking).
        status 는 즉시 "initializing" 으로 설정됩니다.
        """
        if self._status == STATUS_RUNNING:
            print(f"[Worker:{self.config.line_name}] Already running.")
            return
        if self._status == STATUS_INITIALIZING:
            print(f"[Worker:{self.config.line_name}] Already initializing.")
            return

        self._stop_event.clear()
        self._defect_count = 0
        self._total_count  = 0
        self._last_error   = ""
        self._reset_date   = date.today()
        self._init_stage   = ""
        self._init_current = 0
        self._init_total   = 0
        self._status = STATUS_INITIALIZING   # 즉시 "initializing" 설정

        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"worker-{self.config.line_name}",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        """
        검사 루프 종료를 요청합니다.
        이 메서드는 즉시 반환됩니다 (non-blocking).
        루프가 완전히 끝나길 기다리려면 join() 을 추가로 호출하세요.
        """
        print(f"[Worker:{self.config.line_name}] 종료 요청 중...")
        self._stop_event.set()
        # 즉시 리젝트 OFF (스레드 종료 대기 없이 바로 신호 차단)
        if self._camera is not None:
            try:
                self._camera.set_reject_output(False)
            except Exception:
                pass

    def join(self, timeout: float = 5.0):
        """
        백그라운드 스레드가 종료될 때까지 최대 timeout 초 대기합니다.

        Parameters
        ----------
        timeout : 최대 대기 시간 [초]. 기본 5초.
        """
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def update_class_thresholds(self, class_thresholds: Optional[dict]) -> None:
        """
        검사 중 class threshold를 동적으로 변경합니다.
        save_thresholds가 있으면 effective_thresholds를 재계산하여 detector에 전달합니다.

        Parameters
        ----------
        class_thresholds : dict or None
            새로운 class threshold (e.g., {"defect": 0.75, "pinhole": 0.90})
            None 이면 threshold 초기화.
        """
        self.config.class_thresholds = class_thresholds
        self._original_class_thresholds = class_thresholds
        if self._detector is not None:
            # save_thresholds가 있으면 effective = min(class, save)
            effective = dict(class_thresholds or {})
            if self.config.save_thresholds:
                for cls, thr in self.config.save_thresholds.items():
                    if cls in effective:
                        effective[cls] = min(effective[cls], thr)
                    else:
                        effective[cls] = thr
            self._detector.set_class_thresholds(effective if effective else class_thresholds)

    # ──────────────────────────────────────────────────────────────────
    # 내부 메서드 (Internal Methods) — 외부에서 호출하지 마세요
    # ──────────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────────
    # 초기화 단계별 sub-methods (_run_loop 에서 단계별로 호출)
    # ──────────────────────────────────────────────────────────────────

    def _init_camera(self):
        """[Init Step] 카메라 인스턴스 생성 + 연결."""
        c = self.config
        if c.camera_type == "webcam":
            from webcam_camera import WebcamCamera          # noqa: PLC0415
            self._camera = WebcamCamera(
                camera_ip=c.camera_ip,
                rotation=c.rotation,
                crop_region=c.crop_region,
            )
        else:
            from camera import BaslerCamera                 # noqa: PLC0415
            self._camera = BaslerCamera(
                camera_ip=c.camera_ip,
                pfs_file=c.pfs_file,
                rotation=c.rotation,
                crop_region=c.crop_region,
            )
        self._camera.open()
        # 카메라 연결 직후 리젝트 신호 강제 OFF (이전 세션에서 ON 상태로 남은 경우 대비)
        self._camera.set_reject_output(False)

        # Force trigger mode if configured (Basler only, 'auto' = use PFS setting)
        # Must stop grabbing first — TriggerMode cannot be changed while grabbing.
        force_trigger = getattr(c, 'collection_mode', 'auto') or 'auto'
        if force_trigger != 'auto' and c.camera_type == 'basler':
            try:
                from pypylon import pylon                    # noqa: PLC0415
                mode_val = "On" if force_trigger == 'trigger' else "Off"
                self._camera._cameras.StopGrabbing()
                self._camera._cam.TriggerMode.SetValue(mode_val)
                self._camera._cameras.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
                print(f"[Worker:{c.line_name}] TriggerMode forced → {mode_val}")
            except Exception as e:
                print(f"[Worker:{c.line_name}] Warning: could not force TriggerMode: {e}")

        # 트리거 모드 전용: 센서 인식 후 촬영 딜레이 [sec → µs 변환]
        trigger_delay_sec = getattr(c, 'trigger_delay_sec', None)
        if trigger_delay_sec and force_trigger == 'trigger' and c.camera_type == 'basler':
            try:
                delay_us = float(trigger_delay_sec) * 1_000_000
                self._camera.set_trigger_delay(delay_us)
                print(f"[Worker:{c.line_name}] TriggerDelayAbs → {delay_us} µs ({trigger_delay_sec} sec)")
            except Exception as e:
                print(f"[Worker:{c.line_name}] Warning: could not set trigger delay: {e}")

        # 트리거 노이즈 제거: LineDebouncerHighTime [sec → µs 변환]
        debounce_sec = getattr(c, 'trigger_debounce_sec', None)
        if debounce_sec and force_trigger == 'trigger' and c.camera_type == 'basler':
            try:
                debounce_us = float(debounce_sec) * 1_000_000
                cam = self._camera._cam
                cam.LineSelector.SetValue("Line1")
                cam.LineDebouncerHighTimeAbs.SetValue(debounce_us)
                print(f"[Worker:{c.line_name}] LineDebouncerHighTime → {debounce_us} µs ({debounce_sec} sec)")
            except Exception as e:
                print(f"[Worker:{c.line_name}] Warning: could not set debounce: {e}")

    def _init_detector(self):
        """[Init Step] AI 디텍터 생성 (inspection 모드 전용)."""
        c = self.config
        from detector import create_detector                # noqa: PLC0415

        # class_thresholds: None 또는 {} → 모든 클래스 감지 (threshold 0.5)
        raw_thr = c.class_thresholds if c.class_thresholds else None
        effective_thresholds = dict(raw_thr or {})
        self._original_class_thresholds = raw_thr
        if c.save_thresholds:
            for cls, thr in c.save_thresholds.items():
                if cls in effective_thresholds:
                    effective_thresholds[cls] = min(effective_thresholds[cls], thr)
                else:
                    effective_thresholds[cls] = thr

        self._detector = create_detector(
            detector_type=getattr(c, 'detector_type', 'yolo'),
            model_path=c.model_path,
            class_thresholds=effective_thresholds if effective_thresholds else None,
            device=c.device,
            detector_config=getattr(c, 'detector_config', None),
        )

    def _measure_camera_fps(self, n_frames: int = 20) -> float:
        """카메라에서 n_frames를 촬영해 실제 FPS를 측정합니다.
        연속(continuous) 카메라 모드에서 reject_delay_seconds → frames 변환에 사용.
        트리거 모드에서는 호출하지 않습니다.
        """
        import time as _time
        grabbed = 0
        t0 = _time.time()
        for _ in range(n_frames):
            _, _, ok = self._camera.grab()
            if ok:
                grabbed += 1
        elapsed = _time.time() - t0
        if grabbed < 2 or elapsed < 0.01:
            return 30.0  # 측정 실패 시 기본값 30fps
        fps = grabbed / elapsed
        print(f"[Worker:{self.config.line_name}] FPS 측정: {fps:.1f} fps ({grabbed}/{n_frames} 프레임, {elapsed:.2f}s)")
        return fps

    def _init_support_modules(self):
        """[Init Step] 리젝터 + DataManager 생성."""
        c = self.config
        if self._camera is not None:
            from rejecter import Rejecter                   # noqa: PLC0415

            delay_frames = c.reject_delay_frames
            collection_mode = getattr(c, 'collection_mode', 'auto')

            if collection_mode == 'continuous':
                # ── Continuous 모드: reject_delay 없이 valve delay만 사용 ──
                # 불량 감지 즉시 → pre_valve_delay 후 → valve ON
                delay_frames = 0
                print(f"[Worker:{c.line_name}] Continuous mode: "
                      f"no reject delay, valve_on={c.time_valve_on}s, "
                      f"pre_valve_delay={c.pre_valve_delay}s")

            self._rejecter = Rejecter(
                camera=self._camera,
                reject_delay_frames=delay_frames,
                reject_positions=c.reject_positions,
                reject_mode=getattr(c, 'reject_mode', 'individual'),
                time_valve_on=c.time_valve_on,
                pre_valve_delay=c.pre_valve_delay,
                debug=True,
            )
        else:
            self._rejecter = None

        from datamanager import DataManager                 # noqa: PLC0415
        self._data_manager = DataManager(save_root=c.save_root)
        if c.retention_days > 0:
            self._data_manager.cleanup_old_data(c.retention_days)

    def _default_process(self, frame, cropped):
        """
        기본 처리 흐름: AI 감지 → 박스 그리기 → 리젝트 신호 → 저장.

        역할 분리:
          - class_thresholds → 리젝트 판정만
          - save_thresholds  → 이미지 저장만

        Returns
        -------
        (annotated, is_defect) : 박스가 그려진 이미지, 불량 여부
        """
        # ── AI 감지 ───────────────────────────────────────────────────
        if self._detector is not None:
            detections = self._detector.detect(cropped)

            # save_thresholds 사용 시: 원래 class_thresholds로 is_defect 재평가
            # (detector에는 낮은 effective_thresholds를 전달했으므로)
            # 단, class_thresholds에 없는 라벨은 디텍터의 원래 판정 유지 (OCR 등)
            orig_thr = getattr(self, '_original_class_thresholds', None)
            if orig_thr is not None and self.config.save_thresholds:
                for det in detections:
                    if det.label in orig_thr:
                        det.is_defect = det.confidence >= orig_thr[det.label]
                    # else: 디텍터 원래 is_defect 유지 (OCR 패턴 매칭 등)

            annotated = self._detector.draw(cropped, detections)
            is_defect = self._detector.has_defect(detections)
        else:
            detections = []
            annotated  = cropped
            is_defect  = False

        # ── 리젝트 신호 (class_thresholds 기준) ──────────────────────
        if self._rejecter is not None:
            collection_mode = getattr(self.config, 'collection_mode', 'auto')
            if collection_mode != 'continuous':
                # Trigger/Auto: 프레임 기반 슬라이딩 윈도우 구동 + 리젝트 발사
                self._rejecter.push(is_defect=is_defect)
            # Continuous: 리젝트는 _push_frame에서 바 위치 기반으로 ON/OFF

        # ── 이미지 저장 (save_thresholds 기준) ───────────────────────
        line = self.config.line_name
        saved_category = None
        saved_dets = None
        saved_paths = (None, None)
        if self.config.save_thresholds:
            save_thr = self.config.save_thresholds
            save_dets = [
                d for d in detections
                if d.label in save_thr and d.confidence >= save_thr[d.label]
            ]
            if save_dets:
                if is_defect:
                    saved_paths = self._data_manager.save_defect(
                        image=cropped, annotated=annotated,
                        detections=detections, line_name=line,
                    )
                    saved_category = "defect"
                    saved_dets = detections
                else:
                    saved_paths = self._data_manager.save_borderline(
                        image=cropped, annotated=annotated,
                        detections=save_dets, line_name=line,
                    )
                    saved_category = "borderline"
                    saved_dets = save_dets
            elif is_defect:
                # save_thresholds에 없는 레이블(OCR 등)이지만 불량 판정된 경우
                saved_paths = self._data_manager.save_defect(
                    image=cropped, annotated=annotated,
                    detections=detections, line_name=line,
                )
                saved_category = "defect"
                saved_dets = detections
        else:
            # save_thresholds 미설정: 불량이면 저장
            if is_defect:
                saved_paths = self._data_manager.save_defect(
                    image=cropped, annotated=annotated,
                    detections=detections, line_name=line,
                )
                saved_category = "defect"
                saved_dets = detections

        # ── DEBUG: 감지 결과 추적 (borderline 진단용) ─────────────────
        if detections:
            _det_str = ", ".join(
                f"{d.label}:{d.confidence:.2f}({'R' if d.is_defect else 'b'})"
                for d in detections
            )
            _save_str = saved_category or "skip"
            print(f"[DETECT:{line}] {_det_str} → {_save_str}")

        # ── 저장 후 콜백 (S3 업로드 등) ───────────────────────────────
        if saved_category is not None and self._on_save_callback is not None:
            try:
                self._on_save_callback(
                    category=saved_category,
                    save_root=self.config.save_root,
                    line_name=line,
                    detections=saved_dets or detections,
                    saved_paths=saved_paths,
                )
            except Exception:
                pass  # 콜백 실패가 검사 루프를 중단하지 않도록

        # ── WS 즉시 전달용 defect_meta 구성 ──────────────────────────
        defect_meta = None
        if saved_category is not None and saved_paths[0] is not None:
            best_det = max(saved_dets, key=lambda d: d.confidence) if saved_dets else None
            defect_meta = {
                "defect_image_url": "/api/history/image?path=" + saved_paths[0],
                "defect_mark_url": "/api/history/image?path=" + saved_paths[1] if saved_paths[1] else None,
                "defect_class": best_det.label if best_det else "unknown",
                "defect_conf": round(best_det.confidence, 4) if best_det else 0.0,
                "defect_ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "defect_category": saved_category,
            }

        return annotated, is_defect, defect_meta

    def _async_detect_fn(self):
        """비동기 감지 스레드: 느린 디텍터(OCR/CNN)를 카메라 루프와 분리하여 실행.

        카메라 루프가 최신 프레임을 _async_input에 쓰면, 이 스레드가 꺼내서
        _default_process()를 실행한 뒤 결과를 _async_annotated에 저장합니다.
        카메라 루프는 기다리지 않고 최신 결과를 스트리밍합니다.
        """
        while not self._stop_event.is_set():
            if not self._async_event.wait(timeout=0.05):
                continue
            self._async_event.clear()

            with self._async_lock:
                item = self._async_input
                self._async_input = None

            if item is None:
                continue

            frame, cropped = item
            try:
                annotated, is_defect, defect_meta = self._default_process(frame, cropped)
                if is_defect:
                    self._defect_count += 1
                    with self._pending_rejects_lock:
                        self._pending_rejects.append(time.time())
                with self._async_result_lock:
                    self._async_annotated = annotated
                    self._async_is_defect = is_defect
                    self._async_defect_meta = defect_meta
            except Exception:
                traceback.print_exc()

    def _run_loop(self):
        """백그라운드 스레드: 단계별 초기화 → 검사 루프."""
        c = self.config

        # ── 초기화 단계 목록 구성 ────────────────────────────────────
        steps = []
        step_num = 1
        steps.append((step_num, "Camera", c.camera_type, self._init_camera))
        step_num += 1
        # inspection 모드만 지원
        det_type = getattr(c, 'detector_type', 'yolo')
        steps.append((step_num, "AI Model", det_type, self._init_detector))
        step_num += 1
        steps.append((step_num, "Rejecter / DataManager", "", self._init_support_modules))

        # 초기화 단계 수 동적 계산
        total = len(steps)
        self._init_total = total

        # ── 배너 출력 ───────────────────────────────────────────────
        banner_w = 47
        print(f"\n{'=' * banner_w}")
        print(f"  [{c.line_name}] Initialization")
        print(f"{'=' * banner_w}")

        try:
            # ── 단계별 초기화 (각 단계 최소 1초 유지) ─────────────────
            MIN_STEP_SEC = 1.0

            for num, label, detail, init_fn in steps:
                self._init_stage = label
                self._init_current = num
                tag = f"{label} ({detail})" if detail else label
                step_t0 = time.time()
                try:
                    init_fn()
                except Exception as e:
                    pad = max(2, 34 - len(f"[{num}/{total}] {tag}"))
                    print(f"  [{num}/{total}] {tag} {'.' * pad} FAIL")
                    raise RuntimeError(
                        f"Initialization failed at step [{num}/{total}] {tag}: {e}"
                    ) from e
                pad = max(2, 34 - len(f"[{num}/{total}] {tag}"))
                print(f"  [{num}/{total}] {tag} {'.' * pad} OK")
                # 최소 1초 대기 (프론트엔드에서 단계별 진행이 보이도록)
                remaining = MIN_STEP_SEC - (time.time() - step_t0)
                if remaining > 0:
                    time.sleep(remaining)

            # ── 최종 단계: Streaming started ─────────────────────────
            # STATUS_INITIALIZING 유지 → 프론트엔드가 이 단계를 볼 수 있도록
            self._init_stage = "Streaming"
            self._init_current = total
            print(f"  [{total}/{total}] Streaming started")
            print(f"{'=' * banner_w}\n")
            time.sleep(MIN_STEP_SEC * 2)
            # 최소 대기 후 RUNNING 전환
            self._init_stage = ""
            self._init_current = 0
            self._status = STATUS_RUNNING

            # 비동기 감지 스레드 시작 (느린 디텍터: paddleocr, cnn 등)
            _det_type = getattr(c, 'detector_type', 'yolo')
            self._async_detect = (_det_type not in ('yolo', None, '') and
                                   self._process_fn is None)
            if self._async_detect:
                threading.Thread(
                    target=self._async_detect_fn,
                    name=f"async-detect-{c.line_name}",
                    daemon=True,
                ).start()
                print(f"[Worker:{c.line_name}] Async detection enabled (detector={_det_type})")

            # ── 검사 루프 ────────────────────────────────────────────
            while not self._stop_event.is_set():
                loop_start = time.time()

                # 일별 통계 카운터 리셋 (자정 경과 시)
                today = date.today()
                if today != self._reset_date:
                    print(f"[Worker:{c.line_name}] Daily stats reset "
                          f"({self._reset_date} -> {today}, "
                          f"total={self._total_count}, defect={self._defect_count})")
                    self._total_count = 0
                    self._defect_count = 0
                    self._reset_date = today

                frame, cropped, triggered = self._camera.grab()
                if not triggered or frame is None:
                    continue

                defect_meta = None
                if self._process_fn is not None:
                    self._data_manager.last_saved = None  # reset before each frame
                    annotated, is_defect = self._process_fn(
                        frame, cropped,
                        detector=self._detector,
                        rejecter=self._rejecter,
                        data_manager=self._data_manager,
                        config=self.config,
                    )
                    self._total_count += 1
                    if is_defect:
                        self._defect_count += 1
                        with self._pending_rejects_lock:
                            self._pending_rejects.append(time.time())
                        # build defect_meta from DataManager.last_saved
                        ls = self._data_manager.last_saved
                        if ls is not None:
                            img_path, mark_path, saved_cat, saved_dets = ls
                            best_det = max(saved_dets, key=lambda d: d.confidence) if saved_dets else None
                            defect_meta = {
                                "defect_image_url": "/api/history/image?path=" + img_path,
                                "defect_mark_url": "/api/history/image?path=" + mark_path if mark_path else None,
                                "defect_class": best_det.label if best_det else "unknown",
                                "defect_conf": round(best_det.confidence, 4) if best_det else 0.0,
                                "defect_ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                                "defect_category": saved_cat,
                            }
                            if self._on_save_callback is not None:
                                try:
                                    self._on_save_callback(
                                        category=saved_cat,
                                        save_root=self.config.save_root,
                                        line_name=self.config.line_name,
                                        detections=saved_dets,
                                        saved_paths=(img_path, mark_path),
                                    )
                                except Exception:
                                    pass
                elif self._async_detect:
                    # 비동기 감지: 최신 프레임을 감지 스레드에 넘기고 스트리밍
                    with self._async_lock:
                        self._async_input = (frame, cropped)
                    self._async_event.set()
                    with self._async_result_lock:
                        annotated = self._async_annotated
                        is_defect = self._async_is_defect
                        defect_meta = self._async_defect_meta
                        self._async_defect_meta = None  # consume once; prevent stale repeats
                    if annotated is None:
                        annotated = cropped
                    self._total_count += 1
                else:
                    annotated, is_defect, defect_meta = self._default_process(frame, cropped)
                    self._total_count += 1
                    if is_defect:
                        self._defect_count += 1
                        with self._pending_rejects_lock:
                            self._pending_rejects.append(time.time())

                # 크롭 영역 오버레이: 전체 프레임 어둡게, 크롭 영역만 원본 밝기
                if self.config.crop_region is not None and frame is not None:
                    x1, y1, x2, y2 = self.config.crop_region
                    h_f, w_f = frame.shape[:2]
                    cx1 = max(0, x1); cy1 = max(0, y1)
                    cx2 = min(w_f, x2); cy2 = min(h_f, y2)
                    if cx2 > cx1 and cy2 > cy1:
                        display = cv2.convertScaleAbs(frame, alpha=0.3, beta=0)
                        crop_h, crop_w = cy2 - cy1, cx2 - cx1
                        if annotated.shape[:2] != (crop_h, crop_w):
                            annotated = cv2.resize(annotated, (crop_w, crop_h))
                        display[cy1:cy2, cx1:cx2] = annotated
                    else:
                        display = annotated
                else:
                    display = annotated

                elapsed = time.time() - loop_start
                inst_fps = 1.0 / max(elapsed, 1e-6)
                # 지수 이동 평균 (EMA): alpha=0.1로 부드러운 FPS 계산
                alpha = 0.1
                self._fps = alpha * inst_fps + (1 - alpha) * self._fps

                self._push_frame(display, is_defect, defect_meta)

        except Exception as e:
            import traceback as _tb
            self._last_error = str(e)
            self._status = STATUS_ERROR
            # 초기화 도중 실패했으면 배너 닫기
            if self._init_current < total:
                print(f"{'=' * banner_w}")
            print(f"\n  [{c.line_name}] ERROR: {e}")
            _tb.print_exc()
            print()
        finally:
            self._init_stage = ""
            self._init_current = 0
            self._cleanup()

    def _push_frame(self, image, is_defect: bool, defect_meta: Optional[dict] = None):
        """
        annotated 이미지를 JPEG bytes 로 인코딩하고, window meta 와 함께
        (jpeg_bytes, meta_dict) 튜플로 frame_queue 에 넣습니다.
        큐가 꽉 찼으면 오래진 프레임을 버리고 최신 프레임을 유지합니다.

        최적화:
        - 스트리밍용 해상도 축소 (너비 640px 이하)
        - JPEG 품질 50 (서버 대역폭 65% 이상 감소)
        """
        # 이미지 유효성 체크 (cropped이 0×0이거나 None이면 인코딩 실패하므로 미리 거름)
        if image is None or image.size == 0 or image.shape[0] == 0 or image.shape[1] == 0:
            print(f"[Worker:{self.config.line_name}] _push_frame: invalid image "
                  f"(shape={None if image is None else image.shape}). "
                  f"Check crop_region or detector output.")
            return

        # 불량 감지 시 영상에 빨간 테두리 그리기 (프론트 WebSocket meta 무관하게 확실히 표시)
        if is_defect:
            h, w = image.shape[:2]
            t = max(3, min(h, w) // 80)  # 테두리 두께: 이미지 크기 비례
            cv2.rectangle(image, (0, 0), (w - 1, h - 1), (0, 0, 255), t)

        # 스트리밍용 해상도 축소 (너비 640px 이상이면 리사이징)
        h, w = image.shape[:2]
        orig_w, orig_h = w, h                   # 축소 전 원본 해상도
        self._orig_frame_size = (w, h)          # 축소 전 원본 해상도 기록
        if w > 640:
            scale = 640 / w
            new_w = 640
            new_h = int(h * scale)
            image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        # JPEG 인코딩 (품질 50 → 대역폭 65% 감소, 시각적 품질은 충분)
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 50])
        if not ok:
            print(f"[Worker:{self.config.line_name}] _push_frame: cv2.imencode failed "
                  f"(image shape={image.shape}, dtype={image.dtype})")
            return
        jpeg_bytes = buf.tobytes()

        # 리젝트 바 메타: 모드별 분기
        collection_mode = getattr(self.config, 'collection_mode', 'auto')
        if collection_mode == 'continuous':
            # Continuous: valve delay + valve on time 기반 시각화
            meta = self._build_valve_bar_meta()
            # 밸브 ON/OFF: 정밀 타임스탬프 비교 (바 양자화 갭 없음)
            should_on = self._is_in_valve_window()
            if self._camera is not None:
                if should_on and not self._cont_valve_on:
                    self._camera.set_reject_output(True)
                    self._cont_valve_on = True
                    print(f"[Rejecter] CONTINUOUS ON")
                elif not should_on and self._cont_valve_on:
                    self._camera.set_reject_output(False)
                    self._cont_valve_on = False
                    print(f"[Rejecter] CONTINUOUS OFF")
        elif self._rejecter is not None:
            # Trigger/Auto: 프레임 기반 슬라이딩 윈도우 (reject_delay_frames+1 칸)
            window = self._rejecter.window_state
            win_size = len(window)  # reject_delay_frames + 1
            meta = {
                "reject_window_size": win_size,
                "reject_window_marks": [i for i, v in enumerate(window) if v == 1],
                "reject_delay_ratio": None,  # 프레임 기반은 delay_ratio 없음
            }
        else:
            meta = {"reject_window_size": 0, "reject_window_marks": [], "reject_delay_ratio": None}

        meta["is_defect"] = is_defect
        meta["orig_w"] = orig_w
        meta["orig_h"] = orig_h
        if defect_meta:
            meta.update(defect_meta)
        item = (jpeg_bytes, meta)

        # 큐가 꽉 찼으면 오래된 프레임 버리기
        try:
            self.frame_queue.put_nowait(item)
        except Full:
            try:
                self.frame_queue.get_nowait()   # 오래된 것 제거
                self.frame_queue.put_nowait(item)
            except Exception:
                pass

    def _fire_time_based_reject(self, timestamp: float):
        """Timer 콜백: 지정 시간이 지난 후 Rejecter에 즉시 발사 명령."""
        with self._pending_rejects_lock:
            if timestamp in self._pending_rejects:
                self._pending_rejects.remove(timestamp)
        self._rejecter.push(is_defect=True)

    def _is_in_valve_window(self) -> bool:
        """현재 시각이 pending defect 중 하나의 밸브 구간 안에 있는지 확인.

        각 불량 타임스탬프 t에 대해 밸브 구간:
          [t + pre_valve_delay, t + pre_valve_delay + time_valve_on)

        바 포지션 양자화 없이 정밀 비교하므로 연속 불량 시 갭이 없음.
        """
        c = self.config
        delay = c.pre_valve_delay
        valve_on = c.time_valve_on
        now = time.time()

        with self._pending_rejects_lock:
            for t in self._pending_rejects:
                elapsed = now - t
                if delay <= elapsed < delay + valve_on:
                    return True
        return False

    def _build_valve_bar_meta(self) -> dict:
        """valve delay + valve on time 기반 바 메타 생성.

        바 구성: [== delay 구간 (흰색) ==][== on time 구간 (노란색) ==]
        불량 감지 시 빨간 마크가 왼쪽→오른쪽으로 이동.
        마크가 노란 영역 도달 = 실제 리젝트 발사와 동기화.
        """
        c = self.config
        delay = c.pre_valve_delay if c else 0
        valve_on = c.time_valve_on if c else 0
        total_time = delay + valve_on
        VIRTUAL_SIZE = 20

        if total_time <= 0:
            return {"reject_window_size": VIRTUAL_SIZE, "reject_window_marks": [],
                    "reject_delay_ratio": 0.0}

        delay_ratio = delay / total_time

        now = time.time()
        marks = []
        with self._pending_rejects_lock:
            # 만료된 항목 정리
            self._pending_rejects = [
                t for t in self._pending_rejects
                if now - t < total_time
            ]
            for t in self._pending_rejects:
                progress = (now - t) / total_time  # 0.0 → 1.0
                pos = min(VIRTUAL_SIZE - 1, int(progress * VIRTUAL_SIZE))
                marks.append(pos)
        return {
            "reject_window_size": VIRTUAL_SIZE,
            "reject_window_marks": marks,
            "reject_delay_ratio": round(delay_ratio, 3),
        }

    def manual_reject(self):
        """수동 리젝트 테스트: 시간 기반이면 딜레이 후 발사, 아니면 즉시 push."""
        if self._rejecter is not None:
            collection_mode = getattr(self.config, 'collection_mode', 'auto')
            if collection_mode == 'continuous':
                # Continuous 모드: 타임스탬프 기반 (바 표시 + 밸브 ON/OFF)
                with self._pending_rejects_lock:
                    self._pending_rejects.append(time.time())
            elif self._time_based_reject:
                now = time.time()
                with self._pending_rejects_lock:
                    self._pending_rejects.append(now)
                threading.Timer(
                    self._reject_delay_sec,
                    self._fire_time_based_reject,
                    args=(now,),
                ).start()
            else:
                self._rejecter.push(is_defect=True)

    def _cleanup(self):
        """루프 종료 후 자원 해제. 리젝트 신호를 확실히 OFF."""
        if self._status != STATUS_ERROR:
            self._status = STATUS_STOPPED
        self._init_stage = ""
        self._init_current = 0
        # Continuous 모드 상태 초기화
        self._cont_valve_on = False
        # 리젝트 OFF: rejecter.reset()과 카메라 직접 OFF 이중 보장
        if self._rejecter is not None:
            self._rejecter.reset()
        if self._camera is not None:
            try:
                self._camera.set_reject_output(False)
            except Exception:
                pass
            self._camera.close()
        print(f"[Worker:{self.config.line_name}] Worker stopped.")
