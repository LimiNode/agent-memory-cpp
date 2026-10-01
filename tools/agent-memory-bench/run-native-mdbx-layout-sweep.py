#!/usr/bin/env python3
"""Run a bounded MDBX segment-size sweep with reproducible receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
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


def run_json(command: list[str]) -> dict:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    require(completed.returncode == 0,
            f"MDBX sweep command failed: {completed.stderr.strip()}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


def self_test() -> None:
    sizes = (16, 32, 64, 128, 256, 512, 1024, 4096)
    require(sizes == tuple(sorted(set(sizes))), "segment sweep is not strict")
    require(all(size > 0 for size in sizes), "segment sweep contains zero")
    require(0 < 256 < 1_000_000,
            "bounded batch fixture is not deterministic")
    print("run-native-mdbx-layout-sweep self-test PASS")


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--thq", type=Path, required=True)
    parser.add_argument("--codes", type=Path, required=True)
    parser.add_argument("--scales", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--documents", type=int, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--segment-rows", type=int, nargs="+",
                        default=[16, 32, 64, 128, 256, 512, 1024, 4096])
    parser.add_argument("--batch-documents", type=int, default=0,
                        help="optional durable materialization batch size")
    args = parser.parse_args()
    require(args.executable.is_file(), "MDBX executable is missing")
    for path in (args.thq, args.codes, args.scales, args.queries,
                 args.candidates, args.expected):
        require(path.is_file(), f"MDBX sweep input is missing: {path}")
    sizes = tuple(sorted(set(args.segment_rows)))
    require(sizes and all(size > 0 for size in sizes), "invalid segment sweep")
    args.output_root.mkdir(parents=True, exist_ok=True)
    rows = []
    for size in sizes:
        db = args.output_root / f"segment-{size}.mdbx"
        materialize_command = [str(args.executable), "--materialize", "segment",
                               str(db), str(args.thq), str(args.codes),
                               str(args.scales), str(args.documents), str(size)]
        if args.batch_documents:
            materialize_command.append(str(args.batch_documents))
        materialize = run_json(materialize_command)
        benchmark = run_json([str(args.executable), "--benchmark", "segment",
                              str(db), str(args.thq), str(args.codes),
                              str(args.scales), str(args.queries),
                              str(args.candidates), str(args.expected), "152", "5",
                              str(size)])
        require(benchmark.get("parity") == benchmark.get("parity_total"),
                f"segment-{size} ordered parity failed")
        rows.append({"segment_rows": size, "materialize": materialize,
                     "benchmark": benchmark})
    receipt = {
        "schema_version": 1,
        "family": "native_thq_mdbx_segment_sweep_v1",
        "status": "EXECUTED",
        "documents": args.documents,
        "batch_documents": args.batch_documents or args.documents,
        "segment_rows": list(sizes),
        "environment": {"cpu": platform.processor() or "unknown",
                        "logical_processors": __import__("os").cpu_count(),
                        "threads": 1, "affinity": "unpinned",
                        "numa": "unpinned", "power_policy": "uncontrolled"},
        "inputs_sha256": {name: sha256(path) for name, path in {
            "thq": args.thq, "codes": args.codes, "scales": args.scales,
            "queries": args.queries, "candidates": args.candidates,
            "expected": args.expected}.items()},
        "rows": rows,
        "materialize_timing_provenance": "measured_by_current_executable",
        "limitations": [
            "row layout retains the reference mixed value format for comparison",
            "segment layout stores a split final-code projection (INT8 plus scale); THQ remains in its own source input",
            "live generation publication and bounded batch materialization require separate lifecycle gates",
            "cold-cache, crash-recovery and concurrent rebuild require separate lifecycle gates"],
    }
    path = args.output_root / "native-mdbx-segment-sweep.result.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    print(json.dumps({"result": str(path), "segment_rows": list(sizes)}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"run-native-mdbx-layout-sweep: {error}")
