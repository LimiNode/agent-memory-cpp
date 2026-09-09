#!/usr/bin/env python3
"""Validate compact evidence for the #256 native reduction sensitivity."""
from __future__ import annotations

import argparse
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


runner = load("neuroute_final_nonlinear_native_evidence_runner",
              "run-neuroute-final-nonlinear-int5-native-sensitivity.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def run(args: argparse.Namespace) -> None:
    runner.load_contract(args.contract)
    result = json.loads(args.result.read_text(encoding="utf-8"))
    manifest = json.loads(args.input_manifest.read_text(encoding="utf-8"))
    native = json.loads(args.native_report.read_text(encoding="utf-8"))
    require(result["contract_sha256"] == runner.parent.sha256(args.contract)
            and result["native_input_manifest_sha256"] ==
            runner.parent.sha256(args.input_manifest)
            and result["native_report_sha256"] ==
            runner.parent.sha256(args.native_report)
            and result["native_executable_sha256"] ==
            runner.parent.sha256(args.native_executable),
            "final nonlinear INT5 native evidence binding differs")
    require(native["input_manifest_sha256"] ==
            runner.parent.sha256(args.input_manifest) and
            result["decision"]["native_reduction_confirms_python_rejection"]
            is True and result["decision"]["nonlinear_replacement_licensed"]
            is False,
            "final nonlinear INT5 native evidence decision differs")
    payloads = [manifest[name] for name in
        ("queries", "positions", "ranks", "grades")]
    payloads.extend(row["reconstructed"] for row in manifest["treatments"])
    total_bytes = 0
    for value in payloads:
        path = args.input_manifest.parent / value["file"]
        require(path.is_file() and runner.parent.sha256(path) == value["sha256"],
                "final nonlinear INT5 native input payload differs")
        total_bytes += path.stat().st_size
    output = {"schema_version": 1,
        "family": "neuroute_final_nonlinear_int5_native_sensitivity_evidence",
        "passed": True, "contract_sha256": result["contract_sha256"],
        "result_sha256": runner.parent.sha256(args.result),
        "native_input_manifest_sha256": result[
            "native_input_manifest_sha256"],
        "native_report_sha256": result["native_report_sha256"],
        "native_executable_sha256": result["native_executable_sha256"],
        "native_evaluator_source_manifest_sha256": result[
            "native_evaluator_source_manifest_sha256"],
        "input_payloads_rehashed": len(payloads),
        "input_payload_bytes": total_bytes, "cases": result["cases"],
        "python_native_ranked_agreements": result[
            "python_native_ranked_agreements"],
        "python_native_ranked_comparisons": result[
            "python_native_ranked_comparisons"],
        "summary": result["summary"], "decision": result["decision"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.parent.canonical(output))


def self_test() -> None:
    contract = runner.load_contract(THIS /
        "neuroute-final-nonlinear-int5-native-sensitivity.example.json")
    require(contract["decision"]["retain_uniform_if_native_confirms_failure"]
            is True,
            "final nonlinear INT5 native evidence self-test differs")
    print("NeuRoute final nonlinear INT5 native sensitivity evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
        "neuroute-final-nonlinear-int5-native-sensitivity.example.json")
    for name in ("result", "input-manifest", "native-report",
                 "native-executable", "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all native sensitivity evidence paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print("write-neuroute-final-nonlinear-int5-native-sensitivity-evidence: "
              + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
