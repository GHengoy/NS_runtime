# NS Runtime

A real-time AI vision inspection system for factory production lines. Detects product defects using YOLOv12 and controls conveyor belt reject signals.

**Key Features:**
- Real-time defect detection with YOLOv12 (Ultralytics)
- Multi-camera support (up to 10 — Basler GigE or USB webcam)
- Web-based dashboard with React
- Pluggable detector system (YOLO, PaddleOCR, CNN)
- WebSocket video streaming
- File-based data management with auto-archiving

---

## Supported / Tested Versions

| Component | Required | Tested / Recommended | Notes |
|---|---|---|---|
| **Ubuntu** | 22.04 LTS or later | 22.04 LTS, 24.04 LTS | Other Linux distros may work but are unsupported |
| **Python** | 3.11+ | 3.11, 3.12 | Defined in `pyproject.toml` |
| **Node.js** | 18+ | 20 LTS | For frontend build (Vite + React) |
| **npm** | 10+ | 10.x | Bundled with Node.js 20 |
| **NVIDIA Driver** | 525+ | 535 | Optional — GPU inference only |
| **CUDA Toolkit** | 11.8 | 11.8 | Required only for GPU-accelerated PyTorch |
| **uv** | 0.4+ | latest | Python package manager (`pip` replacement) |

### Core Python Packages

| Package | Minimum | Purpose |
|---|---|---|
| `fastapi` | 0.133.0 | Backend web server |
| `uvicorn[standard]` | 0.41.0 | ASGI server |
| `ultralytics` | 8.4.16 | YOLOv12 inference |
| `opencv-python` | 4.13.0.92 | Image processing |
| `pypylon` | 26.1.0 | Basler GigE camera driver |
| `paddleocr` | 3.4.0 | OCR text recognition |
| `boto3` | 1.42.58 | AWS S3 integration (optional) |
| `python-multipart` | 0.0.22 | FastAPI multipart form parsing |

### Hardware Requirements

| Item | Minimum | Recommended |
|---|---|---|
| **CPU** | 4-core (Intel i5 or equiv.) | 8-core (i7 / Xeon) |
| **RAM** | 8 GB | 16 GB+ |
| **GPU** | CPU-only is supported | NVIDIA CUDA GPU (RTX 3060+) |
| **Storage** | 50 GB free | 200 GB+ SSD |
| **Camera** | USB Webcam | Basler GigE (acA series) |

---

## Installation (Fresh Ubuntu)

### Step 1: Clone the repository

```bash
cd ~
git clone https://github.com/GHengoy/NS_runtime.git
cd NS_runtime
```

### Step 2: Run setup.sh

`setup.sh` handles everything automatically — system packages, Python, Node.js, and the frontend build.

```bash
chmod +x setup.sh
./setup.sh
```

**What setup.sh does:**

| Step | Action |
|------|--------|
| 1 | Installs system packages via `apt` (build tools, OpenCV libs, etc.) |
| 2 | Installs `uv` (Python package manager) if not present |
| 3 | Installs Node.js 20 LTS via NodeSource if not present |
| 4 | Runs `uv sync` to install all Python dependencies |
| 5 | Installs PaddlePaddle GPU version if an NVIDIA GPU is detected |
| 6 | Runs `npm install && npm run build` to build the React frontend |

After setup completes, add `uv` to your PATH if you haven't already:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

---

### Manual Installation (step-by-step reference)

If you prefer to install dependencies manually:

#### System packages

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y build-essential git curl wget \
    python3-dev python3-pip python3-venv \
    libgl1-mesa-glx libglib2.0-0 libsm6 libxext6 libxrender-dev \
    lsof net-tools
```

#### NVIDIA GPU driver & CUDA (optional — skip for CPU-only)

```bash
# Check if GPU is present
lspci | grep -i nvidia

# Install driver
sudo apt install -y nvidia-driver-535
sudo reboot

# Verify
nvidia-smi
```

#### uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
uv --version
```

