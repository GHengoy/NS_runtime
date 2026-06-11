#!/bin/bash
# ============================================================
#  export_onnx.sh — PaddleOCR ONNX 모델 준비
#                   (개발 PC 또는 Jetson에서 실행)
#
#  Usage:    bash tools/export_onnx.sh
#
#  Option 1) GitHub Releases에서 다운로드 (권장, 빠름)
#  Option 2) 로컬 변환 (paddlepaddle + paddle2onnx 필요)
# ============================================================

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

REPO="GHengoy/NS_runtime"
RELEASE_TAG="onnx-models-v1"
OUTPUT_BASE="./onnx_export"
LANGS=("en" "korean" "ch" "japan")

# ── 다운로드 모드 ─────────────────────────────────────────────
_do_download() {
  echo ""
  echo -e "${BOLD}[1/2] 언어 선택${RESET}"
  echo "  1) English only"
  echo "  2) Korean only"
  echo "  3) All languages (en + korean + ch + japan)"
  echo ""
  read -rp "선택 [1-3]: " LANG_CHOICE

  case "$LANG_CHOICE" in
    1) DL_LANGS=("en") ;;
    2) DL_LANGS=("korean") ;;
    3) DL_LANGS=("en" "korean" "ch" "japan") ;;
    *) echo -e "${RED}잘못된 선택${RESET}"; exit 1 ;;
  esac

  # curl / wget 확인
  if command -v curl &>/dev/null; then
    DL_CMD="curl"
  elif command -v wget &>/dev/null; then
    DL_CMD="wget"
  else
    echo -e "${RED}❌ curl 또는 wget이 필요합니다.${RESET}"
    exit 1
  fi

  BASE_URL="https://github.com/${REPO}/releases/download/${RELEASE_TAG}"

  echo ""
  echo -e "${BOLD}[2/2] 다운로드${RESET}"
  echo ""

  STATUS=()
  for LANG in "${DL_LANGS[@]}"; do
    OUT_DIR="${OUTPUT_BASE}/${LANG}"
    mkdir -p "$OUT_DIR"
    echo -e "  ┌── ${CYAN}${LANG}${RESET} ─────────────────────────────"
    LANG_OK=true

    for FILE in det.onnx rec.onnx dict.txt; do
      echo -n "  │  ⬇  ${FILE} ... "
      URL="${BASE_URL}/${LANG}_${FILE}"
      DEST="${OUT_DIR}/${FILE}"

      if [ "$DL_CMD" = "curl" ]; then
        curl -fsSL "$URL" -o "$DEST" 2>/dev/null
      else
        wget -q "$URL" -O "$DEST" 2>/dev/null
      fi

      if [ -f "$DEST" ] && [ -s "$DEST" ]; then
        SIZE=$(du -sh "$DEST" | cut -f1)
        echo -e "${GREEN}완료 (${SIZE})${RESET}"
      else
        echo -e "${RED}실패${RESET}"
        rm -f "$DEST"
        LANG_OK=false
      fi
    done

    echo -e "  └──────────────────────────────────────"
    $LANG_OK && STATUS+=("✅ ${LANG}") || STATUS+=("❌ ${LANG}")
  done

  _print_summary "${STATUS[@]}" "${DL_LANGS[0]}"
}

