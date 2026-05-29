# PaddleOCR 설정 가이드

## 📋 **사용 가능한 프리셋**

### 1️⃣ **속도 최우선** ⚡
```
configs/paddleocr_date_check.json
```
- **FPS**: 20~30 fps
- **용도**: 실시간 검사, 고속 라인
- **설정**:
  - `use_angle_cls: false` (회전 감지 OFF)
  - `det_limit_side_len: 480` (작은 이미지)
  - `rec_batch_num: 10` (큰 배치)

### 2️⃣ **균형** ⚖️
```
configs/paddleocr_date_check_balanced.json
```
- **FPS**: 10~15 fps
- **용도**: 일반적인 검사
- **설정**:
  - `use_angle_cls: true`
  - `det_limit_side_len: 960` (기본값)
  - `rec_batch_num: 6`

### 3️⃣ **고정확도** 🎯
```
configs/paddleocr_date_check_accurate.json
```
- **FPS**: 5~10 fps
- **용도**: 품질 중심 검사
- **설정**:
  - `use_angle_cls: true`
  - `det_limit_side_len: 1280` (큰 이미지)
  - `rec_batch_num: 3` (작은 배치)
  - `use_dilation: true` (영역 확대)

---

## 🚀 **사용 방법**

### **Worker에서 설정 파일 로드**

```python
from config import InspectionConfig

# 1. 파일에서 로드
config = InspectionConfig.from_json("./configs/paddleocr_date_check.json")

# 2. 라인 시작
from inspection_runtime import run_local
run_local(config)
```

### **변수 커스터마이징**

각 JSON 파일에서 다음을 수정하세요:

```json
{
  "change_date": "2026\\.06\\.01",  // ← 검사할 날짜 패턴 변경
  "class_name": "date_check",       // ← 저장 폴더명 변경
  "camera_ip": "0",                 // ← 카메라 선택
  "save_root": "./data"             // ← 저장 경로
}
```

---

## ⚙️ **파라미터 상세 설명**

| 파라미터 | 범위 | 설명 |
|---------|------|------|
| `use_gpu` | true/false | GPU 가속 (true 권장) |
| `use_angle_cls` | true/false | 회전 텍스트 감지 |
| `det_limit_side_len` | 320~1920 | 감지 이미지 크기 |
| `rec_batch_num` | 1~32 | 배치 크기 (큼=빠름) |
| `use_dilation` | true/false | 감지 영역 팽창 |

---

## 🔧 **커스텀 프리셋 생성**

새로운 JSON을 만들고 아래처럼 설정하세요:

```json
{
  "detector_config": {
    "lang": "en",
    "change_date": "2026\\.06\\.01",
    "class_name": "date_check",
    "use_gpu": true,
    "use_angle_cls": false,
    "det_limit_side_len": 600,      // ← 커스터마이징
    "rec_batch_num": 8              // ← 커스터마이징
  }
}
```

---

## ✅ **테스트 방법**

```bash
# 속도 최우선 버전 테스트
cd /Users/nongshim/Desktop/Anseong_code
python -c "
from inspection_framework.config import InspectionConfig
from inspection_framework.inspection_runtime import run_local

config = InspectionConfig.from_json('./configs/paddleocr_date_check.json')
run_local(config)
"

# 종료: 'q' 키 누르기
```

---

## 📊 **성능 비교**

```
속도 최우선      균형 (권장)      고정확도
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FPS:  20~30      10~15            5~10
정확도: 85%      95%              98%
메모리: 낮음      중간             높음
```

---

## 💡 **권장 사항**

- **일반 검사**: `paddleocr_date_check_balanced.json` ⭐
- **고속 라인**: `paddleocr_date_check.json`
- **품질 검사**: `paddleocr_date_check_accurate.json`
