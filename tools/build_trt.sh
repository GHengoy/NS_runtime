#!/bin/bash
# ============================================================
#  build_trt.sh — GitHub에서 ONNX 다운로드 후 TRT 엔진 빌드
#                 (Jetson에서 실행)
#
#  Usage:
#    bash tools/build_trt.sh
#
#  Requirements:
#    JetPack 6.x (trtexec 포함)
# ============================================================

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

REPO="GHengoy/NS_runtime"
RELEASE_TAG="onnx-models-v1"
ONNX_DIR="./onnx_export"
TRT_DIR="${HOME}/ocr_models"
LANGS=("en" "korean" "ch" "japan")
FP16_OPT="--fp16"
DET_OPT="960"

echo ""
echo -e "${BOLD}=== ONNX 다운로드 + TensorRT 엔진 빌드 ===${RESET}"
echo ""

# ── 1. trtexec 확인 ───────────────────────────────────────────
echo -e "${BOLD}[1/3] trtexec 확인${RESET}"
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
  echo -e "  ${RED}❌ trtexec를 찾을 수 없습니다.${RESET}"
  echo "     JetPack 설치 확인: /usr/src/tensorrt/bin/trtexec"
  exit 1
fi
echo -e "  ✅ ${TRTEXEC}"

# ── 2. ONNX 다운로드 ──────────────────────────────────────────
echo ""
echo -e "${BOLD}[2/3] GitHub Releases에서 ONNX 다운로드${RESET}"
echo -e "  출처: ${CYAN}github.com/${REPO}/releases/tag/${RELEASE_TAG}${RESET}"
echo ""

if command -v curl &>/dev/null; then
  DL_CMD="curl"
elif command -v wget &>/dev/null; then
  DL_CMD="wget"
else
  echo -e "${RED}❌ curl 또는 wget이 필요합니다.${RESET}"
  exit 1
fi

BASE_URL="https://github.com/${REPO}/releases/download/${RELEASE_TAG}"
DL_STATUS=()

for LANG in "${LANGS[@]}"; do
  OUT_DIR="${ONNX_DIR}/${LANG}"
  mkdir -p "$OUT_DIR"
  echo -e "  ┌── ${CYAN}${LANG}${RESET} ─────────────────────────────"
  LANG_OK=true

  for FILE in det.onnx rec.onnx dict.txt; do
    DEST="${OUT_DIR}/${FILE}"
    # 이미 있으면 스킵
    if [ -f "$DEST" ] && [ -s "$DEST" ]; then
      echo -e "  │  ✅ ${FILE} (cached)"
      continue
    fi
    echo -n "  │  ⬇  ${FILE} ... "
    if [ "$DL_CMD" = "curl" ]; then
      curl -fsSL "${BASE_URL}/${LANG}_${FILE}" -o "$DEST" 2>/dev/null
    else
      wget -q "${BASE_URL}/${LANG}_${FILE}" -O "$DEST" 2>/dev/null
    fi
    if [ -f "$DEST" ] && [ -s "$DEST" ]; then
      echo -e "${GREEN}완료 ($(du -sh "$DEST" | cut -f1))${RESET}"
    else
      echo -e "${RED}실패${RESET}"; rm -f "$DEST"; LANG_OK=false
    fi
  done

  echo -e "  └──────────────────────────────────────"
  $LANG_OK && DL_STATUS+=("✅ ${LANG}") || DL_STATUS+=("❌ ${LANG}")
done

# 다운로드 실패 확인
for S in "${DL_STATUS[@]}"; do
  if [[ "$S" == ❌* ]]; then
    echo -e "${RED}다운로드 실패가 있어 TRT 빌드를 중단합니다.${RESET}"
    for SS in "${DL_STATUS[@]}"; do echo -e "  ${SS}"; done
    exit 1
  fi
done

