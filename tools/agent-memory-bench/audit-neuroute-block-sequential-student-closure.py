#!/usr/bin/env python3
"""Fail closed when the sequential oracle does not license student training."""

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
            and contract.get("family")
            == "neuroute_block_sequential_student_closure_contract",
            "block-sequential closure contract family differs")
    require(set(contract) == {
        "schema_version", "family", "claim_scope", "activation",
        "required_parent_decision", "required_parent_matrix",
        "dormant_student_protocol_if_licensed",
    }, "block-sequential closure contract members differ")
    require(contract["claim_scope"]
            == "conditional_student_activation_only_no_training_measurement_or_production_selection",
            "block-sequential closure claim scope differs")
    require(set(contract["activation"])
            == {"oracle_result_sha256", "oracle_evidence_sha256"}
            and all(isinstance(value, str) and len(value) == 64
                    for value in contract["activation"].values()),
            "block-sequential closure activation differs")
    require(contract["required_parent_decision"] == {
        "sequential_teacher_headroom_supported": False,
        "student_followup_licensed": False,
        "production_selection_licensed": False,
    }, "block-sequential closure parent decision differs")
    require(contract["required_parent_matrix"] == {
        "dataset_count": 3, "rows_per_dataset": 18,
        "total_rows": 54, "queries_per_row": 76,
    }, "block-sequential closure parent matrix differs")
    dormant = contract["dormant_student_protocol_if_licensed"]
    require(dormant["block_sizes"] == [16, 32, 64]
            and dormant["preferred_initialization"] == "centroid_initialized_id"
            and dormant["student_observability"]
            == "no_privileged_relevant_document_state"
            and dormant["production_selection_licensed"] is False,
            "block-sequential dormant protocol differs")
    return contract


def validate_parent(contract: dict[str, Any], result: dict[str, Any],
                    evidence: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    activation = {
        "oracle_result_sha256": sha256(args.oracle_result),
        "oracle_evidence_sha256": sha256(args.oracle_evidence),
    }
    require(activation == contract["activation"],
            "block-sequential closure parent bytes differ")
    require(result.get("schema_version") == 1
            and result.get("family") == "neuroute_sequential_oracle_diagnostic_result",
            "block-sequential closure parent result differs")
    require(evidence.get("schema_version") == 1
            and evidence.get("family") == "neuroute_sequential_oracle_diagnostic_evidence"
            and evidence.get("passed") is True
            and evidence.get("result_sha256") == activation["oracle_result_sha256"]
            and evidence.get("result_byte_replay_passed") is True
            and evidence.get("authoritative_qrels_to_quality_replay_passed") is True,
            "block-sequential closure parent evidence differs")
    observed_decision = {
        name: result["decision"][name] for name in contract["required_parent_decision"]
    }
    require(observed_decision == contract["required_parent_decision"]
            and evidence.get("decision") == result["decision"],
            "block-sequential closure parent gate differs")
    expected = contract["required_parent_matrix"]
    observed_matrix = {
        "dataset_count": len(result.get("datasets", [])),
        "rows_per_dataset": min((len(row.get("rows", []))
                                 for row in result.get("datasets", [])), default=0),
        "total_rows": sum(len(row.get("rows", []))
                          for row in result.get("datasets", [])),
        "queries_per_row": min((len(treatment.get("queries", []))
                                for dataset in result.get("datasets", [])
                                for treatment in dataset.get("rows", [])), default=0),
    }
    require(observed_matrix == expected
            and all(len(dataset["rows"]) == expected["rows_per_dataset"]
                    for dataset in result["datasets"])
            and all(len(row["queries"]) == expected["queries_per_row"]
                    for dataset in result["datasets"] for row in dataset["rows"]),
            "block-sequential closure parent matrix differs")
    return {"activation": activation, "decision": observed_decision,
            "matrix": observed_matrix}


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    result = json.loads(args.oracle_result.read_text(encoding="utf-8"))
    evidence = json.loads(args.oracle_evidence.read_text(encoding="utf-8"))
    parent = validate_parent(contract, result, evidence, args)
    closure = {
        "schema_version": 1,
        "family": "neuroute_block_sequential_student_activation_closure",
        "passed": True,
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "closure_source_sha256": sha256(Path(__file__)),
        "parent": parent,
        "dormant_student_protocol_if_licensed":
            contract["dormant_student_protocol_if_licensed"],
        "student_followup_licensed": False,
        "student_training_executed": False,
        "student_models_created": False,
        "student_calibration_executed": False,
        "internal_evaluation_opened": False,
        "student_measurement_rows": [],
        "native_implementation_created": False,
        "production_selection_licensed": False,
        "reason": "the frozen sequential oracle did not beat static target-gain density",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(closure))


def self_test() -> None:
    contract = load_contract(THIS /
                             "neuroute-block-sequential-student-closure.example.json")
    dormant = contract["dormant_student_protocol_if_licensed"]
    require(dormant["teacher_forcing"] is True
            and dormant["evaluation_partition"]
            == "separate_german_internal_evaluation_queries_only",
            "block-sequential closure self-test differs")
    print("NeuRoute block-sequential student closure self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-block-sequential-student-closure.example.json")
    parser.add_argument("--oracle-result", type=Path)
    parser.add_argument("--oracle-evidence", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name not in ("self_test", "contract")):
            parser.error("all block-sequential closure paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"audit-neuroute-block-sequential-student-closure: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
