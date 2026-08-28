#!/usr/bin/env python3
"""Replay and bind the frozen exact-E5 rerank evidence."""

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


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load("neuroute_exact_e5_evidence_runner", "run-neuroute-exact-e5-rerank.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def replay_namespace(args: argparse.Namespace, output: Path, materialization_root: Path) -> argparse.Namespace:
    values = vars(args).copy()
    values["output"] = output
    values["materialization_root"] = materialization_root
    return argparse.Namespace(**values)


def validate_report(result: dict[str, Any], contract: dict[str, Any]) -> None:
    require(result.get("schema_version") == 1
            and result.get("family") == "neuroute_exact_e5_rerank_quality_result"
            and result.get("claim_scope") == contract["claim_scope"],
            "exact-E5 evidence quality family differs")
    require(result.get("source_files_sha256") == runner.source_hashes(),
            "exact-E5 evidence quality sources differ")
    require([row.get("id") for row in result.get("datasets", [])]
            == [row["id"] for row in contract["datasets"]],
            "exact-E5 evidence datasets differ")
    for dataset in result["datasets"]:
        require(len(dataset.get("rows", [])) == 3, "exact-E5 evidence seed rows differ")
        for row in dataset["rows"]:
            require(row.get("query_count") > 0 and len(row.get("queries", [])) == row["query_count"],
                    "exact-E5 evidence query rows differ")
            require(all(len(row.get(f"exact_e5_on_adc_{limit}_sequence_sha256", "")) == 64
                        for limit in contract["cascade"]["adc_limits"]),
                    "exact-E5 evidence sequence digest differs")


def validate_native(native: dict[str, Any], contract: dict[str, Any], manifest_sha256: str) -> None:
    require(native.get("schema_version") == 1
            and native.get("family") == "neuroute_exact_e5_rerank_native_result"
            and native.get("materialization_sha256") == manifest_sha256
            and native.get("timings_recorded") is True,
            "exact-E5 evidence native binding differs")
    require(len(native.get("rows", [])) == 3 * 3 * 4,
            "exact-E5 evidence native row count differs")
    for row in native["rows"]:
        timing = row.get("timing_ms_per_query", {})
        require(all(isinstance(timing.get(name), (int, float)) and timing[name] >= 0.0
                    for name in ("mean", "p50", "p95", "p99")),
                "exact-E5 evidence native timing differs")
    require(len(native.get("evaluator_source_manifest_sha256", "")) == 64,
            "exact-E5 native source manifest differs")


def self_test() -> None:
    contract = runner.planner.load_contract(THIS / "neuroute-exact-e5-rerank.example.json")
    require(len(runner.planner.plan(contract)) == 9 and contract["cascade"]["adc_limits"][-1] == 512,
            "exact-E5 evidence self-test differs")
    print("NeuRoute exact-E5 evidence self-test passed")


def run(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    manifest_path = args.materialization_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    native = json.loads(args.native_report.read_text(encoding="utf-8"))
    require(result.get("contract_sha256") == runner.sha256(args.contract),
            "exact-E5 evidence result contract differs")
    require(manifest.get("contract_sha256") == runner.sha256(args.contract)
            and manifest.get("quality_result_sha256") == runner.sha256(args.result),
            "exact-E5 evidence materialization differs")
    validate_report(result, contract)
    validate_native(native, contract, runner.sha256(manifest_path))
    with tempfile.TemporaryDirectory(prefix="neuroute-exact-e5-replay-") as directory:
        root = Path(directory)
        replay_result = root / "result.json"
        replay_materialization = root / "materialized"
        runner.run(replay_namespace(args, replay_result, replay_materialization))
        require(replay_result.read_bytes() == args.result.read_bytes(),
                "exact-E5 replay quality bytes differ")
        replay_manifest = replay_materialization / "manifest.json"
        require(replay_manifest.read_bytes() == manifest_path.read_bytes(),
                "exact-E5 replay materialization manifest differs")
    completed = subprocess.run([
        str(args.native_executable), "--validate", str(args.contract),
        str(manifest_path), str(args.native_report),
    ], check=False, capture_output=True, text=True)
    require(completed.returncode == 0, f"exact-E5 native replay failed: {completed.stderr.strip()}")
    receipt = {
        "schema_version": 1, "family": "neuroute_exact_e5_rerank_evidence",
        "passed": True, "contract_sha256": runner.sha256(args.contract),
        "quality_result_sha256": runner.sha256(args.result),
        "materialization_sha256": runner.sha256(manifest_path),
        "native_report_sha256": runner.sha256(args.native_report),
        "native_executable_sha256": runner.sha256(args.native_executable),
        "quality_source_files_sha256": runner.source_hashes(),
        "native_evaluator_source_manifest_sha256": native["evaluator_source_manifest_sha256"],
        "quality_replay_byte_identical": True, "materialization_replay_byte_identical": True,
        "native_sequence_replay_passed": True, "decision": result["decision"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(receipt))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "neuroute-exact-e5-rerank.example.json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--materialization-root", type=Path)
    parser.add_argument("--native-report", type=Path)
    parser.add_argument("--native-executable", type=Path)
    parser.add_argument("--v4-contract", type=Path)
    parser.add_argument("--v4-result", type=Path)
    parser.add_argument("--v4-evidence", type=Path)
    parser.add_argument("--v4-native-result", type=Path)
    parser.add_argument("--v4-native-materialization", type=Path)
    parser.add_argument("--training-model-root", type=Path)
    for language in ("de", "fr", "ja"):
        for name in ("result", "e5", "input"):
            parser.add_argument(f"--{language}-{name}-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = [value for name, value in vars(args).items()
                    if name != "self_test" and name != "contract"]
        if any(value is None for value in required):
            parser.error("all evidence replay paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"write-neuroute-exact-e5-rerank-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
