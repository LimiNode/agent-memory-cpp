#!/usr/bin/env python3
"""Replay and bind compact evidence for the #260 final-rerank ceiling."""
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
    path = THIS / "run-neuroute-final-rerank-ceiling.py"
    spec = importlib.util.spec_from_file_location("neuroute_final_ceiling", path)
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
            result["decision"]["gates_passed"] is True and
            result["decision"]["uniform_int5_native_identity_preserved"] is True,
            "final-rerank ceiling evidence decision differs")
    for row in result["reports"]:
        require(runner.sha256(Path(row["path"])) == row["sha256"],
                "final-rerank ceiling report hash differs")
    with tempfile.TemporaryDirectory(prefix="neuroute-final-ceiling-") as root:
        replay = Path(root) / "result.json"
        replay_protocol = Path(root) / "protocol.json"
        command = [sys.executable, str(THIS /
            "run-neuroute-final-rerank-ceiling.py"), "--contract",
            str(args.contract), "--routing-result", str(args.routing_result),
            "--routing-evidence", str(args.routing_evidence),
            "--routing-protocol", str(args.routing_protocol),
            "--final-result", str(args.final_result), "--final-evidence",
            str(args.final_evidence), "--final-manifest", str(args.final_manifest),
            "--dense-audit-result", str(args.dense_audit_result),
            "--dense-audit-evidence", str(args.dense_audit_evidence),
            "--native-executable", str(args.native_executable),
            "--native-protocol", str(replay_protocol), "--report-root",
            str(args.report_root), "--reuse-reports", "--output", str(replay)]
        completed = subprocess.run(command, check=False, capture_output=True,
                                   text=True)
        require(completed.returncode == 0,
                "final-rerank ceiling replay failed: " +
                completed.stderr.strip())
        replay_value = json.loads(replay.read_text(encoding="utf-8"))
        replay_value["native_protocol_sha256"] = result[
            "native_protocol_sha256"]
        replay.write_bytes(runner.canonical(replay_value))
        require(replay.read_bytes() == args.result.read_bytes(),
                "final-rerank ceiling replay bytes differ")
    output = {"schema_version": 1,
        "family": "neuroute_final_rerank_ceiling_evidence",
        "passed": True, "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "native_executable_sha256": runner.sha256(args.native_executable),
        "final_storage_manifest_sha256": runner.sha256(args.final_manifest),
        "report_hashes_replayed": True, "result_byte_replay_passed": True,
        "selection": result["selection"], "decision": result["decision"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(output))


def self_test() -> None:
    contract = runner.load_contract(THIS /
        "neuroute-final-rerank-ceiling.example.json")
    require(contract["identity"][
        "uniform_int5_kernels_must_match_all_six_stage_hashes"] is True,
        "final-rerank evidence self-test differs")
    print("NeuRoute final-rerank ceiling evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-final-rerank-ceiling.example.json")
    for name in ("result", "routing-result", "routing-evidence",
                 "routing-protocol", "final-result", "final-evidence",
                 "final-manifest", "dense-audit-result",
                 "dense-audit-evidence", "native-executable",
                 "report-root", "output"):
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
            parser.error("all final-rerank evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"write-neuroute-final-rerank-ceiling-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
