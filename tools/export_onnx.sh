#!/bin/bash
# ============================================================
#  export_onnx.sh — Paddle 모델 → ONNX 변환 후 GitHub 업로드
#                   (개발 PC에서 실행)
#
#  Usage:  bash tools/export_onnx.sh
#  Required: pip install paddlepaddle paddle2onnx paddleocr
# ============================================================

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# .env에서 GITHUB_TOKEN 로드
ENV_FILE="${SCRIPT_DIR}/../.env"
if [ -f "$ENV_FILE" ] && [ -z "$GITHUB_TOKEN" ]; then
  GITHUB_TOKEN=$(grep -E '^GITHUB_TOKEN=' "$ENV_FILE" | cut -d= -f2-)
  export GITHUB_TOKEN
fi

# 항상 프로젝트 venv python 사용
PYTHON="${SCRIPT_DIR}/../.venv/bin/python3"
if [ ! -x "$PYTHON" ]; then PYTHON="$(which python3)"; fi

REPO="GHengoy/NS_runtime"
RELEASE_TAG="onnx-models-v1"
OUTPUT_BASE="./onnx_export"

# ── 1. 패키지 확인 ───────────────────────────────────────────
echo ""
echo -e "${BOLD}=== PaddleOCR ONNX 변환 + GitHub 업로드 ===${RESET}"
echo ""
echo -e "${BOLD}[1/3] 패키지 확인${RESET}"
for ITEM in "paddle:paddlepaddle" "paddle2onnx:paddle2onnx" "paddleocr:paddleocr"; do
  IMPORT="${ITEM%%:*}"; INSTALL="${ITEM##*:}"
  if "$PYTHON" -c "import ${IMPORT}" 2>/dev/null; then
    echo -e "  ✅ ${INSTALL}"
  else
    echo -e "  ${RED}❌ ${INSTALL} → pip install ${INSTALL}${RESET}"; exit 1
  fi
done
if [ -z "$GITHUB_TOKEN" ]; then
  echo -e "  ${RED}❌ GITHUB_TOKEN이 없습니다.${RESET}"; exit 1
fi
echo -e "  ✅ GITHUB_TOKEN"

# ── 2. 언어 선택 ──────────────────────────────────────────────
echo ""
echo -e "${BOLD}[2/3] 언어 선택${RESET}"
echo ""
echo -e "  번호를 입력하세요 (공백 구분, ${CYAN}a${RESET} = 전체)"
echo ""
echo -e "  ┌─ 개별 언어 ──────────────────────────────────────────────"
echo -e "  │   1) ch          Chinese Simplified"
echo -e "  │   2) en          English"
echo -e "  │   3) korean      Korean"
echo -e "  │   4) japan       Japanese"
echo -e "  │   5) chinese_cht Chinese Traditional"
echo -e "  │   6) th          Thai"
echo -e "  │   7) el          Greek"
echo -e "  │   8) te          Telugu"
echo -e "  │   9) ta          Tamil"
echo -e "  ├─ 스크립트 그룹 ─────────────────────────────────────────"
echo -e "  │  10) latin       Latin   — de fr es pt it nl pl sv tr vi ..."
echo -e "  │  11) arabic      Arabic  — ar fa ur ps ug ..."
echo -e "  │  12) cyrillic    Cyrillic — ru uk bg mn kk ..."
echo -e "  │  13) devanagari  Devanagari — hi mr ne sa ..."
echo -e "  │  14) eslav       East Slavic — ru be uk"
echo -e "  └──────────────────────────────────────────────────────────"
echo ""
read -rp "  선택: " LANG_INPUT

# 번호 → lang 코드 매핑
declare -A LANG_MAP=(
  [1]="ch" [2]="en" [3]="korean" [4]="japan" [5]="chinese_cht"
  [6]="th" [7]="el" [8]="te" [9]="ta"
  [10]="latin" [11]="arabic" [12]="cyrillic" [13]="devanagari" [14]="eslav"
)
ALL_LANGS=(ch en korean japan chinese_cht th el te ta latin arabic cyrillic devanagari eslav)

