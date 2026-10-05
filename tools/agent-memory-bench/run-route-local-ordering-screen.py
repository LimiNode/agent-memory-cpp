#!/usr/bin/env python3
"""Measure primary-route physical ordering against canonical document order.

This is a research-only screen.  Document IDs remain stable; the treatment is
an explicit document-id -> physical-slot permutation derived from the frozen
spherical IVF primary-cell assignment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

N = 1_000_000
D = 384
MODES = ("prototype_ivf", "modern_r4")
WORKLOADS = ("route5000", "top128")
CODEC_WIDTHS = {"lsq32": 36, "tq1": 52, "tq1-pq8": 64, "int8": 392}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(np.ceil(fraction * len(ordered))) - 1))]


def load_ids(path: Path, queries: int, width: int) -> np.ndarray:
    values = np.fromfile(path, dtype="<i4")
    if values.size != queries * width:
        raise ValueError(f"workload shape differs: {path}")
    values = values.reshape(queries, width)
    if np.any(values < 0) or np.any(values >= N):
        raise ValueError(f"workload IDs out of range: {path}")
    if any(np.unique(row).size != width for row in values):
        raise ValueError(f"workload contains duplicate IDs: {path}")
    return values


def primary_assignment(documents: Path, train_vectors: Path, seed: int, nlist: int,
                       train_rows: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    try:
        import faiss
    except ImportError as error:
        raise RuntimeError("faiss is required for route-local ordering") from error
    docs = np.memmap(documents, mode="r", dtype="<f4", shape=(N, D))
    train = np.memmap(train_vectors, mode="r", dtype="<f4", shape=(train_rows, D))
    train_values = np.asarray(train, dtype="float32").copy()
    train_values /= np.maximum(np.linalg.norm(train_values, axis=1, keepdims=True), 1e-30)
    kmeans = faiss.Kmeans(D, nlist, niter=20, seed=seed, verbose=False, spherical=True)
    kmeans.train(train_values)
    centroids = np.asarray(kmeans.centroids, dtype="float32").reshape(nlist, D)
    assigned = np.empty(N, dtype="int32")
    for begin in range(0, N, 16384):
        end = min(N, begin + 16384)
        block = np.asarray(docs[begin:end], dtype="float32").copy()
        block /= np.maximum(np.linalg.norm(block, axis=1, keepdims=True), 1e-30)
        _, cells = kmeans.index.search(block, 1)
        assigned[begin:end] = cells[:, 0]
    order = np.lexsort((np.arange(N, dtype="int32"), assigned)).astype("int32")
    return assigned, order, centroids


def locality(ids: np.ndarray, physical_slot: np.ndarray, segment_rows: int) -> dict:
    touched = []
    for row in ids:
        blocks = np.unique(physical_slot[row] // segment_rows)
        touched.append(float(blocks.size))
    codec_bytes = {}
    for codec, width in CODEC_WIDTHS.items():
        useful = ids.shape[1] * width
        fetched = [value * segment_rows * width for value in touched]
        codec_bytes[codec] = {
            "logical_useful_bytes": useful,
            "fetched_bytes_p50": percentile(fetched, .50),
            "fetched_bytes_p95": percentile(fetched, .95),
            "amplification_p50": percentile([value / useful for value in fetched], .50),
            "amplification_p95": percentile([value / useful for value in fetched], .95),
        }
    return {
        "queries": int(ids.shape[0]),
        "width": int(ids.shape[1]),
        "segment_rows": segment_rows,
        "touched_blocks_p50": percentile(touched, .50),
        "touched_blocks_p95": percentile(touched, .95),
        "touched_blocks_p99": percentile(touched, .99),
        "codec_bytes": codec_bytes,
        "samples_touched_blocks": [int(value) for value in touched],
    }


def compression(path: Path, order: np.ndarray, width: int, block_bytes: int = 16384) -> dict:
    try:
        import zstandard as zstd
    except ModuleNotFoundError as error:
        raise RuntimeError("zstandard is required for compression arm") from error
    payload = np.memmap(path, mode="r", dtype="u1", shape=(N, width))
    rows_per_block = max(1, block_bytes // width)
    compressor = zstd.ZstdCompressor(level=3)
    compressed = 0
    blocks = 0
    for begin in range(0, N, rows_per_block):
        end = min(N, begin + rows_per_block)
        raw = np.asarray(payload[order[begin:end]], dtype="u1").tobytes()
        compressed += len(compressor.compress(raw))
        blocks += 1
    raw_bytes = N * width
    return {"raw_bytes": raw_bytes, "compressed_bytes": compressed, "blocks": blocks,
            "saving_fraction": 1.0 - compressed / raw_bytes,
            "compression_ratio": raw_bytes / compressed,
            "target_block_bytes": block_bytes, "zstd_level": 3}


def self_test() -> None:
    ids = np.asarray([[7, 20, 33, 49]], dtype="int32")
    slots = np.arange(N, dtype="int32")
    slots[[7, 20, 33, 49]] = [0, 1, 2, 3]
    result = locality(ids, slots, 4)
    if result["touched_blocks_p50"] != 1 or result["codec_bytes"]["tq1-pq8"]["logical_useful_bytes"] != 4 * 64:
        raise AssertionError("route-local locality self-test differs")
    print("route-local-ordering self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--documents", type=Path)
    parser.add_argument("--train-vectors", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fixture-root", type=Path)
    parser.add_argument("--payload-root", type=Path)
    parser.add_argument("--route-manifest", type=Path)
    parser.add_argument("--assignment-output", type=Path)
    parser.add_argument("--permutation-output", type=Path)
    parser.add_argument("--segment-rows", type=int, default=4096)
    parser.add_argument("--nlist", type=int, default=256)
    parser.add_argument("--seed", type=int, default=20261002)
    parser.add_argument("--train-rows", type=int, default=25000)
    args = parser.parse_args()
    if args.self_test:
        self_test(); return
    for name in ("documents", "train_vectors", "output", "fixture_root", "payload_root"):
        if getattr(args, name) is None:
            parser.error(f"--{name.replace('_', '-')} is required")
    assigned, order, centroids = primary_assignment(args.documents, args.train_vectors, args.seed, args.nlist, args.train_rows)
    assignment_output = args.assignment_output or args.output.with_suffix(".assignment.i4")
    permutation_output = args.permutation_output or args.output.with_suffix(".permutation.i4")
    assignment_output.parent.mkdir(parents=True, exist_ok=True)
    assigned.astype("<i4", copy=False).tofile(assignment_output)
    order.astype("<i4", copy=False).tofile(permutation_output)
    physical_slot = np.empty(N, dtype="int32"); physical_slot[order] = np.arange(N, dtype="int32")
    workload_root = args.fixture_root / "workloads"
    workloads = {}
    for mode in MODES:
        workloads[mode] = {}
        for workload, width in (("route5000", 5000), ("top128", 128)):
            path = workload_root / mode / ("route-5000.i4" if workload == "route5000" else "top128.i4")
            workloads[mode][workload] = load_ids(path, 305, width)
    payload_manifest = args.fixture_root / "fixture.manifest.json"
    if not payload_manifest.is_file():
        raise ValueError(f"missing fixture manifest: {payload_manifest}")
    fixture = json.loads(payload_manifest.read_text(encoding="utf-8"))
    if fixture.get("documents") != N or fixture.get("queries") != 305:
        raise ValueError("fixture manifest dimensions differ")
    expected_payloads = fixture.get("payloads", {})
    for codec, width in CODEC_WIDTHS.items():
        entry = expected_payloads.get(codec)
        path = args.payload_root / codec / "payload.bin"
        if not entry or int(entry.get("payload_bytes_doc", -1)) != width:
            raise ValueError(f"fixture payload width differs for {codec}")
        if path.stat().st_size != N * width:
            raise ValueError(f"payload size differs for {codec}: {path}")
        if sha256(path) != entry.get("payload_sha256"):
            raise ValueError(f"payload hash differs from fixture manifest for {codec}")
    workload_sources = {}
    for mode in MODES:
        workload_sources[mode] = {}
        for workload, ids in workloads[mode].items():
            path = workload_root / mode / ("route-5000.i4" if workload == "route5000" else "top128.i4")
            workload_sources[mode][workload] = {
                "path": str(path), "sha256": sha256(path),
                "shape": [int(value) for value in ids.shape], "dtype": "int32",
            }
    route_manifest_binding = None
    if args.route_manifest:
        if not args.route_manifest.is_file():
            raise ValueError(f"missing route manifest: {args.route_manifest}")
        canonical_manifest = json.loads(args.route_manifest.read_text(encoding="utf-8"))
        route_manifest_binding = {
            "path": str(args.route_manifest),
            "sha256": sha256(args.route_manifest),
            "canonical_route_model_sha256": canonical_manifest.get("route_model_sha256"),
            "canonical_nlist": canonical_manifest.get("nlist"),
            "canonical_training_rows": canonical_manifest.get("training_rows"),
            "config_matches": canonical_manifest.get("nlist") == args.nlist and canonical_manifest.get("training_rows") == args.train_rows,
            "comparison_note": "centroids_sha256 is compared as a diagnostic only; route_model_sha256 may cover a wider artifact",
        }
    centroids_sha = sha256_bytes(np.asarray(centroids, dtype="<f4").tobytes())
    if route_manifest_binding is not None:
        route_manifest_binding["regenerated_centroids_sha256"] = centroids_sha
        route_manifest_binding["centroids_hash_equals_manifest_route_model_hash"] = (
            centroids_sha == route_manifest_binding["canonical_route_model_sha256"]
        )
    result = {
        "schema_version": 1,
        "family": "route_local_physical_ordering_screen_v1",
        "status": "EXECUTED",
        "documents": N,
        "queries": 305,
        "dimension": D,
        "fixture_manifest_sha256": sha256(payload_manifest),
        "route_model": {"nlist": args.nlist, "seed": args.seed, "train_rows": args.train_rows,
                         "documents_sha256": sha256(args.documents), "train_vectors_sha256": sha256(args.train_vectors),
                         "centroids_sha256": centroids_sha, "assignment_contract": "spherical_faiss_kmeans_argmax_cosine"},
        "canonical_route_manifest": route_manifest_binding,
        "order": {"policy": "primary_cell_then_numeric_document_id", "assignment_sha256": hashlib.sha256(assigned.astype("<i4", copy=False).tobytes()).hexdigest(),
                  "permutation_sha256": hashlib.sha256(order.astype("<i4", copy=False).tobytes()).hexdigest(),
                  "assignment_path": str(assignment_output), "permutation_path": str(permutation_output)},
        "segment_rows": args.segment_rows,
        "workloads": {},
    }
    canonical_slot = np.arange(N, dtype="int32")
    for mode in MODES:
        result["workloads"][mode] = {}
        for workload, ids in workloads[mode].items():
            result["workloads"][mode][workload] = {"canonical": locality(ids, canonical_slot, args.segment_rows),
                                                   "route_local": locality(ids, physical_slot, args.segment_rows),
                                                   "source": workload_sources[mode][workload]}
    result["compression"] = {}
    for codec, width in CODEC_WIDTHS.items():
        path = args.payload_root / codec / "payload.bin"
        result["compression"][codec] = {"source_path": str(path), "source_sha256": sha256(path),
                                        "canonical": compression(path, np.arange(N, dtype="int32"), width),
                                        "route_local": compression(path, order, width)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "EXECUTED", "output": str(args.output), "permutation_sha256": result["order"]["permutation_sha256"]}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise SystemExit(f"route-local-ordering: {error}")
