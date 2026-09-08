#!/usr/bin/env python3
"""Validate the frozen Japanese third confirmation of NeuRoute v3."""
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
    require(value.get("schema_version") == 1 and value.get("family") == "neuroute_v3_third_external_confirmation", "Japanese family differs")
    dataset = value.get("dataset", {})
    require(dataset.get("id") == "frozen_ja_25k_v1" and dataset.get("language") == "ja" and dataset.get("documents") == 25000 and dataset.get("queries") == 860 and dataset.get("dimension") == 384 and all(isinstance(dataset.get(key), str) and len(dataset[key]) == 64 for key in ("prepared_manifest_sha256", "e5_manifest_sha256", "input_manifest_sha256")), "Japanese dataset differs")
    require(value.get("partitions") == {"algorithm": "sha256_utf8_order_v1", "prefix_utf8": "neuroute-v3-ja-v1\0", "training": 430, "configuration_selection": 215, "internal_evaluation": 215, "internal_evaluation_may_not_select": True}, "Japanese partitions differ")
    require(value.get("encoder") == {"input_dimensions": 384, "hidden_dimensions": [96, 64], "bits": 12, "seeds": [2026082701, 2026082702, 2026082703], "epochs": 80, "batch_size": 512, "training_query_batch_size": 128, "learning_rate": .001, "weight_decay": .0001, "torch_threads": 18}, "Japanese encoder differs")
    require(value.get("positive_geometry") == {"document_neighbours": 16, "query_document_neighbours": 10, "pair_slot": "epoch_modulo_neighbour_count_v1", "loss": "source_cosine_to_normalized_latent_cosine_mse_v1"}, "Japanese positive geometry differs")
    require(value.get("dynamic_false_positives") == {"treatments": ["positive_only_control", "dynamic_false_positive"], "warmup_epochs": 20, "remine_epochs": [20, 40, 60], "latent_neighbour_pool": 32, "selected_e5_farthest": 4, "pair_slot": "epoch_modulo_selected_false_positives_v1", "margin": .05, "loss": "relu_latent_cosine_minus_source_cosine_minus_margin_squared_v1", "weight": 1.0}, "Japanese dynamic negatives differ")
    require(value.get("diversity") == {"variance_weight": .01, "covariance_weight": .01, "minimum_latent_standard_deviation": .05}, "Japanese diversity differs")
    require(value.get("routing") == {"document_placement": "median_threshold_single_address_v3", "query_order": "independent_logit_best_first_v2", "configuration_frontier_probes": [64, 128, 256, 512], "headline_probes": 512, "candidate_mass_target": .1}, "Japanese routing differs")
    require(value.get("cascade") == {"oracle_k": 10, "hamming_limit": 768, "adc_limit": 256, "exact_limit": 256}, "Japanese cascade differs")
    require(value.get("gates") == {"mechanism_minimum_survival_gain": .05, "external_minimum_survival_gain": 0.0, "external_minimum_ndcg_gain": 0.0, "maximum_candidate_fraction": .1, "bootstrap_resamples": 10000, "bootstrap_seed": 2026082799}, "Japanese gates differ")
    return value


def plan(value: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"treatment": treatment, "seed": seed, "bits": 12} for treatment in value["dynamic_false_positives"]["treatments"] for seed in value["encoder"]["seeds"]]


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "neuroute-v3-ja-external-confirmation.example.json"); args = parser.parse_args()
    try:
        print(json.dumps({"schema_version": 1, "family": "neuroute_v3_third_external_confirmation_plan", "rows": plan(load_contract(args.contract))}, indent=2, sort_keys=True)); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-v3-ja-external-confirmation: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
