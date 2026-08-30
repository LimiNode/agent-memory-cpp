#!/usr/bin/env python3
"""Replay and bind the document-level replication-topology diagnostic."""

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


runner = load("neuroute_replication_topology_evidence_runner",
              "run-neuroute-replication-topology.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def mapping_map(rows: list[dict[str, Any]], root: Path) -> dict[str, str]:
    result = {}
    for row in rows:
        name, digest = row.get("file"), row.get("sha256")
        require(isinstance(name, str) and isinstance(digest, str)
                and name not in result and len(digest) == 64,
                "replication mapping manifest differs")
        path = root / name
        require(path.is_file() and runner.sha256(path) == digest
                and row.get("mapped_document_count") == 1000000
                and row.get("all_secondary_addresses_differ") is True,
                f"replication mapping bytes differ: {name}")
        result[name] = digest
    return result


def validate(result: dict[str, Any], contract: dict[str, Any],
             mapping_root: Path) -> dict[str, str]:
    plan = runner.planner.plan(contract)
    require(result.get("schema_version") == 1
            and result.get("family") == "neuroute_replication_topology_result"
            and result.get("matrix") == plan
            and result.get("activation") == contract["activation"],
            "replication result identity differs")
    mappings = result.get("mapping_artifacts")
    expected_mappings = {(int(seed), treatment)
                         for seed in contract["route"]["seeds"]
                         for treatment in [
                             "nearest_semantic_secondary",
                             "soar_complementary_secondary",
                             "training_fitted_complementary"]}
    require(isinstance(mappings, list) and len(mappings) == 9
            and {(int(row.get("seed")), row.get("treatment")) for row in mappings}
            == expected_mappings, "replication mapping matrix differs")
    expected_rows = {(int(seed), treatment)
                     for seed in contract["route"]["seeds"]
                     for treatment in contract["treatments"]}
    for name in ["configuration_rows", "internal_rows"]:
        rows = result.get(name)
        require(isinstance(rows, list) and len(rows) == 15
                and {(int(row.get("seed")), row.get("treatment")) for row in rows}
                == expected_rows, f"replication {name} matrix differs")
        for row in rows:
            expected_factor = (1.0 if row["treatment"]
                               == "single_assignment_control" else 2.0)
            require(row.get("query_count") == 76
                    and row.get("candidate_fraction") <= 0.005
                    and row.get("physical_storage_replication_factor")
                    == expected_factor and len(row.get("queries", [])) == 76,
                    f"replication {name} row differs")
    decision = result.get("decision", {})
    require(decision.get("privileged_per_query_kept_diagnostic") is True
            and decision.get("learned_reranker_used") is False
            and decision.get("native_confirmation_licensed") is False
            and decision.get("production_selection_licensed") is False,
            "replication decision differs")
    return mapping_map(mappings, mapping_root)


def write(args: argparse.Namespace) -> None:
    contract = runner.planner.load_contract(args.contract)
    original_bytes = args.result.read_bytes()
    result = json.loads(original_bytes)
    original_mappings = validate(result, contract, args.mapping_root)
    parent = json.loads(args.decoupled_evidence.read_text(encoding="utf-8"))
    require(parent.get("passed") is True
            and parent.get("authoritative_qrels_to_quality_replay_passed") is True,
            "replication authoritative parent differs")
    with tempfile.TemporaryDirectory(
            prefix="neuroute-replication-topology-evidence-") as directory:
        root = Path(directory)
        replay_args = argparse.Namespace(**vars(args))
        replay_args.output = root / "result.json"
        replay_args.mapping_root = root / "mappings"
        replay_args.result = None
        runner.run(replay_args)
        require(replay_args.output.read_bytes() == original_bytes,
                "replication result is not byte-replayable")
        replay = json.loads(replay_args.output.read_text(encoding="utf-8"))
        require(validate(replay, contract, replay_args.mapping_root)
                == original_mappings,
                "replication regenerated mapping SHA map differs")
    evidence = {
        "schema_version": 1,
        "family": "neuroute_replication_topology_evidence",
        "contract_sha256": runner.sha256(args.contract),
        "result_sha256": runner.sha256(args.result),
        "activation": contract["activation"],
        "source_files_sha256": {
            **result["source_files_sha256"],
            "write-neuroute-replication-topology-evidence.py":
                runner.sha256(Path(__file__)),
        },
        "matrix": result["matrix"],
        "mapping_artifacts": [{"file": name, "sha256": original_mappings[name]}
                              for name in sorted(original_mappings)],
        "authoritative_roots": parent["authoritative_roots"],
        "authoritative_qrels_to_quality_replay_passed": True,
        "document_level_secondary_assignments_validated": True,
        "training_only_fitted_assignment_validated": True,
        "unique_candidate_union_validated": True,
        "physical_storage_replication_validated": True,
        "mapping_sha_map_replay_passed": True,
        "result_byte_replay_passed": True,
        "privileged_per_query_kept_diagnostic": True,
        "decision": result["decision"],
        "native_confirmation_licensed": False,
        "production_selection_licensed": False,
        "passed": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(runner.canonical(evidence))


def self_test() -> None:
    contract = runner.planner.load_contract(
        THIS / "neuroute-replication-topology.example.json")
    require(runner.planner.plan(contract)["global_mapping_count"] == 9
            and runner.planner.plan(contract)["learned_model_fits"] == 0,
            "replication evidence self-test differs")
    print("NeuRoute replication-topology evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-replication-topology.example.json")
    for name in [
            "decoupled-result", "decoupled-evidence",
            "feasible-frontier-result", "feasible-frontier-evidence",
            "r3-summary-result", "r3-summary-evidence",
            "r3-summary-materialization-root", "matched-representation-result",
            "matched-representation-evidence", "ambiguity-result",
            "ambiguity-evidence", "nonlinear-result", "nonlinear-evidence",
            "prototype-gain-density-result", "prototype-gain-density-evidence",
            "multilingual-query-root", "width-materialization-root",
            "german-split-result", "de-1m-e5-root", "de-1m-input-root",
            "parent-cache-root", "decoupled-teacher-cache-root", "mapping-root",
            "result", "output"]:
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
            parser.error("all replication evidence paths are required")
        write(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError,
            MemoryError) as error:
        print(f"write-neuroute-replication-topology-evidence: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
