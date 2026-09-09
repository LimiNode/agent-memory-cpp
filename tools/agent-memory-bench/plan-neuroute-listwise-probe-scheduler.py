#!/usr/bin/env python3
"""Validate and summarize the frozen listwise scheduler protocol."""

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
            value.get("family") == "neuroute_listwise_probe_scheduler",
            "listwise scheduler contract identity differs")
    route, training = value["route"], value["training"]
    calibration, evaluation, decision = (
        value["calibration"], value["evaluation"], value["decision"])
    require(route.get("width") == 16 and
            route.get("seeds") == [2026082701, 2026082702, 2026082703] and
            route.get("document_encoder_updates_forbidden") is True and
            route.get("document_replication") == 1,
            "listwise scheduler frozen route differs")
    require(training.get("partition") == "frozen_training_query_ids" and
            training.get("queries") == 153 and
            training.get("teacher_exact_e5_top_k") == 100 and
            training.get("feature_count") == 136 and
            training.get("heads") == ["listwise_gain", "cascade_aware"] and
            training.get("positive_address_weight") == 256.0 and
            training.get("updated_parameters") ==
            "query_side_joint_address_feature_head_only",
            "listwise scheduler training differs")
    require(value.get("treatments") == [
        "occupied_logit", "listwise_gain", "listwise_gain_cost", "cascade_aware"],
        "listwise scheduler treatments differ")
    require(calibration.get("probe_budgets") == [128, 256, 512, 1024, 2048, 4096]
            and calibration.get("cost_penalty_grid") == [0.01, 0.03, 0.1]
            and calibration.get("candidate_mass_target") == 0.1,
            "listwise scheduler calibration differs")
    require(evaluation.get("scales") == ["de-25k", "de-100k", "de-1m"] and
            evaluation.get("queries") == 76 and
            evaluation.get("coverage_target") == 0.9,
            "listwise scheduler evaluation differs")
    require(decision.get("baseline") == "occupied_logit" and
            decision.get("all_scales_and_seeds_must_pass_quality") is True and
            decision.get("production_selection_forbidden") is True,
            "listwise scheduler decision differs")
    require(set(value.get("activation", {})) == {
        "task_scheduler_result_sha256", "task_scheduler_evidence_sha256",
        "task_scheduler_authoritative_evidence_sha256", "width_result_sha256",
        "width_evidence_sha256", "width_materialization_sha256",
    }, "listwise scheduler activation differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    seeds = len(contract["route"]["seeds"])
    return {
        "frozen_routes": seeds,
        "learned_query_heads": seeds * len(contract["training"]["heads"]),
        "calibration_rows": seeds * (
            len(contract["calibration"]["probe_budgets"]) * 3
            + len(contract["calibration"]["probe_budgets"])
            * len(contract["calibration"]["cost_penalty_grid"])),
        "held_out_route_treatments": seeds * len(contract["treatments"])
                                      * len(contract["evaluation"]["scales"]),
        "native_rows": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-listwise-probe-scheduler.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        result = plan(load_contract(args.contract))
        require(result == {"frozen_routes": 3, "learned_query_heads": 6,
                           "calibration_rows": 108, "held_out_route_treatments": 36,
                           "native_rows": 0},
                "listwise scheduler plan differs")
        if args.self_test:
            print("NeuRoute listwise probe scheduler planner self-test passed")
        else:
            print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-listwise-probe-scheduler: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
