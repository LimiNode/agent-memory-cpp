#!/usr/bin/env python3
"""Validate the frozen R4 native end-to-end benchmark contract."""
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
            "neuroute_r4_native_end_to_end_frontier",
            "R4 end-to-end contract identity differs")
    require(value["route"] == {"dataset": "de-1m",
            "seeds": [2026082701, 2026082702, 2026082703],
            "partition": "internal", "queries_per_seed": 76,
            "layout_request_offset": 76, "shortlist_addresses": 1024,
            "candidate_fraction": .005},
            "R4 end-to-end frozen route differs")
    require(value["cascade"] == {"candidate_limit": 5000,
            "hamming_limit": 768, "adc_limit": 64, "exact_limit": 10},
            "R4 end-to-end cascade differs")
    require(value["treatments"] == ["baseline_seek_decode_scalar",
            "strict_mmap_fused_scalar_batched",
            "fast_mmap_fused_avx2_batched"],
            "R4 end-to-end treatments differ")
    require(value["concurrency"]["workers"] == [1, 2, 4, 8] and
            value["process_cold"]["paired_requests_per_seed"] == 15,
            "R4 end-to-end system matrix differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, int]:
    seeds = len(contract["route"]["seeds"])
    treatments = len(contract["treatments"])
    queries = contract["route"]["queries_per_seed"]
    return {
        "warm_query_samples": seeds * treatments * queries *
            contract["warm_page_cache"]["measured_passes"],
        "concurrency_batch_samples": seeds *
            len(contract["concurrency"]["treatments"]) *
            len(contract["concurrency"]["workers"]) *
            contract["concurrency"]["measured_passes"],
        "process_cold_samples": seeds * treatments *
            contract["process_cold"]["paired_requests_per_seed"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).resolve().parent /
                        "neuroute-r4-native-end-to-end.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        value = plan(load_contract(args.contract))
        if args.self_test:
            require(value == {"warm_query_samples": 2052,
                    "concurrency_batch_samples": 108,
                    "process_cold_samples": 135},
                    "R4 end-to-end sample counts differ")
            print("NeuRoute R4 native end-to-end planner self-test passed")
        else:
            print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-r4-native-end-to-end: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
