#!/usr/bin/env python3
"""Validate and print the frozen NeuRoute width/scale/budget matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1
            and value.get("family") == "neuroute_width_scale_budget_frontier",
            "width-scale-budget contract identity differs")
    require([row.get("id") for row in value.get("scales", [])]
            == ["de-25k", "de-100k", "de-1m"],
            "width-scale-budget scales differ")
    require(value.get("training", {}).get("widths") == [12, 14, 16, 18]
            and value["training"].get("seeds") == [2026082701, 2026082702, 2026082703]
            and value["training"].get("width_specific_full_output_heads") is True
            and value["training"].get("appending_bits_to_a_12_bit_artifact_forbidden") is True,
            "width-scale-budget training matrix differs")
    require(value.get("calibration", {}).get("probe_budgets")
            == [64, 128, 256, 512, 1024, 2048, 4096]
            and value["calibration"].get("evaluation_partition_forbidden") is True,
            "width-scale-budget calibration differs")
    require(value.get("evaluation", {}).get("fixed_mechanism_probe_budget") == 256
            and value["evaluation"].get("budget_roles")
            == ["fixed_256", "calibration_selected"],
            "width-scale-budget evaluation differs")
    require(len(value.get("activation", {})) == 7
            and all(isinstance(item, str) and len(item) == 64
                    for item in value["activation"].values()),
            "width-scale-budget activation differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    models = [{"width": width, "seed": seed}
              for width in contract["training"]["widths"]
              for seed in contract["training"]["seeds"]]
    calibration = [{"width": width, "seed": seed, "probes": probes}
                   for width in contract["training"]["widths"]
                   for seed in contract["training"]["seeds"]
                   for probes in contract["calibration"]["probe_budgets"]]
    evaluation = [{"scale": scale["id"], "width": width, "seed": seed,
                   "budget_role": role}
                  for scale in contract["scales"]
                  for width in contract["training"]["widths"]
                  for seed in contract["training"]["seeds"]
                  for role in contract["evaluation"]["budget_roles"]]
    return {"models": models, "calibration": calibration, "evaluation": evaluation,
            "model_count": len(models), "calibration_row_count": len(calibration),
            "maximum_evaluation_row_count": len(evaluation)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-width-scale-budget.example.json")
    args = parser.parse_args()
    try:
        print(json.dumps(plan(load_contract(args.contract)), indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-width-scale-budget: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
