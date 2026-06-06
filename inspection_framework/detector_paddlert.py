"""
detector_paddlert.py — PaddleRT OCR Plugin (ONNX + TensorRT auto)
==================================================================

[Role]
    PaddleOCR PP-OCRv5 모델을 ONNX Runtime으로 직접 실행합니다.
    하드웨어에 따라 자동으로 최적 실행 환경을 선택합니다:
      - Jetson AGX Orin : TensorRT provider (FP16, 첫 실행 시 캐시 빌드)
      - PC (CUDA GPU)   : CUDA provider
      - CPU fallback    : CPU provider

    PaddleOCR와 동일한 모델을 사용하므로 정확도가 동일합니다.

[Setup — 개발 PC]
    bash tools/export_onnx.sh
    uv add onnxruntime-gpu

[Setup — Jetson AGX Orin JetPack 6.x]
    pip install paddlepaddle paddleocr paddle2onnx
    bash tools/export_onnx.sh
    pip install onnxruntime-gpu

[Model Directory]
    {model_path}/
    ├── det.onnx   — PP-OCRv5 Detection
    ├── rec.onnx   — PP-OCRv5 Recognition
    └── dict.txt   — 문자 사전 (한 줄에 한 글자)

[detector_config keys]
    change_date        : str   = None         — 날짜 패턴 정규식
    class_name         : str   = "date_check" — 불량 저장 폴더명
    min_confidence     : float = 0.0          — 패턴 매칭 최소 신뢰도
    required_texts     : list  = []           — 필수 텍스트
    required_texts_mode: str   = "and"        — "and" | "or"
    det_limit_side_len : int   = 960          — Detection 최대 해상도
    rec_batch_num      : int   = 6            — Recognition 배치 크기
    db_thresh          : float = 0.3          — DB 이진화 임계값
    db_box_thresh      : float = 0.6          — 박스 신뢰도 임계값
    db_unclip_ratio    : float = 1.5          — 박스 확장 비율
"""

import os
import re
import glob
import ctypes
import cv2
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional

from detector import BaseDetector, DetectionResult, register_detector


def _preload_cudnn():
    """
    onnxruntime-gpu가 pip nvidia-cudnn-cu12 패키지의 cuDNN 9를 찾지 못할 때
    ctypes로 미리 로드해서 CUDA provider가 정상 동작하도록 합니다.
    """
    import sys
    search_paths = []

    # venv 내 nvidia/cudnn/lib 경로 탐색
    for p in sys.path:
        candidate = os.path.join(p, 'nvidia', 'cudnn', 'lib')
        if os.path.isdir(candidate):
            search_paths.append(candidate)

    # 시스템 CUDA 경로
    search_paths += glob.glob('/usr/local/cuda*/targets/*/lib')

    for lib_dir in search_paths:
        lib = os.path.join(lib_dir, 'libcudnn.so.9')
        if os.path.exists(lib):
            try:
                ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
                # LD_LIBRARY_PATH에도 추가 (하위 라이브러리 로드 보장)
                old = os.environ.get('LD_LIBRARY_PATH', '')
                if lib_dir not in old:
                    os.environ['LD_LIBRARY_PATH'] = lib_dir + (f':{old}' if old else '')
                return True
            except Exception:
                pass
    return False


_cudnn_loaded = _preload_cudnn()


