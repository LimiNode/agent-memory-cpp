#!/usr/bin/env python3
"""Fail-closed audit for the experimental MDBX finalist batch."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np


CODECS = ("tq1-pq8", "tq1", "lsq32", "int8")
LAYOUTS = ("row_kv", "segmented")
MODES = ("prototype_ivf", "modern_r4")
WORKLOADS = ("top128", "route5000")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def nearest(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(math.ceil(fraction * len(ordered))) - 1))]


def audit(value: dict) -> None:
    require(value.get("schema_version") == 1 and value.get("family") == "mdbx_finalist_storage_bakeoff_v1", "unexpected receipt family")
    require(value.get("status") == "EXECUTED", "receipt is not executed")
    require(value.get("documents") == 1_000_000 and value.get("queries") == 305, "matrix shape differs")
    require(value.get("segment_rows") == 4096 and value.get("batch_rows") == 65536, "layout parameters differ")
    require(value.get("runs", 0) >= 3 and value.get("repeats", 0) >= 5 and value.get("warmups") == 1, "repeat contract differs")
    manifest = value.get("fixture_manifest_sha256")
    manifest_path = Path(value.get("fixture_manifest_path", ""))
    require(isinstance(manifest, str) and len(manifest) == 64 and manifest_path.is_file(), "fixture provenance is missing")
    require(sha256(manifest_path) == manifest, "fixture manifest hash differs")
    fixture = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(fixture.get("family") == "mdbx_finalist_source_fixture_v1" and fixture.get("documents") == value["documents"] and fixture.get("queries") == value["queries"], "fixture manifest identity differs")
    payloads = value.get("payloads", {})
    require(set(payloads) == set(CODECS), "codec coverage differs")
    for codec in CODECS:
        payload = payloads[codec]
        require(Path(payload["path"]).is_file(), f"payload missing: {codec}")
        require(sha256(Path(payload["path"])) == payload["payload_sha256"], f"payload hash differs: {codec}")
        require(fixture.get("payloads", {}).get(codec) == payload, f"payload fixture binding differs: {codec}")
        for source_path, source_hash in payload.get("source_sha256", {}).items():
            source = Path(source_path)
            require(source.is_file() and sha256(source) == source_hash, f"canonical source hash differs: {codec}/{source_path}")
        require(payload["payload_bytes_doc"] in (36, 52, 64, 392), f"payload width differs: {codec}")
        require(payload["row_key_bytes"] == 4, f"row key width differs: {codec}")
    workloads = value.get("workloads", {})
    require(set(workloads) == set(MODES), "route mode coverage differs")
    for mode in MODES:
        for name, expected_width in (("route", 5000), ("top128", 128)):
            path = Path(workloads[mode][name])
            require(path.is_file() and sha256(path) == workloads[mode][name + "_sha256"], f"workload hash differs: {mode}/{name}")
            require(fixture.get("workloads", {}).get(mode, {}).get(name + "_sha256") == workloads[mode][name + "_sha256"], f"workload fixture binding differs: {mode}/{name}")
            require(path.stat().st_size == 305 * expected_width * 4, f"workload shape differs: {mode}/{name}")
    locality = value.get("locality", {})
    require(set(locality) == set(MODES), "locality coverage differs")
    for mode in MODES:
        for workload, width in (("route5000", 5000), ("top128", 128)):
            item = locality[mode][workload]
            require(item["queries"] == 305 and item["width"] == width, f"locality shape differs: {mode}/{workload}")
            require(0 <= item["candidate_reuse_fraction_mean"] < 1, f"locality reuse differs: {mode}/{workload}")
    expected_keys = {(codec, layout, mode, workload, access) for codec in CODECS for layout in LAYOUTS for mode in MODES for workload in WORKLOADS for access in (("query_transaction", "point_lookup") if layout == "row_kv" and workload == "top128" else ("query_transaction",))}
    rows = value.get("rows", [])
    require(len(rows) == len(expected_keys), f"benchmark row count differs: {len(rows)} != {len(expected_keys)}")
    seen = set()
    for row in rows:
        key = tuple(row.get(name) for name in ("codec", "layout", "mode", "workload", "access"))
        require(key in expected_keys and key not in seen, f"unexpected or duplicate benchmark row: {key}")
        seen.add(key)
        require(row["physical_db_bytes"] >= row["logical_payload_bytes_doc"] * 1_000_000, f"physical size is below payload: {key}")
        require(row["mdbx_allocated_file_bytes"] >= row["mdbx_used_bytes"] >= row["mdbx_data_bytes"] >= 0, f"MDBX space statistics differ: {key}")
        require(row["mdbx_allocated_tail_bytes"] == max(0, row["mdbx_allocated_file_bytes"] - row["mdbx_used_bytes"]), f"MDBX allocated-tail accounting differs: {key}")
        require(row["mdbx_page_size"] > 0 and row["environment_file_size_bytes"] == row["physical_db_bytes"], f"MDBX file metric differs: {key}")
        runs = row.get("runs", [])
        require(len(runs) == value["runs"], f"run count differs: {key}")
        checksums = set()
        for run in runs:
            require(run.get("status") == "EXECUTED" and run.get("queries") == 305 and run.get("width") in (128, 5000), f"run shape differs: {key}")
            require(float(run.get("reopen_coldish_first_query_ms", -1)) >= 0, f"coldish timing missing: {key}")
            samples = run.get("samples_ms", []); reads = run.get("samples_reads", []); fetched = run.get("samples_logical_value_bytes_fetched", []); useful = run.get("samples_useful_bytes", [])
            expected_samples = 305 * value["repeats"]
            require(len(samples) == expected_samples and len(reads) == expected_samples and len(fetched) == expected_samples and len(useful) == expected_samples, f"raw sample count differs: {key}")
            require(all(float(sample) >= 0 for sample in samples), f"negative timing sample: {key}")
            for field, fraction in (("p50_ms", .5), ("p95_ms", .95), ("p99_ms", .99)):
                require(abs(float(run[field]) - nearest([float(sample) for sample in samples], fraction)) <= 1e-6, f"percentile mismatch: {key}/{field}")
            require(all(float(item) >= 0 for item in reads + fetched + useful), f"negative I/O sample: {key}")
            require(run["checksum"] != 0, f"missing content checksum: {key}")
            require(run["mdbx_allocated_tail_bytes"] == max(0, run["mdbx_allocated_file_bytes"] - run["mdbx_used_bytes"]), f"MDBX run allocated-tail accounting differs: {key}")
            checksums.add(run["checksum"])
            require(run["median_useful_bytes"] == nearest([float(item) for item in useful], .5), f"useful-byte aggregate mismatch: {key}")
            require(run["median_logical_value_bytes_fetched"] == nearest([float(item) for item in fetched], .5), f"logical returned-byte aggregate mismatch: {key}")
            if row["layout"] == "row_kv":
                require(all(int(item) == row["width"] for item in reads), f"row read count differs: {key}")
            else:
                require(all(1 <= int(item) <= row["width"] for item in reads), f"segment read count differs: {key}")
        require(len(checksums) == 1, f"content checksum changed between runs: {key}")
    require(seen == expected_keys, "benchmark matrix is incomplete")


def self_test() -> None:
    baseline = {"schema_version": 1, "family": "mdbx_finalist_storage_bakeoff_v1", "status": "EXECUTED", "documents": 1_000_000, "queries": 305, "segment_rows": 4096, "batch_rows": 65536, "runs": 3, "repeats": 5, "warmups": 1, "fixture_manifest_sha256": "0" * 64, "payloads": {}, "workloads": {}, "locality": {}, "rows": []}
    if baseline["runs"] != 3 or baseline["repeats"] != 5: raise ValueError("self-test setup differs")
    for label, mutate in (("missing evidence", lambda x: x.pop("fixture_manifest_sha256")), ("wrong segment", lambda x: x.update(segment_rows=1024)), ("wrong repeat", lambda x: x.update(runs=1))):
        candidate = json.loads(json.dumps(baseline)); mutate(candidate)
        try: require(isinstance(candidate.get("fixture_manifest_sha256"), str) and len(candidate["fixture_manifest_sha256"]) == 64, "fixture provenance is missing"); require(candidate["segment_rows"] == 4096, "layout parameters differ"); require(candidate["runs"] >= 3, "repeat contract differs")
        except ValueError: continue
        raise ValueError(f"mutation accepted: {label}")
    print("audit-mdbx-finalist-batch self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--result", type=Path); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    if args.self_test: self_test(); return
    if args.result is None: raise ValueError("--result is required")
    audit(json.loads(args.result.read_text(encoding="utf-8"))); print(f"audit-mdbx-finalist-batch: PASS ({args.result})")


if __name__ == "__main__":
    try: main()
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error: raise SystemExit(f"audit-mdbx-finalist-batch: {error}")
