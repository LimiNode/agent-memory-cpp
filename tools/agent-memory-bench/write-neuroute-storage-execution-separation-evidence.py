#!/usr/bin/env python3
"""Replay compact evidence for the #261 storage/execution contract."""
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
    path = THIS / "run-neuroute-storage-execution-separation.py"
    spec = importlib.util.spec_from_file_location("neuroute_storage_execution",
                                                  path)
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
            result["decision"][
                "persisted_bytes_are_execution_independent"] is True and
            result["decision"]["one_codec_store_per_index"] is True and
            result["decision"][
                "specialized_avx2_repack_is_production_format"] is False,
            "storage/execution evidence decision differs")
    for report in result["reports"].values():
        require(runner.sha256(Path(report["path"])) == report["sha256"],
                "storage/execution evidence report hash differs")
    with tempfile.TemporaryDirectory(prefix="neuroute-storage-execution-") as root:
        replay = Path(root) / "result.json"
        command = [sys.executable, str(THIS /
            "run-neuroute-storage-execution-separation.py"), "--contract",
            str(args.contract), "--final-rerank-result",
            str(args.final_rerank_result), "--final-rerank-evidence",
            str(args.final_rerank_evidence), "--storage-manifest",
            str(args.storage_manifest), "--safe-executable",
            str(args.safe_executable), "--avx2-executable",
            str(args.avx2_executable), "--safe-cache", str(args.safe_cache),
            "--avx2-cache", str(args.avx2_cache), "--safe-report",
            str(args.safe_report), "--avx2-report", str(args.avx2_report),
            "--reuse-reports", "--output", str(replay)]
        completed = subprocess.run(command, check=False, capture_output=True,
                                   text=True)
        require(completed.returncode == 0,
                "storage/execution evidence replay failed: " +
                completed.stderr.strip())
        require(replay.read_bytes() == args.result.read_bytes(),
                "storage/execution evidence replay bytes differ")
    output = {"schema_version": 1,
        "family": "neuroute_storage_execution_separation_evidence",
        "passed": True, "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "safe_executable_sha256": runner.sha256(args.safe_executable),
        "avx2_executable_sha256": runner.sha256(args.avx2_executable),
        "physical_storage_manifest_sha256": runner.sha256(
            args.storage_manifest),
        "result_byte_replay_passed": True,
        "compatibility": result["compatibility"],
        "decision": result["decision"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(output))


def self_test() -> None:
    contract = runner.load_contract(THIS /
        "neuroute-storage-execution-separation.example.json")
    require(contract["gates"]["require_one_materialized_store"] is True,
            "storage/execution evidence self-test differs")
    print("NeuRoute storage/execution separation evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-storage-execution-separation.example.json")
    for name in ("result", "final-rerank-result", "final-rerank-evidence",
                 "storage-manifest", "safe-executable", "avx2-executable",
                 "safe-cache", "avx2-cache", "safe-report", "avx2-report",
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
            parser.error("all storage/execution evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"write-neuroute-storage-execution-separation-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
