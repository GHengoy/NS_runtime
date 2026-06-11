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
  if [ -x "$CANDIDATE" ]; then TRTEXEC="$CANDIDATE"; break; fi
done

if [ -z "$TRTEXEC" ]; then
  echo -e "  ${RED}❌ trtexec를 찾을 수 없습니다.${RESET}"
  echo "     JetPack 설치 확인: /usr/src/tensorrt/bin/trtexec"
  exit 1
fi
echo -e "  ✅ ${TRTEXEC}"

# curl / wget 확인
if command -v curl &>/dev/null; then DL_CMD="curl"
elif command -v wget &>/dev/null; then DL_CMD="wget"
else echo -e "${RED}❌ curl 또는 wget이 필요합니다.${RESET}"; exit 1
fi

# ── 2. Release 언어 목록 조회 ────────────────────────────────
echo ""
echo -e "${BOLD}[2/3] 언어 선택${RESET}"
echo -e "  Release 조회 중..."

ASSETS_JSON=$(curl -fsSL \
  -H "Accept: application/vnd.github.v3+json" \
  "https://api.github.com/repos/${REPO}/releases/tags/${RELEASE_TAG}" 2>/dev/null)

# det.onnx 파일명에서 언어 추출 (e.g. en_det.onnx → en)
AVAILABLE_LANGS=($(echo "$ASSETS_JSON" | grep -o '"name":"[^"]*_det\.onnx"' | sed 's/"name":"//;s/_det\.onnx"//'))

if [ ${#AVAILABLE_LANGS[@]} -eq 0 ]; then
  echo -e "  ${RED}❌ Release에서 언어 목록을 가져오지 못했습니다.${RESET}"
  echo "     네트워크 또는 Release 태그 확인: ${RELEASE_TAG}"
  exit 1
fi

echo ""
echo -e "  번호를 입력하세요 (공백 구분, ${CYAN}a${RESET} = 전체)"
echo ""
echo -e "  ┌─ Release에 있는 언어 ────────────────────────────────────"
for i in "${!AVAILABLE_LANGS[@]}"; do
  printf "  │  %2d) %s\n" "$((i+1))" "${AVAILABLE_LANGS[$i]}"
done
echo -e "  └──────────────────────────────────────────────────────────"
echo ""
read -rp "  선택: " LANG_INPUT

LANGS=()
if [[ "$LANG_INPUT" == "a" ]]; then
  LANGS=("${AVAILABLE_LANGS[@]}")
else
  for NUM in $LANG_INPUT; do
    IDX=$((NUM - 1))
    if [ "$IDX" -ge 0 ] && [ "$IDX" -lt "${#AVAILABLE_LANGS[@]}" ]; then
      LANGS+=("${AVAILABLE_LANGS[$IDX]}")
    else
      echo -e "  ${YELLOW}⚠ 잘못된 번호: ${NUM} (무시)${RESET}"
    fi
  done
fi

if [ ${#LANGS[@]} -eq 0 ]; then
  echo -e "${RED}선택된 언어가 없습니다.${RESET}"; exit 1
fi

echo ""
echo -e "  선택된 언어: ${CYAN}${LANGS[*]}${RESET}"
echo ""

# ── 3. ONNX 다운로드 ──────────────────────────────────────────
BASE_URL="https://github.com/${REPO}/releases/download/${RELEASE_TAG}"
DL_STATUS=()

for LANG in "${LANGS[@]}"; do
  OUT_DIR="${ONNX_DIR}/${LANG}"
  mkdir -p "$OUT_DIR"
  echo -e "  ┌── ${CYAN}${LANG}${RESET} ─────────────────────────────"
  LANG_OK=true

  for FILE in det.onnx rec.onnx dict.txt; do
    DEST="${OUT_DIR}/${FILE}"
    if [ -f "$DEST" ] && [ -s "$DEST" ]; then
      echo -e "  │  ✅ ${FILE} (cached)"; continue
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

for S in "${DL_STATUS[@]}"; do
  if [[ "$S" == ❌* ]]; then
    echo -e "${RED}다운로드 실패가 있어 TRT 빌드를 중단합니다.${RESET}"
    for SS in "${DL_STATUS[@]}"; do echo -e "  ${SS}"; done
    exit 1
  fi
done

# ── 4. TRT 엔진 빌드 ──────────────────────────────────────────
echo ""
echo -e "${BOLD}[4/4] TensorRT 엔진 빌드 (FP16, Det ${DET_OPT}×${DET_OPT})${RESET}"
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
echo "  │    Model Directory → ${TRT_ABS}/<언어>"
echo "  └──────────────────────────────────────────────────────────┘"
echo -e "${RESET}"
