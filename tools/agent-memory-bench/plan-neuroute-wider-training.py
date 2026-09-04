#!/usr/bin/env python3
"""Validate and summarize the wider-router training-data protocol."""
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
            value.get("family") == "neuroute_wider_training_sufficiency",
            "wider-training schema differs")
    activation = value.get("activation", {})
    require(list(activation) == [
        "training_contract_sha256", "training_result_sha256",
        "previous_width_result_sha256", "previous_width_evidence_sha256",
        "german_split_result_sha256"], "wider-training activation differs")
    require(all(isinstance(item, str) and len(item) == 64 for item in activation.values()),
            "wider-training activation digest differs")
    scales = value.get("scales", [])
    require([(row.get("id"), row.get("documents")) for row in scales] ==
            [("de-25k", 25000), ("de-100k", 100000), ("de-1m", 1000000)],
            "wider-training scales differ")
    training = value.get("training", {})
    require([(row.get("id"), row.get("source_scale"), row.get("documents"))
             for row in training.get("regimes", [])] ==
            [("matched_25k", "de-25k", 25000),
             ("expanded_100k", "de-100k", 100000)],
            "wider-training regimes differ")
    require(training.get("reference_scale") == "de-25k" and
            training.get("reference_documents") == 25000 and
            training.get("partition") == "frozen_training_query_ids" and
            training.get("widths") == [14, 16] and
            training.get("seeds") == [2026082701, 2026082702, 2026082703] and
            training.get("width_specific_full_output_heads") is True and
            training.get("appending_bits_to_a_12_bit_artifact_forbidden") is True and
            training.get("epochs") == 80,
            "wider-training model matrix differs")
    require(training.get("document_pair_schedule") == {
        "exact_reference_neighbours": 12,
        "deterministic_contrast_pairs": 4,
        "slots_per_document": 16,
        "contrast_seed": 2026082801,
        "contrast_policy": "cyclic_offsets_seeded_v1",
        "dynamic_all_pairs_remine_forbidden": True,
    }, "wider-training pair schedule differs")
    require(training.get("query_positive_neighbours") == 10,
            "wider-training query geometry differs")
    calibration = value.get("calibration", {})
    require(calibration.get("scale") == "de-25k" and
            calibration.get("partition") == "frozen_training_query_ids" and
            calibration.get("evaluation_partition_forbidden") is True and
            calibration.get("probe_budgets") == [256, 512, 1024, 2048, 4096],
            "wider-training calibration differs")
    evaluation = value.get("evaluation", {})
    require(evaluation == {
        "partition": "frozen_configuration_selection_query_ids",
        "queries": 76,
        "fixed_mechanism_probe_budget": 256,
        "budget_roles": ["fixed_256", "calibration_selected"],
    }, "wider-training evaluation differs")
    require(value.get("cascade") == {
        "oracle_k": 10, "hamming_limit": 768, "adc_limit": 64,
        "exact_limit": 64, "result_k": 10,
    }, "wider-training cascade differs")
    decision = value.get("decision", {})
    require(decision.get("primary_width") == 14 and
            decision.get("diagnostic_width") == 16 and
            decision.get("production_winner_requires_expanded_regime_gate_pass") is True,
            "wider-training decision differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    training = contract["training"]
    models = (len(training["regimes"]) * len(training["widths"]) *
              len(training["seeds"]))
    return {
        "family": contract["family"],
        "models": models,
        "calibration_rows": models * len(contract["calibration"]["probe_budgets"]),
        "evaluation_route_rows_maximum": models * len(contract["scales"]) * 2,
        "training_documents": sum(row["documents"] for row in training["regimes"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-wider-training.example.json")
    args = parser.parse_args()
    try:
        print(json.dumps(plan(load_contract(args.contract)), sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-wider-training: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