#### Node.js 20 LTS

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
node --version   # v20.x.x
```

#### Python dependencies

```bash
cd ~/NS_runtime
uv sync
```

#### Frontend

```bash
cd ~/NS_runtime/frontend
npm install
npm run build
cd ..
```

---

## Running the Application

```bash
cd ~/NS_runtime
chmod +x start.sh
./start.sh
```

`start.sh` kills any stale processes on ports 8000 / 5173, then starts both the backend and frontend.

### Accessing the Dashboard

| URL | Description |
|-----|-------------|
| `http://localhost:5173` | Main dashboard |
| `http://localhost:8000/docs` | API docs (Swagger UI) |
| `http://localhost:8000/redoc` | API docs (ReDoc) |

### Default Admin Password

- **Password**: `1234`
- Change this immediately via the Admin page or in `configs/global_settings.json`.

---

## Project Structure

```
NS_runtime/
├── backend/                    # FastAPI backend
│   ├── main.py                 # API endpoints
│   ├── collection.py           # Video collection logic
│   ├── storage.py              # Data storage manager
│   └── history_db.py           # History tracking
│
├── frontend/                   # React dashboard
│   ├── src/
│   │   ├── pages/              # Dashboard, Admin
│   │   ├── components/         # CameraCard, LineModal, etc.
│   │   └── api.ts              # API client
│   ├── package.json
│   └── vite.config.ts
│
├── inspection_framework/       # Core AI inspection engine
│   ├── config.py               # Configuration dataclass
│   ├── camera.py               # Basler GigE camera interface
│   ├── detector.py             # Plugin detector system
│   ├── detector_yolo.py        # YOLO object detection
│   ├── detector_paddleocr.py   # Text recognition (OCR)
│   ├── detector_cnn.py         # Image classification
│   ├── rejecter.py             # Reject signal control
│   ├── datamanager.py          # Data storage & archiving
│   ├── inspection_worker.py    # Background worker thread
│   └── inspection_runtime.py  # Standalone mode (OpenCV display)
│
├── configs/
│   └── global_settings.json   # Global settings (storage, admin, layout)
│
├── workers/                    # Per-line worker directories
│   ├── worker-01/
│   │   ├── config.json         # Line config (camera, model, thresholds)
│   │   ├── camera.pfs          # Basler camera settings
│   │   ├── weights/            # Model files (.pt)
│   │   └── data/               # Defect images & archives
│   └── worker-02/
│       └── ...
│
├── pyproject.toml             # Python dependencies
├── uv.lock                    # Locked dependency versions
├── setup.sh                   # One-time setup script
└── start.sh                   # Application launcher
```

---

## Worker Directory Convention

Each inspection line lives in its own `workers/worker-XX/` directory:

- **config.json** — Line configuration (camera IP, model path, thresholds, reject timing)
- **camera.pfs** — Basler camera settings exported from Pylon Viewer
- **weights/** — Model weight files (`.pt` for YOLO, `.pth` for CNN)
- **data/** — Defect images, preview images, and archives

File paths in `config.json` (e.g., `model_path`, `pfs_file`, `save_root`) are **relative to the worker directory**.

---

## Troubleshooting

### Port already in use

```bash
lsof -ti :8000 | xargs kill -9
lsof -ti :5173 | xargs kill -9
```

### Camera not found (Basler GigE)

```bash
# Verify camera is on the same subnet
ip addr show | grep inet

# Basler cameras typically use 192.168.1.x
# Assign your NIC to the same subnet:
sudo ip addr add 192.168.1.1/24 dev eth0
```

### CUDA not available

```bash
nvidia-smi
uv run python -c "import torch; print('CUDA:', torch.cuda.is_available())"

# If False, install CUDA-enabled PyTorch:
uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```

### ModuleNotFoundError

```bash
uv sync
```

### Frontend build issues

```bash
cd frontend
rm -rf node_modules package-lock.json
npm install
npm run build
```

### uv not found in new terminal

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

---

**Last Updated:** 2026-05-30
