# Gaussian Adaptive Streamer

Gaussian Adaptive Streamer is a prototype system for adaptive streaming of 3D Gaussian Splatting scenes over modern web transport protocols. The project combines HTTP/3-based delivery, DASH-style adaptive streaming, and server-side Gaussian model handling to efficiently stream large neural rendering datasets to a client.

# Platform Support

- NVIDIA/CUDA systems: full gsplat rasterization path and hardware H.264 encoding when available.
- macOS Apple Silicon systems: no CUDA required. The server uses MPS/CPU where possible and automatically falls back to preview-image rendering when gsplat/CUDA rasterization is unavailable.

# Requirements

- Python 3.12
- ffmpeg installed and accessible from PATH (or set `FFMPEG` to a full binary path)
- Model assets under `static/models` (see structure below)

## Optional platform-specific requirements

- NVIDIA/CUDA path:
  - A compatible NVIDIA GPU and driver
  - CUDA-enabled PyTorch wheels from `requirements.txt`
  - ffmpeg with `h264_nvenc`
- macOS/Apple Silicon path:
  - Apple Silicon Mac (M1/M2/M3)
  - ffmpeg with `h264_videotoolbox` (preferred) or `libx264` fallback
  - Use `requirements-macos.txt` (CUDA-free dependency set)

## Directory structure

Create a models directory in the project root and place all models inside it:

```bash
project_root/
├── static
│    └── models/
│       ├── modelID/
│       │   ├── modelName.ply
│       │   └── preview.jpg
│       └── anotherModelID/
│           ├── anotherModelName.ply
│           └── anotherPreview.jpg
└── requirements.txt
```

These models and previews will be loaded automatically when starting the server.

### Using external dataset folders with symlinks

If your real models live outside the repo (for example in `~/Desktop/Datasets/models`), you can create repo-local pointers in the expected structure without copying data:

```bash
bash scripts/link_dataset_models.sh /Users/manu/Desktop/Datasets/models
```

This creates links like `static/models/bicycle/input.ply -> /Users/manu/Desktop/Datasets/models/bicycle/point_cloud/iteration_30000/point_cloud.ply`.

Optional environment variables:

- `GS_ITERATION_DIR=iteration_7000` to target a different iteration subfolder.
- `DATASETS_MODELS_DIR=/path/to/models` to set a default source folder when no argument is passed.

## Installation

Create a clean Python environment and install the project dependencies.

### 1. Create environment

```bash
conda create -n render python=3.12 -y
conda activate render
```

### 2. Install dependencies

NVIDIA/CUDA environment:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

macOS / Apple Silicon environment:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements-macos.txt
```

### 3. Verify runtime device availability

```bash
python -c "import torch; print('Torch:', torch.__version__); print('CUDA available:', torch.cuda.is_available()); print('MPS available:', hasattr(torch.backends, 'mps') and torch.backends.mps.is_available())"
```

### 4. Notes

- `requirements.txt` installs CUDA 11.8 PyTorch wheels for NVIDIA environments.
- `requirements-macos.txt` avoids CUDA-only dependencies for Apple Silicon.
- In non-CUDA environments, rendering can run in preview fallback mode (resized model preview images) so HTTP endpoints and DASH streaming still function.

## Runtime Behavior and Configuration

### Device selection

The server selects torch device in this order:

1. Explicit device passed by code
2. `GS_DEVICE` environment variable (`cuda`, `mps`, or `cpu`)
3. Auto-detect: CUDA -> MPS -> CPU

### Render backend selection

The renderer selects backend as follows:

- `gsplat` backend when CUDA + gsplat are available
- `preview` fallback backend otherwise

You can override with:

- `GS_RENDER_BACKEND=gsplat` (force gsplat; errors if unavailable)
- `GS_RENDER_BACKEND=preview` (force preview fallback)

When rendering requests are served, responses include `X-Render-Backend` to indicate active backend (`gsplat` or `preview`).

### ffmpeg encoder selection

The DASH pipeline auto-selects H.264 encoder in this priority:

1. `FFMPEG_VIDEO_ENCODER` (if supported by local ffmpeg)
2. `h264_videotoolbox` on macOS
3. `h264_nvenc` on non-macOS hosts
4. `libx264` fallback

The `FFMPEG` environment variable can point to a specific ffmpeg binary.

## Running the Server

Start the streaming server:

```bash
python http3_server.py --certificate certificates/ssl_cert.pem --private-key certificates/ssl_key.pem
```

Open Google Chrome with QUIC flags (recommended, cross-platform):

```bash
bash scripts/launch_quic_chrome.sh
```

macOS equivalent command:

```bash
open -a "Google Chrome" --args \
  --enable-experimental-web-platform-features \
  --ignore-certificate-errors-spki-list=BSQJ0jkQ7wwhR7KvPZ+DSNk2XTZ/MS6xCbo9qu++VdQ= \
  --origin-to-force-quic-on=localhost:4433 \
  https://localhost:4433/models-ui
```

Linux equivalent command:

```bash
 google-chrome \
  --enable-experimental-web-platform-features \
  --ignore-certificate-errors-spki-list=BSQJ0jkQ7wwhR7KvPZ+DSNk2XTZ/MS6xCbo9qu++VdQ= \
  --origin-to-force-quic-on=localhost:4433 \
  https://localhost:4433/models-ui
```

**Note:**
- For trying the experimental version with dash.js as player type instead of /models-ui, go to /player-dash.
- Close Google Chrome before running this command.
- To open a different page with the helper script, pass a path: `bash scripts/launch_quic_chrome.sh player-dash`.


## Preview

### Model Selection

![Model selection](images/chooseModel.png)

### Viewer

![Viewer](images/Viewer.png)

### Experiment

![Experiment](images/Experiment.png)