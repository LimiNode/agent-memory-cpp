#!/usr/bin/env python3
"""Validate the narrow pre-registered NeuRoute-inspired v2 matrix."""

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
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == "neuroute_inspired_semantic_address_v2",
            "NeuRoute-inspired v2 contract family differs")
    require(value.get("dataset") == "frozen_es_25k_v1" and value.get("predecessor") == "shared_learned_semantic_address_encoder_v2",
            "NeuRoute-inspired v2 dataset differs")
    partitions = value.get("partitions", {})
    require(partitions == {"source": "direct_learned_semantic_address_splits_v1", "training_queries": 324,
                           "configuration_selection_queries": 162, "internal_evaluation_queries": 162,
                           "internal_evaluation_may_not_select": True}, "NeuRoute-inspired v2 partitions differ")
    encoder = value.get("encoder", {})
    require(encoder.get("input_dimensions") == 384 and encoder.get("hidden_dimensions") == [96, 64]
            and encoder.get("bits") == [12, 16, 20, 24] and encoder.get("seeds") == [2026082601, 2026082602, 2026082603]
            and encoder.get("epochs") == 80 and encoder.get("batch_size") == 512 and encoder.get("learning_rate") == 0.001
            and encoder.get("weight_decay") == 0.0001 and encoder.get("torch_threads") == 18,
            "NeuRoute-inspired v2 encoder contract differs")
    mining = value.get("pair_mining", {})
    require(mining == {"document_neighbours": 16, "training_query_document_neighbours": 10,
                       "document_pair_slot": "epoch_modulo_document_neighbours_v1",
                       "query_pair_slot": "epoch_modulo_training_query_document_neighbours_v1",
                       "training_query_batch_size": 128,
                       "source_similarity": "frozen_e5_cosine_with_fail_closed_l2_audit_v2",
                       "selected_pair_loss": "masked_source_cosine_to_normalized_latent_cosine_mse_v2"},
            "NeuRoute-inspired v2 pair mining differs")
    losses = value.get("losses", {})
    require(losses == {"full": {"geometry": 1.0, "variance": 0.01, "covariance": 0.01, "minimum_latent_standard_deviation": 0.05},
                       "no_covariance_ablation": {"geometry": 1.0, "variance": 0.01, "covariance": 0.0,
                                                   "minimum_latent_standard_deviation": 0.05, "bits": [16]}},
            "NeuRoute-inspired v2 losses differ")
    routing = value.get("routing", {})
    require(routing == {"document_placement": "median_threshold_single_address_v2",
                        "query_orders": ["independent_logit_best_first_v2", "hard_code_hamming_order_control_v2"],
                        "maximum_probes": [16, 32, 64, 128, 256], "candidate_mass_target": 0.1,
                        "selection_partition": "configuration_selection"},
            "NeuRoute-inspired v2 routing differs")
    require(value.get("cascade") == {"oracle_k": 10, "hamming_limit": 768, "adc_limit": 256, "exact_limit": 256},
            "NeuRoute-inspired v2 cascade differs")
    require(value.get("success_gate", {}).get("candidate_fraction_at_most") == 0.1
            and value["success_gate"].get("minimum_adc_survival_absolute_gain") == 0.03
            and value["success_gate"].get("maximum_ndcg_at_10_absolute_loss") == 0.01
            and value["success_gate"].get("bootstrap_resamples") == 10000
            and value["success_gate"].get("bootstrap_seed") == 2026082699,
            "NeuRoute-inspired v2 success gate differs")
    return value


def plan(contract: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for bits in contract["encoder"]["bits"]:
        for seed in contract["encoder"]["seeds"]:
            result.append({"loss": "full", "bits": bits, "seed": seed})
    for seed in contract["encoder"]["seeds"]:
        result.append({"loss": "no_covariance_ablation", "bits": 16, "seed": seed})
    require(len(result) == 15 and len({(row["loss"], row["bits"], row["seed"]) for row in result}) == len(result),
            "NeuRoute-inspired v2 plan differs")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "neuroute-inspired-semantic-address-v2.example.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        rows = plan(load_contract(args.contract))
        output = json.dumps({"schema_version": 1, "family": "neuroute_inspired_semantic_address_v2_plan", "rows": rows}, indent=2, sort_keys=True) + "\n"
        if args.output is None:
            print(output, end="")
        else:
            args.output.write_text(output, encoding="utf-8")
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-inspired-semantic-address-v2: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