@register_detector("paddlert")
class PaddleRtDetector(BaseDetector):

    # PP-OCRv5 det 정규화 상수 (inference.yml 기준)
    _DET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    _DET_STD  = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    _REC_MEAN = 0.5
    _REC_STD  = 0.5

    def __init__(
        self,
        model_path: str,
        class_thresholds: Optional[Dict[str, float]] = None,
        device: str = 'cuda',
        detector_config: Optional[Dict] = None,
    ):
        import onnxruntime as ort

        dc = detector_config or {}
        self.class_thresholds = class_thresholds
        self.change_date = dc.get("change_date")
        self.class_name = dc.get("class_name", "date_check")
        self.min_confidence = float(dc.get("min_confidence", 0.0))
        self.required_texts = [t.strip() for t in dc.get("required_texts", []) if t and t.strip()]
        self.required_texts_mode = dc.get("required_texts_mode", "and")

        model_dir = Path(model_path)
        for fname in ("det.onnx", "rec.onnx", "dict.txt"):
            if not (model_dir / fname).exists():
                raise FileNotFoundError(
                    f"[Detector:PaddleRT] {fname} not found in {model_dir}\n"
                    "  → Run: bash tools/export_onnx.sh"
                )

        self._dict = (model_dir / "dict.txt").read_text(encoding="utf-8").strip().split("\n")

        # Provider 선택: TRT → CUDA → CPU
        # onnxruntime은 TRT/CUDA가 available 목록에 있어도 실제 .so 없으면 에러 → 미리 확인
        available = ort.get_available_providers()
        use_gpu   = device.lower() != "cpu"

        def _lib_exists(lib_name: str) -> bool:
            """LD_LIBRARY_PATH + 표준 경로에서 라이브러리 존재 여부 확인."""
            search_dirs = os.environ.get('LD_LIBRARY_PATH', '').split(':')
            search_dirs += ['/usr/lib', '/usr/local/lib', '/usr/local/cuda/lib64']
            for d in search_dirs:
                if d and os.path.exists(os.path.join(d, lib_name)):
                    return True
            return False

        trt_available  = "TensorrtExecutionProvider" in available and _lib_exists("libnvinfer.so.10")
        cuda_available = "CUDAExecutionProvider"     in available

        if use_gpu and trt_available:
            cache = str(model_dir / ".trt_cache")
            os.makedirs(cache, exist_ok=True)
            providers = [
                ("TensorrtExecutionProvider", {
                    "trt_fp16_enable":         True,
                    "trt_engine_cache_enable":  True,
                    "trt_engine_cache_path":    cache,
                    "trt_max_workspace_size":   1 << 30,
                }),
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            ]
            backend = "TensorRT FP16"
        elif use_gpu and cuda_available:
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            backend = "CUDA"
        else:
            providers = ["CPUExecutionProvider"]
            backend = "CPU"

        opts = ort.SessionOptions()
        opts.log_severity_level = 3

        print(f"[Detector:PaddleRT] Backend: {backend}")
        print(f"[Detector:PaddleRT] Model dir: {model_dir}")
        self._det = ort.InferenceSession(str(model_dir / "det.onnx"), sess_options=opts, providers=providers)
        self._rec = ort.InferenceSession(str(model_dir / "rec.onnx"), sess_options=opts, providers=providers)
        self._det_in = self._det.get_inputs()[0].name
        self._rec_in = self._rec.get_inputs()[0].name

        self._det_limit     = int(dc.get("det_limit_side_len", 960))
        self._rec_batch     = int(dc.get("rec_batch_num", 6))
        self._db_thresh     = float(dc.get("db_thresh", 0.3))
        self._db_box_thresh = float(dc.get("db_box_thresh", 0.6))
        self._db_unclip     = float(dc.get("db_unclip_ratio", 1.5))

        print(f"[Detector:PaddleRT] Ready — dict={len(self._dict)}자  "
              f"det_limit={self._det_limit}  rec_batch={self._rec_batch}")

    # ── Detection ─────────────────────────────────────────────────────────────

    def _preprocess_det(self, img: np.ndarray):
        h, w = img.shape[:2]
        ratio = self._det_limit / max(h, w)
        nh = max(32, round(h * ratio / 32) * 32)
        nw = max(32, round(w * ratio / 32) * 32)
        resized = cv2.resize(img, (nw, nh))
        x = resized.astype(np.float32) / 255.0
        x = (x - self._DET_MEAN) / self._DET_STD
        return x.transpose(2, 0, 1)[np.newaxis].astype(np.float32), h / nh, w / nw

    def _db_postprocess(self, prob: np.ndarray, sh: float, sw: float) -> list:
        bitmap  = (prob > self._db_thresh).astype(np.uint8)
        contours, _ = cv2.findContours(bitmap, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for cnt in contours:
            if len(cnt) < 4:
                continue
            rect = cv2.minAreaRect(cnt)
            box  = cv2.boxPoints(rect).astype(np.float32)
            if min(rect[1]) < 3:
                continue
            if self._box_score(prob, box) < self._db_box_thresh:
                continue
            box = self._unclip(box)
            if box is None:
                continue
            box[:, 0] = np.clip(box[:, 0] * sw, 0, prob.shape[1] * sw)
            box[:, 1] = np.clip(box[:, 1] * sh, 0, prob.shape[0] * sh)
            boxes.append(box)
        return boxes

    def _box_score(self, bitmap: np.ndarray, box: np.ndarray) -> float:
        h, w = bitmap.shape
        b = box.astype(int)
        x1 = np.clip(b[:, 0].min(), 0, w-1); x2 = np.clip(b[:, 0].max(), 0, w-1)
        y1 = np.clip(b[:, 1].min(), 0, h-1); y2 = np.clip(b[:, 1].max(), 0, h-1)
        mask = np.zeros((y2-y1+1, x2-x1+1), dtype=np.uint8)
        lb = b.copy(); lb[:, 0] -= x1; lb[:, 1] -= y1
        cv2.fillPoly(mask, [lb], 1)
        return float(cv2.mean(bitmap[y1:y2+1, x1:x2+1], mask=mask)[0])

    def _unclip(self, box: np.ndarray) -> Optional[np.ndarray]:
        try:
            from shapely.geometry import Polygon
            poly = Polygon(box)
            if not poly.is_valid or poly.area < 1:
                return None
            expanded = poly.buffer(poly.area * self._db_unclip / poly.length)
            coords   = np.array(expanded.exterior.coords[:-1], dtype=np.float32)
            return cv2.boxPoints(cv2.minAreaRect(coords)).astype(np.float32)
        except ImportError:
            cx = box.mean(axis=0)
            return (cx + (box - cx) * self._db_unclip).astype(np.float32)

    # ── Recognition ───────────────────────────────────────────────────────────

    def _order_pts(self, pts: np.ndarray) -> np.ndarray:
        s, d = pts.sum(1), np.diff(pts, axis=1)
        r = np.zeros((4, 2), dtype=np.float32)
        r[0]=pts[np.argmin(s)]; r[2]=pts[np.argmax(s)]
        r[1]=pts[np.argmin(d)]; r[3]=pts[np.argmax(d)]
        return r

    def _crop_text(self, img: np.ndarray, box: np.ndarray) -> np.ndarray:
        pts = self._order_pts(box.astype(np.float32))
        w = int(max(np.linalg.norm(pts[0]-pts[1]), np.linalg.norm(pts[2]-pts[3])))
        h = int(max(np.linalg.norm(pts[0]-pts[3]), np.linalg.norm(pts[1]-pts[2])))
        w, h = max(w, 1), max(h, 1)
        dst  = np.array([[0,0],[w-1,0],[w-1,h-1],[0,h-1]], dtype=np.float32)
        M    = cv2.getPerspectiveTransform(pts, dst)
        crop = cv2.warpPerspective(img, M, (w, h), borderMode=cv2.BORDER_REPLICATE)
        if h > w * 1.5:
            crop = cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)
        return crop

    def _preprocess_rec(self, crops: list) -> np.ndarray:
        imgs = []
        for c in crops:
            ch, cw = c.shape[:2]
            tw = max(int(cw * 48 / ch), 1)
            imgs.append(cv2.resize(c, (min(tw, 3200), 48)))
        max_w = max(x.shape[1] for x in imgs)
        batch = []
        for x in imgs:
            x = x.astype(np.float32) / 255.0
            x = (x - self._REC_MEAN) / self._REC_STD
            x = x.transpose(2, 0, 1)
            pad = max_w - x.shape[2]
            if pad > 0:
                x = np.pad(x, ((0,0),(0,0),(0,pad)))
            batch.append(x)
        return np.stack(batch).astype(np.float32)

    def _ctc_decode(self, logits: np.ndarray):
        idx  = np.argmax(logits, axis=-1)
        text, prev = "", -1
        for i in map(int, idx):
            if i > 0 and i != prev and (i-1) < len(self._dict):
                text += self._dict[i-1]
            prev = i
        conf = float(np.exp(np.max(logits, axis=-1)).mean()) if len(logits) else 0.0
        return text, min(conf, 1.0)

    # ── Main ──────────────────────────────────────────────────────────────────

    def detect(self, image_bgr: np.ndarray) -> List[DetectionResult]:
        if image_bgr is None:
            return []

        # 1. Detection
        inp, sh, sw = self._preprocess_det(image_bgr)
        prob  = self._det.run(None, {self._det_in: inp})[0][0, 0]
        boxes = self._db_postprocess(prob, sh, sw)

        has_check = bool(self.change_date or self.required_texts)
        if not boxes:
            if has_check:
                return [DetectionResult(
                    label=f"text:{self.class_name}", confidence=0.0,
                    bbox_xyxy=[0, 0, 100, 100], is_defect=True,
                    class_threshold=1.0, recognized_text="(no text found)",
                )]
            return []

        # 2. Recognition (batched)
        crops = [self._crop_text(image_bgr, b) for b in boxes]
        rec   = []
        for i in range(0, len(crops), self._rec_batch):
            tensor = self._preprocess_rec(crops[i:i+self._rec_batch])
            out    = self._rec.run(None, {self._rec_in: tensor})[0]
            # shape: (batch,T,cls) or (T,batch,cls)
            if out.ndim == 3 and out.shape[0] == tensor.shape[0]:
                for j in range(out.shape[0]): rec.append(self._ctc_decode(out[j]))
            else:
                out = out.transpose(1, 0, 2)
                for j in range(out.shape[0]): rec.append(self._ctc_decode(out[j]))

        # 3. 텍스트 데이터
        texts = []
        for box, (text, conf) in zip(boxes, rec):
            texts.append({
                "text": text, "confidence": conf,
                "bbox": [int(box[:,0].min()), int(box[:,1].min()),
                         int(box[:,0].max()), int(box[:,1].max())],
            })

        # 4. 날짜 패턴
        date_ok = True
        if self.change_date:
            pat = re.sub(r'\\?\.', '[.,]?', self.change_date.replace(" ", ""))
            date_ok = False
            for d in texts:
                if d["confidence"] >= self.min_confidence:
                    if re.search(pat, d["text"].replace(" ","").replace(",",".")):
                        date_ok = True; break

        # 5. 필수 텍스트
        texts_ok = True
        if self.required_texts:
            def _found(req):
                return any(req in d["text"] and d["confidence"] >= self.min_confidence
                           for d in texts)
            fn = any if self.required_texts_mode == "or" else all
            texts_ok = fn(_found(t) for t in self.required_texts)

        is_defect = not (date_ok and texts_ok) if has_check else False

        return [
            DetectionResult(
                label=f"text:{self.class_name}",
                confidence=d["confidence"],
                bbox_xyxy=d["bbox"],
                is_defect=is_defect,
                class_threshold=1.0,
                recognized_text=d["text"],
            )
            for d in texts
        ]

    # ── Visualization ─────────────────────────────────────────────────────────

    _FONTS = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/AppleGothic.ttf",
        "C:/Windows/Fonts/malgun.ttf",
    ]

    @classmethod
    def _pil_font(cls, size: int):
        from PIL import ImageFont
        for fp in cls._FONTS:
            if os.path.exists(fp):
                try: return ImageFont.truetype(fp, size)
                except Exception: pass
        return ImageFont.load_default()

    def draw(self, image_bgr: np.ndarray, detections: List[DetectionResult]) -> np.ndarray:
        from PIL import Image, ImageDraw
        h, w  = image_bgr.shape[:2]
        fsz   = max(18, int(min(h, w) / 28))
        pad   = max(4, fsz // 4)
        font  = self._pil_font(fsz)
        pil   = Image.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))
        draw  = ImageDraw.Draw(pil)
        for det in detections:
            x1, y1, x2, y2 = det.bbox_xyxy
            col   = (220, 30, 30) if det.is_defect else (0, 200, 0)
            draw.rectangle([x1, y1, x2, y2], outline=col, width=2)
            txt   = det.recognized_text or det.label.replace("text:", "")
            label = f'"{txt}" ({det.confidence:.2f})'
            bb    = draw.textbbox((0, 0), label, font=font)
            tw, th = bb[2]-bb[0], bb[3]-bb[1]
            ty = y1 - th - pad*2 if y1 - th - pad*2 >= 0 else y2
            draw.rectangle([x1, ty, x1+tw+pad*2, ty+th+pad*2], fill=col)
            draw.text((x1+pad, ty+pad), label, font=font, fill=(255, 255, 255))
        return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

    def set_change_date(self, change_date: Optional[str]) -> None:
        self.change_date = change_date
        print(f"[Detector:PaddleRT] change_date → {change_date}")
