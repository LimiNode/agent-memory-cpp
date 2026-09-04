#!/usr/bin/env python3
"""Validate and summarize the random overcomplete ADC ceiling diagnostic."""
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
            value.get("family") == "neuroute_random_overcomplete_adc_ceiling",
            "random-ADC ceiling schema differs")
    activation = value.get("activation", {})
    require(list(activation) == ["conditional_result_sha256",
                                 "conditional_evidence_sha256",
                                 "final_materialization_sha256"] and
            all(isinstance(item, str) and len(item) == 64 for item in activation.values()),
            "random-ADC ceiling activation differs")
    require(value.get("datasets") == ["de-25k", "fr-25k", "ja-25k", "de-1m"] and
            value.get("seeds") == [2026082701, 2026082702, 2026082703],
            "random-ADC ceiling datasets differ")
    require(value.get("frozen_input") == {
        "pool_stage": "adc256", "pool_size": 64, "result_k": 10,
    }, "random-ADC ceiling pool differs")
    require(value.get("frozen_parent_widths") == [512, 768, 1024] and
            value.get("diagnostic_widths") == [1536, 2048, 4096],
            "random-ADC ceiling widths differ")
    require(value.get("projection") == {
        "kind": "frozen_rademacher_overcomplete_binary_adc",
        "seed": 2026082802,
        "normalization": "divide_by_sqrt_384",
        "threshold": "deterministic_document_sample_up_to_100000_projection_median",
        "centroids": "same_sample_per_bit_projection_conditional_mean",
        "training_labels": "none",
    }, "random-ADC ceiling projection differs")
    decision = value.get("decision", {})
    require(decision.get("production_native_implementation_forbidden") is True and
            decision.get("production_winner_selection_forbidden") is True and
            decision.get("learned_final_reranker_remains_separate") is True,
            "random-ADC ceiling decision differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "family": contract["family"],
        "new_quality_rows": (len(contract["datasets"]) * len(contract["seeds"]) *
                             len(contract["diagnostic_widths"])),
        "curve_widths": contract["frozen_parent_widths"] + contract["diagnostic_widths"],
        "native_rows": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-random-adc-ceiling.example.json")
    args = parser.parse_args()
    try:
        print(json.dumps(plan(load_contract(args.contract)), sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-random-adc-ceiling: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
