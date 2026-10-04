#!/usr/bin/env python3
"""Write the compact committed summary from a full MDBX receipt."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--result", type=Path, required=True); parser.add_argument("--output", type=Path, required=True); args = parser.parse_args()
    receipt = json.loads(args.result.read_text(encoding="utf-8"))
    compact = {key: receipt[key] for key in ("schema_version", "family", "status", "documents", "queries", "segment_rows", "batch_rows", "runs", "repeats", "warmups", "environment", "fixture_manifest_path", "fixture_manifest_sha256", "payloads", "workloads", "locality", "protocol_refresh", "limitations") if key in receipt}
    compact["raw_receipt_path"] = str(args.result)
    compact["raw_receipt_sha256"] = sha256(args.result)
    compact["rows"] = []
    for row in receipt["rows"]:
        compact_row = {key: row[key] for key in ("codec", "layout", "mode", "workload", "width", "access", "logical_payload_bytes_doc", "mdbx_allocated_file_bytes", "environment_file_size_bytes", "mdbx_used_bytes", "mdbx_used_pages", "mdbx_page_size", "mdbx_allocated_tail_bytes", "physical_db_bytes", "physical_db_bytes_doc") if key in row}
        compact_row["runs"] = []
        for run in row["runs"]:
            compact_row["runs"].append({key: run[key] for key in ("run", "status", "queries", "width", "warmups", "repeats", "reopen_coldish_first_query_ms", "p50_ms", "p95_ms", "p99_ms", "median_reads", "median_logical_value_bytes_fetched", "median_useful_bytes", "checksum", "mdbx_allocated_file_bytes", "environment_file_size_bytes", "mdbx_used_pages", "mdbx_used_bytes", "mdbx_data_pages", "mdbx_data_bytes", "mdbx_allocated_tail_bytes") if key in run})
        compact["rows"].append(compact_row)
    args.output.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "WRITTEN", "sha256": sha256(args.output), "rows": len(compact["rows"])}, sort_keys=True))


if __name__ == "__main__":
    main()
