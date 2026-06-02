#!/bin/bash
# start.sh — Start NS Runtime (backend + frontend)
# Usage: ./start.sh

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Add uv to PATH in case this is a fresh terminal
export PATH="$HOME/.local/bin:$PATH"

echo "========================================"
echo "  NS Runtime"
echo "========================================"

# ── 0) Kill existing processes on ports 8000 / 5173 ──────
echo ""
echo "[0/2] Cleaning up existing processes..."
lsof -ti :8000 | xargs kill -9 2>/dev/null && echo "  Released port 8000" || true
lsof -ti :5173 | xargs kill -9 2>/dev/null && echo "  Released port 5173" || true
sleep 1

# ── 1) FastAPI backend ────────────────────────────────────
echo "[1/2] Starting FastAPI backend (port 8000)..."
cd "$ROOT_DIR"
uv run python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --no-access-log &
BACKEND_PID=$!

# ── 2) React frontend ─────────────────────────────────────
echo "[2/2] Starting React frontend (port 5173)..."
cd "$ROOT_DIR/frontend"
npm run preview &
FRONTEND_PID=$!

echo ""
echo "----------------------------------------"
echo "  Local:    http://localhost:5173"
echo "  Network:  http://$(hostname -I | awk '{print $1}'):5173"
echo "  Backend:  http://localhost:8000"
echo "  API Docs: http://localhost:8000/docs"
echo "----------------------------------------"
echo "  Press Ctrl+C to stop"
echo ""

# Open browser after short delay
sleep 3
xdg-open "http://localhost:5173" 2>/dev/null || true

# Clean up child processes on exit
trap "echo ''; echo 'Shutting down...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM

wait
