#!/usr/bin/env python3
"""Validate and summarize the prototype gain-density reranker contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    require(contract.get("schema_version") == 1
            and contract.get("family") == "neuroute_prototype_gain_density_reranker",
            "prototype gain-density contract family differs")
    require(set(contract) == {
        "schema_version", "family", "claim_scope", "activation", "route",
        "partitions", "scales", "prototype_shortlist", "model", "treatments",
        "diagnostic", "cascade", "selection", "decision",
    }, "prototype gain-density contract members differ")
    require(all(isinstance(value, str) and len(value) == 64
                for value in contract["activation"].values()),
            "prototype gain-density activation hashes differ")
    require(contract["route"] == {
        "width": 16,
        "seeds": [2026082701, 2026082702, 2026082703],
        "document_replication": 1,
        "document_addresses": "frozen_per_scale_from_width_materialization",
    }, "prototype gain-density route differs")
    require(contract["partitions"] == {
        "training": {"source": "training_query_ids", "queries": 153},
        "configuration": {"source": "configuration_selection_query_ids", "queries": 76},
        "internal_evaluation": {"source": "internal_evaluation_query_ids", "queries": 76},
        "training_then_configuration_then_internal_required": True,
    }, "prototype gain-density partitions differ")
    require([row["id"] for row in contract["scales"]]
            == ["de-25k", "de-100k", "de-1m"],
            "prototype gain-density scale order differs")
    require(contract["prototype_shortlist"] == {
        "requested_prototypes_per_address": 8,
        "construction": "inherit_nested_mean_then_farthest_from_multi_prototype_parent",
        "address_score": "maximum_cosine_over_effective_prototypes",
        "training_shortlist": 1024,
        "configuration_shortlists": [512, 1024],
    }, "prototype gain-density shortlist differs")
    require(contract["model"]["hard_negatives_per_positive"] == 32
            and contract["model"]["ridge_alphas"] == [0.001, 0.01, 0.1, 1.0]
            and contract["model"]["training_queries_only"] is True,
            "prototype gain-density model differs")
    require(contract["treatments"] == [
        "prototype_score", "posting_cost_heuristic",
        "learned_pairwise_gain_density", "privileged_gain_density_teacher",
        "privileged_gain_density_teacher_maximum_shortlist",
    ], "prototype gain-density treatments differ")
    require(contract["diagnostic"]["address_budgets"] == [128, 256, 512, 1024]
            and contract["diagnostic"]["selection_address_budget"] == 256
            and contract["diagnostic"]["privileged_teacher_is_diagnostic_only"] is True,
            "prototype gain-density diagnostic differs")
    require(contract["cascade"] == {
        "oracle_k": 10, "hamming_limit": 768, "adc_limit": 64, "result_k": 10,
    }, "prototype gain-density cascade differs")
    require(contract["selection"]["partition"] == "configuration"
            and contract["decision"]["production_selection_forbidden"] is True,
            "prototype gain-density selection differs")
    return contract


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    datasets = len(contract["scales"])
    seeds = len(contract["route"]["seeds"])
    calibrations = (len(contract["prototype_shortlist"]["configuration_shortlists"])
                    * len(contract["model"]["ridge_alphas"]))
    treatments = len(contract["treatments"])
    return {
        "dataset_count": datasets,
        "seeds_per_dataset": seeds,
        "calibration_rows_per_seed": calibrations,
        "total_calibration_rows": datasets * seeds * calibrations,
        "internal_treatments_per_seed": treatments,
        "total_internal_rows": datasets * seeds * treatments,
        "training_queries_per_model": contract["partitions"]["training"]["queries"],
        "configuration_queries_per_row": contract["partitions"]["configuration"]["queries"],
        "internal_queries_per_row": contract["partitions"]["internal_evaluation"]["queries"],
        "address_budgets": contract["diagnostic"]["address_budgets"],
    }


def self_test() -> None:
    contract = load_contract(THIS / "neuroute-prototype-gain-density-reranker.example.json")
    require(plan(contract) == {
        "dataset_count": 3,
        "seeds_per_dataset": 3,
        "calibration_rows_per_seed": 8,
        "total_calibration_rows": 72,
        "internal_treatments_per_seed": 5,
        "total_internal_rows": 45,
        "training_queries_per_model": 153,
        "configuration_queries_per_row": 76,
        "internal_queries_per_row": 76,
        "address_budgets": [128, 256, 512, 1024],
    }, "prototype gain-density plan self-test differs")
    print("NeuRoute prototype gain-density planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-prototype-gain-density-reranker.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract)
        if args.self_test:
            self_test()
        else:
            print(json.dumps(plan(contract), indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-prototype-gain-density-reranker: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
