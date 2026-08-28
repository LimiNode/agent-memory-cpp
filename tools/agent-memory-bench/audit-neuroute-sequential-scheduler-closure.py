#!/usr/bin/env python3
"""Fail closed when nonlinear routing does not license sequential distillation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8")


def load_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    require(contract.get("schema_version") == 1
            and contract.get("family") == "neuroute_sequential_scheduler_closure_contract",
            "sequential scheduler closure contract family differs")
    require(set(contract) == {
        "schema_version", "family", "claim_scope", "activation",
        "required_parent_decision", "required_parent_matrix",
        "dormant_protocol_if_licensed",
    }, "sequential scheduler closure contract members differ")
    require(contract["claim_scope"]
            == "conditional_activation_only_no_sequential_measurement_or_production_selection",
            "sequential scheduler closure claim scope differs")
    require(set(contract["activation"])
            == {"nonlinear_result_sha256", "nonlinear_evidence_sha256"}
            and all(len(value) == 64 for value in contract["activation"].values()),
            "sequential scheduler closure activation differs")
    require(contract["required_parent_decision"]["sequential_followup_licensed"] is False
            and contract["required_parent_decision"]["production_selection_licensed"] is False
            and not any(contract["required_parent_decision"]["treatment_success"].values()),
            "sequential scheduler closure parent gate differs")
    require(contract["required_parent_matrix"] == {
        "model_count": 30,
        "calibration_row_count": 198,
        "selected_model_count": 6,
        "dataset_count": 3,
        "rows_per_dataset": 9,
    }, "sequential scheduler closure parent matrix differs")
    require(contract["dormant_protocol_if_licensed"]["production_selection_licensed"] is False,
            "sequential scheduler dormant protocol production gate differs")
    return contract


def validate_parent(contract: dict[str, Any], result: dict[str, Any],
                    evidence: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    activation = {
        "nonlinear_result_sha256": sha256(args.nonlinear_result),
        "nonlinear_evidence_sha256": sha256(args.nonlinear_evidence),
    }
    require(activation == contract["activation"],
            "sequential scheduler closure parent bytes differ")
    require(result.get("schema_version") == 1
            and result.get("family") == "neuroute_nonlinear_scheduler_result",
            "sequential scheduler closure parent result differs")
    require(evidence.get("schema_version") == 1
            and evidence.get("family") == "neuroute_nonlinear_scheduler_evidence"
            and evidence.get("passed") is True
            and evidence.get("result_sha256") == activation["nonlinear_result_sha256"]
            and evidence.get("result_byte_replay_passed") is True
            and evidence.get("model_byte_replay_passed") is True
            and evidence.get("authoritative_qrels_to_quality_replay_passed") is True,
            "sequential scheduler closure parent evidence differs")
    expected = contract["required_parent_matrix"]
    observed_matrix = {
        "model_count": len(result.get("models", [])),
        "calibration_row_count": len(result.get("calibration", [])),
        "selected_model_count": len(result.get("selection", {}).get("models", [])),
        "dataset_count": len(result.get("datasets", [])),
        "rows_per_dataset": min((len(row.get("rows", []))
                                 for row in result.get("datasets", [])), default=0),
    }
    require(observed_matrix == expected
            and all(len(row.get("rows", [])) == expected["rows_per_dataset"]
                    for row in result["datasets"]),
            "sequential scheduler closure parent result matrix differs")
    observed_decision = {
        "sequential_followup_licensed": result["decision"][
            "sequential_followup_licensed"],
        "production_selection_licensed": result["decision"][
            "production_selection_licensed"],
        "treatment_success": result["decision"]["treatment_success"],
    }
    require(observed_decision == contract["required_parent_decision"],
            "sequential scheduler closure parent decision differs")
    require(evidence.get("decision") == result["decision"]
            and evidence.get("production_selection_licensed") is False,
            "sequential scheduler closure evidence decision differs")
    return {"activation": activation, "matrix": observed_matrix,
            "decision": observed_decision}


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    result = json.loads(args.nonlinear_result.read_text(encoding="utf-8"))
    evidence = json.loads(args.nonlinear_evidence.read_text(encoding="utf-8"))
    parent = validate_parent(contract, result, evidence, args)
    closure = {
        "schema_version": 1,
        "family": "neuroute_sequential_scheduler_activation_closure",
        "passed": True,
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "closure_source_sha256": sha256(Path(__file__)),
        "parent": parent,
        "dormant_protocol_if_licensed": contract["dormant_protocol_if_licensed"],
        "sequential_followup_licensed": False,
        "sequential_training_executed": False,
        "sequential_measurement_executed": False,
        "sequential_model_files_created": False,
        "sequential_measurement_rows": [],
        "production_selection_licensed": False,
        "reason": "the frozen nonlinear parent failed its predeclared all-seed activation gate",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(closure))


def self_test() -> None:
    contract = load_contract(THIS / "neuroute-sequential-scheduler-closure.example.json")
    require(contract["required_parent_matrix"]["model_count"] == 30
            and contract["dormant_protocol_if_licensed"]["teacher"]
            == "greedy_teacher_forced_next_occupied_address",
            "sequential scheduler closure self-test differs")
    print("NeuRoute sequential scheduler closure self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-sequential-scheduler-closure.example.json")
    parser.add_argument("--nonlinear-result", type=Path)
    parser.add_argument("--nonlinear-evidence", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name not in ("self_test", "contract")):
            parser.error("all sequential scheduler closure paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"audit-neuroute-sequential-scheduler-closure: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
