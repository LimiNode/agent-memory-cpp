#!/usr/bin/env python3
"""Validate and summarize the frozen nonlinear listwise reranker contract."""

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
    require(set(contract) == {
        "schema_version", "family", "claim_scope", "activation", "route",
        "query_partitions", "prototype_shortlist", "teacher", "models",
        "training", "cascade", "evaluation", "selection", "decision",
    }, "nonlinear listwise contract members differ")
    require(contract.get("schema_version") == 1
            and contract.get("family") == "neuroute_nonlinear_listwise_reranker",
            "nonlinear listwise contract family differs")
    require(contract.get("claim_scope")
            == "frozen_de_1m_k8_top1024_nonlinear_listwise_capacity_by_multilingual_training_density",
            "nonlinear listwise claim scope differs")
    require(contract["activation"] == {
        "prototype_gain_density_result_sha256":
            "665aad19510e6da74de1c5b840c2868f9978b85c2b46703475e0d29ee5878988",
        "prototype_gain_density_evidence_sha256":
            "cd29f1505c62379b54d748b65ab636ca96ac5830133e1bb50fb433fdc6a1f5d3",
        "multilingual_query_manifest_sha256":
            "fb9684f2c17ae4465cb04b308704c4d551a87d7516fdfecdf01a24b4f1a900c0",
        "width_materialization_sha256":
            "1e1e7f83072a8114f48e4018ab5744ccfa8cfe0fa445e19972a936c93c0d25b9",
        "german_split_result_sha256":
            "231482ff859a0a6506298f92a544f1697bf948275cb748008bf0c444f360d286",
    }, "nonlinear listwise activation differs")
    require(contract["route"] == {
        "dataset": "de-1m", "documents": 1000000, "width": 16,
        "seeds": [2026082701, 2026082702, 2026082703],
        "document_replication": 1,
        "document_addresses": "frozen_per_scale_from_width_materialization",
    }, "nonlinear listwise route differs")
    require(contract["query_partitions"] == {
        "base_training": {"source": "german_training_query_ids", "queries": 153},
        "additional_training": {"source": "miracl_es_fr_ru_train", "queries": 7988},
        "configuration": {
            "source": "german_configuration_selection_query_ids", "queries": 76},
        "internal_evaluation": {
            "source": "german_internal_evaluation_query_ids", "queries": 76},
        "training_then_configuration_then_internal_required": True,
    }, "nonlinear listwise query partitions differ")
    require(contract["prototype_shortlist"] == {
        "requested_prototypes_per_address": 8,
        "construction": "inherit_nested_mean_then_farthest_from_multi_prototype_parent",
        "address_score": "maximum_cosine_over_effective_prototypes",
        "address_shortlist": 1024,
        "retrieval": "exact_over_all_occupied_address_prototypes",
        "shortlist_frozen_before_reranking": True,
    }, "nonlinear listwise shortlist differs")
    require(contract["teacher"] == {
        "corpus": "de-1m", "retrieval": "exact_e5", "top_k": 10,
        "rank_discount": "inverse_log2_v1",
        "target": "discounted_address_gain_per_posting_entry",
        "computed_for_all_training_queries": True,
        "privileged_configuration_and_internal_teacher_is_diagnostic_only": True,
    }, "nonlinear listwise teacher differs")
    require(contract["models"] == {
        "variants": ["ridge_control", "pointwise_listnet",
                     "context_deepsets_listnet"],
        "ridge_alphas": [0.001, 0.01, 0.1, 1.0],
        "ridge_hard_negatives_per_positive": 32,
        "ridge_feature_basis": "parent_k8_query_address_features",
        "pointwise_feature_basis": "query_and_parent_k8_query_address_features",
        "context_feature_basis": "pointwise_features_plus_querywise_deepsets_context",
        "listwise_loss": "querywise_target_distribution_cross_entropy",
    }, "nonlinear listwise model matrix differs")
    require(contract["training"] == {
        "nested_query_counts": [153, 512, 2048, 4096, 8141],
        "nested_order": "german153_then_frozen_multilingual_manifest_order",
        "zero_target_policy": "exclude_from_loss_and_report",
        "torch_version_prefix": "2.8.0", "device": "cpu",
        "deterministic_algorithms": True, "torch_threads": 8,
        "local_hidden_dimensions": 32, "query_hidden_dimensions": 32,
        "context_hidden_dimensions": 32, "epochs": 4, "batch_queries": 8,
        "learning_rate": 0.003, "weight_decay": 0.0001, "score_scale": 8.0,
        "exact_query_batch_size": 16, "feature_query_batch_size": 8,
    }, "nonlinear listwise training differs")
    require(contract["cascade"] == {
        "oracle_k": 10, "hamming_limit": 768, "adc_limit": 64, "result_k": 10,
    }, "nonlinear listwise cascade differs")
    require(contract["evaluation"] == {
        "address_budgets": [128, 256, 512], "headline_address_budget": 256,
        "configuration_queries": 76, "internal_evaluation_queries": 76,
        "candidate_mass_target": 0.1,
        "prototype_order_control": True, "privileged_teacher_control": True,
    }, "nonlinear listwise evaluation differs")
    require(contract["selection"] == {
        "partition": "configuration", "per_seed_and_variant": True,
        "order": "highest_actionable_gain_at_256_then_lowest_candidate_fraction_then_smaller_training_count_then_lower_ridge_alpha",
        "internal_evaluation_must_remain_closed_until_selection": True,
    }, "nonlinear listwise selection differs")
    require(contract["decision"] == {
        "headline_address_budget": 256, "minimum_actionable_gain": 0.9,
        "maximum_candidate_fraction": 0.005,
        "minimum_prototype_to_teacher_gap_closed": 0.5,
        "maximum_candidate_fraction_ratio_vs_prototype_order": 1.05,
        "requires_every_de_1m_seed": True,
        "direct_and_progress_gates_are_alternatives": True,
        "teacher_objective_ablation_predeclared": True,
        "native_confirmation_forbidden": True,
        "production_selection_forbidden": True,
    }, "nonlinear listwise decision differs")
    return contract


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    seeds = len(contract["route"]["seeds"])
    variants = len(contract["models"]["variants"])
    sizes = len(contract["training"]["nested_query_counts"])
    budgets = len(contract["evaluation"]["address_budgets"])
    return {
        "trained_models": seeds * variants * sizes,
        "ridge_fits": seeds * sizes * len(contract["models"]["ridge_alphas"]),
        "neural_models": seeds * (variants - 1) * sizes,
        "configuration_rows": seeds * variants * sizes,
        "configuration_budget_measurements": seeds * variants * sizes * budgets,
        "selected_models": seeds * variants,
        "internal_rows": seeds * (variants + 2),
        "internal_budget_measurements": seeds * (variants + 2) * budgets,
        "native_rows": 0,
        "training_query_counts": contract["training"]["nested_query_counts"],
        "headline_address_budget": contract["evaluation"]["headline_address_budget"],
    }


def self_test() -> None:
    contract = load_contract(THIS / "neuroute-nonlinear-listwise-reranker.example.json")
    require(plan(contract) == {
        "trained_models": 45,
        "ridge_fits": 60,
        "neural_models": 30,
        "configuration_rows": 45,
        "configuration_budget_measurements": 135,
        "selected_models": 9,
        "internal_rows": 15,
        "internal_budget_measurements": 45,
        "native_rows": 0,
        "training_query_counts": [153, 512, 2048, 4096, 8141],
        "headline_address_budget": 256,
    }, "nonlinear listwise plan self-test differs")
    print("NeuRoute nonlinear listwise reranker planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-nonlinear-listwise-reranker.example.json")
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
        print(f"plan-neuroute-nonlinear-listwise-reranker: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
