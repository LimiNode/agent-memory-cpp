#!/usr/bin/env python3
"""Fail-closed audit for the post-freeze MDBX prototype receipt."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nearest(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return float(ordered[index])


def audit(value: dict, raw_paths: dict[str, Path] | None = None) -> None:
    require(value.get("schema_version") == 1, "unsupported schema")
    require(value.get("family") == "native_mdbx_packed_int8_prototype_v1", "unexpected family")
    require(value.get("status") == "EXECUTED", "prototype did not execute")
    require(value.get("metric") == "reconstructed_cosine_exact", "metric is not exact cosine")
    require(value.get("documents") == 1_000_000 and value.get("queries") == 305, "fixture shape differs")
    require(value.get("warmups") == 1 and value.get("repeats") == 5, "timing contract differs")
    require(value.get("candidate_k") == 128 and value.get("candidate_source", "").startswith("exact FP32"), "candidate isolation contract differs")
    for digest_name in ("fixture_receipt_sha256", "runner_source_sha256"):
        digest = value.get(digest_name)
        require(isinstance(digest, str) and len(digest) == 64, f"missing {digest_name}")
    rows = value.get("rows")
    require(isinstance(rows, list) and {row.get("layout") for row in rows} == {"row_kv", "segment_blob"}, "layout set differs")
    for row in rows:
        require(row.get("ordered_parity") == "305/305", f"{row.get('layout')} parity incomplete")
        require(row.get("db_bytes", 0) > 0 and row.get("materialize_ms", -1) >= 0 and row.get("durable_commits", 0) > 0, "materialization receipt is invalid")
        for name in ("reopen_first_query_ms", "p50_ms", "p95_ms", "p99_ms", "read_decode_p50_ms", "score_p50_ms"):
            require(isinstance(row.get(name), (int, float)) and row[name] >= 0, f"{row.get('layout')}.{name} is invalid")
        if raw_paths is not None:
            raw_path = raw_paths[row["layout"]]
            require(row.get("raw_benchmark_sha256") == sha256(raw_path),
                    f"{row['layout']} raw SHA differs")
            raw = json.loads(raw_path.read_text(encoding="utf-8-sig"))
            expected_modes = {"row_kv": {"row", "row_kv"}, "segment_blob": {"segment", "segment_blob"}}
            require(raw.get("mode") in expected_modes[row["layout"]], f"{row['layout']} raw mode differs")
            require(raw.get("queries") == 305 and raw.get("warmups") == 1 and raw.get("repeats") == 5,
                    f"{row['layout']} raw timing contract differs")
            samples = raw.get("samples_ms")
            require(isinstance(samples, list) and len(samples) == 305 * 5 and all(float(x) >= 0 for x in samples),
                    f"{row['layout']} raw sample coverage differs")
            require(abs(float(raw["p50_ms"]) - nearest([float(x) for x in samples], .50)) < 1e-9,
                    f"{row['layout']} raw p50 is not nearest-rank")
            require(abs(float(raw["p95_ms"]) - nearest([float(x) for x in samples], .95)) < 1e-9,
                    f"{row['layout']} raw p95 is not nearest-rank")
            require(abs(float(raw["p99_ms"]) - nearest([float(x) for x in samples], .99)) < 1e-9,
                    f"{row['layout']} raw p99 is not nearest-rank")
            for name in ("p50_ms", "p95_ms", "p99_ms"):
                require(abs(float(row[name]) - float(raw[name])) < 1e-9,
                        f"{row['layout']}.{name} differs from raw")
            require(raw.get("parity") == 305 and raw.get("parity_total") == 305,
                    f"{row['layout']} raw parity differs")
    limitations = value.get("limitations")
    require(isinstance(limitations, list) and any("not Prototype-IVF" in item for item in limitations), "prototype limitation is missing")


def self_test() -> None:
    baseline = {"schema_version": 1, "family": "native_mdbx_packed_int8_prototype_v1", "status": "EXECUTED", "metric": "reconstructed_cosine_exact", "documents": 1_000_000, "queries": 305, "warmups": 1, "repeats": 5, "candidate_k": 128, "candidate_source": "exact FP32 top-k", "fixture_receipt_sha256": "0" * 64, "runner_source_sha256": "1" * 64, "rows": [{"layout": name, "ordered_parity": "305/305", "db_bytes": 1, "materialize_ms": 1, "durable_commits": 1, "reopen_first_query_ms": 1, "p50_ms": 1, "p95_ms": 1, "p99_ms": 1, "read_decode_p50_ms": 1, "score_p50_ms": 1} for name in ("row_kv", "segment_blob")], "limitations": ["not Prototype-IVF"]}
    audit(baseline)
    baseline["rows"][0]["ordered_parity"] = "0/305"
    try:
        audit(baseline)
    except ValueError:
        print("audit-mdbx-prototype self-test PASS")
        return
    raise ValueError("negative parity mutation was accepted")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--raw-row", type=Path)
    parser.add_argument("--raw-segment", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.result is None:
        parser.error("--result is required")
    raw_paths = None
    if args.raw_row is not None or args.raw_segment is not None:
        if args.raw_row is None or args.raw_segment is None:
            parser.error("--raw-row and --raw-segment must be supplied together")
        raw_paths = {"row_kv": args.raw_row, "segment_blob": args.raw_segment}
    audit(json.loads(args.result.read_text(encoding="utf-8")), raw_paths)
    print(f"audit-mdbx-prototype: PASS ({args.result})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
