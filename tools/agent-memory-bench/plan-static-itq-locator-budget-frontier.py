#!/usr/bin/env python3
"""Validate and print the predeclared static locator budget frontier."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
FAMILY = "static_itq_locator_budget_frontier_v1"
BITS = (64, 80, 96, 112, 128)
RADIUS_THREE_KEYS = 697
RADIUS_FOUR_KEYS = 2517


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY, "static locator budget frontier identity differs")
    require(value.get("purpose") == "calibration_only_static_locator_r3_to_r4_budget_frontier_not_selection_or_confirmation", "static locator budget frontier purpose differs")
    require(tuple(value.get("bit_counts", [])) == BITS, "static locator budget frontier widths differ")
    require(value.get("subset") == {"variant": "random_seeded_v1", "random_seed": 20260830}, "static locator budget frontier subset differs")
    require(value.get("bands") == {"width_bits": 16, "initial_local_radius": 3, "expanded_local_radius": 4, "schedule": "sorted_locator_band_prefix_expands_from_r3_to_r4_v1"}, "static locator budget frontier band schedule differs")
    require(value.get("budget") == {"maximum_candidate_fraction": 0.25, "maximum_candidate_generator_p50_ratio_to_fresh_baseline": 1.0, "stop_after_first_observed_budget_exhaustion_per_bit_count": True, "do_not_measure_schedule_with_independent_null_candidate_fraction_above_budget": True}, "static locator budget frontier budget differs")
    baseline = value.get("baseline")
    require(isinstance(baseline, dict) and baseline.get("id") == "fresh_full_itq256_m19_uniform_radius2" and baseline.get("band_widths") == [14] * 9 + [13] * 10 and baseline.get("local_radii") == [2] * 19, "static locator budget frontier baseline differs")
    require(value.get("learned_locator") == "forbidden_in_this_protocol", "static locator budget frontier learned scope differs")
    return value


def independent_null_candidate_fraction(band_count: int, expanded_bands: int) -> float:
    require(0 <= expanded_bands <= band_count, "static locator budget frontier expansion differs")
    probability_r3 = RADIUS_THREE_KEYS / (1 << 16)
    probability_r4 = RADIUS_FOUR_KEYS / (1 << 16)
    return 1.0 - ((1.0 - probability_r4) ** expanded_bands) * ((1.0 - probability_r3) ** (band_count - expanded_bands))


def schedule_rows(contract: dict[str, Any]) -> list[dict[str, Any]]:
    budget = contract["budget"]["maximum_candidate_fraction"]
    rows: list[dict[str, Any]] = []
    for bit_count in contract["bit_counts"]:
        band_count = bit_count // contract["bands"]["width_bits"]
        for expanded_bands in range(band_count + 1):
            expected_fraction = independent_null_candidate_fraction(band_count, expanded_bands)
            if expected_fraction > budget and contract["budget"]["do_not_measure_schedule_with_independent_null_candidate_fraction_above_budget"]:
                break
            rows.append({
                "id": f"random-b{bit_count}-r4prefix{expanded_bands}",
                "bit_count": bit_count,
                "band_count": band_count,
                "r4_prefix_bands": expanded_bands,
                "local_radii": [4] * expanded_bands + [3] * (band_count - expanded_bands),
                "independent_null_candidate_fraction": expected_fraction,
            })
    return rows


def self_test() -> None:
    contract = load_contract(THIS / "static-itq-locator-budget-frontier.example.json")
    rows = schedule_rows(contract)
    require(len(rows) == 34, "static locator budget frontier row count differs")
    require(rows[0] == {"id": "random-b64-r4prefix0", "bit_count": 64, "band_count": 4, "r4_prefix_bands": 0, "local_radii": [3, 3, 3, 3], "independent_null_candidate_fraction": independent_null_candidate_fraction(4, 0)}, "static locator budget frontier initial schedule differs")
    require(rows[-1]["id"] == "random-b128-r4prefix7" and rows[-1]["independent_null_candidate_fraction"] < 0.25 < independent_null_candidate_fraction(8, 8), "static locator budget frontier 128-bit ceiling differs")
    require(math.isclose(independent_null_candidate_fraction(6, 0), 0.06213945603717741, rel_tol=0.0, abs_tol=1e-15), "static locator budget frontier r3 null differs")
    print("static ITQ locator budget frontier planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "static-itq-locator-budget-frontier.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract)
        if args.self_test:
            self_test()
        else:
            print(json.dumps({"schema_version": 1, "family": FAMILY, "rows": schedule_rows(contract)}, indent=2, sort_keys=True))
        return 0
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"plan-static-itq-locator-budget-frontier: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
