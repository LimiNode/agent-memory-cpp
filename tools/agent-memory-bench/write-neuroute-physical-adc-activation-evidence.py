#!/usr/bin/env python3
"""Replay the fail-closed physical ADC activation audit."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load("neuroute_physical_adc_activation_evidence_runner",
              "audit-neuroute-physical-adc-activation.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def run(args: argparse.Namespace) -> None:
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("family") == "neuroute_physical_adc_benchmark_activation_audit"
            and result.get("benchmark_activated") is False
            and result.get("benchmark_executed") is False
            and result.get("physical_files_created") is False
            and result.get("timing_rows") == []
            and result.get("production_selection_licensed") is False
            and result.get("source_files_sha256") == runner.source_hashes(),
            "physical ADC activation evidence result differs")
    with tempfile.TemporaryDirectory(prefix="neuroute-physical-adc-activation-") as directory:
        replay = Path(directory) / "result.json"
        runner.run(SimpleNamespace(contract=args.contract,
                                   nested_adc_result=args.nested_adc_result,
                                   nested_adc_evidence=args.nested_adc_evidence,
                                   output=replay))
        require(replay.read_bytes() == args.result.read_bytes(),
                "physical ADC activation audit is not byte-replayable")
    evidence = {"schema_version": 1, "family": "neuroute_physical_adc_benchmark_activation_evidence",
                "passed": True, "contract_sha256": runner.sha256(args.contract),
                "result_sha256": runner.sha256(args.result),
                "source_files_sha256": {**runner.source_hashes(),
                    "write-neuroute-physical-adc-activation-evidence.py": runner.sha256(Path(__file__))},
                "benchmark_activated": False, "benchmark_executed": False,
                "physical_files_created": False, "result_byte_replay_passed": True,
                "production_selection_licensed": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))


def self_test() -> None:
    runner.self_test()
    print("NeuRoute physical ADC activation evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-physical-adc-activation.example.json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--nested-adc-result", type=Path)
    parser.add_argument("--nested-adc-evidence", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name not in ("self_test", "contract")):
            parser.error("all physical ADC activation evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"write-neuroute-physical-adc-activation-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
