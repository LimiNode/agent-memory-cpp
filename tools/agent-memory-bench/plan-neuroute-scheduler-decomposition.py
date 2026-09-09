#!/usr/bin/env python3
"""Validate and summarize the frozen scheduler decomposition protocol."""

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
            and value.get("family") == "neuroute_scheduler_decomposition",
            "scheduler decomposition contract differs")
    require(value["route"] == {
        "width": 16,
        "seeds": [2026082701, 2026082702, 2026082703],
        "document_addresses": "frozen_per_scale_from_width_materialization",
    }, "scheduler decomposition route differs")
    require([row["queries"] for row in value["partitions"]] == [153, 76]
            and value["stages"] == ["occupied_logit", "direct_teacher",
                                    "best_per_query_quadratic", "learned_quadratic"],
            "scheduler decomposition matrix differs")
    require(value["teacher"]["exact_e5_top_k"] == 100
            and value["teacher"]["feature_count"] == 136
            and value["evaluation"]["primary_probe_budget"] == 2048,
            "scheduler decomposition teacher differs")
    require(value["decision"]["production_selection_forbidden"] is True,
            "scheduler decomposition production decision differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, int]:
    routes = len(contract["route"]["seeds"])
    datasets = len(contract["evaluation"]["scales"])
    partitions = len(contract["partitions"])
    stages = len(contract["stages"])
    budgets = len(contract["evaluation"]["probe_budgets"])
    return {
        "frozen_routes": routes,
        "diagnostic_groups": datasets * routes * partitions,
        "stage_rows": datasets * routes * partitions * stages,
        "frontier_rows": datasets * routes * partitions * stages * budgets,
        "native_rows": 0,
    }


def self_test() -> None:
    contract = load_contract(THIS / "neuroute-scheduler-decomposition.example.json")
    require(plan(contract) == {"frozen_routes": 3, "diagnostic_groups": 18,
                               "stage_rows": 72, "frontier_rows": 432,
                               "native_rows": 0},
            "scheduler decomposition plan differs")
    print("NeuRoute scheduler decomposition planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-scheduler-decomposition.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
        else:
            print(json.dumps(plan(load_contract(args.contract)), indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-scheduler-decomposition: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
