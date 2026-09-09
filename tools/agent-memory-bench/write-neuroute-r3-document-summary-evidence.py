#!/usr/bin/env python3
"""Replay and bind the frozen R3 document-summary materialization."""

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


runner = load("neuroute_r3_document_summary_evidence_runner",
              "run-neuroute-r3-document-summary.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def artifact_map(result: dict[str, Any], root: Path) -> dict[str, str]:
    artifacts = {}
    for seed in result["seeds"]:
        seed_value = int(seed["seed"])
        for row in seed["artifacts"]:
            key = f"seed-{seed_value}/{row['path']}"
            require(key not in artifacts and isinstance(row.get("sha256"), str),
                    "R3 document-summary artifact manifest differs")
            path = root / f"seed-{seed_value}" / row["path"]
            require(path.is_file() and runner.sha256(path) == row["sha256"]
                    and path.stat().st_size == row["bytes"],
                    f"R3 document-summary artifact bytes differ: {key}")
            artifacts[key] = row["sha256"]
    return artifacts


def validate_result(result: dict[str, Any], contract: dict[str, Any],
                    root: Path) -> dict[str, str]:
    require(result.get("schema_version") == 1
            and result.get("family") == "neuroute_r3_document_summary_result"
            and result.get("matrix") == runner.planner.plan(contract)
            and result.get("activation") == contract["activation"],
            "R3 document-summary result identity differs")
    seeds = result.get("seeds")
    require(isinstance(seeds, list) and len(seeds) == 3
            and [int(row.get("seed")) for row in seeds]
            == contract["route"]["seeds"],
            "R3 document-summary seed matrix differs")
    roles = {
        "local_document_count", "mean_residual",
        "diagonal_residual_variance", "top_centered_residual_direction",
        "top_residual_eigenvalue", "total_residual_energy",
    }
    for seed in seeds:
        require(seed.get("assigned_document_count") == 1000000
                and seed.get("document_count") == 1000000
                and {row.get("role") for row in seed.get("artifacts", [])} == roles
                and [row.get("occupancy_bucket")
                     for row in seed.get("occupancy_buckets", [])]
                == contract["audit"]["address_occupancy_buckets"]
                and seed.get("centroid_slot_is_not_a_document_representative")
                is True, "R3 document-summary seed audit differs")
    decision = result.get("decision", {})
    require(decision.get("every_document_assigned_once") is True
            and decision.get("finite_summary_audit_passed") is True
            and decision.get("zero_fallback_semantics_passed") is True
            and decision.get("summary_is_query_independent") is True
            and decision.get("summary_is_teacher_blind") is True
            and decision.get("matched_r3_ladder_licensed") is True
            and decision.get("stateful_policy_licensed") is False
            and decision.get("native_confirmation_licensed") is False
            and decision.get("production_selection_licensed") is False,
            "R3 document-summary decision differs")
    return artifact_map(result, root)


def write(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    original_bytes = args.result.read_bytes()
    result = json.loads(original_bytes)
    original_artifacts = validate_result(
        result, contract, args.materialization_root)
    parent_evidence = json.loads(args.matched_representation_evidence.read_text(
        encoding="utf-8"))
    require(parent_evidence.get("passed") is True
            and parent_evidence.get("authoritative_qrels_to_quality_replay_passed")
            is True and isinstance(parent_evidence.get("authoritative_roots"), list),
            "R3 document-summary authoritative parent differs")
    with tempfile.TemporaryDirectory(
            prefix="neuroute-r3-document-summary-evidence-") as directory:
        root = Path(directory)
        replay_args = argparse.Namespace(**vars(args))
        replay_args.output = root / "result.json"
        replay_args.materialization_root = root / "materialization"
        runner.run(replay_args)
        require(replay_args.output.read_bytes() == original_bytes,
                "R3 document-summary result is not byte-replayable")
        replay = json.loads(replay_args.output.read_text(encoding="utf-8"))
        require(validate_result(replay, contract, replay_args.materialization_root)
                == original_artifacts,
                "R3 document-summary regenerated artifact SHA map differs")
    evidence = {
        "schema_version": 1,
        "family": "neuroute_r3_document_summary_evidence",
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "activation": contract["activation"],
        "source_files_sha256": {
            **result["source_files_sha256"],
            "write-neuroute-r3-document-summary-evidence.py":
                runner.sha256(Path(__file__)),
        },
        "matrix": result["matrix"],
        "artifact_sha256": original_artifacts,
        "authoritative_roots": parent_evidence["authoritative_roots"],
        "authoritative_qrels_to_quality_replay_passed": True,
        "teacher_blind_query_independent_summary_validated": True,
        "every_document_assigned_once": True,
        "finite_summary_audit_passed": True,
        "zero_fallback_semantics_passed": True,
        "artifact_sha_map_replay_passed": True,
        "result_byte_replay_passed": True,
        "matched_r3_ladder_licensed": True,
        "native_confirmation_licensed": False,
        "production_selection_licensed": False,
        "decision": result["decision"],
        "passed": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))


def self_test() -> None:
    contract = runner.planner.load_contract(
        THIS / "neuroute-r3-document-summary.example.json")
    plan = runner.planner.plan(contract)
    require(plan["seed_count"] == 3 and plan["summary_vector_fields"] == 3
            and plan["matched_r3_ladder_licensed_after_replay"] is True,
            "R3 document-summary evidence self-test differs")
    print("NeuRoute R3 document-summary evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r3-document-summary.example.json")
    parser.add_argument("--matched-representation-result", type=Path)
    parser.add_argument("--matched-representation-evidence", type=Path)
    parser.add_argument("--ambiguity-result", type=Path)
    parser.add_argument("--ambiguity-evidence", type=Path)
    parser.add_argument("--nonlinear-result", type=Path)
    parser.add_argument("--nonlinear-evidence", type=Path)
    parser.add_argument("--prototype-gain-density-result", type=Path)
    parser.add_argument("--prototype-gain-density-evidence", type=Path)
    parser.add_argument("--multilingual-query-root", type=Path)
    parser.add_argument("--width-materialization-root", type=Path)
    parser.add_argument("--german-split-result", type=Path)
    parser.add_argument("--de-1m-e5-root", type=Path)
    parser.add_argument("--de-1m-input-root", type=Path)
    parser.add_argument("--parent-cache-root", type=Path)
    parser.add_argument("--materialization-root", type=Path)
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
            parser.error("all R3 document-summary evidence paths are required")
        write(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"write-neuroute-r3-document-summary-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
