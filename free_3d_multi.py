"""Build one textured GLB from an overlapping walk around one still object.

This runs only in the optional Ryzen worker. The web server imports none of its
large dependencies. Each invocation owns its temporary working directory.
"""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import onnxruntime as ort
import pycolmap
import trimesh
from PIL import Image
from rembg import new_session, remove

MIN_PHOTOS = 32
MAX_PHOTOS = 80
THREADS = 4


class MultiViewError(RuntimeError):
    """A user-facing capture or reconstruction failure."""


def _run(binary: Path, args: list[str], work: Path, timeout: int) -> None:
    with (work / (binary.name + ".log")).open("w") as log:
        result = subprocess.run([str(binary), *args], cwd=work, stdout=log,
                                stderr=subprocess.STDOUT, timeout=timeout, check=False)
    if result.returncode:
        raise MultiViewError("These photos could not be joined into one 3D object. Try a full, overlapping circle around a still object.")


def _camera_coverage(reconstruction: pycolmap.Reconstruction) -> tuple[float, np.ndarray]:
    ups, centers = [], []
    for image in reconstruction.images.values():
        pose = image.cam_from_world().matrix()
        rotation, translation = pose[:, :3], pose[:, 3]
        ups.append(rotation.T @ np.array([0., -1., 0.]))
        centers.append(-rotation.T @ translation)
    up = np.mean(ups, axis=0)
    up /= np.linalg.norm(up)
    center = np.median([point.xyz for point in reconstruction.points3D.values()], axis=0)
    first = np.array([1., 0., 0.])
    first -= np.dot(first, up) * up
    if np.linalg.norm(first) < .1:
        first = np.array([0., 0., 1.]) - up[2] * up
    first /= np.linalg.norm(first)
    second = np.cross(up, first)
    centered = np.asarray(centers) - center
    angles = np.sort(np.mod(np.arctan2(centered @ second, centered @ first), 2 * math.pi))
    largest_gap = float(np.degrees(np.diff(np.r_[angles, angles[0] + 2 * math.pi]).max()))
    return largest_gap, up


def _make_masks(mvs: Path, work: Path) -> None:
    masks = work / "masks"
    masks.mkdir()
    options = ort.SessionOptions()
    options.intra_op_num_threads = THREADS
    options.inter_op_num_threads = 1
    session = new_session("u2netp", sess_opts=options)
    for source in sorted((mvs / "images").glob("*.jpg")):
        with Image.open(source) as original:
            preview = original.copy()
            preview.thumbnail((800, 800), Image.Resampling.LANCZOS)
            mask = remove(preview, session=session, only_mask=True).convert("L")
            mask = mask.point(lambda value: 255 if value >= 112 else 0)
            foreground = np.asarray(mask)
            coverage = np.count_nonzero(foreground) / foreground.size
            if not .015 <= coverage <= .85:
                raise MultiViewError("The object could not be separated from its background in every photo. Try clearer, evenly lit views.")
            output = masks / (source.stem + ".mask.png")
            mask.resize(original.size, Image.Resampling.NEAREST).save(output)
            # OpenMVS stages accept either placement, depending on the binary.
            (masks / (source.name + ".mask.png")).symlink_to(output.name)
            (mvs / "images" / output.name).symlink_to(output)


def _orient_and_export(obj: Path, camera_up: np.ndarray, output: Path) -> None:
    scene = trimesh.load(obj, force="scene", process=False)
    if not scene.geometry:
        raise MultiViewError("The photos did not produce a 3D object.")
    vertices = np.vstack([mesh.vertices for mesh in scene.geometry.values()])
    limits = np.percentile(vertices, [2, 98], axis=0)
    trimmed = vertices[np.all((vertices >= limits[0]) & (vertices <= limits[1]), axis=1)]
    if len(trimmed) < 100:
        raise MultiViewError("The photos did not produce enough 3D detail.")
    values, vectors = np.linalg.eigh(np.cov(trimmed.T))
    vectors = vectors[:, np.argsort(values)[::-1]]
    up_index = int(np.argmax(np.abs(vectors.T @ camera_up)))
    up = vectors[:, up_index] * np.sign(vectors[:, up_index] @ camera_up)
    length = vectors[:, next(index for index in range(3) if index != up_index)]
    depth = np.cross(length, up)
    depth /= np.linalg.norm(depth)
    orientation = np.column_stack((length, up, depth))
    transform = np.eye(4)
    transform[:3, :3] = orientation.T
    transform[:3, 3] = -orientation.T @ np.median(trimmed, axis=0)
    scene.apply_transform(transform)
    for mesh in scene.geometry.values():
        material = getattr(mesh.visual, "material", None)
        if material is not None:
            try:
                material.doubleSided = True
            except (AttributeError, TypeError):
                pass
    raw = scene.export(file_type="glb")
    if len(raw) > 40 * 1024 * 1024:
        raise MultiViewError("The 3D model is too large to save. Try fewer photos with less background.")
    output.write_bytes(raw)


