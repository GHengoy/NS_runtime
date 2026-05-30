"""
test_run_local.py — Worker run_local.py process_frame 동작 검증 테스트

테스트 항목:
    R1. collection_mode='continuous' → rejecter.push() 호출 안 함
    R2. collection_mode='auto'       → rejecter.push() 호출됨
    R3. rejecter=None                → 오류 없이 (annotated, bool) 반환
    R4. collection_mode='continuous' + 불량 감지 시에도 push 호출 안 함
"""
import importlib.util
import os
import sys

import numpy as np
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
_WORKER01_PATH = os.path.join(_ROOT, 'workers', 'worker-01', 'run_local.py')


def _load_process_frame(worker_path: str):
    spec = importlib.util.spec_from_file_location('_run_local_under_test', worker_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.process_frame


_process_frame = _load_process_frame(_WORKER01_PATH)


class _FakeDataManager:
    def __init__(self):
        self.last_saved = None  # mirrors DataManager.last_saved

    def save_defect(self, **kwargs):
        pass

    def save_normal(self, image, line_name):
        pass


def _make_frame() -> np.ndarray:
    return np.zeros((480, 640, 3), dtype=np.uint8)


def _make_detector(is_defect: bool = False) -> MagicMock:
    det = MagicMock()
    det.detect.return_value = []
    det.draw.return_value = _make_frame()
    det.has_defect.return_value = is_defect
    return det


def _make_config(collection_mode: str = 'auto') -> MagicMock:
    cfg = MagicMock()
    cfg.collection_mode = collection_mode
    cfg.class_thresholds = None
    cfg.save_thresholds = False
    cfg.line_name = 'test_line'
    return cfg


def _make_config_with_yolo_thresholds(collection_mode: str = 'auto') -> MagicMock:
    """OCR 제품 설정처럼 YOLO class_thresholds/save_thresholds가 남아있는 경우."""
    cfg = MagicMock()
    cfg.collection_mode = collection_mode
    cfg.class_thresholds = {"person": 0.7, "car": 0.7}
    cfg.save_thresholds = {"person": 0.6, "car": 0.6}
    cfg.line_name = 'test_line'
    return cfg


def _make_ocr_detector(is_defect: bool) -> MagicMock:
    """OCR 디텍터: label이 'text:date_check', is_defect는 패턴 매칭 결과."""
    from unittest.mock import MagicMock
    from inspection_framework.detector import DetectionResult

    det = MagicMock()
    result = DetectionResult(
        label="text:date_check",
        confidence=0.9,
        bbox_xyxy=[0, 0, 100, 30],
        is_defect=is_defect,
        class_threshold=1.0,
        recognized_text="2025.01.01" if not is_defect else "WRONG",
    )
    det.detect.return_value = [result]
    det.draw.return_value = _make_frame()
    det.has_defect.side_effect = lambda dets: any(d.is_defect for d in dets)
    return det


# ── R1 ───────────────────────────────────────────────────────────────────────

def test_R1_continuous_mode_skips_rejecter_push():
    """collection_mode='continuous' 이면 rejecter.push()를 호출하지 않는다."""
    frame = _make_frame()
    rejecter = MagicMock()

    _process_frame(
        frame, frame,
        detector=_make_detector(is_defect=False),
        rejecter=rejecter,
        data_manager=_FakeDataManager(),
        config=_make_config(collection_mode='continuous'),
    )

    rejecter.push.assert_not_called()


# ── R2 ───────────────────────────────────────────────────────────────────────

def test_R2_auto_mode_calls_rejecter_push():
    """collection_mode='auto' 이면 rejecter.push()를 한 번 호출한다."""
    frame = _make_frame()
    rejecter = MagicMock()

    _process_frame(
        frame, frame,
        detector=_make_detector(is_defect=False),
        rejecter=rejecter,
        data_manager=_FakeDataManager(),
        config=_make_config(collection_mode='auto'),
    )

    rejecter.push.assert_called_once()


# ── R3 ───────────────────────────────────────────────────────────────────────

def test_R3_none_rejecter_returns_tuple():
    """rejecter=None 이면 예외 없이 (annotated_image, bool) 튜플을 반환한다."""
    frame = _make_frame()

    result = _process_frame(
        frame, frame,
        detector=_make_detector(is_defect=False),
        rejecter=None,
        data_manager=_FakeDataManager(),
        config=_make_config(collection_mode='auto'),
    )

    assert isinstance(result, tuple) and len(result) == 2
    annotated, is_defect = result
    assert isinstance(annotated, np.ndarray)
    assert isinstance(is_defect, bool)


# ── R4 ───────────────────────────────────────────────────────────────────────

def test_R4_continuous_mode_with_defect_skips_push():
    """collection_mode='continuous' 이면 불량이 감지되더라도 rejecter.push()를 호출하지 않는다."""
    frame = _make_frame()
    rejecter = MagicMock()

    _process_frame(
        frame, frame,
        detector=_make_detector(is_defect=True),
        rejecter=rejecter,
        data_manager=_FakeDataManager(),
        config=_make_config(collection_mode='continuous'),
    )

    rejecter.push.assert_not_called()


# ── R5 ───────────────────────────────────────────────────────────────────────

def test_R5_ocr_defect_with_yolo_thresholds_calls_push_with_defect():
    """
    OCR 제품에 YOLO class_thresholds/save_thresholds가 설정되어 있어도
    OCR 패턴 불일치(is_defect=True)가 rejecter.push(is_defect=True)로 전달된다.

    버그 재현: label='text:date_check'가 class_thresholds에 없으면
    이전 코드는 is_defect=False로 강제 덮어써서 리젝트가 안 됨.
    """
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'inspection_framework'))

    frame = _make_frame()
    rejecter = MagicMock()

    _process_frame(
        frame, frame,
        detector=_make_ocr_detector(is_defect=True),
        rejecter=rejecter,
        data_manager=_FakeDataManager(),
        config=_make_config_with_yolo_thresholds(collection_mode='auto'),
    )

    rejecter.push.assert_called_once()
    _, kwargs = rejecter.push.call_args
    assert kwargs.get('is_defect') is True, "OCR 불량이 리젝트 신호로 전달되어야 합니다"


# ── R6 ───────────────────────────────────────────────────────────────────────

def test_R6_ocr_normal_with_yolo_thresholds_calls_push_without_defect():
    """
    OCR 패턴 일치(is_defect=False)이면 rejecter.push(is_defect=False)가 호출된다.
    """
    frame = _make_frame()
    rejecter = MagicMock()

    _process_frame(
        frame, frame,
        detector=_make_ocr_detector(is_defect=False),
        rejecter=rejecter,
        data_manager=_FakeDataManager(),
        config=_make_config_with_yolo_thresholds(collection_mode='auto'),
    )

    rejecter.push.assert_called_once()
    _, kwargs = rejecter.push.call_args
    assert kwargs.get('is_defect') is False, "OCR 정상은 리젝트 신호 없이 전달되어야 합니다"
