#!/bin/bash
# ============================================================
#  build_trt.sh — ONNX → TensorRT 엔진 빌드 (Jetson에서 실행)
#
#  Usage:
#    bash tools/build_trt.sh
#
#  Requirements:
#    JetPack 6.x (trtexec 포함)
# ============================================================

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

# ── trtexec 경로 탐색 ────────────────────────────────────────
TRTEXEC=""
for CANDIDATE in \
    "$(which trtexec 2>/dev/null)" \
    "/usr/src/tensorrt/bin/trtexec" \
    "/usr/bin/trtexec" \
    "/usr/local/bin/trtexec"
do
  if [ -x "$CANDIDATE" ]; then
    TRTEXEC="$CANDIDATE"
    break
  fi
done

if [ -z "$TRTEXEC" ]; then
  echo -e "${RED}❌ trtexec를 찾을 수 없습니다.${RESET}"
  echo "   JetPack 설치 확인: /usr/src/tensorrt/bin/trtexec"
  exit 1
fi

# ── 입력 디렉토리 선택 ───────────────────────────────────────
echo ""
echo -e "${BOLD}=== ONNX → TensorRT Engine Build ===${RESET}"
echo ""

echo "사용할 언어를 선택하세요:"
echo "  1) English"
echo "  2) Korean (한국어)"
echo "  3) Chinese (중국어)"
echo "  4) Japanese (일본어)"
echo ""
read -rp "선택 [1-4]: " LANG_CHOICE

case "$LANG_CHOICE" in
  1) LANG_NAME="en"     ;;
  2) LANG_NAME="korean" ;;
  3) LANG_NAME="ch"     ;;
  4) LANG_NAME="japan"  ;;
  *) echo -e "${RED}잘못된 선택${RESET}"; exit 1 ;;
esac

INPUT_DIR="${HOME}/onnx_models/${LANG_NAME}"
OUTPUT_DIR="${HOME}/ocr_models/${LANG_NAME}"

read -rp "ONNX 입력 디렉토리 (기본: ${INPUT_DIR}): " INPUT_OVERRIDE
[ -n "$INPUT_OVERRIDE" ] && INPUT_DIR="${INPUT_OVERRIDE/#\~/$HOME}"

read -rp "TRT 출력 디렉토리 (기본: ${OUTPUT_DIR}): " OUTPUT_OVERRIDE
[ -n "$OUTPUT_OVERRIDE" ] && OUTPUT_DIR="${OUTPUT_OVERRIDE/#\~/$HOME}"

read -rp "FP16 사용? [Y/n]: " FP16_INPUT
FP16_OPT=""
[[ "$FP16_INPUT" != "n" && "$FP16_INPUT" != "N" ]] && FP16_OPT="--fp16"

read -rp "Detection 최적 해상도 (기본: 960, 빠른처리: 640): " DET_OPT
DET_OPT="${DET_OPT:-960}"

echo ""
echo -e "  입력 : ${CYAN}${INPUT_DIR}${RESET}"
echo -e "  출력 : ${CYAN}${OUTPUT_DIR}${RESET}"
echo -e "  모드  : ${CYAN}${FP16_OPT:-FP32}${RESET}"
echo -e "  Det 해상도: ${CYAN}${DET_OPT}x${DET_OPT}${RESET}"
echo ""

# ── 입력 파일 확인 ───────────────────────────────────────────
echo -e "${BOLD}[1/4] 파일 확인${RESET}"
MISSING=0
for F in det.onnx rec.onnx dict.txt; do
  if [ -f "${INPUT_DIR}/${F}" ]; then
    echo -e "  ✅ ${F}"
  else
    echo -e "  ${RED}❌ ${F} 없음 → ${INPUT_DIR}/${F}${RESET}"
    MISSING=1
  fi
done
[ "$MISSING" -eq 1 ] && exit 1

mkdir -p "$OUTPUT_DIR"

