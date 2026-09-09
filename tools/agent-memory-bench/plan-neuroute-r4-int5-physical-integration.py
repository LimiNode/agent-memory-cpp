#!/usr/bin/env python3
"""Validate the frozen R4 nonlinear INT5 physical-integration protocol."""
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
    require(value.get("schema_version") == 1 and value.get("family") ==
            "neuroute_r4_int5_physical_integration",
            "R4 INT5 integration contract identity differs")
    require(value["route"] == {"dataset": "de-1m",
            "seeds": [2026082701, 2026082702, 2026082703],
            "partition": "internal", "queries_per_seed": 76,
            "layout_request_offset": 76, "shortlist_addresses": 1024,
            "candidate_fraction": .005},
            "R4 INT5 integration route differs")
    require(value["codec"] == {"id": "int5_power_050", "bits": 5,
            "compander": {"kind": "power", "parameter": .5},
            "record_bytes": 244, "uniform_int8_record_bytes": 388},
            "R4 INT5 integration codec differs")
    require(value["treatments"] == ["homogeneous_int8",
            "int5_side_store", "int5_mixed"],
            "R4 INT5 integration treatments differ")
    require(value["cascade"] == {"candidate_limit": 5000,
            "hamming_limit": 768, "adc_limit": 64, "exact_limit": 10},
            "R4 INT5 integration cascade differs")
    require(value["process_cold"]["paired_requests_per_seed"] == 15,
            "R4 INT5 integration fresh-process matrix differs")
    return value


def plan(value: dict[str, Any]) -> dict[str, int]:
    seeds = len(value["route"]["seeds"])
    treatments = len(value["treatments"])
    queries = value["route"]["queries_per_seed"]
    return {
        "mixed_stores": seeds,
        "warm_query_samples": seeds * treatments * queries *
            value["warm_page_cache"]["measured_passes"],
        "process_cold_samples": seeds * treatments *
            value["process_cold"]["paired_requests_per_seed"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
        "neuroute-r4-int5-physical-integration.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        result = plan(load_contract(args.contract))
        if args.self_test:
            require(result == {"mixed_stores": 3, "warm_query_samples": 2052,
                    "process_cold_samples": 135},
                    "R4 INT5 integration plan differs")
            print("NeuRoute R4 INT5 physical-integration planner self-test passed")
        else:
            print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"plan-neuroute-r4-int5-physical-integration: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
