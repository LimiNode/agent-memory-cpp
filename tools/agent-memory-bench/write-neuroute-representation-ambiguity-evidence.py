#!/usr/bin/env python3
"""Replay and bind the frozen R0 representation-ambiguity diagnostic."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
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


runner = load("neuroute_representation_ambiguity_evidence_runner",
              "run-neuroute-representation-ambiguity.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def validate_result(result: dict[str, Any], contract: dict[str, Any]) -> None:
    require(result.get("schema_version") == 1
            and result.get("family")
            == "neuroute_representation_ambiguity_diagnostic_result"
            and result.get("matrix") == runner.planner.plan(contract)
            and result.get("activation") == contract["activation"],
            "representation-ambiguity result identity differs")
    rows = result.get("rows")
    require(isinstance(rows, list) and len(rows) == 3
            and [row.get("seed") for row in rows] == contract["route"]["seeds"],
            "representation-ambiguity seed rows differ")
    for row in rows:
        collisions = row.get("collisions", {})
        require(set(collisions) == {"exact_float32", "quantized_8bit",
                                    "quantized_12bit"},
                "representation-ambiguity collision matrix differs")
        require(all(value.get("queries") == 8141
                    and value.get("rows") == 8141 * 1024
                    for value in collisions.values()),
                "representation-ambiguity collision coverage differs")
        knn = row.get("local_knn", {})
        require(knn.get("sampled_query_count") == 256
                and len(knn.get("cross_validated_folds", [])) == 5
                and knn.get("privileged_neighbour_labels_used") is True
                and knn.get("deployable_scorer_claim_forbidden") is True,
                "representation-ambiguity kNN diagnostic differs")
    decision = result.get("decision", {})
    require(decision.get("approximate_knn_is_empirical_ambiguity_only") is True
            and decision.get("representation_ladder_licensed") is True
            and decision.get("native_confirmation_licensed") is False
            and decision.get("production_selection_licensed") is False,
            "representation-ambiguity decision differs")


def write(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    original_bytes = args.result.read_bytes()
    result = json.loads(original_bytes)
    validate_result(result, contract)
    parent_evidence = json.loads(
        args.teacher_objective_evidence.read_text(encoding="utf-8"))
    require(parent_evidence.get("passed") is True
            and parent_evidence.get("authoritative_qrels_to_quality_replay_passed")
            is True and isinstance(parent_evidence.get("authoritative_roots"), list),
            "representation-ambiguity authoritative parent differs")
    with tempfile.TemporaryDirectory(
            prefix="neuroute-representation-ambiguity-evidence-") as directory:
        replay_args = argparse.Namespace(**vars(args))
        replay_args.output = Path(directory) / "result.json"
        runner.run(replay_args)
        require(replay_args.output.read_bytes() == original_bytes,
                "representation-ambiguity result is not byte-replayable")
        validate_result(json.loads(replay_args.output.read_text(encoding="utf-8")),
                        contract)
    evidence = {
        "schema_version": 1,
        "family": "neuroute_representation_ambiguity_diagnostic_evidence",
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "activation": contract["activation"],
        "source_files_sha256": {
            **result["source_files_sha256"],
            "write-neuroute-representation-ambiguity-evidence.py":
                runner.sha256(Path(__file__)),
        },
        "matrix": result["matrix"],
        "cache_manifest_sha256": contract["cache_manifest_sha256"],
        "authoritative_roots": parent_evidence["authoritative_roots"],
        "authoritative_qrels_to_quality_replay_passed": True,
        "parent_cache_payload_hashes_validated": True,
        "all_cached_queries_collision_scanned": True,
        "result_byte_replay_passed": True,
        "approximate_knn_is_empirical_ambiguity_only": True,
        "native_confirmation_licensed": False,
        "production_selection_licensed": False,
        "decision": result["decision"],
        "passed": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))


def self_test() -> None:
    contract = runner.planner.load_contract(
        THIS / "neuroute-representation-ambiguity.example.json")
    plan = runner.planner.plan(contract)
    require(plan["collision_query_count"] == 24423
            and plan["cross_validation_folds"] == 5,
            "representation-ambiguity evidence self-test differs")
    print("NeuRoute representation-ambiguity evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-representation-ambiguity.example.json")
    parser.add_argument("--nonlinear-result", type=Path)
    parser.add_argument("--nonlinear-evidence", type=Path)
    parser.add_argument("--teacher-objective-result", type=Path)
    parser.add_argument("--teacher-objective-evidence", type=Path)
    parser.add_argument("--parent-cache-root", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all representation-ambiguity evidence paths are required")
        write(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"write-neuroute-representation-ambiguity-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