# ONNX 입력 텐서 이름 확인 (PaddleOCR 기본값: 'x')
INPUT_NAME="x"
if python3 -c "import onnx" 2>/dev/null; then
  INPUT_NAME=$(python3 - <<PYEOF
import onnx
try:
    m = onnx.load('${INPUT_DIR}/det.onnx')
    print(m.graph.input[0].name)
except:
    print('x')
PYEOF
)
fi
echo -e "  입력 텐서: ${CYAN}${INPUT_NAME}${RESET}"

# ── Detection 엔진 빌드 ──────────────────────────────────────
echo ""
echo -e "${BOLD}[2/4] Detection 엔진 빌드${RESET}"
echo "  (시간이 걸릴 수 있습니다 — 수 분 이상)"
echo ""

"$TRTEXEC" \
  --onnx="${INPUT_DIR}/det.onnx" \
  --saveEngine="${OUTPUT_DIR}/det.trt" \
  --minShapes="${INPUT_NAME}:1x3x64x64" \
  --optShapes="${INPUT_NAME}:1x3x${DET_OPT}x${DET_OPT}" \
  --maxShapes="${INPUT_NAME}:1x3x1920x1920" \
  $FP16_OPT

echo -e "  ${GREEN}✅ det.trt 완료${RESET}"

# ── Recognition 엔진 빌드 ────────────────────────────────────
echo ""
echo -e "${BOLD}[3/4] Recognition 엔진 빌드${RESET}"
echo ""

# rec 모델 입력 이름 확인
REC_INPUT_NAME="x"
if python3 -c "import onnx" 2>/dev/null; then
  REC_INPUT_NAME=$(python3 - <<PYEOF
import onnx
try:
    m = onnx.load('${INPUT_DIR}/rec.onnx')
    print(m.graph.input[0].name)
except:
    print('x')
PYEOF
)
fi

"$TRTEXEC" \
  --onnx="${INPUT_DIR}/rec.onnx" \
  --saveEngine="${OUTPUT_DIR}/rec.trt" \
  --minShapes="${REC_INPUT_NAME}:1x3x48x10" \
  --optShapes="${REC_INPUT_NAME}:1x3x48x320" \
  --maxShapes="${REC_INPUT_NAME}:1x3x48x2000" \
  $FP16_OPT

echo -e "  ${GREEN}✅ rec.trt 완료${RESET}"

# ── dict.txt 복사 ────────────────────────────────────────────
echo ""
echo -e "${BOLD}[4/4] 파일 정리${RESET}"
cp "${INPUT_DIR}/dict.txt" "${OUTPUT_DIR}/dict.txt"
DICT_CHARS=$(wc -l < "${OUTPUT_DIR}/dict.txt")
echo -e "  ✅ dict.txt 복사 완료 (${DICT_CHARS}자)"

# ── 완료 안내 ────────────────────────────────────────────────
DET_SIZE=$(du -sh "${OUTPUT_DIR}/det.trt" 2>/dev/null | cut -f1)
REC_SIZE=$(du -sh "${OUTPUT_DIR}/rec.trt" 2>/dev/null | cut -f1)
OUTPUT_ABS="$(cd "$OUTPUT_DIR" && pwd)"

echo ""
echo -e "${GREEN}${BOLD}"
echo "  ┌──────────────────────────────────────────────────────┐"
echo "  │  ✅ TensorRT 엔진 빌드 완료!"
echo "  │"
echo "  │  생성된 파일:"
echo "  │    ${OUTPUT_ABS}/det.trt    (${DET_SIZE})"
echo "  │    ${OUTPUT_ABS}/rec.trt    (${REC_SIZE})"
echo "  │    ${OUTPUT_ABS}/dict.txt   (${DICT_CHARS}자)"
echo "  ├──────────────────────────────────────────────────────┤"
echo "  │  ⚙️  UI 설정:"
echo "  │"
echo "  │    Detector Type   → PaddleRT"
echo "  │    Model Directory → ${OUTPUT_ABS}"
echo "  │"
echo "  │  이 경로를 복사해서 UI의"
echo "  │  'Model Directory' 입력란에 붙여넣으세요."
echo "  └──────────────────────────────────────────────────────┘"
echo -e "${RESET}"
