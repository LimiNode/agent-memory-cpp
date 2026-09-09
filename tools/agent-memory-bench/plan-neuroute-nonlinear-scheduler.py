#!/usr/bin/env python3
"""Validate and summarize the frozen nonlinear scheduler protocol."""

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
            and value.get("family") == "neuroute_nonlinear_scheduler",
            "nonlinear scheduler contract differs")
    require(value["route"]["width"] == 16
            and value["route"]["seeds"] == [2026082701, 2026082702, 2026082703],
            "nonlinear scheduler routes differ")
    require(value["training"]["nested_query_counts"] == [153, 512, 2048, 4096, 8141]
            and value["training"]["variants"] == ["direct_id", "centroid_initialized_id"]
            and value["training"]["teacher_exact_e5_top_k"] == 100,
            "nonlinear scheduler training matrix differs")
    require(value["query_partitions"]["calibration"]["queries"] == 76
            and value["query_partitions"]["evaluation"]["queries"] == 76,
            "nonlinear scheduler held-out partitions differ")
    require(value["decision"]["production_selection_forbidden"] is True,
            "nonlinear scheduler production decision differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, int]:
    seeds = len(contract["route"]["seeds"])
    variants = len(contract["training"]["variants"])
    sizes = len(contract["training"]["nested_query_counts"])
    budgets = len(contract["calibration"]["probe_budgets"])
    scales = len(contract["evaluation"]["scales"])
    return {
        "trained_models": seeds * variants * sizes,
        "calibration_rows": (seeds * variants * sizes + seeds) * budgets,
        "selected_models": seeds * variants,
        "held_out_rows": scales * seeds * (variants + 1),
        "native_rows": 0,
    }


def self_test() -> None:
    contract = load_contract(THIS / "neuroute-nonlinear-scheduler.example.json")
    require(plan(contract) == {"trained_models": 30, "calibration_rows": 198,
                               "selected_models": 6, "held_out_rows": 27,
                               "native_rows": 0},
            "nonlinear scheduler plan differs")
    print("NeuRoute nonlinear scheduler planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-nonlinear-scheduler.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
        else:
            print(json.dumps(plan(load_contract(args.contract)), indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-nonlinear-scheduler: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
