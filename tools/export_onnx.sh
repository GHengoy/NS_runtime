#!/bin/bash
# ============================================================
#  export_onnx.sh — PaddleOCR(PaddleX 3.x) 전체 언어 ONNX 변환
#                   (개발 PC에서 한 번만 실행)
#
#  Usage:    bash tools/export_onnx.sh
#  Required: pip install paddlepaddle paddle2onnx paddleocr
#
#  PaddleX 3.x 모델 저장 경로: ~/.paddlex/official_models/
#  파일 형식: inference.json + inference.pdiparams (pdmodel 없음)
# ============================================================

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

OUTPUT_BASE="./onnx_export"
PADDLEX_HOME="${HOME}/.paddlex/official_models"
LANGS=("en" "korean" "ch" "japan")
STATUS=()

echo ""
echo -e "${BOLD}=== PaddleOCR 전체 언어 ONNX 일괄 변환 ===${RESET}"
echo -e "  대상: ${CYAN}en / korean / ch / japan${RESET}"
echo ""

# ── 1. 패키지 확인 ───────────────────────────────────────────
echo -e "${BOLD}[1/3] 패키지 확인${RESET}"
for ITEM in "paddle:paddlepaddle" "paddle2onnx:paddle2onnx" "paddleocr:paddleocr"; do
  IMPORT="${ITEM%%:*}"; INSTALL="${ITEM##*:}"
  if python3 -c "import ${IMPORT}" 2>/dev/null; then
    echo -e "  ✅ ${INSTALL}"
  else
    echo -e "  ${RED}❌ ${INSTALL} → pip install ${INSTALL}${RESET}"; exit 1
  fi
done

# ── 2. 언어별 변환 ───────────────────────────────────────────
echo ""
echo -e "${BOLD}[2/3] 다운로드 및 ONNX 변환${RESET}"
echo ""

for LANG in "${LANGS[@]}"; do
  OUT_DIR="${OUTPUT_BASE}/${LANG}"
  mkdir -p "${OUT_DIR}"
  echo -e "  ┌── ${CYAN}${LANG}${RESET} ─────────────────────────────"

  # ── 2-1. 모델 다운로드 ──────────────────────────────────────
  echo -n "  │  📥 다운로드... "
  python3 - 2>/dev/null <<PYEOF
import os, warnings, logging
warnings.filterwarnings('ignore')
os.environ['GLOG_minloglevel'] = '3'
for lg in ['ppocr','ppdet','paddle','root','paddlex']:
    logging.getLogger(lg).setLevel(logging.ERROR)
from paddleocr import PaddleOCR
try:
    PaddleOCR(lang='${LANG}', use_gpu=False, show_log=False)
except (TypeError, ValueError):
    PaddleOCR(lang='${LANG}')
