#!/usr/bin/env bash
# Optional free multi-photo worker setup. Run on the Ryzen beside setup_3d_cpu.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKER_DIR="${VISUAL_BRAIN_3D_WORKER_DIR:-$ROOT/.local-3d-worker}"
OPENMVS_SOURCE="${VISUAL_BRAIN_OPENMVS_SOURCE_BIN:-$HOME/work/visual-brain-multiview-benchmark/openmvs}"
OPENMVS_DIR="$WORKER_DIR/openmvs"
PYTHON="$WORKER_DIR/multi-venv/bin/python"
OPENMVS_URL="https://github.com/cdcseacave/openMVS/releases/download/v2.4.0/OpenMVS_Ubuntu_x64.zip"
OPENMVS_SHA256="7104ae1ddd6ca38fbca9e0e4a70b20af59e21e0b497eb7181c864fbf38ca8d00"
DOWNLOAD_DIR=""
trap 'if [[ -n "$DOWNLOAD_DIR" ]]; then rm -rf "$DOWNLOAD_DIR"; fi' EXIT

test -f "$WORKER_DIR/.ready" || { echo "Set up the single-photo worker first" >&2; exit 1; }
mkdir -p "$OPENMVS_DIR" "$WORKER_DIR/model-cache"
if [[ ! -x "$OPENMVS_SOURCE/InterfaceCOLMAP" && -z "${VISUAL_BRAIN_OPENMVS_SOURCE_BIN:-}" ]]; then
  DOWNLOAD_DIR="$(mktemp -d -t visual-brain-openmvs-XXXXXX)"
  curl --fail --location --retry 3 --silent --show-error "$OPENMVS_URL" -o "$DOWNLOAD_DIR/openmvs.zip"
  printf '%s  %s\n' "$OPENMVS_SHA256" "$DOWNLOAD_DIR/openmvs.zip" | sha256sum --check --status
  unzip -j -q "$DOWNLOAD_DIR/openmvs.zip" InterfaceCOLMAP DensifyPointCloud ReconstructMesh TextureMesh -d "$DOWNLOAD_DIR"
  OPENMVS_SOURCE="$DOWNLOAD_DIR"
fi
for name in InterfaceCOLMAP DensifyPointCloud ReconstructMesh TextureMesh; do
  test -x "$OPENMVS_SOURCE/$name" || { echo "Missing OpenMVS binary: $name" >&2; exit 1; }
  cp "$OPENMVS_SOURCE/$name" "$OPENMVS_DIR/$name"
  if ldd "$OPENMVS_DIR/$name" | grep -q 'not found'; then
    echo "OpenMVS dependency is missing for $name" >&2
    exit 1
  fi
done
if [[ ! -x "$PYTHON" ]]; then
  uv venv --python 3.11 "$WORKER_DIR/multi-venv"
fi
uv pip install --python "$PYTHON" 'numpy==2.4.6' 'pycolmap==4.2.1' \
  'onnxruntime==1.31.1' 'rembg==2.0.86' 'trimesh==5.2.0' \
  'Pillow==13.0.0'
U2NET_HOME="$WORKER_DIR/model-cache" OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=2 \
  "$PYTHON" -c 'import numpy, onnxruntime, pycolmap, trimesh; from rembg import new_session; new_session("u2netp")'
touch "$WORKER_DIR/.multi-ready"
echo "Free multi-photo worker ready"
