#!/usr/bin/env python3
"""Validate the frozen NeuRoute replication-topology diagnostic."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1
            and value.get("family") == "neuroute_replication_topology",
            "replication-topology family differs")
    require(value["route"] == {
        "dataset": "de-1m", "documents": 1000000, "width": 16,
        "seeds": [2026082701, 2026082702, 2026082703],
        "prototypes_per_address": 8, "candidate_fraction_budget": 0.005},
        "replication-topology route differs")
    require(value["treatments"] == [
        "single_assignment_control", "nearest_semantic_secondary",
        "soar_complementary_secondary", "training_fitted_complementary",
        "privileged_per_query_replication_ceiling"],
        "replication-topology treatment matrix differs")
    assignment = value["assignment"]
    require(assignment["deployable_secondary_assignments_are_global"] is True
            and assignment["secondary_assignment_granularity"] == "document"
            and assignment["training_fitted_uses_training_queries_only"] is True
            and assignment["teacher_aware_k8_forbidden"] is True
            and assignment["privileged_per_query_is_diagnostic_only"] is True,
            "replication-topology assignment boundary differs")
    require(value["storage"]["raw_posting_count_control"] == 1000000
            and value["storage"]["raw_posting_count_replicated"] == 2000000,
            "replication-topology storage contract differs")
    require(value["decision"]["learned_reranker_forbidden"] is True
            and value["decision"]["native_confirmation_forbidden"] is True
            and value["decision"]["production_selection_forbidden"] is True,
            "replication-topology activation boundary differs")
    require(all(isinstance(digest, str) and len(digest) == 64
                for digest in value["activation"].values()),
            "replication-topology activation hashes differ")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    seeds = contract["route"]["seeds"]
    treatments = contract["treatments"]
    return {
        "dataset": "de-1m",
        "seed_count": len(seeds),
        "treatments": treatments,
        "global_mapping_count": len(seeds) * 3,
        "rebuilt_k8_topologies": len(seeds) * 3,
        "configuration_rows": len(seeds) * len(treatments),
        "internal_rows": len(seeds) * len(treatments),
        "learned_model_fits": 0,
        "teacher_aware_k8": False,
        "privileged_deployable": False,
    }


def self_test() -> None:
    current = plan(load_contract(Path(__file__).with_name(
        "neuroute-replication-topology.example.json")))
    require(current["global_mapping_count"] == 9
            and current["rebuilt_k8_topologies"] == 9
            and current["configuration_rows"] == 15
            and current["internal_rows"] == 15
            and current["learned_model_fits"] == 0,
            "replication-topology planner self-test differs")
    print("NeuRoute replication-topology planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).with_name(
        "neuroute-replication-topology.example.json"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
        else:
            print(json.dumps(plan(load_contract(args.contract)), indent=2,
                             sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-replication-topology: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