LANGS=()
if [[ "$LANG_INPUT" == "a" ]]; then
  LANGS=("${ALL_LANGS[@]}")
else
  for NUM in $LANG_INPUT; do
    CODE="${LANG_MAP[$NUM]}"
    if [ -n "$CODE" ]; then
      LANGS+=("$CODE")
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

# ── 3. ONNX 변환 ──────────────────────────────────────────────
echo -e "${BOLD}[3/3] Paddle 모델 다운로드 및 ONNX 변환${RESET}"
echo ""

CONV_STATUS=()

get_model_filename() {
  local D="$1"
  [ -f "${D}/inference.json"    ] && echo "inference.json"    && return
  [ -f "${D}/inference.pdmodel" ] && echo "inference.pdmodel" && return
  echo ""
}

for LANG in "${LANGS[@]}"; do
  OUT_DIR="${OUTPUT_BASE}/${LANG}"
  mkdir -p "${OUT_DIR}"
  echo -e "  ┌── ${CYAN}${LANG}${RESET} ─────────────────────────────"

  # 그룹명 → PaddleOCR 대표 언어 코드 변환
  declare -A GROUP_REPR=([latin]="fr" [arabic]="ar" [cyrillic]="ru" [devanagari]="hi" [eslav]="ru")
  DL_LANG="${GROUP_REPR[$LANG]:-$LANG}"

  # Paddle 모델 다운로드
  echo -n "  │  📥 Paddle 모델 다운로드... "
  "$PYTHON" - 2>/dev/null <<PYEOF
import os, warnings, logging
warnings.filterwarnings('ignore')
os.environ['GLOG_minloglevel'] = '3'
for lg in ['ppocr','ppdet','paddle','root','paddlex']:
    logging.getLogger(lg).setLevel(logging.ERROR)
from paddleocr import PaddleOCR
try:
    PaddleOCR(lang='${DL_LANG}', use_gpu=False, show_log=False)
except (TypeError, ValueError):
    PaddleOCR(lang='${DL_LANG}')
