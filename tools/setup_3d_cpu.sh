#!/usr/bin/env bash
# Free local 3D worker setup. Run on the Ryzen, never on the user's Air.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKER_DIR="${VISUAL_BRAIN_3D_WORKER_DIR:-$ROOT/.local-3d-worker}"
TRIPOSR_DIR="$WORKER_DIR/TripoSR"
PYTHON="$WORKER_DIR/.venv/bin/python"
TRIPOSR_COMMIT=107cefdc244c39106fa830359024f6a2f1c78871

mkdir -p "$WORKER_DIR"
if [[ -f "$WORKER_DIR/.ready" && -x "$PYTHON" && -f "$TRIPOSR_DIR/run.py" ]]; then
  echo "Free 3D worker already ready: $PYTHON"
  exit 0
fi
rm -f "$WORKER_DIR/.ready"
if [[ ! -d "$TRIPOSR_DIR/.git" ]]; then
  git clone --depth 1 https://github.com/VAST-AI-Research/TripoSR.git "$TRIPOSR_DIR"
fi
git -C "$TRIPOSR_DIR" fetch --depth 1 origin "$TRIPOSR_COMMIT"
git -C "$TRIPOSR_DIR" checkout --detach "$TRIPOSR_COMMIT"
if [[ ! -x "$PYTHON" ]]; then
  uv venv --python 3.11 "$WORKER_DIR/.venv"
fi
uv pip install --python "$PYTHON" setuptools wheel ninja scikit-build-core 'cmake>=3.24' pybind11
uv pip install --python "$PYTHON" torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu
uv pip install --python "$PYTHON" 'numpy<2' omegaconf==2.3.0 Pillow==10.1.0 einops==0.7.0 transformers==4.35.0 trimesh==4.0.5 rembg onnxruntime 'imageio[ffmpeg]' xatlas==0.0.9 moderngl==5.10.0 huggingface-hub
export CMAKE_PREFIX_PATH="$("$PYTHON" -m pybind11 --cmakedir)"
uv pip install --python "$PYTHON" --no-build-isolation 'git+https://github.com/tatsy/torchmcubes.git@a9ac95aa329957ba646e7bb057f825f5dfa77f38'
export OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 HF_HOME="$WORKER_DIR/model-cache"
timeout 1800 "$PYTHON" "$TRIPOSR_DIR/run.py" "$TRIPOSR_DIR/examples/chair.png" \
  --device cpu --model-save-format glb --mc-resolution 64 --chunk-size 1024 \
  --output-dir "$WORKER_DIR/self-test"
test -s "$WORKER_DIR/self-test/0/mesh.glb"
touch "$WORKER_DIR/.ready"
echo "Free 3D worker ready: $PYTHON"