def generate(images: Path, output: Path, openmvs_bin: Path) -> None:
    files = sorted(images.glob("*.jpg"))
    if not MIN_PHOTOS <= len(files) <= MAX_PHOTOS:
        raise MultiViewError(f"Choose {MIN_PHOTOS} to {MAX_PHOTOS} overlapping photos of one still object.")
    for name in ("InterfaceCOLMAP", "DensifyPointCloud", "ReconstructMesh", "TextureMesh"):
        if not (openmvs_bin / name).is_file():
            raise MultiViewError("The free multi-photo worker is not installed.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="visual-brain-multi-") as temporary:
        work = Path(temporary)
        database = work / "database.db"
        extraction = pycolmap.FeatureExtractionOptions()
        extraction.num_threads = THREADS
        pycolmap.extract_features(database, images, camera_mode=pycolmap.CameraMode.SINGLE,
                                   extraction_options=extraction, device=pycolmap.Device.cpu)
        matching = pycolmap.FeatureMatchingOptions()
        matching.num_threads = THREADS
        pycolmap.match_exhaustive(database, matching_options=matching, device=pycolmap.Device.cpu)
        options = pycolmap.IncrementalPipelineOptions()
        options.num_threads = THREADS
        maps = pycolmap.incremental_mapping(database, images, work / "maps", options=options)
        if not maps:
            raise MultiViewError("The photos do not overlap enough to build one object. Try walking all the way around it.")
        reconstruction = max(maps.values(), key=lambda item: item.num_reg_images())
        registered = reconstruction.num_reg_images()
        if registered < max(28, math.ceil(.7 * len(files))):
            raise MultiViewError("Too few photos joined together. Try more overlapping views around the same still object.")
        largest_gap, camera_up = _camera_coverage(reconstruction)
        if largest_gap > 100:
            raise MultiViewError("One side of the object is missing. Add photos around the full circle.")
        sparse = work / "sparse"
        sparse.mkdir()
        reconstruction.write(sparse)
        mvs = work / "mvs"
        pycolmap.undistort_images(mvs, sparse, images, num_threads=THREADS)
        _make_masks(mvs, work)
        _run(openmvs_bin / "InterfaceCOLMAP", ["-i", "mvs", "-o", "scene.mvs", "--max-threads", "4"], work, 300)
        _run(openmvs_bin / "DensifyPointCloud", ["-i", "scene.mvs", "-o", "scene_dense.mvs", "--max-threads", "6",
                                                   "--resolution-level", "1", "--mask-path", "masks/", "--ignore-mask-label", "0"], work, 1200)
        _run(openmvs_bin / "ReconstructMesh", ["-i", "scene_dense.mvs", "-p", "scene_dense.ply", "-o", "scene_dense_mesh.mvs",
                                                 "--max-threads", "6", "--decimate", "0.45", "--remove-spurious", "4",
                                                 "--close-holes", "10", "--smooth", "2"], work, 600)
        _run(openmvs_bin / "TextureMesh", ["-i", "scene_dense.mvs", "-m", "scene_dense_mesh.ply", "-o", "scene_dense_mesh_texture.mvs",
                                             "--max-threads", "6", "--export-type", "obj", "--ignore-mask-label", "0",
                                             "--global-seam-leveling", "0", "--local-seam-leveling", "0"], work, 600)
        _orient_and_export(work / "scene_dense_mesh_texture.obj", camera_up, output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("images", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--openmvs-bin", type=Path, default=Path(os.environ.get("VISUAL_BRAIN_OPENMVS_BIN", "")))
    args = parser.parse_args()
    try:
        generate(args.images, args.output, args.openmvs_bin)
    except MultiViewError as error:
        print(f"VISUAL_BRAIN_MULTI_ERROR:{error}", file=sys.stderr)
        raise SystemExit(2) from None
    print("MULTI_VIEW_READY", args.output.stat().st_size)
