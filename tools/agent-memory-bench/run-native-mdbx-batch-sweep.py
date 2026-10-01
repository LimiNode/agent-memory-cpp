#!/usr/bin/env python3
"""Measure bounded MDBX materialization batches on a fixed input fixture."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def self_test() -> None:
    batches = (1_024, 8_192, 65_536, 131_072, 262_144, 524_288, 1_000_000)
    require(batches == tuple(sorted(set(batches))), "batch sweep is not strict")
    require(all(batch > 0 for batch in batches), "batch sweep contains zero")
    require(batches[-1] == 1_000_000, "batch sweep must include full-corpus control")
    print("run-native-mdbx-batch-sweep self-test PASS")


def run_json(command: list[str]) -> dict:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    require(completed.returncode == 0, f"MDBX command failed: {completed.stderr.strip()}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


def main() -> int:
    import sys
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--thq", type=Path, required=True)
    parser.add_argument("--codes", type=Path, required=True)
    parser.add_argument("--scales", type=Path, required=True)
    parser.add_argument("--documents", type=int, default=1_000_000)
    parser.add_argument("--segment-rows", type=int, default=128)
    parser.add_argument("--batch-documents", type=int, nargs="+",
                        default=[1_024, 8_192, 65_536, 131_072, 262_144, 524_288, 1_000_000])
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    require(args.executable.is_file(), "MDBX executable is missing")
    for path in (args.thq, args.codes, args.scales):
        require(path.is_file(), f"MDBX input is missing: {path}")
    require(args.documents > 0 and args.segment_rows > 0, "invalid corpus shape")
    batches = tuple(sorted(set(args.batch_documents)))
    require(batches and all(0 < batch <= args.documents for batch in batches),
            "invalid batch sweep")
    require(all(batch % args.segment_rows == 0 or batch == args.documents for batch in batches),
            "segment batches must align to segment rows")
    args.output_root.mkdir(parents=True, exist_ok=True)
    rows = []
    for batch in batches:
        db = args.output_root / f"segment-{args.segment_rows}-batch-{batch}.mdbx"
        row = run_json([str(args.executable), "--materialize", "segment", str(db),
                        str(args.thq), str(args.codes), str(args.scales),
                        str(args.documents), str(args.segment_rows), str(batch)])
        require(row.get("status") == "MATERIALIZED", f"batch {batch} did not materialize")
        require(row.get("durable_commits") == (args.documents + batch - 1) // batch,
                f"batch {batch} commit count differs")
        row["batch_documents"] = batch
        rows.append(row)
    receipt = {
        "schema_version": 1,
        "family": "native_thq_mdbx_bounded_batch_sweep_v1",
        "status": "EXECUTED",
        "fixture_kind": "caller-supplied; quality is not evaluated by this gate",
        "documents": args.documents,
        "segment_rows": args.segment_rows,
        "batch_documents": list(batches),
        "environment": {"cpu": platform.processor() or "unknown",
                        "logical_processors": __import__("os").cpu_count(),
                        "threads": 1, "affinity": "unpinned"},
        "inputs_sha256": {name: sha256(path) for name, path in {
            "thq": args.thq, "codes": args.codes, "scales": args.scales}.items()},
        "rows": rows,
        "limitations": [
            "materialization-only gate; no query quality or latency claim",
            "synthetic/caller-supplied payload provenance must be reviewed per run",
            "publication, concurrent readers and crash recovery remain separate gates"],
    }
    path = args.output_root / "native-mdbx-batch-sweep.result.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"result": str(path), "batch_documents": list(batches)}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"run-native-mdbx-batch-sweep: {error}")
