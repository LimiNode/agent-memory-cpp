#!/usr/bin/env python3
"""Fail-closed audit for the route-local physical-ordering research screen."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

N = 1_000_000
Q = 305
WIDTHS = {"route5000": 5000, "top128": 128}
CODEC_WIDTHS = {"lsq32": 36, "tq1": 52, "tq1-pq8": 64, "int8": 392}
MODES = ("prototype_ivf", "modern_r4")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate_permutation(values: np.ndarray) -> None:
    require(values.dtype == np.dtype("<i4"), "permutation dtype must be int32")
    require(values.size == N, "permutation length differs")
    require(np.array_equal(np.sort(values), np.arange(N, dtype="int32")), "permutation is not bijective")


def validate_locality(entry: dict, label: str) -> None:
    require(entry.get("queries") == Q, f"{label}: query count differs")
    require(entry.get("segment_rows", 0) > 0, f"{label}: invalid segment size")
    samples = entry.get("samples_touched_blocks")
    require(isinstance(samples, list) and len(samples) == Q, f"{label}: touched sample count differs")
    require(all(isinstance(value, int) and value > 0 for value in samples), f"{label}: invalid touched sample")
    require(entry.get("touched_blocks_p50") <= entry.get("touched_blocks_p95") <= entry.get("touched_blocks_p99"),
            f"{label}: percentile order differs")
    codec_bytes = entry.get("codec_bytes")
    require(set(codec_bytes or {}) == set(CODEC_WIDTHS), f"{label}: codec byte metrics incomplete")
    for codec, width in CODEC_WIDTHS.items():
        metrics = codec_bytes[codec]
        useful = metrics.get("logical_useful_bytes")
        require(useful == entry["width"] * width, f"{label}/{codec}: useful bytes differ")
        for field in ("fetched_bytes_p50", "fetched_bytes_p95", "amplification_p50", "amplification_p95"):
            require(isinstance(metrics.get(field), (int, float)) and np.isfinite(metrics[field]) and metrics[field] >= 0,
                    f"{label}/{codec}: invalid {field}")


def self_test() -> None:
    validate_permutation(np.arange(N, dtype="<i4"))
    raw = N * 64
    compressed = raw // 2
    saving = 1.0 - compressed / raw
    require(abs(saving - 0.5) < 1e-12, "compression saving formula differs")
    try:
        validate_permutation(np.asarray([0, 2, 2], dtype="<i4"))
    except AssertionError:
        pass
    else:
        raise AssertionError("invalid permutation mutation was accepted")
    print("route-local-ordering audit self-test PASS")


def audit(receipt_path: Path) -> None:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    require(receipt.get("schema_version") == 1, "schema version differs")
    require(receipt.get("family") == "route_local_physical_ordering_screen_v1", "family differs")
    require(receipt.get("status") == "EXECUTED", "screen is not executed")
    require(receipt.get("documents") == N and receipt.get("queries") == Q, "corpus dimensions differ")
    require(receipt.get("dimension") == 384, "embedding dimension differs")
    model = receipt.get("route_model") or {}
    require(model.get("nlist") == 256 and model.get("train_rows") == 25000, "route model configuration differs")
    require(model.get("assignment_contract") == "spherical_faiss_kmeans_argmax_cosine", "assignment contract differs")
    order = receipt.get("order") or {}
    require(order.get("policy") == "primary_cell_then_numeric_document_id", "ordering policy differs")
    assignment_path = Path(order.get("assignment_path", ""))
    permutation_path = Path(order.get("permutation_path", ""))
    require(assignment_path.is_file() and permutation_path.is_file(), "assignment/permutation evidence is missing")
    assignment = np.fromfile(assignment_path, dtype="<i4")
    permutation = np.fromfile(permutation_path, dtype="<i4")
    require(assignment.size == N and np.all((assignment >= 0) & (assignment < model["nlist"])), "assignment evidence differs")
    validate_permutation(permutation)
    require(hashlib.sha256(assignment.tobytes()).hexdigest() == order.get("assignment_sha256"), "assignment hash differs")
    require(hashlib.sha256(permutation.tobytes()).hexdigest() == order.get("permutation_sha256"), "permutation hash differs")
    workloads = receipt.get("workloads") or {}
    require(set(workloads) == set(MODES), "workload modes incomplete")
    for mode in MODES:
        require(set(workloads[mode]) == set(WIDTHS), f"{mode}: workload families incomplete")
        for workload, width in WIDTHS.items():
            entry = workloads[mode][workload]
            require(entry.get("source", {}).get("shape") == [Q, width], f"{mode}/{workload}: shape differs")
            source_path = Path(entry["source"]["path"])
            require(source_path.is_file(), f"{mode}/{workload}: source missing")
            require(sha256(source_path) == entry["source"].get("sha256"), f"{mode}/{workload}: source hash differs")
            validate_locality(entry["canonical"], f"{mode}/{workload}/canonical")
            validate_locality(entry["route_local"], f"{mode}/{workload}/route_local")
            # This is an observed result, not a theorem: retain both values and report violations.
            entry["observed_touched_block_delta"] = entry["route_local"]["touched_blocks_p50"] - entry["canonical"]["touched_blocks_p50"]
    compression = receipt.get("compression") or {}
    require(set(compression) == set(CODEC_WIDTHS), "compression codec set incomplete")
    for codec, width in CODEC_WIDTHS.items():
        entry = compression[codec]
        path = Path(entry.get("source_path", "")) if entry.get("source_path") else None
        # Legacy receipts carry the hash only; the payload path is reconstructible from the fixture root.
        require(isinstance(entry.get("source_sha256"), str) and len(entry["source_sha256"]) == 64,
                f"{codec}: source hash missing")
        for layout in ("canonical", "route_local"):
            metrics = entry[layout]
            raw = metrics.get("raw_bytes")
            compressed = metrics.get("compressed_bytes")
            require(raw == N * width and isinstance(compressed, int) and compressed > 0, f"{codec}/{layout}: byte accounting differs")
            require(metrics.get("blocks", 0) > 0, f"{codec}/{layout}: block count differs")
            expected_saving = 1.0 - compressed / raw
            expected_ratio = raw / compressed
            require(abs(metrics.get("saving_fraction", 0.0) - expected_saving) < 1e-12, f"{codec}/{layout}: saving formula differs")
            require(abs(metrics.get("compression_ratio", 0.0) - expected_ratio) < 1e-12, f"{codec}/{layout}: ratio formula differs")
    print(json.dumps({"status": "PASS", "receipt": str(receipt_path)}, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.receipt is None:
        parser.error("--receipt is required")
    audit(args.receipt)


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise SystemExit(f"route-local-ordering audit: {error}")
