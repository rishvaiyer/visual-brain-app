#!/bin/zsh
set -e
cd "${0:A:h}"
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "Local visual search requires an Apple Silicon Mac."
  read -k 1 '?Press any key to close…'
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "Install uv from https://docs.astral.sh/uv/ and run this again."
  read -k 1 '?Press any key to close…'
  exit 1
fi
uv venv data/clip-venv --python 3.13
uv pip install --python data/clip-venv/bin/python mlx numpy regex Pillow huggingface_hub torch
data/clip-venv/bin/python install_local_clip.py
read -k 1 '?Press any key to close…'