PYEOF
  if [ $? -ne 0 ]; then
    echo -e "${RED}실패${RESET}"
    echo -e "  └──────────────────────────────────────"
    CONV_STATUS+=("❌ ${LANG} (다운로드 실패)")
    continue
  fi
  echo -e "${GREEN}완료${RESET}"

  # 모델 경로 탐색 (버전 무관 패턴 매칭)
  FOUND=$("$PYTHON" - 2>/dev/null <<PYEOF
from pathlib import Path; import sys
base = Path.home() / '.paddlex' / 'official_models'
lang = '${LANG}'

def has_model(p):
    return (p / 'inference.json').exists() or (p / 'inference.pdmodel').exists()

dets = sorted([p for p in base.iterdir() if p.is_dir() and '_det' in p.name and has_model(p)], reverse=True)

# 언어 전용 rec 우선, 없으면 server_rec(다국어 공용) 폴백
recs_lang = sorted([p for p in base.iterdir()
    if p.is_dir() and p.name.startswith(lang) and '_rec' in p.name and has_model(p)], reverse=True)
recs_fallback = sorted([p for p in base.iterdir()
    if p.is_dir() and '_rec' in p.name and has_model(p)
    and not any(p.name.startswith(l) for l in ['en','korean','ch','japan','latin',
        'arabic','cyrillic','devanagari','eslav','th','el','te','ta','chinese_cht'])], reverse=True)
recs = recs_lang or recs_fallback

if dets and recs:
    print(f'DET={dets[0]}'); print(f'REC={recs[0]}'); sys.exit(0)
sys.exit(1)
PYEOF
  )
  if [ -z "$FOUND" ]; then
    echo -e "  │  ${RED}경로 탐색 실패${RESET}"
    echo -e "  └──────────────────────────────────────"
    CONV_STATUS+=("❌ ${LANG} (경로 탐색 실패)")
    continue
  fi

  DET_DIR=$(echo "$FOUND" | grep '^DET=' | cut -d= -f2-)
  REC_DIR=$(echo "$FOUND" | grep '^REC=' | cut -d= -f2-)

  # ONNX 변환
  CONV_FAIL=""
  for MODEL_INFO in "det:${DET_DIR}:${OUT_DIR}/det.onnx" "rec:${REC_DIR}:${OUT_DIR}/rec.onnx"; do
    LABEL="${MODEL_INFO%%:*}"; REST="${MODEL_INFO#*:}"
    MODEL_DIR="${REST%%:*}"; OUT_FILE="${REST##*:}"
    MODEL_FNAME=$(get_model_filename "$MODEL_DIR")
    [ -z "$MODEL_FNAME" ] && CONV_FAIL+=" ${LABEL}" && continue

    echo -n "  │  🔄 ${LABEL}.onnx... "
    for OPSET in 11 13; do
      paddle2onnx --model_dir "$MODEL_DIR" \
        --model_filename "$MODEL_FNAME" --params_filename inference.pdiparams \
        --save_file "$OUT_FILE" --opset_version $OPSET > /dev/null 2>&1
      [ -f "$OUT_FILE" ] && break
    done
    if [ -f "$OUT_FILE" ]; then
      echo -e "${GREEN}완료 ($(du -sh "$OUT_FILE" | cut -f1))${RESET}"
    else
      echo -e "${RED}실패${RESET}"; CONV_FAIL+=" ${LABEL}"
    fi
  done

  if [ -n "$CONV_FAIL" ]; then
    echo -e "  └──────────────────────────────────────"
    CONV_STATUS+=("❌ ${LANG} (변환 실패:${CONV_FAIL})")
    continue
  fi

  # dict.txt 추출 (inference.yml의 character_dict)
  echo -n "  │  📄 dict.txt... "
  DICT_RESULT=$("$PYTHON" - 2>/dev/null <<PYEOF
from pathlib import Path; import yaml, sys
yml = Path('${REC_DIR}') / 'inference.yml'
if not yml.exists(): print('NOT_FOUND'); sys.exit(0)
data = yaml.safe_load(yml.read_text(encoding='utf-8'))
chars = next((s['character_dict'] for s in data.values()
              if isinstance(s, dict) and 'character_dict' in s), None)
if not chars: print('NOT_FOUND'); sys.exit(0)
(Path('${OUT_DIR}') / 'dict.txt').write_text('\n'.join(str(c) for c in chars), encoding='utf-8')
print(f'{len(chars)}자')
PYEOF
  )
  [ "$DICT_RESULT" = "NOT_FOUND" ] \
    && echo -e "${YELLOW}미발견${RESET}" && CONV_STATUS+=("⚠️  ${LANG}") \
    || { echo -e "${GREEN}완료 (${DICT_RESULT})${RESET}"; CONV_STATUS+=("✅ ${LANG}"); }

  echo -e "  └──────────────────────────────────────"
done

# 변환 실패 시 업로드 중단
FAIL_COUNT=0
for S in "${CONV_STATUS[@]}"; do [[ "$S" == ❌* ]] && ((FAIL_COUNT++)); done
if [ "$FAIL_COUNT" -gt 0 ]; then
  echo ""
  echo -e "${RED}변환 실패가 있어 업로드를 중단합니다.${RESET}"
  for S in "${CONV_STATUS[@]}"; do echo -e "  ${S}"; done
  exit 1
fi

# ── 4. GitHub Releases 업로드 ─────────────────────────────────
echo ""
echo -e "${BOLD}[4/4] GitHub Releases 업로드${RESET}"
echo -e "  태그: ${CYAN}${RELEASE_TAG}${RESET}"
echo ""

