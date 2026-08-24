#!/usr/bin/env python3
"""Validate the data-dependent product-locator calibration plan."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
FAMILY = "data_dependent_product_locator_v1"


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY and value.get("purpose") == "external_calibration_only_data_dependent_product_locator_before_native_traversal_engineering", "data-dependent product locator contract identity differs")
    require(value.get("training_source") == "frozen_train_itq256_codes_only" and value.get("implicit_cell_budgets") == [4096, 16384, 65536] and value.get("target_candidate_fractions") == [0.05, 0.10, 0.25], "data-dependent product locator matrix differs")
    require(value.get("scales") == [{"id": "es-100k", "documents": 100000, "input_manifest_sha256": "720fead487f3a7caec62ad190cd93fa79969effd1d0fe825c865ab5d0d437d15", "evaluation_manifest_sha256": "1e89e90596ca8dbf6ea87c4a3f4ff78775d3f2841395bef883bb727539c4e112"}, {"id": "es-1m", "documents": 1000000, "input_manifest_sha256": "697f81bc66b37feb47b413fa168f4ae5efd030b9dbbaeb8d0c67ac8d224a9ae7", "evaluation_manifest_sha256": "616e70a3d0e21a561967b382bebc463b10038280f02ca77477b0b01331c73536"}], "data-dependent product locator scales differ")
    require(value.get("treatments") == [{"id": "local_binary_medoids", "bit_decomposition": "contiguous_balanced_blocks_v1", "local_codebook": "train_only_hamming_kmedoids_v1"}, {"id": "permuted_binary_medoids", "bit_decomposition": "train_only_bit_permutation_balanced_blocks_v1", "local_codebook": "train_only_hamming_kmedoids_v1"}, {"id": "pre_itq_float_product", "bit_decomposition": "contiguous_balanced_blocks_v1", "local_codebook": "train_only_float_kmeans_on_frozen_pre_itq_projections_v1"}], "data-dependent product locator treatments differ")
    require(value.get("routing") == "best_first_sum_local_hamming_then_cell_key_lexicographic_v1" and value.get("cascade") == {"hamming_limit": 768, "adc_limit": 256, "exact_limit": 256, "oracle_k": 10} and value.get("exploratory_gate") == {"candidate_fraction": 0.05, "minimum_e5_oracle_survival_after_adc": 0.70, "meaning": "below_this_gate_do_not_add_native_empty_cell_or_trie_engineering"} and value.get("confirmation") == "forbidden" and value.get("library_dependency") == "forbidden_external_research_only", "data-dependent product locator scope differs")
    return value


def plan(contract: dict[str, Any], contract_path: Path) -> dict[str, Any]:
    rows = [{"scale": scale["id"], "treatment": treatment["id"], "implicit_cell_budget": budget, "target_candidate_fraction": fraction} for scale in contract["scales"] for treatment in contract["treatments"] for budget in contract["implicit_cell_budgets"] for fraction in contract["target_candidate_fractions"]]
    return {"schema_version": 1, "family": "data_dependent_product_locator_plan_v1", "contract_sha256": sha256(contract_path), "row_count": len(rows), "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "data-dependent-product-locator.example.json"); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        result = plan(load_contract(args.contract), args.contract)
        require(result["row_count"] == 54, "data-dependent product locator row count differs")
        print("data-dependent product locator planner self-test passed" if args.self_test else json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-data-dependent-product-locator: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
