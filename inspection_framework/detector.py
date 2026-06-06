"""
detector.py — Detector Plugin Framework
=========================================

[Role]
    Base class, registry, and factory for all detector plugins.
    Concrete detectors live in separate files:
        detector_yolo.py, detector_paddleocr.py, detector_cnn.py, ...

[Adding a new detector]
    1. Create detector_mytype.py
    2. Subclass BaseDetector and decorate with @register_detector("mytype")
    3. Implement __init__() and detect()
    4. (Optional) Add "mytype": "detector_mytype" to _BUILTIN_MODULES below

    Example:
        from detector import BaseDetector, DetectionResult, register_detector

        @register_detector("mytype")
        class MyDetector(BaseDetector):
            def __init__(self, model_path, class_thresholds=None,
                         device='cuda', detector_config=None):
                ...
            def detect(self, image_bgr):
                ...
                return [DetectionResult(...)]
"""

import cv2
import numpy as np
import importlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional


# ──────────────────────────────────────────────────────────────────────
#  DetectionResult (모든 디텍터가 공유하는 결과 데이터 클래스)
# ──────────────────────────────────────────────────────────────────────

@dataclass
class DetectionResult:
    """
    Single detection result.

    Attributes
    ----------
    label           : Detected class name (e.g. 'defect', 'pinhole', 'text:Hello')
    confidence      : Confidence score (0.0 ~ 1.0)
    bbox_xyxy       : Bounding box [x1, y1, x2, y2] in pixels.
                      For classifiers without localization, use full image bounds.
    is_defect       : Whether this detection is a defect
    class_threshold : Threshold applied to this class
    """
    label: str
    confidence: float
    bbox_xyxy: List[int]
    is_defect: bool = True
    class_threshold: float = 0.5
    recognized_text: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────
#  Registry
# ──────────────────────────────────────────────────────────────────────

_DETECTOR_REGISTRY: Dict[str, type] = {}


def register_detector(name: str):
    """
    Class decorator to register a detector plugin.

    Usage:
        @register_detector("yolo")
        class YoloDetector(BaseDetector):
            ...
    """
    def wrapper(cls):
        _DETECTOR_REGISTRY[name] = cls
        return cls
    return wrapper


def list_detector_types() -> List[str]:
    """Return sorted list of registered detector type names."""
    _load_builtins()
    return sorted(_DETECTOR_REGISTRY.keys())


# ──────────────────────────────────────────────────────────────────────
#  BaseDetector ABC
# ──────────────────────────────────────────────────────────────────────

class BaseDetector(ABC):
    """
    Abstract base for all detector plugins.

    Required to implement:
        __init__(model_path, class_thresholds, device, detector_config)
        detect(image_bgr) -> List[DetectionResult]

    Optional to override:
        draw(image_bgr, detections) -> annotated image
        has_defect(detections) -> bool
    """

    @abstractmethod
    def __init__(
        self,
        model_path: str,
        class_thresholds: Optional[Dict[str, float]] = None,
        device: str = 'cuda',
        detector_config: Optional[Dict] = None,
    ):
        ...

    @abstractmethod
    def detect(self, image_bgr: np.ndarray) -> List[DetectionResult]:
        """Run inference and return detection results."""
        ...

    def draw(
        self,
        image_bgr: np.ndarray,
        detections: List[DetectionResult],
    ) -> np.ndarray:
        """
        Draw detection results on image.
        Default: red box = defect, green box = normal.
        Subclasses can override for custom visualization.
        """
        annotated = image_bgr.copy()

        for det in detections:
            x1, y1, x2, y2 = det.bbox_xyxy
            color = (0, 0, 255) if det.is_defect else (0, 200, 0)
            label_text = f"{det.label} {det.confidence:.2f}"

            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                annotated, label_text,
                (x1, max(y1 - 8, 0)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2,
            )

        return annotated

    def has_defect(self, detections: List[DetectionResult]) -> bool:
        """Return True if any detection is a defect."""
        return any(d.is_defect for d in detections)

    def set_class_thresholds(self, class_thresholds: Optional[Dict[str, float]]) -> None:
        """
        Update class thresholds at runtime.

        Parameters
        ----------
        class_thresholds : dict or None
            New class thresholds (e.g., {"defect": 0.70, "pinhole": 0.85})
        """
        # Default implementation: subclasses should override if they store thresholds
        pass


# ──────────────────────────────────────────────────────────────────────
#  Factory
# ──────────────────────────────────────────────────────────────────────

# Built-in detector modules for auto-import.
# To add a new built-in detector, add one entry here.
_BUILTIN_MODULES = {
    "yolo":       "detector_yolo",
    "paddleocr":  "detector_paddleocr",
    "paddlert":   "detector_paddlert",
    "cnn":        "detector_cnn",
}

_builtins_loaded = set()


def _load_builtins():
    """Try importing all known built-in detector modules (silently skip missing)."""
    for name, mod_name in _BUILTIN_MODULES.items():
        if name not in _builtins_loaded:
            try:
                importlib.import_module(mod_name)
            except ImportError:
                pass
            _builtins_loaded.add(name)


def create_detector(
    detector_type: str = "yolo",
    *,
    model_path: str,
    class_thresholds: Optional[Dict[str, float]] = None,
    device: str = 'cuda',
    detector_config: Optional[Dict] = None,
) -> BaseDetector:
    """
    Factory: create a detector instance by type name.

    Auto-imports the corresponding module if not yet loaded.
    Convention: detector type "xyz" → module "detector_xyz".

    Parameters
    ----------
    detector_type    : "yolo", "paddleocr", "cnn", or any registered name
    model_path       : Model weights / directory path
    class_thresholds : Per-class confidence thresholds
    device           : 'cuda' or 'cpu'
    detector_config  : Detector-specific extra settings (dict)
    """
    # Auto-import if not yet registered
    if detector_type not in _DETECTOR_REGISTRY:
        mod_name = _BUILTIN_MODULES.get(detector_type, f"detector_{detector_type}")
        try:
            importlib.import_module(mod_name)
        except ImportError as e:
            raise ImportError(
                f"[Detector] '{detector_type}' detector not found. "
                f"Tried importing '{mod_name}'. Error: {e}"
            )

    cls = _DETECTOR_REGISTRY.get(detector_type)
    if cls is None:
        raise ValueError(
            f"[Detector] '{detector_type}' is not registered. "
            f"Available: {list(_DETECTOR_REGISTRY.keys())}"
        )

    return cls(
        model_path=model_path,
        class_thresholds=class_thresholds,
        device=device,
        detector_config=detector_config,
    )


# ──────────────────────────────────────────────────────────────────────
#  Backward compatibility: `from detector import YoloDetector`
# ──────────────────────────────────────────────────────────────────────
try:
    from detector_yolo import YoloDetector  # noqa: F401
except ImportError:
    pass
