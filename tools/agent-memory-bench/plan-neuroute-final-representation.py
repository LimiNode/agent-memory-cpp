#!/usr/bin/env python3
"""Validate and print the frozen final-representation frontier."""

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
            and value.get("family") == "neuroute_final_representation_frontier",
            "final-representation contract identity differs")
    require(value.get("claim_scope")
            == "fixed_adc256_top64_final_rerank_only_no_router_or_candidate_selection",
            "final-representation claim scope differs")
    require([row.get("id") for row in value.get("datasets", [])]
            == ["de-25k", "fr-25k", "ja-25k", "de-1m"],
            "final-representation datasets differ")
    frozen = value.get("frozen_input", {})
    require(frozen == {"pool_stage": "adc256", "pool_size": 64, "result_k": 10,
                       "seeds": [2026082701, 2026082702, 2026082703],
                       "scale_route_policy": "per_scale_document_median"},
            "final-representation frozen input differs")
    require([row.get("id") for row in value.get("representations", [])] == [
        "fp32", "fp16", "int8_symmetric", "int4_symmetric", "ternary_2bit",
        "five_level_3bit", "binary_adc256", "coordinate_binary_adc384"],
        "final-representation matrix differs")
    timing = value.get("native_timing", {})
    require(timing.get("warmup_passes") == 3 and timing.get("measured_passes") == 21
            and timing.get("microbatch_repeats") == 64
            and timing.get("stages") == ["decode_and_score", "top10_selection", "total"],
            "final-representation native timing differs")
    require(len(value.get("activation", {})) == 8
            and all(isinstance(item, str) and len(item) == 64
                    for item in value["activation"].values()),
            "final-representation activation differs")
    return value


def plan(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"dataset": dataset["id"], "seed": seed, "representation": representation["id"]}
            for dataset in contract["datasets"]
            for seed in contract["frozen_input"]["seeds"]
            for representation in contract["representations"]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-final-representation.example.json")
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract)
        print(json.dumps({"rows": plan(contract), "row_count": len(plan(contract))}, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-final-representation: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
