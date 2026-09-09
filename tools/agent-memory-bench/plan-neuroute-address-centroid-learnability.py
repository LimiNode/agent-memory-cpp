#!/usr/bin/env python3
"""Validate and summarize the address-centroid learnability contract."""

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
            and contract.get("family") == "neuroute_address_centroid_learnability",
            "address-centroid contract family differs")
    require(set(contract) == {
        "schema_version", "family", "claim_scope", "activation", "route",
        "partition", "scales", "prototype", "diagnostic", "cascade", "decision",
    }, "address-centroid contract members differ")
    require(all(isinstance(value, str) and len(value) == 64
                for value in contract["activation"].values()),
            "address-centroid activation hashes differ")
    require(contract["route"] == {
        "width": 16,
        "seeds": [2026082701, 2026082702, 2026082703],
        "document_replication": 1,
        "document_addresses": "frozen_per_scale_from_width_materialization",
    }, "address-centroid route differs")
    require(contract["partition"] == {
        "source": "configuration_selection_query_ids",
        "queries": 76,
        "internal_evaluation_partition_forbidden": True,
    }, "address-centroid partition differs")
    require([row["id"] for row in contract["scales"]]
            == ["de-25k", "de-100k", "de-1m"],
            "address-centroid scale order differs")
    require(contract["prototype"] == {
        "kind": "normalized_mean_e5_document_centroid",
        "prototypes_per_address": 1,
        "score": "cosine_divided_by_posting_count_power_alpha",
        "cost_alphas": [0.0, 0.25, 0.5, 0.75, 1.0],
    }, "address-centroid prototype matrix differs")
    require(contract["diagnostic"]["hard_negative_pool"] == 1024
            and contract["diagnostic"]["global_address_budgets"] == [256, 512, 1024]
            and contract["diagnostic"]["coverage_targets"] == [0.5, 0.75, 0.9, 0.95]
            and contract["diagnostic"]["candidate_mass_target"] == 0.1,
            "address-centroid diagnostic matrix differs")
    require(contract["cascade"] == {
        "oracle_k": 10, "hamming_limit": 768, "adc_limit": 64, "result_k": 10,
    }, "address-centroid cascade differs")
    require(contract["decision"]["multi_prototype_followup_predeclared"] is True
            and contract["decision"]["internal_evaluation_forbidden"] is True
            and contract["decision"]["production_selection_forbidden"] is True,
            "address-centroid decision differs")
    return contract


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    datasets = len(contract["scales"])
    seeds = len(contract["route"]["seeds"])
    treatments = len(contract["prototype"]["cost_alphas"])
    return {
        "dataset_count": datasets,
        "seeds_per_dataset": seeds,
        "treatments_per_seed": treatments,
        "rows_per_dataset": seeds * treatments,
        "total_rows": datasets * seeds * treatments,
        "queries_per_row": contract["partition"]["queries"],
        "diagnostic_tasks": contract["diagnostic"]["tasks"],
        "internal_evaluation_expected": False,
    }


def self_test() -> None:
    contract = load_contract(THIS / "neuroute-address-centroid-learnability.example.json")
    result = plan(contract)
    require(result["dataset_count"] == 3 and result["seeds_per_dataset"] == 3
            and result["treatments_per_seed"] == 5 and result["total_rows"] == 45
            and result["queries_per_row"] == 76
            and result["internal_evaluation_expected"] is False,
            "address-centroid plan self-test differs")
    print("NeuRoute address-centroid learnability planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-address-centroid-learnability.example.json")
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
        print(f"plan-neuroute-address-centroid-learnability: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