RELEASE_ID=$(curl -s \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  "https://api.github.com/repos/${REPO}/releases/tags/${RELEASE_TAG}" \
  | "$PYTHON" -c "import json,sys; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null)

if [ -z "$RELEASE_ID" ]; then
  echo -n "  릴리즈 생성... "
  RELEASE_ID=$(curl -s -X POST \
    -H "Authorization: token $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    "https://api.github.com/repos/${REPO}/releases" \
    -d "{\"tag_name\":\"${RELEASE_TAG}\",\"name\":\"PaddleRT ONNX Models\",\"body\":\"Auto-generated by export_onnx.sh\",\"draft\":false,\"prerelease\":false}" \
    | "$PYTHON" -c "import json,sys; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
  [ -n "$RELEASE_ID" ] && echo -e "${GREEN}완료 (id=${RELEASE_ID})${RESET}" \
                       || { echo -e "${RED}실패${RESET}"; exit 1; }
else
  echo -e "  릴리즈 확인: ${GREEN}id=${RELEASE_ID}${RESET}"
fi

UPLOAD_URL="https://uploads.github.com/repos/${REPO}/releases/${RELEASE_ID}/assets"
EXISTING_ASSETS=$(curl -s \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  "https://api.github.com/repos/${REPO}/releases/${RELEASE_ID}/assets")

delete_asset() {
  local NAME="$1"
  local AID
  AID=$(echo "$EXISTING_ASSETS" | "$PYTHON" -c "
import json,sys
for a in json.load(sys.stdin):
    if a['name'] == '${NAME}': print(a['id']); break
" 2>/dev/null)
  [ -n "$AID" ] && curl -s -X DELETE \
    -H "Authorization: token $GITHUB_TOKEN" \
    "https://api.github.com/repos/${REPO}/releases/assets/${AID}" > /dev/null
}

UPLOAD_STATUS=()
for LANG in "${LANGS[@]}"; do
  echo -e "  ┌── ${CYAN}${LANG}${RESET} ─────────────────────────────"
  LANG_OK=true
  for FILE in det.onnx rec.onnx dict.txt; do
    ASSET_NAME="${LANG}_${FILE}"
    LOCAL_FILE="${OUTPUT_BASE}/${LANG}/${FILE}"
    echo -n "  │  ⬆  ${ASSET_NAME} ... "
    delete_asset "$ASSET_NAME"
    STATE=$(curl -s -X POST \
      -H "Authorization: token $GITHUB_TOKEN" \
      -H "Content-Type: application/octet-stream" \
      "${UPLOAD_URL}?name=${ASSET_NAME}" \
      --data-binary @"${LOCAL_FILE}" \
      | "$PYTHON" -c "import json,sys; print(json.load(sys.stdin).get('state','error'))" 2>/dev/null)
    [ "$STATE" = "uploaded" ] && echo -e "${GREEN}완료${RESET}" \
                              || { echo -e "${RED}실패${RESET}"; LANG_OK=false; }
  done
  echo -e "  └──────────────────────────────────────"
  $LANG_OK && UPLOAD_STATUS+=("✅ ${LANG}") || UPLOAD_STATUS+=("❌ ${LANG}")
done

echo ""
echo -e "${BOLD}결과 요약${RESET}"
for S in "${UPLOAD_STATUS[@]}"; do echo -e "  ${S}"; done
echo ""
echo -e "${GREEN}${BOLD}"
echo "  ┌──────────────────────────────────────────────────────────┐"
echo "  │  ✅ 완료!"
echo "  │  https://github.com/${REPO}/releases/tag/${RELEASE_TAG}"
echo "  ├──────────────────────────────────────────────────────────┤"
echo "  │  Jetson에서 TRT 빌드:  bash tools/build_trt.sh"
echo "  └──────────────────────────────────────────────────────────┘"
echo -e "${RESET}"
