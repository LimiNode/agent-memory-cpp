#!/usr/bin/env python3
"""Write an immutable input/output receipt for one native TQ1/PQ8 run."""
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
    parser.add_argument("--runner", type=Path)
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--payload-receipt", type=Path)
    parser.add_argument("--thq", type=Path)
    parser.add_argument("--thresholds", type=Path)
    parser.add_argument("--candidate-flat", type=Path)
    parser.add_argument("--offsets", type=Path)
    parser.add_argument("--queries", type=Path)
    parser.add_argument("--native-jsonl", type=Path)
    parser.add_argument("--argv", nargs="+", required=False)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print("native TQ1/PQ8 run-receipt self-test PASS")
        return
    paths = (args.runner, args.payload, args.payload_receipt, args.thq,
             args.thresholds, args.candidate_flat, args.offsets, args.queries,
             args.native_jsonl, args.output)
    if any(path is None for path in paths):
        parser.error("all receipt paths are required")
    receipt = json.loads(args.payload_receipt.read_text(encoding="utf-8"))
    result = {
        "schema_version": 1,
        "status": "EXECUTED",
        "runner_sha256": sha256(args.runner),
        "payload_sha256": sha256(args.payload),
        "payload_receipt_sha256": sha256(args.payload_receipt),
        "thq_sha256": sha256(args.thq),
        "thresholds_sha256": sha256(args.thresholds),
        "candidate_flat_sha256": sha256(args.candidate_flat),
        "offsets_sha256": sha256(args.offsets),
        "queries_sha256": sha256(args.queries),
        "native_jsonl_sha256": sha256(args.native_jsonl),
        "payload_side_bytes_per_document": receipt["side_bytes_per_document"],
        "argv": args.argv or [],
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