# ── 변환 모드 ─────────────────────────────────────────────────
_do_convert() {
  echo ""
  echo -e "${BOLD}[1/3] 패키지 확인${RESET}"
  for ITEM in "paddle:paddlepaddle" "paddle2onnx:paddle2onnx" "paddleocr:paddleocr"; do
    IMPORT="${ITEM%%:*}"; INSTALL="${ITEM##*:}"
    if python3 -c "import ${IMPORT}" 2>/dev/null; then
      echo -e "  ✅ ${INSTALL}"
    else
      echo -e "  ${RED}❌ ${INSTALL} → pip install ${INSTALL}${RESET}"; exit 1
    fi
  done

  echo ""
  echo -e "${BOLD}[2/3] 다운로드 및 ONNX 변환${RESET}"
  echo ""

  STATUS=()
  PADDLEX_HOME="${HOME}/.paddlex/official_models"

  for LANG in "${LANGS[@]}"; do
    OUT_DIR="${OUTPUT_BASE}/${LANG}"
    mkdir -p "${OUT_DIR}"
    echo -e "  ┌── ${CYAN}${LANG}${RESET} ─────────────────────────────"

    # 다운로드
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

    # 모델 경로 탐색
    FOUND=$(python3 - 2>/dev/null <<PYEOF
from pathlib import Path
import sys

home = Path.home()
lang = '${LANG}'
hints = {
  'en':     ('en/en_PP-OCRv4_det_infer',      'en/en_PP-OCRv4_rec_infer'),
  'korean': ('ch/ch_PP-OCRv4_det_infer',       'korean/korean_PP-OCRv4_rec_infer'),
  'ch':     ('ch/ch_PP-OCRv4_det_infer',       'ch/ch_PP-OCRv4_rec_infer'),
  'japan':  ('ch/ch_PP-OCRv4_det_infer',       'japan/japan_PP-OCRv4_rec_infer'),
}

def check(base, det_h, rec_h):
    d = base / 'det' / det_h
    r = base / 'rec' / rec_h
    if (d/'inference.pdmodel').exists() and (r/'inference.pdmodel').exists():
        return d, r
    return None, None

det_h, rec_h = hints.get(lang, ('',''))

for base in [home/'.paddleocr'/'whl']:
    d, r = check(base, det_h, rec_h)
    if d:
        print(f'DET={d}'); print(f'REC={r}'); sys.exit(0)

for base in [home/'.paddleocr', home/'.paddlex', home/'.paddlex'/'official_models']:
    if not base.exists(): continue
    dets = sorted(base.rglob('*det*infer*/inference.pdmodel'))
    recs = sorted(base.rglob('*rec*infer*/inference.pdmodel'))
    rm = [p for p in recs if lang in str(p)] or recs
    if dets and rm:
        print(f'DET={dets[0].parent}'); print(f'REC={rm[0].parent}'); sys.exit(0)
sys.exit(1)
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

    # ONNX 변환
    get_model_filename() {
      local D="$1"
      [ -f "${D}/inference.json"    ] && echo "inference.json"    && return
      [ -f "${D}/inference.pdmodel" ] && echo "inference.pdmodel" && return
      echo ""
    }

    CONV_FAIL=""
    for MODEL_INFO in "det:${DET_DIR}:${OUT_DIR}/det.onnx" "rec:${REC_DIR}:${OUT_DIR}/rec.onnx"; do
      LABEL="${MODEL_INFO%%:*}"; REST="${MODEL_INFO#*:}"
      MODEL_DIR="${REST%%:*}"; OUT_FILE="${REST##*:}"
      MODEL_FNAME=$(get_model_filename "$MODEL_DIR")
      [ -z "$MODEL_FNAME" ] && CONV_FAIL+=" ${LABEL}" && continue

      echo -n "  │  🔄 ${LABEL}.onnx [${MODEL_FNAME}]... "
      for OPSET in 11 13; do
        paddle2onnx --model_dir "$MODEL_DIR" \
          --model_filename "$MODEL_FNAME" --params_filename inference.pdiparams \
          --save_file "$OUT_FILE" --opset_version $OPSET > /dev/null 2>&1
        [ -f "$OUT_FILE" ] && break
      done
      if [ -f "$OUT_FILE" ]; then
        SIZE=$(du -sh "$OUT_FILE" | cut -f1)
        echo -e "${GREEN}완료 (${SIZE})${RESET}"
      else
        echo -e "${RED}실패${RESET}"; CONV_FAIL+=" ${LABEL}"
      fi
    done

    if [ -n "$CONV_FAIL" ]; then
      echo -e "  └──────────────────────────────────────"
      STATUS+=("❌ ${LANG} (변환 실패:${CONV_FAIL})")
      continue
    fi

    # dict.txt
    echo -n "  │  📄 dict.txt... "
    DICT_NAMES=("en:en_dict.txt" "korean:korean_dict.txt" "ch:ppocr_keys_v1.txt" "japan:japan_dict.txt")
    DICT_FILE=""
    for I in "${DICT_NAMES[@]}"; do [ "${I%%:*}" = "$LANG" ] && DICT_FILE="${I##*:}"; done
    DICT_RESULT=$(python3 - 2>/dev/null <<PYEOF
import shutil; from pathlib import Path; import paddleocr
pkg = Path(paddleocr.__file__).parent
for p in pkg.rglob('${DICT_FILE}'):
    shutil.copy(p, '${OUT_DIR}/dict.txt')
    print(f'{sum(1 for _ in open(p, encoding="utf-8"))}자')
    break
else:
    print('NOT_FOUND')
PYEOF
)
    [ "$DICT_RESULT" = "NOT_FOUND" ] \
      && echo -e "${YELLOW}미발견${RESET}" && STATUS+=("⚠️  ${LANG}") \
      || { echo -e "${GREEN}완료 (${DICT_RESULT})${RESET}"; STATUS+=("✅ ${LANG}"); }

    echo -e "  └──────────────────────────────────────"
  done

  _print_summary "${STATUS[@]}" "korean"
}

# ── 완료 안내 ─────────────────────────────────────────────────
_print_summary() {
  local -a STATUSES=("${@:1:$#-1}")
  local FIRST_LANG="${@: -1}"
  local OUTPUT_ABS
  OUTPUT_ABS="$(cd "$OUTPUT_BASE" && pwd)"

  echo ""
  echo -e "${BOLD}결과 요약${RESET}"
  for S in "${STATUSES[@]}"; do echo -e "  ${S}"; done

  echo ""
  echo -e "${GREEN}${BOLD}"
  echo "  ┌──────────────────────────────────────────────────────────┐"
  echo "  │  ✅ 완료!"
  echo "  │  출력: ${OUTPUT_ABS}"
  echo "  ├──────────────────────────────────────────────────────────┤"
  echo "  │  🚀 Jetson에서 TRT 엔진 빌드:"
  echo "  │    bash tools/build_trt.sh"
  echo "  │"
  echo "  │  ⚙️  UI 설정:"
  echo "  │    Detector Type   → PaddleRT"
  echo "  │    Model Directory → ${OUTPUT_ABS}/${FIRST_LANG}"
  echo "  └──────────────────────────────────────────────────────────┘"
  echo -e "${RESET}"
}

# ── 진입점 ────────────────────────────────────────────────────
case "${1:-}" in
  --download) _do_download ;;
  --convert)  _do_convert ;;
  *)
    echo ""
    echo -e "${BOLD}=== PaddleRT ONNX 모델 준비 ===${RESET}"
    echo ""
    echo "준비 방법을 선택하세요:"
    echo "  1) GitHub Releases에서 다운로드 (권장 — 빠름, 별도 패키지 불필요)"
    echo "  2) 로컬 변환 (paddlepaddle + paddle2onnx 직접 변환)"
    echo ""
    read -rp "선택 [1-2]: " METHOD
    case "$METHOD" in
      1) _do_download ;;
      2) _do_convert ;;
      *) echo -e "${RED}잘못된 선택${RESET}"; exit 1 ;;
    esac
    ;;
esac
