#!/usr/bin/env python3
"""Fail closed when nested ADC quality does not license physical timing."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
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


planner = load("neuroute_physical_adc_activation_planner",
               "plan-neuroute-physical-adc-activation.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def source_hashes() -> dict[str, str]:
    names = ("plan-neuroute-physical-adc-activation.py",
             "audit-neuroute-physical-adc-activation.py")
    return {name: sha256(THIS / name) for name in names}


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    actual = {"nested_adc_result_sha256": sha256(args.nested_adc_result),
              "nested_adc_evidence_sha256": sha256(args.nested_adc_evidence)}
    require(actual == contract["activation"], "physical ADC activation bytes differ")
    result = json.loads(args.nested_adc_result.read_text(encoding="utf-8"))
    evidence = json.loads(args.nested_adc_evidence.read_text(encoding="utf-8"))
    require(result.get("family") == "neuroute_nested_multiseed_adc_replication_result"
            and evidence.get("family") == "neuroute_nested_multiseed_adc_replication_evidence"
            and evidence.get("passed") is True
            and evidence.get("result_sha256") == actual["nested_adc_result_sha256"],
            "physical ADC parent evidence differs")
    observed = {name: result["decision"][name]
                for name in contract["required_parent_decision"]}
    require(observed == contract["required_parent_decision"],
            "physical ADC parent decision does not match fail-closed contract")
    output = {"schema_version": 1, "family": "neuroute_physical_adc_benchmark_activation_audit",
              "claim_scope": contract["claim_scope"], "contract_sha256": sha256(args.contract),
              "activation": actual, "source_files_sha256": source_hashes(),
              "parent_decision": observed, "dormant_benchmark_protocol": contract["benchmark_if_activated"],
              "benchmark_activated": False, "benchmark_executed": False,
              "physical_files_created": False, "timing_rows": [],
              "reason": "nested multi-seed ADC produced no quality-eligible candidate",
              "production_selection_licensed": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(output))


def self_test() -> None:
    contract = planner.load_contract(THIS / "neuroute-physical-adc-activation.example.json")
    require(planner.plan(contract) == {"benchmark_expected": False,
                                       "physical_rows_expected": 0,
                                       "required_stages_if_activated": 6},
            "physical ADC activation self-test differs")
    print("NeuRoute physical ADC activation self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-physical-adc-activation.example.json")
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
            parser.error("all physical ADC activation paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"audit-neuroute-physical-adc-activation: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
