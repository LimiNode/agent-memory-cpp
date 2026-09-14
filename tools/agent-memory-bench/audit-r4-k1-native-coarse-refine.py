#!/usr/bin/env python3
"""Fail-closed audit for the native mean-coarse timing control."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np


A_VALUES = (128, 256, 512, 1_024, 2_048, 4_096, 8_192, 16_384)
SEEDS = (2026082701, 2026082702, 2026082703)
QUERIES = 152


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def aggregate(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {"min": float(array.min()), "mean": float(array.mean()),
            "p05": float(np.percentile(array, 5)),
            "p50": float(np.percentile(array, 50)),
            "p95": float(np.percentile(array, 95)),
            "max": float(array.max())}


def assert_aggregate(actual: dict[str, float], values: list[float], label: str) -> None:
    expected = aggregate(values)
    for name, value in expected.items():
        require(math.isclose(float(actual[name]), value, rel_tol=0.0, abs_tol=1e-9),
                f"summary aggregate mismatch: {label}/{name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--native-executable", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    require(receipt["family"] == "semantic_r4_k1_coarse_k16_native_control_v1" and
            receipt["execution_status"] == "EXECUTED" and
            receipt["production_activation"] is False and
            receipt["scientific_control"] is True, "native receipt differs")
    require(raw["family"] == "semantic_r4_k1_coarse_k16_native_samples_v1" and
            raw["schema_version"] == 1, "native raw schema differs")
    require(receipt["raw_output"]["sha256"] == sha256(args.raw),
            "native raw SHA differs")
    require(receipt["native_executable"]["bytes"] == args.native_executable.stat().st_size and
            receipt["native_executable"]["sha256"] == sha256(args.native_executable),
            "native executable binding differs")
    require(receipt["seeds"] == list(SEEDS) and receipt["a_values"] == list(A_VALUES),
            "native grid differs")
    samples = raw["samples"]
    passes = int(receipt["measured_passes"])
    expected = len(SEEDS) * QUERIES * len(A_VALUES) * passes
    require(len(samples) == expected, "native sample count differs")
    identities: set[tuple[int, int, int, int]] = set()
    for row in samples:
        identity = (int(row["seed"]), int(row["query"]),
                    int(row["addresses_refined"]), int(row["pass"]))
        require(identity not in identities and identity[0] in SEEDS and
                0 <= identity[1] < QUERIES and identity[2] in A_VALUES and
                0 <= identity[3] < passes, f"native sample identity differs: {identity}")
        identities.add(identity)
        for field in ("coarse_ms", "refine_ms"):
            require(float(row[field]) >= 0.0 and np.isfinite(float(row[field])),
                    f"native timing invalid: {identity}/{field}")
        require(int(row["representatives_scored"]) >= identity[2],
                f"native representative accounting differs: {identity}")
    require(len(identities) == expected, "native sample matrix incomplete")
    require(len(receipt["native_outputs"]) == len(SEEDS), "native output matrix differs")
    for output in receipt["native_outputs"]:
        path = Path(output["path"])
        require(path.is_file() and int(output["bytes"]) == path.stat().st_size and
                output["sha256"] == sha256(path), f"native output binding differs: {path}")
    summaries = receipt["summaries"]
    require(len(summaries) == len(A_VALUES), "native summary count differs")
    seen: set[int] = set()
    for summary in summaries:
        a = int(summary["addresses_refined"])
        require(a not in seen and a in A_VALUES, f"native summary identity differs: {a}")
        seen.add(a)
        selected = [row for row in samples if int(row["addresses_refined"]) == a]
        require(int(summary["query_count"]) == len(selected),
                f"native summary count differs: {a}")
        assert_aggregate(summary["coarse_ms"], [float(row["coarse_ms"]) for row in selected],
                         f"coarse/{a}")
        assert_aggregate(summary["refine_ms"], [float(row["refine_ms"]) for row in selected],
                         f"refine/{a}")
        assert_aggregate(summary["representatives_scored"],
                         [float(row["representatives_scored"]) for row in selected],
                         f"representatives/{a}")
    require(len(seen) == len(A_VALUES), "native summary matrix incomplete")
    print(json.dumps({"family": "semantic_r4_k1_coarse_k16_native_audit_v1",
                      "status": "PASS", "samples": len(samples),
                      "summaries": len(summaries)}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"audit-r4-k1-native-coarse-refine: {error}")
