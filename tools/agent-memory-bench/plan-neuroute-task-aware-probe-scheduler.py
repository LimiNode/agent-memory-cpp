#!/usr/bin/env python3
"""Validate and summarize the task-aware query-side scheduler protocol."""

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
            value.get("family") == "neuroute_task_aware_probe_scheduler",
            "task-aware scheduler contract identity differs")
    routes = value["routes"]
    training = value["training"]
    calibration = value["calibration"]
    evaluation = value["evaluation"]
    decision = value["decision"]
    require(routes.get("widths") == [14, 16] and
            routes.get("seeds") == [2026082701, 2026082702, 2026082703] and
            routes.get("document_encoder_updates_forbidden") is True,
            "task-aware scheduler route matrix differs")
    require(training.get("partition") == "frozen_training_query_ids" and
            training.get("queries") == 153 and
            training.get("heads") == ["anchored_reachability", "anchored_mass_aware"] and
            training.get("updated_parameters") == "query_weight3_and_query_bias3_only",
            "task-aware scheduler training differs")
    require(value.get("treatments") == [
        "current_full_space", "occupied_logit", "occupied_mass_aware",
        "anchored_reachability", "anchored_mass_aware"],
        "task-aware scheduler treatments differ")
    require(calibration.get("probe_budgets") == [64, 128, 256, 512, 1024, 2048, 4096] and
            calibration.get("mass_penalty_grid") == [0.25, 0.5, 1.0] and
            calibration.get("candidate_mass_target") == 0.1,
            "task-aware scheduler calibration differs")
    require(evaluation.get("scales") == ["de-25k", "de-100k", "de-1m"] and
            evaluation.get("fixed_mechanism_probe_budget") == 256,
            "task-aware scheduler evaluation differs")
    require(decision.get("all_scales_and_seeds_must_pass") is True and
            decision.get("native_confirmation_only_if_passed") is True and
            decision.get("production_selection_forbidden") is True,
            "task-aware scheduler decision differs")
    require(set(value.get("activation", {})) == {
        "diagnostic_result_sha256", "diagnostic_evidence_sha256",
        "width_result_sha256", "width_evidence_sha256",
        "width_materialization_sha256",
    }, "task-aware scheduler activation differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    routes = len(contract["routes"]["widths"]) * len(contract["routes"]["seeds"])
    return {
        "frozen_routes": routes,
        "learned_query_heads": routes * len(contract["training"]["heads"]),
        "treatments_per_route": len(contract["treatments"]),
        "evaluation_scales": len(contract["evaluation"]["scales"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-task-aware-probe-scheduler.example.json")
    args = parser.parse_args()
    try:
        print(json.dumps(plan(load_contract(args.contract)), sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-task-aware-probe-scheduler: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
