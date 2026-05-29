#!/usr/bin/env bash
# ============================================================
#  setup.sh — One-time setup for fresh Ubuntu installation
#  Usage:  chmod +x setup.sh && ./setup.sh
# ============================================================
set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}[OK]${NC} $1"; }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $1"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $1"; exit 1; }
info() { echo -e "  ${BLUE}[INFO]${NC} $1"; }

echo ""
echo "========================================"
echo "  NS Runtime — Setup"
echo "========================================"
echo ""

# ── 1. System Packages (apt) ───────────────────────────────
echo "[1/6] Installing system packages..."

if command -v apt >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq \
        build-essential git curl wget \
        python3-dev python3-pip python3-venv \
        libgl1-mesa-glx libglib2.0-0 libsm6 libxext6 libxrender-dev \
        lsof net-tools
    ok "System packages installed"
else
    warn "apt not found — skipping system package installation"
fi

# ── 2. uv (Python package manager) ────────────────────────
echo ""
echo "[2/6] Setting up uv..."

# Add ~/.local/bin to PATH so a freshly installed uv is found
export PATH="$HOME/.local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Re-source env in case the installer added its own path
    [ -f "$HOME/.local/bin/env" ] && source "$HOME/.local/bin/env" || true
    export PATH="$HOME/.local/bin:$PATH"
fi

command -v uv >/dev/null 2>&1 || fail "uv not found after install. Run: curl -LsSf https://astral.sh/uv/install.sh | sh  then re-run this script."
ok "uv ready: $(uv --version)"

# ── 3. Node.js ─────────────────────────────────────────────
echo ""
echo "[3/6] Setting up Node.js..."

# Load nvm if present (covers existing installs)
if [ -d "$HOME/.nvm" ]; then
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
fi

if ! command -v node >/dev/null 2>&1; then
    info "Installing Node.js 20 LTS via NodeSource..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - >/dev/null 2>&1
    sudo apt-get install -y nodejs >/dev/null 2>&1
fi

command -v node >/dev/null 2>&1 || fail "Node.js installation failed. Install manually: https://nodejs.org"
command -v npm  >/dev/null 2>&1 || fail "npm not found after Node.js install."
ok "Node.js $(node --version) / npm $(npm --version)"

# ── 4. Python Dependencies ─────────────────────────────────
echo ""
echo "[4/6] Installing Python dependencies (uv sync)..."

uv sync
ok "Python dependencies installed"

# ── 5. PaddlePaddle GPU (optional) ────────────────────────
echo ""
echo "[5/6] PaddlePaddle GPU setup..."

HAS_GPU=false
if command -v nvidia-smi >/dev/null 2>&1; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    if [ -n "$GPU_NAME" ]; then
        HAS_GPU=true
        ok "GPU detected: $GPU_NAME"
    fi
fi

if [ "$HAS_GPU" = true ]; then
    PADDLE_CUDA=$(uv run python -c "import paddle; print(paddle.device.is_compiled_with_cuda())" 2>/dev/null || echo "False")

    if [ "$PADDLE_CUDA" = "False" ]; then
        info "Replacing CPU PaddlePaddle with GPU version..."
        uv pip uninstall paddlepaddle 2>/dev/null || true

        PY_TAG=$(uv run python -c "import sys; print(f'cp{sys.version_info.major}{sys.version_info.minor}')")
        WHEEL_URL="https://paddle-whl.bj.bcebos.com/stable/cu126/paddlepaddle-gpu/paddlepaddle_gpu-3.3.0-${PY_TAG}-${PY_TAG}-linux_x86_64.whl"

        info "Downloading: $WHEEL_URL"
        if uv run python -m pip install "$WHEEL_URL" 2>&1 | tail -3; then
            info "Restoring PyTorch NVIDIA library versions..."
            TORCH_NVIDIA_DEPS=$(uv run python -c "
import importlib.metadata as md
deps = md.requires('torch') or []
pkgs = []
for d in deps:
    if 'nvidia' in d and 'cu12' in d and 'platform_system' in d:
        name_ver = d.split(';')[0].strip()
        pkgs.append(name_ver)
print(' '.join(pkgs))
" 2>/dev/null || echo "")

            if [ -n "$TORCH_NVIDIA_DEPS" ]; then
                uv run python -m pip install $TORCH_NVIDIA_DEPS 2>&1 | tail -3
            fi

            PADDLE_CUDA=$(uv run python -c "import paddle; print(paddle.device.is_compiled_with_cuda())" 2>/dev/null || echo "False")
            if [ "$PADDLE_CUDA" = "True" ]; then
                ok "PaddlePaddle GPU installed successfully"
            else
                warn "PaddlePaddle GPU install failed — OCR will run on CPU (slower)"
            fi
        else
            warn "GPU wheel download failed — keeping CPU version (OCR will be slow)"
        fi
    else
        ok "PaddlePaddle GPU already installed"
    fi
else
    warn "No GPU detected — PaddleOCR will run on CPU (slower)"
fi

# ── 6. Frontend Build ──────────────────────────────────────
echo ""
echo "[6/6] Building frontend..."

cd "$ROOT_DIR/frontend"
npm install --silent 2>&1 | tail -3
npm run build 2>&1 | tail -5
ok "Frontend built → frontend/dist/"
cd "$ROOT_DIR"

# ── Done ───────────────────────────────────────────────────
echo ""
echo "========================================"
echo "  Setup complete!"
echo "========================================"
echo ""
echo "  If 'uv' is not found in a new terminal, add it to PATH:"
echo "    echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc"
echo "    source ~/.bashrc"
echo ""
echo "  Start the application:"
echo "    chmod +x start.sh && ./start.sh"
echo ""
echo "  Dashboard: http://localhost:5173"
echo "  API Docs:  http://localhost:8000/docs"
echo ""
