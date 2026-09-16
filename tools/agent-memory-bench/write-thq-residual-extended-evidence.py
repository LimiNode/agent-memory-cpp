#!/usr/bin/env python3
"""Write a compact, fail-closed receipt for the extended residual screen."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--query-ids", type=Path)
    parser.add_argument("--document-ids", type=Path)
    parser.add_argument("--qrels", type=Path)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    if result.get("family") != "thq_residual_extended_frontier_stage_local_v1":
        raise SystemExit("unexpected extended result family")
    if result.get("status") != "EXECUTED":
        raise SystemExit("extended result is not EXECUTED")
    rows = result.get("rows", [])
    if not rows or any(int(row.get("bytes", 0)) < 96 for row in rows):
        raise SystemExit("extended rows do not contain full payload accounting")
    compact = {
        "schema_version": 1,
        "family": "thq_residual_extended_frontier_compact_v1",
        "status": "EXECUTED",
        "evidence_status": result.get("evidence_status"),
        "documents": result.get("documents"),
        "training_count": result.get("training_count"),
        "query_count": result.get("query_count"),
        "prefilter": result.get("prefilter"),
        "model_hashes": result.get("model_hashes"),
        "summaries": result.get("summaries"),
        "limitations": result.get("limitations"),
    }
    args.output.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt = {
        "schema_version": 1,
        "family": "thq_residual_extended_frontier_receipt_v1",
        "status": "EXECUTED",
        "runner_sha256": sha256(Path(__file__).with_name("run-thq-residual-extended-frontier.py")),
        "result_sha256": sha256(args.result),
        "compact_result_sha256": sha256(args.output),
        "result_inputs": {key: result.get(key) for key in (
            "documents_sha256", "training_sha256", "queries_sha256", "thq_sha256",
            "thresholds_sha256", "signs_sha256")},
        "model_hashes": result.get("model_hashes"),
        "scope": {
            "query_count": result.get("query_count"),
            "canonical_152_query_payload": False,
            "native_latency": False,
            "page_or_storage_measurement": False,
        },
    }
    for label, path in (("query_ids_sha256", args.query_ids),
                        ("document_ids_sha256", args.document_ids),
                        ("qrels_sha256", args.qrels)):
        if path is not None:
            receipt["result_inputs"][label] = sha256(path)
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
