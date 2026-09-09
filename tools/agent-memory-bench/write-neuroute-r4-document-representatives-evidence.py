#!/usr/bin/env python3
"""Replay and bind the frozen R4 actual-document representative materialization."""

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


runner = load("neuroute_r4_document_representatives_evidence_runner",
              "run-neuroute-r4-document-representatives.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def artifact_map(result: dict[str, Any], root: Path) -> dict[str, str]:
    values = {}
    for seed in result["seeds"]:
        require(seed["posting_count"] == 1000000
                and seed["actual_representative_audit"][
                    "all_active_representatives_unique_within_address"] is True
                and seed["actual_representative_audit"][
                    "all_representative_primary_addresses_match"] is True,
                "R4 representative seed audit differs")
        for row in seed["artifacts"]:
            name = f"seed-{seed['seed']}/{row['path']}"
            path = root / name
            require(name not in values and path.is_file()
                    and runner.sha256(path) == row["sha256"]
                    and path.stat().st_size == row["bytes"],
                    f"R4 representative artifact differs: {name}")
            values[name] = row["sha256"]
    require(len(values) == 12, "R4 representative artifact count differs")
    return values


def validate(result: dict[str, Any], contract: dict[str, Any], root: Path
             ) -> dict[str, str]:
    require(result.get("schema_version") == 1
            and result.get("family")
            == "neuroute_r4_document_representatives_result"
            and result.get("activation") == contract["activation"]
            and result.get("matrix") == runner.planner.plan(contract)
            and len(result.get("seeds", [])) == 3,
            "R4 representative result identity differs")
    decision = result.get("decision", {})
    require(decision.get("materialization_audit_passed") is True
            and decision.get("fine_grained_interaction_ladder_licensed") is True
            and decision.get("teacher_trained_selection_used") is False
            and decision.get("production_selection_licensed") is False,
            "R4 representative decision differs")
    return artifact_map(result, root)


def write(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    original_bytes = args.result.read_bytes()
    result = json.loads(original_bytes)
    original_artifacts = validate(result, contract, args.materialization_root)
    with tempfile.TemporaryDirectory(
            prefix="neuroute-r4-document-representatives-evidence-") as directory:
        root = Path(directory)
        replay_args = argparse.Namespace(**vars(args))
        replay_args.materialization_root = root / "materialized"
        replay_args.output = root / "result.json"
        replay_args.result = None
        runner.run(replay_args)
        require(replay_args.output.read_bytes() == original_bytes,
                "R4 representative result is not byte-replayable")
        replay = json.loads(replay_args.output.read_text(encoding="utf-8"))
        require(validate(replay, contract, replay_args.materialization_root)
                == original_artifacts,
                "R4 representative regenerated artifact SHA map differs")
    evidence = {
        "schema_version": 1,
        "family": "neuroute_r4_document_representatives_evidence",
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "activation": contract["activation"],
        "source_files_sha256": {
            **result["source_files_sha256"],
            "write-neuroute-r4-document-representatives-evidence.py":
                runner.sha256(Path(__file__)),
        },
        "matrix": result["matrix"],
        "artifact_sha_map": original_artifacts,
        "authoritative_roots": result["authoritative_roots"],
        "teacher_blind_actual_document_selection_validated": True,
        "unique_within_address_validated": True,
        "primary_address_membership_validated": True,
        "artifact_sha_map_replay_passed": True,
        "result_byte_replay_passed": True,
        "decision": result["decision"],
        "native_confirmation_licensed": False,
        "production_selection_licensed": False,
        "passed": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))


def self_test() -> None:
    contract = runner.planner.load_contract(
        THIS / "neuroute-r4-document-representatives.example.json")
    value = runner.planner.plan(contract)
    require(value["materialized_seed_artifact_count"] == 12
            and value["teacher_queries_read"] == 0,
            "R4 representative evidence self-test differs")
    print("NeuRoute R4 document-representative evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-document-representatives.example.json")
    for name in [
            "replication-result", "replication-evidence",
            "feasible-result", "feasible-evidence",
            "width-materialization-root", "de-1m-e5-root",
            "de-1m-input-root", "materialization-root", "result", "output"]:
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all R4 representative evidence paths are required")
        write(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"write-neuroute-r4-document-representatives-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
