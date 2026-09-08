#!/usr/bin/env python3
"""Validate the conditional physical ADC benchmark activation contract."""

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
            and value.get("family") == "neuroute_physical_adc_benchmark_activation",
            "physical ADC activation identity differs")
    require(value.get("required_parent_decision") == {
        "selected_candidate_width": None, "physical_benchmark_licensed": False,
        "production_selection_licensed": False,
        "held_out_seed_cherry_picking_performed": False},
        "physical ADC required parent decision differs")
    benchmark = value["benchmark_if_activated"]
    require(benchmark.get("dataset") == "de-1m"
            and benchmark.get("same_top64_ids_and_paired_requests") is True
            and benchmark.get("cache_scenarios") == ["warm_page_cache", "fresh_process_first_fetch"]
            and benchmark.get("document_reprojection_at_query_time_forbidden") is True,
            "physical ADC dormant benchmark protocol differs")
    decision = value["decision"]
    require(decision.get("benchmark_must_not_run_without_parent_license") is True
            and decision.get("synthetic_timing_substitution_forbidden") is True
            and decision.get("production_selection_forbidden") is True,
            "physical ADC activation decision differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    licensed = contract["required_parent_decision"]["physical_benchmark_licensed"]
    return {"benchmark_expected": licensed, "physical_rows_expected": 0 if not licensed else None,
            "required_stages_if_activated": len(contract["benchmark_if_activated"]["stages"])}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-physical-adc-activation.example.json")
    args = parser.parse_args()
    try:
        print(json.dumps(plan(load_contract(args.contract)), sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-physical-adc-activation: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
