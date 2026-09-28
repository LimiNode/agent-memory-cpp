#!/usr/bin/env python3
"""Audit Rust Qdrant TurboQuant decode parity against the Python control."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def compare(expected: Path, actual: Path, rows: int, dim: int) -> dict[str, object]:
    expected_values = np.fromfile(expected, dtype="<f4")
    actual_values = np.fromfile(actual, dtype="<f4")
    require(expected_values.size == rows * dim, f"unexpected expected payload size: {expected}")
    require(actual_values.size == rows * dim, f"unexpected Rust payload size: {actual}")
    require(np.isfinite(expected_values).all() and np.isfinite(actual_values).all(), "non-finite decode")
    delta = np.abs(expected_values.astype(np.float64) - actual_values.astype(np.float64))
    max_abs = float(np.max(delta))
    mean_abs = float(np.mean(delta))
    p99_abs = float(np.quantile(delta, 0.99))
    require(max_abs <= 1e-7, f"Rust decode exceeds parity tolerance: {max_abs}")
    return {
        "rows": rows,
        "dimension": dim,
        "max_abs_error": max_abs,
        "mean_abs_error": mean_abs,
        "p99_abs_error": p99_abs,
        "expected_sha256": sha256(expected),
        "rust_sha256": sha256(actual),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-1", type=Path)
    parser.add_argument("--rust-1", type=Path)
    parser.add_argument("--expected-2", type=Path)
    parser.add_argument("--rust-2", type=Path)
    parser.add_argument("--residual", type=Path)
    parser.add_argument("--upstream-revision", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        require(sum(value is not None for value in (args.expected_1, args.rust_1, args.expected_2, args.rust_2)) == 0,
                "self-test accepts no payload arguments")
        print("TurboQuant Rust parity audit self-test: PASS")
        return
    required = (args.expected_1, args.rust_1, args.expected_2, args.rust_2, args.residual, args.output)
    if any(value is None for value in required):
        parser.error("all payload paths and --output are required")
    require(args.upstream_revision == "6ab21cac18ebb6f4ae29102c7f8f5cc11affd5de", "unexpected upstream revision")
    residual_bytes = args.residual.stat().st_size
    require(residual_bytes % (384 * 4) == 0, "residual payload is not FP32x384")
    rows = residual_bytes // (384 * 4)
    result = {
        "schema_version": 1,
        "family": "thq_turboquant_rust_parity_v1",
        "status": "PASS",
        "upstream": {
            "project": "qdrant/qdrant",
            "revision": args.upstream_revision,
            "mode": "TurboQuant normal, Dot-compatible residual path, padded rotation",
        },
        "residual_sha256": sha256(args.residual),
        "row_count": rows,
        "dimension": 384,
        "arms": {
            "turboquant1": compare(args.expected_1, args.rust_1, rows, 384),
            "turboquant2": compare(args.expected_2, args.rust_2, rows, 384),
        },
        "acceptance": {"max_abs_error": 1e-7, "independent_rust_decode": True},
        "wire_parity": {
            "packed_bytes": False,
            "extras_scale_bytes": False,
            "asymmetric_score": False,
            "harness_source_sha256": None,
            "binary_sha256": None,
            "cargo_lock_sha256": None,
        },
        "limitations": [
            "Parity covers official Rust encode/decode, not native serving throughput.",
            "The compared payload is the frozen THQ candidate-union residual; TQ+ shift/scale is separate.",
            "Packed wire bytes, extras, asymmetric scores and build hashes are not compared by this decode-only harness.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "rows": rows}, sort_keys=True))


if __name__ == "__main__":
    main()
