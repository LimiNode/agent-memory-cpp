#!/usr/bin/env python3
"""Validate and summarize the learned final binary reranker protocol."""
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
    require(value.get("schema_version") == 1 and
            value.get("family") == "neuroute_learned_final_binary_reranker",
            "learned-final schema differs")
    activation = value.get("activation", {})
    require(list(activation) == [
        "final_result_sha256", "final_materialization_sha256", "final_evidence_sha256",
        "conditional_result_sha256", "conditional_evidence_sha256",
        "random_ceiling_result_sha256", "random_ceiling_evidence_sha256",
        "german_split_result_sha256",
    ] and all(isinstance(item, str) and len(item) == 64 for item in activation.values()),
            "learned-final activation differs")
    require(value.get("datasets") == ["de-25k", "fr-25k", "ja-25k", "de-1m"],
            "learned-final datasets differ")
    require(value.get("frozen_input") == {
        "pool_stage": "adc256", "pool_size": 64,
        "router_seeds": [2026082701, 2026082702, 2026082703], "result_k": 10,
    }, "learned-final pools differ")
    partition = value.get("query_partition", {})
    require(partition.get("source") == "frozen_de_configuration_selection_query_ids" and
            partition.get("policy") == "sha256_prefix_then_query_id_v1" and
            partition.get("teacher_training_queries") == 50 and
            partition.get("heldout_de_queries") == 26 and
            partition.get("fr_and_ja_training_forbidden") is True and
            partition.get("de_1m_reuses_heldout_de_ids") is True,
            "learned-final query partition differs")
    models = value.get("models", {})
    require(models.get("widths") == [512, 768, 1024] and
            models.get("seeds") == [2026082804, 2026082805, 2026082806] and
            models.get("bytes_per_document") == {"512": 64, "768": 96, "1024": 128},
            "learned-final model matrix differs")
    training = value.get("training", {})
    require(training.get("teacher") == "exact_e5_scores_inside_each_frozen_top64" and
            training.get("epochs") == 40 and training.get("document_batch_size") == 512 and
            training.get("pool_batch_size") == 10 and
            training.get("temperature_start") == 1.0 and
            training.get("temperature_end") == 0.2 and
            training.get("ranking_loss") == "smooth_l1_on_per_pool_standardized_scores",
            "learned-final training differs")
    decision = value.get("decision", {})
    require(decision.get("native_followup_only_if_quality_winner_exists") is True and
            decision.get("random_adc_comparison_is_diagnostic_only") is True and
            decision.get("production_storage_selection_deferred") is True,
            "learned-final decision differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    models = len(contract["models"]["widths"]) * len(contract["models"]["seeds"])
    evaluation_queries = (contract["query_partition"]["heldout_de_queries"] * 2 + 85 + 215)
    return {
        "family": contract["family"], "models": models,
        "teacher_pool_rows": (contract["query_partition"]["teacher_training_queries"] *
                              len(contract["frozen_input"]["router_seeds"])),
        "evaluation_query_rows_per_model_seed": evaluation_queries,
        "native_rows": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-learned-final-reranker.example.json")
    args = parser.parse_args()
    try:
        print(json.dumps(plan(load_contract(args.contract)), sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-learned-final-reranker: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