PYEOF
  DL_RC=$?

  if [ $DL_RC -ne 0 ]; then
    echo -e "${RED}실패${RESET}"
    echo -e "  └──────────────────────────────────────"
    STATUS+=("❌ ${LANG} (다운로드 실패)")
    continue
  fi
  echo -e "${GREEN}완료${RESET}"

  # ── 2-2. 모델 경로 탐색 (PaddleX 3.x: ~/.paddlex/official_models/) ──
  FOUND=$(python3 - 2>/dev/null <<PYEOF
from pathlib import Path
import sys

base = Path('${PADDLEX_HOME}')
lang = '${LANG}'

if not base.exists():
    sys.exit(1)

# detection: 모든 언어 공통 (PP-OCRv5_server_det 또는 유사)
det_candidates = sorted(base.glob('*det*'))
det_dir = det_candidates[0] if det_candidates else None

# recognition: 언어 우선 → 일반 rec 순
rec_candidates = (
    sorted(base.glob(f'{lang}*rec*')) +
    sorted(base.glob(f'*{lang}*rec*')) +
    sorted(base.glob('*server_rec*')) +
    sorted(base.glob('*rec*'))
)
rec_dir = next((d for d in rec_candidates
                if (d / 'inference.json').exists() or (d / 'inference.pdmodel').exists()), None)

if not det_dir or not rec_dir:
    sys.exit(1)

print(f'DET={det_dir}')
print(f'REC={rec_dir}')
PYEOF
)

  if [ -z "$FOUND" ]; then
    echo -e "  │  ${RED}└ 모델 경로 탐색 실패${RESET}"
    echo -e "  └──────────────────────────────────────"
    STATUS+=("❌ ${LANG} (경로 탐색 실패)")
    continue
  fi
  DET_DIR=$(echo "$FOUND" | grep '^DET=' | cut -d= -f2-)
  REC_DIR=$(echo "$FOUND" | grep '^REC=' | cut -d= -f2-)

  # ── 2-3. ONNX 변환 ──────────────────────────────────────────
  # PaddleX 3.x: inference.json + inference.pdiparams
  # PaddleX 2.x: inference.pdmodel + inference.pdiparams (호환 유지)
  get_model_filename() {
    local DIR="$1"
    [ -f "${DIR}/inference.json"    ] && echo "inference.json"    && return
    [ -f "${DIR}/inference.pdmodel" ] && echo "inference.pdmodel" && return
    echo ""
  }

  CONV_FAIL=""
  for MODEL_INFO in "det:${DET_DIR}:${OUT_DIR}/det.onnx" "rec:${REC_DIR}:${OUT_DIR}/rec.onnx"; do
    LABEL="${MODEL_INFO%%:*}"
    REST="${MODEL_INFO#*:}"
    MODEL_DIR="${REST%%:*}"
    OUT_FILE="${REST##*:}"

    MODEL_FNAME=$(get_model_filename "$MODEL_DIR")
    if [ -z "$MODEL_FNAME" ]; then
      echo -e "  │  ${RED}❌ ${LABEL}: 모델 파일 없음 (${MODEL_DIR})${RESET}"
      CONV_FAIL="${CONV_FAIL} ${LABEL}"
      continue
    fi

    echo -n "  │  🔄 ${LABEL}.onnx 변환 [${MODEL_FNAME}]... "

    for OPSET in 11 13; do
      paddle2onnx \
        --model_dir       "$MODEL_DIR" \
        --model_filename  "$MODEL_FNAME" \
        --params_filename inference.pdiparams \
        --save_file       "$OUT_FILE" \
        --opset_version   $OPSET \
        > /dev/null 2>&1
      [ -f "$OUT_FILE" ] && break
    done

    if [ -f "$OUT_FILE" ]; then
      SIZE=$(du -sh "$OUT_FILE" | cut -f1)
      echo -e "${GREEN}완료 (${SIZE})${RESET}"
    else
      echo -e "${RED}실패${RESET}"
      CONV_FAIL="${CONV_FAIL} ${LABEL}"
    fi
  done

  if [ -n "$CONV_FAIL" ]; then
    echo -e "  └──────────────────────────────────────"
    STATUS+=("❌ ${LANG} (변환 실패:${CONV_FAIL})")
    continue
  fi

  # ── 2-4. dict.txt — inference.yml의 character_dict 추출 ──────
  echo -n "  │  📄 dict.txt 추출... "
  DICT_RESULT=$(python3 - 2>/dev/null <<PYEOF
import yaml, sys
from pathlib import Path

rec_dir = Path('${REC_DIR}')
yml_path = rec_dir / 'inference.yml'

if not yml_path.exists():
    print('NO_YML')
    sys.exit(0)

with open(yml_path, encoding='utf-8') as f:
    cfg = yaml.safe_load(f)

chars = cfg.get('PostProcess', {}).get('character_dict', [])
if not chars:
    print('NO_DICT')
    sys.exit(0)

out_path = '${OUT_DIR}/dict.txt'
with open(out_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(str(c) for c in chars))
print(f'{len(chars)}자')
PYEOF
)

  case "$DICT_RESULT" in
    *자)  echo -e "${GREEN}완료 (${DICT_RESULT})${RESET}"
          STATUS+=("✅ ${LANG}") ;;
    NO_YML|NO_DICT)
          echo -e "${YELLOW}미발견${RESET}"
          STATUS+=("⚠️  ${LANG} (dict 없음)") ;;
    *)    echo -e "${RED}실패${RESET}"
          STATUS+=("⚠️  ${LANG} (dict 실패)") ;;
  esac

  echo -e "  └──────────────────────────────────────"
done

# ── 3. 결과 요약 ─────────────────────────────────────────────
echo ""
echo -e "${BOLD}[3/3] 결과 요약${RESET}"
echo ""
for S in "${STATUS[@]}"; do echo -e "  ${S}"; done

SUCCESS_COUNT=0
for S in "${STATUS[@]}"; do [[ "$S" == ✅* ]] && ((SUCCESS_COUNT++)) || true; done

if [ "$SUCCESS_COUNT" -eq 0 ]; then
  echo -e "\n  ${RED}❌ 성공한 언어가 없습니다.${RESET}"; exit 1
fi

OUTPUT_ABS="$(cd "$OUTPUT_BASE" && pwd)"

echo ""
echo -e "${GREEN}${BOLD}"
echo "  ┌──────────────────────────────────────────────────────────┐"
echo "  │  ✅ 변환 완료! (${SUCCESS_COUNT}개 언어 성공)"
echo "  │"
echo "  │  📦 Jetson으로 전송:"
echo "  │    scp -r ${OUTPUT_ABS} jetson:~/onnx_models"
echo "  │"
echo "  ├──────────────────────────────────────────────────────────┤"
echo "  │  🚀 Jetson에서 실행:"
echo "  │    bash tools/build_trt.sh"
echo "  │    → 언어 선택 → TRT 엔진 빌드"
echo "  │"
echo "  ├──────────────────────────────────────────────────────────┤"
echo "  │  ⚙️  UI 설정 (Jetson):"
echo "  │    Detector Type   → PaddleRT"
echo "  │    Model Directory → ~/ocr_models/{lang}"
echo "  └──────────────────────────────────────────────────────────┘"
echo -e "${RESET}"
