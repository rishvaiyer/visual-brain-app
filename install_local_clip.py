#!/usr/bin/env python3
"""Install official CLIP weights for the vendored MIT-licensed MLX inference code."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import mlx.core as mx
import torch
from huggingface_hub import snapshot_download


ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("VISUAL_BRAIN_DATA_DIR", ROOT / "data"))
MODEL = DATA / "clip-model"
DOWNLOAD = DATA / "clip-download"
SOURCE = "openai/clip-vit-base-patch32"


def main():
    MODEL.mkdir(parents=True, exist_ok=True)
    if (MODEL / "source.json").exists() and (MODEL / "model.safetensors").exists():
        print("Local visual search is already installed.")
        return
    print("Downloading the original CLIP checkpoint to this Mac (about 600 MB)…", flush=True)
    snapshot_download(SOURCE, local_dir=DOWNLOAD,
                      allow_patterns=["pytorch_model.bin", "config.json", "merges.txt", "vocab.json", "preprocessor_config.json"])
    print("Converting for Apple Silicon…", flush=True)
    weights = torch.load(DOWNLOAD / "pytorch_model.bin", map_location="cpu", weights_only=True)
    converted = {name: mx.array(value.float().numpy()) for name, value in weights.items()}
    temp = MODEL / "model.new.safetensors"
    mx.save_safetensors(str(temp), converted)
    for name in ("config.json", "merges.txt", "vocab.json", "preprocessor_config.json"):
        shutil.copyfile(DOWNLOAD / name, MODEL / name)
    temp.replace(MODEL / "model.safetensors")
    (MODEL / "source.json").write_text(json.dumps({"source": SOURCE, "conversion": "MLX CLIP"}))
    shutil.rmtree(DOWNLOAD)
    print("Installed. Restart Visual Brain to enable Find by image content.")


if __name__ == "__main__":
    main()