# ── 3. TRT 엔진 빌드 ──────────────────────────────────────────
echo ""
echo -e "${BOLD}[3/3] TensorRT 엔진 빌드 (FP16, Det ${DET_OPT}×${DET_OPT})${RESET}"
echo "  (언어당 수 분 소요)"
echo ""

TRT_STATUS=()

get_input_name() {
  local ONNX_FILE="$1"
  if python3 -c "import onnx" 2>/dev/null; then
    python3 -c "
import onnx
try:
    m = onnx.load('${ONNX_FILE}')
    print(m.graph.input[0].name)
except:
    print('x')
" 2>/dev/null
  else
    echo "x"
  fi
}

for LANG in "${LANGS[@]}"; do
  IN_DIR="${ONNX_DIR}/${LANG}"
  OUT_DIR="${TRT_DIR}/${LANG}"
  mkdir -p "$OUT_DIR"
  echo -e "  ┌── ${CYAN}${LANG}${RESET} ─────────────────────────────"
  LANG_OK=true

  # Detection 엔진
  echo -n "  │  🔨 det.trt ... "
  DET_INPUT=$(get_input_name "${IN_DIR}/det.onnx")
  "$TRTEXEC" \
    --onnx="${IN_DIR}/det.onnx" \
    --saveEngine="${OUT_DIR}/det.trt" \
    --minShapes="${DET_INPUT}:1x3x64x64" \
    --optShapes="${DET_INPUT}:1x3x${DET_OPT}x${DET_OPT}" \
    --maxShapes="${DET_INPUT}:1x3x1920x1920" \
    $FP16_OPT > /dev/null 2>&1
  if [ -f "${OUT_DIR}/det.trt" ]; then
    echo -e "${GREEN}완료 ($(du -sh "${OUT_DIR}/det.trt" | cut -f1))${RESET}"
  else
    echo -e "${RED}실패${RESET}"; LANG_OK=false
  fi

  # Recognition 엔진
  echo -n "  │  🔨 rec.trt ... "
  REC_INPUT=$(get_input_name "${IN_DIR}/rec.onnx")
  "$TRTEXEC" \
    --onnx="${IN_DIR}/rec.onnx" \
    --saveEngine="${OUT_DIR}/rec.trt" \
    --minShapes="${REC_INPUT}:1x3x48x10" \
    --optShapes="${REC_INPUT}:1x3x48x320" \
    --maxShapes="${REC_INPUT}:1x3x48x2000" \
    $FP16_OPT > /dev/null 2>&1
  if [ -f "${OUT_DIR}/rec.trt" ]; then
    echo -e "${GREEN}완료 ($(du -sh "${OUT_DIR}/rec.trt" | cut -f1))${RESET}"
  else
    echo -e "${RED}실패${RESET}"; LANG_OK=false
  fi

  # dict.txt 복사
  cp "${IN_DIR}/dict.txt" "${OUT_DIR}/dict.txt"
  echo -e "  │  ✅ dict.txt 복사"

  echo -e "  └──────────────────────────────────────"
  $LANG_OK && TRT_STATUS+=("✅ ${LANG}") || TRT_STATUS+=("❌ ${LANG}")
done

# ── 완료 안내 ─────────────────────────────────────────────────
TRT_ABS="$(cd "$TRT_DIR" && pwd)"
echo ""
echo -e "${BOLD}결과 요약${RESET}"
for S in "${TRT_STATUS[@]}"; do echo -e "  ${S}"; done
echo ""
echo -e "${GREEN}${BOLD}"
echo "  ┌──────────────────────────────────────────────────────────┐"
echo "  │  ✅ TensorRT 엔진 빌드 완료!"
echo "  │  출력: ${TRT_ABS}"
echo "  ├──────────────────────────────────────────────────────────┤"
echo "  │  ⚙️  UI 설정:"
echo "  │    Detector Type   → PaddleRT"
echo "  │    Model Directory → ${TRT_ABS}/korean"
echo "  └──────────────────────────────────────────────────────────┘"
echo -e "${RESET}"
