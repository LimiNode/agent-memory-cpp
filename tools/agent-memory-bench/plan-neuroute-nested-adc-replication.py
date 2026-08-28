#!/usr/bin/env python3
"""Validate and summarize the nested multi-seed ADC protocol."""

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
    require(value.get("schema_version") == 1
            and value.get("family") == "neuroute_nested_multiseed_adc_replication",
            "nested ADC contract identity differs")
    require(value.get("datasets") == ["de-25k", "fr-25k", "ja-25k", "de-1m"]
            and value.get("projection_seeds") == list(range(2026082802, 2026082810))
            and value.get("widths") == [512, 768, 1024, 1536, 2048, 3072, 4096],
            "nested ADC matrix differs")
    projection = value["projection"]
    require(projection.get("shape") == [384, 4096]
            and projection.get("nested_widths") == "strict_column_prefixes"
            and projection.get("statistics_column_batch") == 512,
            "nested ADC projection differs")
    selection = value["seed_selection"]
    require(selection.get("held_out_queries_forbidden") is True
            and selection.get("partition") == "de_25k_frozen_training_query_ids",
            "nested ADC seed selection differs")
    decision = value["decision"]
    require(decision.get("held_out_seed_cherry_picking_forbidden") is True
            and decision.get("production_selection_forbidden") is True,
            "nested ADC decision differs")
    require(set(value.get("activation", {})) == {
        "random_ceiling_result_sha256", "random_ceiling_evidence_sha256",
        "final_materialization_sha256"}, "nested ADC activation differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, int]:
    datasets = len(contract["datasets"])
    seeds = len(contract["projection_seeds"])
    widths = len(contract["widths"])
    route_seeds = 3
    return {"master_projections": seeds, "calibration_rows": seeds * widths,
            "held_out_adc_rows": datasets * seeds * widths * route_seeds,
            "held_out_fp32_rows": datasets * route_seeds}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-nested-adc-replication.example.json")
    args = parser.parse_args()
    try:
        print(json.dumps(plan(load_contract(args.contract)), sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-nested-adc-replication: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
