#!/usr/bin/env python3
"""Validate the frozen configuration-only NeuRoute training sanity study."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
TREATMENTS = (
    "v3_cosine_dynamic_frozen_control",
    "raw_euclidean_mined_pairs",
    "dual_mask_euclidean",
    "dual_mask_euclidean_bn",
    "dual_mask_euclidean_bn_query_mining",
)


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == "neuroute_training_sanity_config_only",
            "training sanity identity differs")
    require(value.get("claim_scope") == "observed_languages_configuration_only_no_confirmation",
            "training sanity claim scope differs")
    activation = value.get("activation", {})
    require(all(isinstance(activation.get(name), str) and len(activation[name]) == 64
                for name in ("alignment_audit_result_sha256", "alignment_audit_evidence_sha256")),
            "training sanity activation differs")
    datasets = value.get("datasets", [])
    expected = [("de-25k", "de", 305, 153, 76), ("fr-25k", "fr", 343, 172, 85),
                ("ja-25k", "ja", 860, 430, 215)]
    require([(row.get("id"), row.get("language"), row.get("queries"), row.get("training_queries"),
              row.get("configuration_queries")) for row in datasets] == expected,
            "training sanity datasets differ")
    require(all(isinstance(row.get("result_sha256"), str) and len(row["result_sha256"]) == 64 for row in datasets),
            "training sanity result binding differs")
    require(value.get("encoder") == {"input_dimensions": 384, "hidden_dimensions": [96, 64], "bits": 12,
                                     "seeds": [2026082701, 2026082702, 2026082703], "epochs": 80,
                                     "batch_size": 512, "training_query_batch_size": 128,
                                     "pairwise_subbatch": 128, "learning_rate": 0.001,
                                     "weight_decay": 0.0001, "optimizer": "AdamW", "torch_threads": 18,
                                     "batch_norm_epsilon": 0.00001, "batch_norm_momentum": 0.1},
            "training sanity encoder differs")
    require(value.get("distance_objective") == {"metric": "euclidean", "gamma": 0.6,
                                                "dimension_scale": "sqrt_latent_over_input_v1",
                                                "source_mask_quantile": 0.005, "diagonal_excluded": True,
                                                "query_positive_weight": 1.0},
            "training sanity distance objective differs")
    require(value.get("mining") == {"remine_epochs": [20, 40, 60], "latent_neighbour_pool": 32,
                                    "selected_e5_farthest": 4, "document_false_positive_weight": 1.0,
                                    "query_false_positive_weight": 1.0,
                                    "latent_metric": "normalized_cosine_for_mined_pairs_v1"},
            "training sanity mining differs")
    treatments = value.get("treatments", [])
    expected_treatments = [
        {"id": "v3_cosine_dynamic_frozen_control", "source": "reuse_frozen_v3_dynamic_model_bytes",
         "loss": "normalized_cosine_positive_plus_mined_document_false_positive_v3",
         "batch_norm": False, "query_false_positive_mining": False},
        {"id": "raw_euclidean_mined_pairs", "source": "train",
         "loss": "scaled_raw_euclidean_on_v3_positive_and_mined_document_pairs_v1",
         "batch_norm": False, "query_false_positive_mining": False},
        {"id": "dual_mask_euclidean", "source": "train",
         "loss": "scaled_raw_euclidean_source_or_latent_near_dual_mask_v1",
         "batch_norm": False, "query_false_positive_mining": False},
        {"id": "dual_mask_euclidean_bn", "source": "train",
         "loss": "scaled_raw_euclidean_source_or_latent_near_dual_mask_v1",
         "batch_norm": True, "query_false_positive_mining": False},
        {"id": "dual_mask_euclidean_bn_query_mining", "source": "train",
         "loss": "scaled_raw_euclidean_source_or_latent_near_dual_mask_v1",
         "batch_norm": True, "query_false_positive_mining": True},
    ]
    require(treatments == expected_treatments and tuple(row["id"] for row in treatments) == TREATMENTS,
            "training sanity treatments differ")
    require(value.get("routing") == {"probe_budgets": [16, 32, 64, 128, 256, 512],
                                     "candidate_mass_target": 0.1, "pca_bits": 8, "pca_probes": 16,
                                     "pca_replication": 4}, "training sanity routing differs")
    require(value.get("cascade") == {"oracle_k": 10, "hamming_limit": 768, "adc_limit": 256,
                                     "exact_limit": 256}, "training sanity cascade differs")
    decision = value.get("decision", {})
    require(decision == {"maximum_candidate_fraction": 0.1, "maximum_probe_budget_for_efficiency": 128,
                         "maximum_adc_survival_loss_vs_v3_512": 0.02,
                         "maximum_ndcg_loss_vs_v3_512": 0.01, "maximum_ndcg_loss_vs_pca": 0.01,
                         "requires_all_languages": True,
                         "selection_order": "lowest_maximum_probe_then_highest_cross_language_mean_ndcg_then_treatment_id_v1",
                         "next_if_none": "preregister_relevance_aware_v4",
                         "next_if_pass": "freeze_on_new_external_confirmation_before_scale"},
            "training sanity decision differs")
    return value


def matrix(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"dataset": dataset["id"], "treatment": treatment["id"], "seed": seed, "probes": probes}
            for dataset in contract["datasets"] for treatment in contract["treatments"]
            for seed in contract["encoder"]["seeds"] for probes in contract["routing"]["probe_budgets"]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "neuroute-training-sanity.example.json")
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract)
        print(json.dumps({"family": contract["family"], "model_count": 45,
                          "trained_model_count": 36, "quality_row_count": len(matrix(contract)),
                          "claim_scope": contract["claim_scope"]}, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-training-sanity: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
