#!/usr/bin/env python3
"""Fail-closed audit for the offline MDBX Z0 compression screen."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np

CODECS = ("fp32", "int8", "lsq32", "tq1", "tq1-pq8")
WIDTHS = {"fp32": 1536, "int8": 392, "lsq32": 36, "tq1": 52, "tq1-pq8": 64}
BLOCKS = (4096, 16384, 65536)
LEVELS = (1, 3)
SUMMARY_PATH = Path(__file__).parents[2] / "guides" / "experiments" / "2026-10-04-mdbx-z0-compression-screen.summary.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def require_close(actual: float, expected: float, message: str) -> None:
    require(math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9), message)


def validate_row_derived(row: dict, threshold: float) -> None:
    raw_bytes = row["raw_bytes"]
    compressed_bytes = row["compressed_bytes"]
    expected_saving = 1.0 - compressed_bytes / raw_bytes
    expected_ratio = raw_bytes / compressed_bytes
    require_close(float(row["saving_fraction"]), expected_saving, "saving fraction differs")
    require_close(float(row["compression_ratio"]), expected_ratio, "compression ratio differs")
    require(bool(row["carry_forward_if_saving_at_least_5_percent"]) == (expected_saving >= threshold), "carry-forward decision differs")


def best_rows(rows: list[dict]) -> dict[str, dict]:
    result = {}
    for codec in CODECS:
        candidates = [row for row in rows if row["codec"] == codec]
        require(candidates, f"missing rows for {codec}")
        # Receipt order is the deterministic tie-breaker after saving_fraction.
        result[codec] = max(candidates, key=lambda row: float(row["saving_fraction"]))
    return result


def validate_summary(summary: dict, rows: list[dict], raw_result: Path | None, fixture_sha: str) -> None:
    require(summary.get("schema_version") == 1 and summary.get("family") == "mdbx_z0_compression_screen_summary_v1", "summary family differs")
    require(summary.get("status") == "EXECUTED", "summary status differs")
    if raw_result is not None and raw_result.is_file():
        require(summary.get("raw_receipt_sha256") == sha256(raw_result), "summary raw receipt binding differs")
    require(summary.get("fixture_manifest_sha256") == fixture_sha, "summary fixture binding differs")
    contract = summary.get("contract", {})
    require(tuple(contract.get("representations", ())) == CODECS, "summary representation contract differs")
    require(tuple(contract.get("target_block_bytes", ())) == BLOCKS and tuple(contract.get("zstd_levels", ())) == LEVELS, "summary compression contract differs")
    threshold = float(contract.get("carry_forward_saving_threshold", 0.05))
    require_close(threshold, 0.05, "summary carry-forward threshold differs")
    observed = summary.get("best_observed_saving", {})
    require(set(observed) == set(CODECS), "summary best-arm coverage differs")
    for codec, row in best_rows(rows).items():
        expected = observed[codec]
        require(expected.get("transform") == row["transform"], f"summary best transform differs: {codec}")
        require(expected.get("target_block_bytes") == row["target_block_bytes"], f"summary best block differs: {codec}")
        require(expected.get("zstd_level") == row["zstd_level"], f"summary best level differs: {codec}")
        require(math.isclose(float(expected.get("saving_percent")), float(row["saving_fraction"]) * 100.0, rel_tol=1e-6, abs_tol=1e-4), f"summary best saving differs: {codec}")
        require(bool(expected.get("carry_forward")) == (float(row["saving_fraction"]) >= threshold), f"summary best decision differs: {codec}")


def audit(value: dict, summary_path: Path | None = None, raw_result: Path | None = None) -> None:
    require(value.get("schema_version") == 1 and value.get("family") == "mdbx_z0_compression_screen_v1", "receipt family differs")
    require(value.get("status") == "EXECUTED" and value.get("stage") == "Z0_offline_compressibility_screen", "receipt status differs")
    require(value.get("documents") == 1_000_000 and tuple(value.get("block_target_bytes", ())) == BLOCKS and tuple(value.get("zstd_levels", ())) == LEVELS, "screen contract differs")
    manifest_path = Path(value.get("fixture_manifest_path", ""))
    require(manifest_path.is_file() and sha256(manifest_path) == value.get("fixture_manifest_sha256"), "fixture binding differs")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("family") == "mdbx_finalist_source_fixture_v1", "fixture family differs")
    source_hashes = value.get("source_sha256", {})
    require(set(source_hashes) == set(CODECS), "source coverage differs")
    canonical_source_hashes = value.get("canonical_source_sha256", {})
    require(set(canonical_source_hashes) == {"int8", "lsq32", "tq1", "tq1-pq8"}, "canonical source coverage differs")
    source_paths = value.get("source_paths", {})
    require(set(source_paths) == set(CODECS), "source paths differ")
    for codec in CODECS:
        path = Path(source_paths[codec])
        require(path.is_file() and sha256(path) == source_hashes[codec], f"source hash differs: {codec}")
        if codec != "fp32":
            fixture_payload = manifest["payloads"][codec]
            require(Path(fixture_payload["path"]).is_file() and fixture_payload["payload_sha256"] == sha256(Path(fixture_payload["path"])), f"fixture payload differs: {codec}")
            require(canonical_source_hashes[codec] == fixture_payload.get("source_sha256", {}), f"canonical source binding differs: {codec}")
    rows = value.get("rows", [])
    require(len(rows) == 36, f"row count differs: {len(rows)}")
    seen = set()
    for row in rows:
        key = (row.get("codec"), row.get("transform"), row.get("target_block_bytes"), row.get("zstd_level"))
        require(row["codec"] in CODECS and row["target_block_bytes"] in BLOCKS and row["zstd_level"] in LEVELS, f"row contract differs: {key}")
        require(row["transform"] in (("plain", "byte_shuffle") if row["codec"] == "fp32" else ("plain",)), f"transform differs: {key}")
        require(key not in seen, f"duplicate row: {key}")
        seen.add(key)
        require(row["raw_bytes"] == 1_000_000 * WIDTHS[row["codec"]] and row["compressed_bytes"] > 0, f"size differs: {key}")
        require(row["blocks"] >= 1 and row["actual_block_bytes"] == row["rows_per_block"] * WIDTHS[row["codec"]], f"block geometry differs: {key}")
        require(row["compression_ratio"] > 0 and row["decompression_throughput_gib_s"] > 0, f"throughput differs: {key}")
        validate_row_derived(row, float(value.get("saving_filter_fraction", 0.05)))
    require(len(seen) == 36, "compression matrix incomplete")
    summary_file = summary_path or SUMMARY_PATH
    require(summary_file.is_file(), f"summary is missing: {summary_file}")
    validate_summary(json.loads(summary_file.read_text(encoding="utf-8")), rows, raw_result, value["fixture_manifest_sha256"])


def self_test() -> None:
    baseline = {"schema_version": 1, "family": "mdbx_z0_compression_screen_v1", "status": "EXECUTED", "stage": "Z0_offline_compressibility_screen", "documents": 1_000_000, "block_target_bytes": list(BLOCKS), "zstd_levels": list(LEVELS), "fixture_manifest_path": "missing", "fixture_manifest_sha256": "0" * 64, "source_sha256": {}, "rows": []}
    require(baseline["documents"] == 1_000_000, "self-test setup differs")
    row = {"codec": "int8", "transform": "plain", "target_block_bytes": 65536, "zstd_level": 3,
           "raw_bytes": 392000000, "compressed_bytes": 376000000, "saving_fraction": 1.0 - 376000000 / 392000000,
           "compression_ratio": 392000000 / 376000000, "carry_forward_if_saving_at_least_5_percent": False}
    validate_row_derived(row, 0.05)
    summary = {"schema_version": 1, "family": "mdbx_z0_compression_screen_summary_v1", "status": "EXECUTED",
               "fixture_manifest_sha256": "0" * 64, "contract": {"representations": list(CODECS), "target_block_bytes": list(BLOCKS), "zstd_levels": list(LEVELS), "carry_forward_saving_threshold": 0.05},
               "best_observed_saving": {codec: {"transform": "plain", "target_block_bytes": 65536, "zstd_level": 3, "saving_percent": 4.0816326531, "carry_forward": False} for codec in CODECS}}
    validate_summary(summary, [dict(row, codec=codec) for codec in CODECS], None, "0" * 64)
    for mutation in (lambda x: x.update(status="PARTIAL"), lambda x: x.update(block_target_bytes=[4096]), lambda x: x.update(rows=[{}])):
        candidate = json.loads(json.dumps(baseline)); mutation(candidate)
        try:
            require(candidate["status"] == "EXECUTED", "status mutation accepted")
            require(tuple(candidate["block_target_bytes"]) == BLOCKS, "block mutation accepted")
            require(len(candidate["rows"]) == 36, "row mutation accepted")
        except ValueError:
            continue
        raise ValueError("compression audit mutation accepted")
    for label, mutate in (("saving", lambda x: x.update(saving_fraction=0.5)),
                          ("ratio", lambda x: x.update(compression_ratio=1.0)),
                          ("carry", lambda x: x.update(carry_forward_if_saving_at_least_5_percent=True))):
        candidate = dict(row); mutate(candidate)
        try: validate_row_derived(candidate, 0.05)
        except ValueError: continue
        raise ValueError(f"derived mutation accepted: {label}")
    for label, mutate in (("best arm", lambda x: x["best_observed_saving"]["int8"].update(transform="byte_shuffle")),
                          ("best value", lambda x: x["best_observed_saving"]["int8"].update(saving_percent=50.0))):
        candidate = json.loads(json.dumps(summary)); mutate(candidate)
        try: validate_summary(candidate, [dict(row, codec=codec) for codec in CODECS], None, "0" * 64)
        except ValueError: continue
        raise ValueError(f"summary mutation accepted: {label}")
    print("audit-mdbx-compression-screen self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--result", type=Path); parser.add_argument("--summary", type=Path); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    if args.self_test:
        self_test(); return
    if args.result is None: raise ValueError("--result is required")
    audit(json.loads(args.result.read_text(encoding="utf-8")), args.summary, args.result); print(f"audit-mdbx-compression-screen: PASS ({args.result})")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise SystemExit(f"audit-mdbx-compression-screen: {error}")
