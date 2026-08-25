#!/usr/bin/env python3
"""Materialize byte-bound float-IVF centroid routing inputs for native C++ runs."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import faiss
import numpy


THIS = Path(__file__).resolve().parent


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("native_centroid_routing_planner", "plan-native-centroid-routing.py")


def validate_float_evidence(path: Path) -> tuple[dict[str, Any], bytes]:
    require(path.is_file(), "native centroid routing float evidence ZIP is missing")
    payload = path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("bundle/evidence-manifest.json"))
        members = manifest.get("members")
        require(manifest.get("schema_version") == 1 and manifest.get("family") == "float_semantic_ivf_evidence_v1" and manifest.get("row_count") == 12 and isinstance(members, dict) and len(archive.namelist()) == len(set(archive.namelist())) and set(archive.namelist()) == set(members) | {"bundle/evidence-manifest.json"}, "native centroid routing float evidence manifest differs")
        for name, metadata in members.items():
            value = archive.read(name)
            require(metadata == {"sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}, f"native centroid routing float evidence member differs: {name}")
    return manifest, payload


def frozen_evaluation_manifest_sha256(path: Path, manifest: dict[str, Any], scale: str) -> str:
    name = f"bundle/{scale}/frozen-evaluation-manifest.json"
    members = manifest["members"]
    require(name in members, f"native centroid routing archived evaluation manifest is missing: {scale}")
    with zipfile.ZipFile(path) as archive:
        value = archive.read(name)
    require(members[name] == {"sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}, f"native centroid routing archived evaluation manifest differs: {scale}")
    return members[name]["sha256"]


def projection(dimensions: int, seed: int, centroid_count: int) -> numpy.ndarray:
    generator = numpy.random.Generator(numpy.random.PCG64(seed + centroid_count * 1009 + 512))
    return generator.integers(0, 2, size=(512, dimensions), dtype=numpy.int8).astype(numpy.float32) * 2.0 - 1.0


def materialize_scale(contract: dict[str, Any], scale: dict[str, Any], scale_root: Path, float_root: Path, float_manifest: dict[str, Any], float_evidence_path: Path, output_root: Path) -> None:
    scale_id = scale["id"]
    input_manifest = scale_root / scale_id / "input" / "manifest.json"
    evaluation_root = scale_root / scale_id / "e5"
    evaluation_manifest = evaluation_root / "manifest.json"
    require(sha256(input_manifest) == scale["input_manifest_sha256"], f"native centroid routing input manifest differs: {scale_id}")
    expected_evaluation = frozen_evaluation_manifest_sha256(float_evidence_path, float_manifest, scale_id)
    require(sha256(evaluation_manifest) == expected_evaluation, f"native centroid routing evaluation manifest differs: {scale_id}")
    evaluation_data = json.loads(evaluation_manifest.read_text(encoding="utf-8"))
    query_output = evaluation_data.get("outputs", {}).get("evaluation_query_vectors", {})
    query_path = evaluation_root / query_output.get("path", "")
    require(query_path.is_file() and query_output.get("sha256") == sha256(query_path) and query_output.get("count") == contract["evaluation"]["query_count"] and query_output.get("dimension") == 384 and query_output.get("dtype") == "float32_le", f"native centroid routing query payload differs: {scale_id}")
    queries = numpy.fromfile(query_path, dtype="<f4").reshape(contract["evaluation"]["query_count"], 384)
    for centroid_count in scale["centroid_counts"]:
        index_path = float_root / scale_id / "indexes" / f"centroids-{centroid_count}.faiss"
        assignment_path = float_root / scale_id / "assignments" / f"centroids-{centroid_count}.npy"
        members = float_manifest["members"]
        for path, name in ((index_path, f"bundle/{scale_id}/indexes/{index_path.name}"), (assignment_path, f"bundle/{scale_id}/assignments/{assignment_path.name}")):
            require(name in members and path.is_file() and sha256(path) == members[name]["sha256"], f"native centroid routing frozen artifact differs: {name}")
        index = faiss.read_index(str(index_path))
        centroids = numpy.asarray(index.reconstruct_n(0, centroid_count), dtype="<f4")
        assignments = numpy.asarray(numpy.load(assignment_path, allow_pickle=False), dtype="<u4")
        require(centroids.shape == (centroid_count, 384) and assignments.shape == (scale["documents"],) and numpy.all(assignments < centroid_count), f"native centroid routing shape differs: {scale_id}/{centroid_count}")
        matrix = projection(384, contract["binary_code"]["seed"], centroid_count)
        centroid_projection = centroids @ matrix.T
        query_projection = queries @ matrix.T
        centroid_codes = numpy.packbits((centroid_projection >= 0.0).astype(numpy.uint8), axis=1, bitorder="little")
        query_codes = numpy.packbits((query_projection >= 0.0).astype(numpy.uint8), axis=1, bitorder="little")
        output = output_root / scale_id / f"k{centroid_count}"
        output.mkdir(parents=True, exist_ok=True)
        paths = {"centroids": output / "centroids.f32", "queries": output / "queries.f32", "assignments": output / "assignments.u32", "projection": output / "rademacher512-projection.f32", "centroid_codes": output / "rademacher512-centroid-codes.u8", "query_projection": output / "rademacher512-query-projections.f32", "query_codes": output / "rademacher512-query-codes.u8"}
        centroids.astype("<f4", copy=False).tofile(paths["centroids"]); queries.astype("<f4", copy=False).tofile(paths["queries"]); assignments.astype("<u4", copy=False).tofile(paths["assignments"]); matrix.astype("<f4", copy=False).tofile(paths["projection"]); centroid_codes.tofile(paths["centroid_codes"]); query_projection.astype("<f4", copy=False).tofile(paths["query_projection"]); query_codes.tofile(paths["query_codes"])
        metadata = {"schema_version": 1, "family": "native_centroid_routing_materialization_v1", "scale": scale_id, "documents": scale["documents"], "centroid_count": centroid_count, "dimension": 384, "query_count": contract["evaluation"]["query_count"], "input_manifest_sha256": sha256(input_manifest), "evaluation_manifest_sha256": sha256(evaluation_manifest), "float_evidence_sha256": sha256(float_evidence_path), "centroid_index_sha256": sha256(index_path), "assignment_sha256": sha256(assignment_path), "binary_code": contract["binary_code"], "outputs": {name: {"path": path.name, "sha256": sha256(path), "bytes": path.stat().st_size} for name, path in paths.items()}}
        (output / "manifest.json").write_bytes(canonical(metadata))


def self_test() -> None:
    matrix = projection(3, 4, 5)
    require(matrix.shape == (512, 3) and numpy.array_equal(matrix, projection(3, 4, 5)) and not numpy.array_equal(matrix, projection(3, 4, 6)), "native centroid routing projection differs")
    print("native centroid routing materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "native-centroid-routing.example.json"); parser.add_argument("--scale-root", type=Path); parser.add_argument("--float-root", type=Path); parser.add_argument("--float-evidence", type=Path); parser.add_argument("--output-root", type=Path); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test(); return 0
        if any(value is None for value in (args.scale_root, args.float_root, args.float_evidence, args.output_root)):
            parser.error("--scale-root, --float-root, --float-evidence, and --output-root are required")
        contract = planner.load_contract(args.contract)
        require(faiss.__version__ == contract["faiss_version"], "native centroid routing Faiss version differs")
        manifest, _ = validate_float_evidence(args.float_evidence)
        for scale in contract["scales"]:
            materialize_scale(contract, scale, args.scale_root, args.float_root, manifest, args.float_evidence, args.output_root)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"materialize-native-centroid-routing: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
