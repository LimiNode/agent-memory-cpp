#!/usr/bin/env python3
"""Replay and bind compact evidence for the #259 dense hot-path audit."""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


def load() -> Any:
    path = THIS / "run-neuroute-dense-performance-audit.py"
    spec = importlib.util.spec_from_file_location("neuroute_dense_audit_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load()


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def run(args: argparse.Namespace) -> None:
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result["contract_sha256"] == runner.sha256(args.contract) and
            result["activation"] == runner.activation(args) and
            result["decision"] == {
                "audit_gates_passed": True,
                "audited_hot_path_frozen_for_followups": True,
                "algorithm_or_persisted_format_changed": False},
            "dense performance evidence decision differs")
    for row in result["comparisons"]:
        require(runner.sha256(Path(row["report"])) == row["report_sha256"] and
                runner.sha256(Path(row["control_report"])) ==
                row["control_report_sha256"] and
                runner.sha256(Path(row["parent_report"])) ==
                row["parent_report_sha256"],
                "dense performance evidence report differs")
    with tempfile.TemporaryDirectory(prefix="neuroute-dense-audit-") as directory:
        replay = Path(directory) / "result.json"
        command = [sys.executable, str(THIS /
            "run-neuroute-dense-performance-audit.py"), "--contract",
            str(args.contract), "--parent-result", str(args.parent_result),
            "--parent-evidence", str(args.parent_evidence), "--parent-protocol",
            str(args.parent_protocol), "--native-executable",
            str(args.native_executable), "--control-executable",
            str(args.control_executable), "--report-root", str(args.report_root),
            "--control-report-root", str(args.control_report_root),
            "--reuse-reports", "--output", str(replay)]
        completed = subprocess.run(command, check=False, capture_output=True,
                                   text=True)
        require(completed.returncode == 0,
                "dense performance evidence replay failed: " +
                completed.stderr.strip())
        require(replay.read_bytes() == args.result.read_bytes(),
                "dense performance evidence replay bytes differ")
    output = {"schema_version": 1,
        "family": "neuroute_dense_performance_audit_evidence",
        "passed": True, "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "parent_result_sha256": runner.sha256(args.parent_result),
        "parent_evidence_sha256": runner.sha256(args.parent_evidence),
        "control_executable_sha256": runner.sha256(args.control_executable),
        "native_executable_sha256": runner.sha256(args.native_executable),
        "all_report_hashes_replayed": True,
        "result_byte_replay_passed": True,
        "summary": result["summary"], "decision": result["decision"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(output))


def self_test() -> None:
    contract = runner.load_contract(THIS /
        "neuroute-dense-performance-audit.example.json")
    require(contract["gates"]["require_all_stage_identities"] is True,
            "dense performance evidence self-test differs")
    print("NeuRoute dense performance audit evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
        "neuroute-dense-performance-audit.example.json")
    for name in ("result", "parent-result", "parent-evidence",
                 "parent-protocol", "control-executable",
                 "native-executable", "control-report-root", "report-root",
                 "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"contract", "self_test"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all dense performance evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"write-neuroute-dense-performance-audit-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
