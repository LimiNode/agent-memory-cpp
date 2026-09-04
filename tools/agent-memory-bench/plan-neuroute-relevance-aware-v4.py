#!/usr/bin/env python3
"""Validate the frozen relevance-aware NeuRoute v4 configuration study."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
TREATMENTS = (
    "raw_euclidean_document_mining_control",
    "raw_euclidean_query_document_mining",
    "raw_euclidean_relevance_ranking",
)


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1
            and value.get("family") == "neuroute_relevance_aware_v4_config_only",
            "relevance-aware v4 identity differs")
    require(value.get("claim_scope") ==
            "observed_languages_configuration_only_no_confirmation_no_scale_transfer",
            "relevance-aware v4 claim scope differs")
    activation = value.get("activation", {})
    required_hashes = (
        "training_sanity_result_sha256", "training_sanity_evidence_sha256",
        "training_sanity_model_set_sha256", "native_cost_result_sha256",
        "native_cost_evidence_sha256", "native_cost_materialization_sha256",
    )
    require(all(isinstance(activation.get(name), str) and len(activation[name]) == 64
                for name in required_hashes), "relevance-aware v4 activation differs")
    expected_datasets = [
        ("de-25k", "de", 305, 153, 76, 427),
        ("fr-25k", "fr", 343, 172, 85, 363),
        ("ja-25k", "ja", 860, 430, 215, 893),
    ]
    datasets = value.get("datasets", [])
    require([(row.get("id"), row.get("language"), row.get("queries"),
              row.get("training_queries"), row.get("configuration_queries"),
              row.get("training_relevant_pairs")) for row in datasets] == expected_datasets,
            "relevance-aware v4 datasets differ")
    require(all(isinstance(row.get("result_sha256"), str) and len(row["result_sha256"]) == 64
                for row in datasets), "relevance-aware v4 dataset binding differs")
    require(value.get("encoder") == {
        "input_dimensions": 384, "hidden_dimensions": [96, 64], "bits": 12,
        "seeds": [2026082701, 2026082702, 2026082703], "epochs": 80,
        "batch_size": 512, "training_query_batch_size": 128,
        "learning_rate": 0.001, "weight_decay": 0.0001, "optimizer": "AdamW",
        "torch_threads": 18, "batch_norm": False,
    }, "relevance-aware v4 encoder differs")
    require(value.get("distance_objective") == {
        "metric": "euclidean", "gamma": 0.6,
        "dimension_scale": "sqrt_latent_over_input_v1", "query_positive_weight": 1.0,
    }, "relevance-aware v4 distance objective differs")
    require(value.get("mining") == {
        "document_remine_epochs": [20, 40, 60], "query_remine_epochs": [20, 40, 60],
        "latent_neighbour_pool": 32, "selected_e5_farthest": 4,
        "document_false_positive_weight": 1.0, "query_false_positive_weight": 1.0,
        "latent_metric": "normalized_cosine_for_mined_pairs_v1",
    }, "relevance-aware v4 mining differs")
    require(value.get("diversity") == {
        "variance_weight": 0.01, "covariance_weight": 0.01,
        "minimum_latent_standard_deviation": 0.05,
    }, "relevance-aware v4 diversity differs")
    require(value.get("relevance_objective") == {
        "training_labels": "training_partition_qrels_grade_gt_zero_only",
        "configuration_labels_forbidden": True,
        "positive_sampling": "grade_descending_then_document_id_epoch_rotation_v1",
        "negative_exclusion": "all_training_qrels_grade_gt_zero",
        "negative_remine_epochs": [0, 20, 40, 60], "negative_pool": 32,
        "selected_negatives": 4,
        "address_normalization": "detached_document_median_and_standard_deviation_v1",
        "soft_mismatch": "mean_abs_query_times_sigmoid_negative_product_v1",
        "soft_mismatch_temperature": 0.5, "pairwise_ranking_margin": 0.1,
        "pairwise_ranking_temperature": 0.1, "ranking_weight": 0.25,
    }, "relevance-aware v4 ranking objective differs")
    expected_treatments = [
        {"id": "raw_euclidean_document_mining_control",
         "source": "reuse_frozen_training_sanity_raw_euclidean_model_bytes",
         "document_false_positive_mining": True, "query_false_positive_mining": False,
         "relevance_ranking": False, "batch_norm": False, "dual_mask": False},
        {"id": "raw_euclidean_query_document_mining", "source": "train",
         "document_false_positive_mining": True, "query_false_positive_mining": True,
         "relevance_ranking": False, "batch_norm": False, "dual_mask": False},
        {"id": "raw_euclidean_relevance_ranking", "source": "train",
         "document_false_positive_mining": True, "query_false_positive_mining": False,
         "relevance_ranking": True, "batch_norm": False, "dual_mask": False},
    ]
    treatments = value.get("treatments", [])
    require(treatments == expected_treatments
            and tuple(row["id"] for row in treatments) == TREATMENTS,
            "relevance-aware v4 treatments differ")
    require(value.get("routing") == {
        "probe_budgets": [64, 128, 256, 512], "primary_probe_budget": 256,
        "candidate_mass_target": 0.1, "pca_bits": 8, "pca_probes": 16,
        "pca_replication": 4,
    }, "relevance-aware v4 routing differs")
    require(value.get("cascade") == {
        "oracle_k": 10, "hamming_limit": 768, "adc_limit": 256, "exact_limit": 256,
    }, "relevance-aware v4 cascade differs")
    require(value.get("storage") == {
        "backend": "libmdbx_via_mdbx_containers_key_value_table",
        "table": "neuroute_postings",
        "key_layout": "route_u8_address_u16be_kind_u8_page_u32be_v1",
        "posting_encoding": "packed_u32le", "page_entries": 256,
        "transaction_scope": "one_read_only_transaction_per_query",
        "directory_entry": "u32le_posting_count_and_u32le_page_count",
    }, "relevance-aware v4 storage differs")
    require(value.get("native_timing") == {
        "database": "repository_pinned_mdbx", "cache_state": "warm",
        "warmup_passes": 2, "measured_passes": 9,
        "stages": ["address_generation", "mdbx_lookup_and_decode",
                   "generation_array_dedup_and_ceiling", "hamming_and_top_k",
                   "binary_adc_and_top_k", "total"],
        "native_python_sequence_replay_required": True,
    }, "relevance-aware v4 native timing differs")
    require(value.get("decision") == {
        "primary_probe_budget": 256, "maximum_candidate_fraction": 0.1,
        "maximum_native_p95_ratio_vs_pca": 1.15,
        "minimum_cross_language_mean_ndcg_gain_vs_control": 0.01,
        "maximum_per_language_ndcg_loss_vs_control": 0.01,
        "requires_all_languages": True,
        "selection_order": "highest_cross_language_mean_ndcg_then_lowest_cross_language_mean_native_p95_then_treatment_id_v1",
        "next_if_none": "diagnose_relevance_objective_before_external_confirmation",
        "next_if_pass": "freeze_selected_recipe_for_new_external_confirmation_before_scale",
    }, "relevance-aware v4 decision differs")
    return value


def matrix(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"dataset": dataset["id"], "treatment": treatment["id"], "seed": seed,
             "probes": probes}
            for dataset in contract["datasets"] for treatment in contract["treatments"]
            for seed in contract["encoder"]["seeds"] for probes in contract["routing"]["probe_budgets"]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-relevance-aware-v4.example.json")
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract)
        print(json.dumps({
            "family": contract["family"], "model_count": 27, "trained_model_count": 18,
            "quality_row_count": len(matrix(contract)), "native_timing_row_count": len(matrix(contract)) + 3,
            "claim_scope": contract["claim_scope"],
        }, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-relevance-aware-v4: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
