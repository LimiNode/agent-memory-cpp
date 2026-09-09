#!/usr/bin/env python3
"""Validate and summarize the multi-prototype address frontier contract."""

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
            and contract.get("family") == "neuroute_address_multi_prototype_frontier",
            "multi-prototype contract family differs")
    require(set(contract) == {
        "schema_version", "family", "claim_scope", "activation", "route",
        "partitions", "scales", "prototypes", "diagnostic", "cascade",
        "selection", "decision",
    }, "multi-prototype contract members differ")
    require(all(isinstance(value, str) and len(value) == 64
                for value in contract["activation"].values()),
            "multi-prototype activation hashes differ")
    require(contract["route"] == {
        "width": 16,
        "seeds": [2026082701, 2026082702, 2026082703],
        "document_replication": 1,
        "document_addresses": "frozen_per_scale_from_width_materialization",
    }, "multi-prototype route differs")
    require(contract["partitions"] == {
        "configuration": {"source": "configuration_selection_query_ids", "queries": 76},
        "internal_evaluation": {"source": "internal_evaluation_query_ids", "queries": 76},
        "selection_must_precede_internal_evaluation": True,
    }, "multi-prototype partitions differ")
    require([row["id"] for row in contract["scales"]]
            == ["de-25k", "de-100k", "de-1m"],
            "multi-prototype scale order differs")
    require(contract["prototypes"] == {
        "counts": [1, 2, 4, 8],
        "construction": "nested_normalized_mean_then_farthest_frozen_member",
        "effective_count": "minimum_of_requested_count_and_posting_count",
        "member_selection": "minimize_maximum_cosine_to_existing_prefix",
        "members_are_unique": True,
        "farthest_tie_break": "lowest_frozen_document_position",
        "address_score": "maximum_cosine_over_prefix_prototypes",
        "posting_cost_normalization": "none_to_isolate_prototype_capacity",
    }, "multi-prototype treatments differ")
    require(contract["diagnostic"]["global_address_budgets"] == [128, 256, 512, 1024]
            and contract["diagnostic"]["coverage_targets"] == [0.5, 0.75, 0.9, 0.95]
            and contract["diagnostic"]["candidate_mass_target"] == 0.1
            and contract["diagnostic"]["hard_negative_pool"] == 1024,
            "multi-prototype diagnostic matrix differs")
    require(contract["cascade"] == {
        "oracle_k": 10, "hamming_limit": 768, "adc_limit": 64, "result_k": 10,
    }, "multi-prototype cascade differs")
    require(contract["selection"]["partition"] == "configuration"
            and contract["decision"][
                "multimodality_minimum_internal_gain_at_256_improvement"] == 0.03
            and contract["decision"][
                "multimodality_minimum_supporting_de_1m_seeds"] == 2
            and contract["decision"]["production_selection_forbidden"] is True,
            "multi-prototype selection or decision differs")
    return contract


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    datasets = len(contract["scales"])
    seeds = len(contract["route"]["seeds"])
    treatments = len(contract["prototypes"]["counts"])
    partitions = 2
    return {
        "dataset_count": datasets,
        "seeds_per_dataset": seeds,
        "prototype_counts_per_seed": treatments,
        "partitions_per_treatment": partitions,
        "rows_per_dataset": seeds * treatments * partitions,
        "total_rows": datasets * seeds * treatments * partitions,
        "queries_per_row": 76,
        "address_budgets": contract["diagnostic"]["global_address_budgets"],
    }


def self_test() -> None:
    contract = load_contract(THIS / "neuroute-address-multi-prototype.example.json")
    require(plan(contract) == {
        "dataset_count": 3,
        "seeds_per_dataset": 3,
        "prototype_counts_per_seed": 4,
        "partitions_per_treatment": 2,
        "rows_per_dataset": 24,
        "total_rows": 72,
        "queries_per_row": 76,
        "address_budgets": [128, 256, 512, 1024],
    }, "multi-prototype plan self-test differs")
    print("NeuRoute address multi-prototype planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-address-multi-prototype.example.json")
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
        print(f"plan-neuroute-address-multi-prototype: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
