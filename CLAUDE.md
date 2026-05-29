# CLAUDE.md

이 파일은 Claude Code(claude.ai/code)가 이 저장소에서 작업할 때 참고하는 가이드입니다.

## 프로젝트 개요

공장 생산 라인용 AI 비전 검사 프레임워크입니다. YOLOv12(Ultralytics)와 Basler GigE 카메라를 사용해 제품 불량을 실시간으로 감지하고, 컨베이어 벨트의 리젝트 신호를 제어합니다.

## 참고할 폴더 : inspection_framework
sample_Anseong_factory 폴더는 무시

## 프로그램 사용자 : 미국인
화면 내 텍스트는 영어
단, 프로그램 개발자는 한국사람이니 너랑 대화를 나누는건 한글로

## 기술 스택

| 영역 | 기술 |
|---|---|
| 백엔드 | Python + FastAPI |
| 프론트엔드 | React |
| Python 패키지 관리 | [uv](https://github.com/astral-sh/uv) |

## 설치

```bash
# Python 의존성 (uv 사용)
uv add ultralytics pypylon-pylon opencv-python
```

별도 빌드 과정 없음 — 순수 Python 프레임워크입니다.

## 검사 라인 실행

```bash
cd inspection_framework
python example_pinhole.py
```

`q` 키로 종료합니다. 실행 시 설정이 `configs/{line_name}.json`으로 자동 저장됩니다.

## 새 검사 라인 추가하기

1. `inspection_framework/example_pinhole.py`를 새 이름으로 복사합니다.
   예: `example_neoguri_ocr.py`
2. 파일 상단의 `InspectionConfig(...)` 블록만 수정합니다.
   (카메라 IP, 모델 경로, 클래스별 임계값, 리젝트 타이밍, 저장 경로)
3. 새 파일을 바로 실행합니다.

**절대 수정하지 말 것:** `camera.py`, `detector.py`, `rejecter.py`, `datamanager.py`, `inspection_runtime.py`
이 파일들은 프레임워크 핵심 모듈입니다.

## 아키텍처

```
inspection_framework/
├── config.py               — InspectionConfig 데이터클래스 (JSON 직렬화 지원)
├── camera.py               — BaslerCamera: GigE 카메라 캡처 + I/O 리젝트 신호
├── detector.py             — YoloDetector: YOLOv12 추론, 클래스별 임계값 처리
├── rejecter.py             — Rejecter: 프레임 딜레이를 보정해 리젝트 신호를 큐잉
├── datamanager.py          — DataManager: 불량 이미지/txt 저장 및 preview 폴더 관리
├── inspection_worker.py    — InspectionWorker: 검사 루프를 백그라운드 스레드로 실행
├── inspection_runtime.py   — run_local(): 단독 실행용 OpenCV 디스플레이 래퍼
└── example_pinhole.py      — 템플릿: 이 파일을 복사해서 새 라인을 만듭니다

sample_Anseong_factory/      — 레거시 YOLOv4/Darknet 구현체 (참고용만)
```

### 데이터 흐름

```
BaslerCamera.grab()
    → 크롭 / 회전
    → YoloDetector.detect()
    → Rejecter.push(is_defect)
    → (딜레이 후) 리젝트 I/O 신호 출력
    → DataManager.save_defect()
```

### 주요 설계 포인트

**`InspectionConfig`**
모든 설정의 단일 출처입니다. JSON으로 직렬화되어 향후 FastAPI/React UI와 연동됩니다.

**`class_thresholds`**
클래스 이름 → 신뢰도 임계값 딕셔너리입니다. 딕셔너리에 등록된 클래스만 리젝트를 트리거합니다.
`None`으로 설정하면 YOLO가 감지한 모든 클래스를 신뢰도 0.5 기준으로 불량 처리합니다.

```python
class_thresholds = {
    "defect":  0.70,   # 70% 이상만 불량 처리
    "pinhole": 0.85,   # 오탐이 잦으면 임계값을 높입니다
    "scratch": 0.60,
}
```

**`reject_delay_frames`**
카메라와 리젝트 액추에이터 사이의 물리적 거리를 프레임 수로 보정합니다.
컨베이어 속도가 바뀌면 이 값을 재조정합니다.

**`InspectionWorker`**
검사 루프를 백그라운드 스레드로 감싸며, JPEG 바이트를 담는 `frame_queue`를 제공합니다.
FastAPI WebSocket 스트리밍 및 멀티카메라 운용에 사용합니다.

**데이터 저장 구조**
DB 없이 파일시스템 계층으로 저장됩니다. `preview/`에 최신 N개 불량 이미지가 유지되고, 초과분은 `archive/`로 이동합니다.

```
{save_root}/
├── defect/
│   └── YYYY-MM-DD/
│       └── HH/
│           ├── defect-0.93-20260224-160001-line.jpg   ← 원본
│           └── defect-0.93-20260224-160001-line.txt   ← 감지 좌표
├── preview/    ← 최근 불량 이미지 (최대 N개)
├── archive/    ← preview 초과분 자동 이동
└── normal/     ← save_normal=True 일 때만 생성
```

### 디텍터 플러그인 시스템

프레임워크는 플러그인 방식으로 여러 종류의 AI 모델을 지원합니다.

**기존 빌트인 디텍터:**

| 타입 | 파일 | 용도 | `detector_config` 키 |
|---|---|---|---|
| `yolo` | `detector_yolo.py` | 객체 검출 (바운딩 박스) | 없음 |
| `paddleocr` | `detector_paddleocr.py` | 텍스트 인식 (OCR) | `lang`, `expected_text`, `use_gpu`, `rec_model_dir`, `det_model_dir` |
| `cnn` | `detector_cnn.py` | 이미지 분류 (전체 이미지) | `input_size`, `class_names` |

**새 디텍터 추가 절차:**

1. `inspection_framework/detector_{타입명}.py` 파일을 생성합니다.
2. `BaseDetector`를 상속하고 `@register_detector("타입명")`으로 장식합니다.
3. `__init__()` 와 `detect()` 를 구현합니다.
4. (선택) `detector.py`의 `_BUILTIN_MODULES` 딕셔너리에 등록합니다.

```python
# detector_mytype.py
from detector import BaseDetector, DetectionResult, register_detector

@register_detector("mytype")
class MyDetector(BaseDetector):
    def __init__(self, model_path, class_thresholds=None,
                 device='cuda', detector_config=None):
        # model_path       : str  — 모델 파일 경로 (YOLO .pt, CNN .pth 등)
        # class_thresholds : dict | None — {"클래스명": 0.0~1.0} 임계값 딕셔너리
        # device           : str  — "cuda" 또는 "cpu"
        # detector_config  : dict | None — 이 디텍터 고유 설정 (프론트에서 전달)
        dc = detector_config or {}
        # dc에서 커스텀 설정값 꺼내기
        ...

    def detect(self, image_bgr) -> list[DetectionResult]:
        # image_bgr : np.ndarray (OpenCV BGR 이미지)
        # 반환값: DetectionResult 리스트
        return [DetectionResult(
            label="defect",       # str: 클래스 이름
            confidence=0.95,      # float: 0.0~1.0
            bbox_xyxy=[x1,y1,x2,y2],  # List[int]: 바운딩 박스 좌표
            is_defect=True,       # bool: 불량 여부 (리젝트 트리거)
            class_threshold=0.70, # float: 적용된 임계값
        )]

    # (선택) 시각화 커스터마이징
    def draw(self, image_bgr, detections) -> np.ndarray:
        ...
```

**주의사항:**

- **파일 명명 규칙**: 반드시 `detector_{타입명}.py` 형식. `create_detector()`가 이 규칙으로 모듈을 자동 임포트합니다.
- **`__init__` 시그니처 고정**: 인자 순서와 이름은 `(model_path, class_thresholds, device, detector_config)` 그대로 유지해야 합니다. 팩토리 함수가 이 키워드 인자로 호출합니다.
- **`detect()` 반환 타입 고정**: 반드시 `List[DetectionResult]`을 반환해야 합니다. 빈 리스트 `[]`가 "정상(불량 없음)"을 의미합니다.
- **`detector_config`로 확장**: 디텍터 고유 설정은 모두 `detector_config` 딕셔너리를 통해 전달합니다. 프론트엔드 LineModal에서 디텍터 타입별 설정 UI를 조건부로 렌더링합니다.
- **제품(Product) 레벨 필드**: `detector_type`과 `detector_config`는 제품별 설정입니다. 같은 라인(카메라)에서 제품 A는 YOLO, 제품 B는 PaddleOCR처럼 다른 디텍터를 쓸 수 있습니다.
- **Lazy import 권장**: 무거운 라이브러리(`torch`, `paddleocr`, `ultralytics`)는 `__init__` 안에서 임포트합니다. 안 쓰는 디텍터의 의존성까지 강제하지 않기 위함입니다.
- **`detector.py` 수정 금지**: 새 디텍터를 추가할 때 `detector.py` 핵심 로직은 건드리지 않습니다. `_BUILTIN_MODULES` 딕셔너리에 한 줄 추가만 허용됩니다.

**프론트엔드 연동 (LineModal.tsx):**

새 디텍터의 설정 UI를 추가하려면 `LineModal.tsx`에서:
1. `handleDetectorTypeChange` 함수의 `defaults` 객체에 새 타입 기본값 추가
2. `{/* CNN Configuration */}` 블록 아래에 조건부 렌더링 블록 추가
3. 디텍터 타입 선택 버튼 배열에 새 항목 추가

### 감지 후 커스텀 콜백 추가

```python
def on_defect(detections):
    send_slack_alert(detections)   # 슬랙 알림, PLC 신호, DB 저장 등

runtime = InspectionRuntime(..., on_defect_callback=on_defect)
```


# main 프로그램
worker : 최대 10개 허용
실시간 영상 보여주기

## 화면 주요 기능
1.구성
    카메라영상 보여주기 : 최대 10개, 설정가능, predict 된 영역 포함
    관리자 페이지
    영상 영역별 : 
        사진만 모으기 기능(모달 팝업 처리)
        개별 실행/정지/리젝트 중지(리젝트 중지는 관리자 암호 필요)
        


