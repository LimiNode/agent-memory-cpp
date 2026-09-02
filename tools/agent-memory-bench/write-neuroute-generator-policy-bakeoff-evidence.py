#!/usr/bin/env python3
"""Validate compact evidence for the common generator policy bake-off."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

THIS = Path(__file__).resolve().parent
sys.dont_write_bytecode = True


def load() -> Any:
    path = THIS / "run-neuroute-generator-policy-bakeoff.py"
    spec = importlib.util.spec_from_file_location("neuroute_generator_policy", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path.name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-generator-policy-bakeoff.example.json")
    for name in ("result", "generator-result", "generator-evidence",
                 "training-result", "training-evidence", "hierarchy-result",
                 "hierarchy-evidence", "output"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        runner.self_test()
        return 0
    required = ("result", "generator_result", "generator_evidence",
        "training_result", "training_evidence", "hierarchy_result",
        "hierarchy_evidence", "output")
    runner.require(all(getattr(args, name) is not None for name in required),
                   "generator policy evidence inputs are required")
    bindings = {"generator": (args.generator_result, args.generator_evidence),
        "training": (args.training_result, args.training_evidence),
        "hierarchy": (args.hierarchy_result, args.hierarchy_evidence)}
    result = json.loads(args.result.read_text(encoding="utf-8"))
    expected = runner.build_result(args.contract, bindings)
    runner.require(result == expected,
                   "generator policy result or decision differs")
    decision = result["decision"]
    runner.require(decision["cheap_selector_passed"] is False and
                   decision["adaptive_prefix_training_required"] is True and
                   decision["quality_control"]["role"] == "prototype_ann" and
                   decision["native_integration_licensed"] is False and
                   decision["production_licensed"] is False,
                   "generator policy evidence decision differs")
    evidence = {"schema_version": 1,
        "family": "neuroute_generator_policy_bakeoff_evidence",
        "result_sha256": runner.sha256(args.result),
        "inputs": result["inputs"],
        "configuration_points": len(result["configuration_points"]),
        "result_binding_and_decision_validation_passed": True,
        "adaptive_prefix_training_required": True,
        "production_licensed": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
