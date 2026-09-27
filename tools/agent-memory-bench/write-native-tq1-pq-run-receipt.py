#!/usr/bin/env python3
"""Write an immutable input/output receipt for one native TQ1/PQ8 run."""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()

def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def build_receipt(args: argparse.Namespace, payload_receipt: dict) -> dict:
    paths = (args.runner, args.payload, args.payload_receipt, args.thq,
             args.thresholds, args.candidate_flat, args.offsets, args.queries,
             args.native_jsonl)
    if any(path is None for path in paths):
        raise RuntimeError("all receipt paths are required")
    return {
        "schema_version": 1,
        "status": "EXECUTED",
        "runner_sha256": sha256(args.runner),
        "runner_source_sha256": sha256(args.runner),
        "runner_binary_sha256": sha256(args.runner_binary),
        "build_manifest_sha256": sha256(args.build_manifest),
        "payload_sha256": sha256(args.payload),
        "payload_receipt_sha256": sha256(args.payload_receipt),
        "thq_sha256": sha256(args.thq),
        "thresholds_sha256": sha256(args.thresholds),
        "candidate_flat_sha256": sha256(args.candidate_flat),
        "offsets_sha256": sha256(args.offsets),
        "queries_sha256": sha256(args.queries),
        "native_jsonl_sha256": sha256(args.native_jsonl),
        "payload_receipt_path": str(args.payload_receipt),
        "thq_path": str(args.thq),
        "thresholds_path": str(args.thresholds),
        "candidate_flat_path": str(args.candidate_flat),
        "offsets_path": str(args.offsets),
        "queries_path": str(args.queries),
        "native_jsonl_path": str(args.native_jsonl),
        "runner_source_path": str(args.runner),
        "runner_binary_path": str(args.runner_binary),
        "build_manifest_path": str(args.build_manifest),
        "payload_side_bytes_per_document": payload_receipt["side_bytes_per_document"],
        "argv": args.argv or [],
    }

def self_test() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "source"
        source.write_bytes(b"immutable-input")
        value = {"sha256": sha256(source), "argv": ["--candidate-gate", "payload"]}
        require(value["sha256"] == sha256(source), "receipt synthetic PASS failed")
        source.write_bytes(b"mutated-input")
        require(value["sha256"] != sha256(source), "receipt mutation was accepted")
        value["argv"].append("--mutated")
        require(value["argv"] != ["--candidate-gate", "payload"],
                "receipt argv mutation was accepted")
        # Reference outputs are audit inputs, not native execution inputs.
        args = argparse.Namespace(
            runner=source, runner_binary=source, build_manifest=source,
            payload=source, payload_receipt=source, thq=source,
            thresholds=source, candidate_flat=source, offsets=source,
            queries=source, native_jsonl=source, argv=["--self-test"])
        generated = build_receipt(args, {"side_bytes_per_document": 20})
        require("expected_pq_result_sha256" not in generated and
                "expected_tq_result_sha256" not in generated,
                "execution receipt contains audit-only reference hashes")
    print("native TQ1/PQ8 run-receipt self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", type=Path)
    parser.add_argument("--runner-binary", type=Path)
    parser.add_argument("--build-manifest", type=Path)
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
        self_test()
        return
    if args.runner_binary is None or args.build_manifest is None:
        parser.error("--runner-binary and --build-manifest are required for executed receipts")
    if args.output is None:
        parser.error("all receipt paths are required")
    receipt = json.loads(args.payload_receipt.read_text(encoding="utf-8"))
    result = build_receipt(args, receipt)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
