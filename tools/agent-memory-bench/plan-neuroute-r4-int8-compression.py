#!/usr/bin/env python3
"""Validate the R4 lossless INT8 SIMDComp frontier."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from typing import Any


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") ==
            "neuroute_r4_lossless_int8_compression_frontier",
            "R4 INT8 compression identity differs")
    require(value["treatments"] == ["raw_int8", "simdcomp_fixed8",
            "simdcomp_adaptive_for", "simdcomp_adaptive_zigzag"],
            "R4 INT8 compression matrix differs")
    require(value["simdcomp"]["commit"] ==
            "009c67807670d16f8984c0534aef0e630e5465a4" and
            value["simdcomp"]["block_values"] == 128,
            "R4 INT8 SIMDComp pin differs")
    require(value["route"]["documents"] == 1000000 and
            value["route"]["queries_per_seed"] == 152 and
            value["process_cold"]["paired_requests_per_seed"] == 15,
            "R4 INT8 compression request matrix differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    seeds = len(contract["route"]["seeds"])
    treatments = len(contract["treatments"])
    return {"seed_count": seeds, "treatment_count": treatments,
            "documents_materialized": seeds * contract["route"]["documents"] * 3,
            "warm_samples": seeds * treatments * contract["route"][
                "queries_per_seed"] * contract["warm_page_cache"]["measured_passes"],
            "fresh_process_samples": seeds * treatments * contract[
                "process_cold"]["paired_requests_per_seed"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).resolve().parent /
                        "neuroute-r4-int8-compression.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        value = plan(load_contract(args.contract))
        if args.self_test:
            require(value["warm_samples"] == 5472 and
                    value["fresh_process_samples"] == 180,
                    "R4 INT8 compression sample count differs")
            print("NeuRoute R4 INT8 compression planner self-test passed")
        else:
            print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-r4-int8-compression: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
