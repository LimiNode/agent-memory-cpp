#!/usr/bin/env python3
"""Validate the frozen R4 representative physical-layout benchmark."""
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
            "neuroute_r4_physical_layout_benchmark",
            "R4 layout contract identity differs")
    require(value["selected_representation"] == {"id": "int8",
            "record_bytes": 388, "layout": "raw_unsigned_plus_f32_scale"},
            "R4 layout selected codec differs")
    require([row["id"] for row in value["layouts"]] == [
        "address_major_fp32", "address_major_int8",
        "document_major_int8_indirect"], "R4 layout matrix differs")
    require(value["route"]["queries_per_seed"] == 152
            and value["route"]["addresses_per_query"] == 1024
            and value["process_cold"]["paired_requests_per_seed"] == 15,
            "R4 layout request matrix differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    seeds = len(contract["route"]["seeds"])
    layouts = len(contract["layouts"])
    requests = contract["route"]["queries_per_seed"]
    return {
        "seed_count": seeds,
        "layout_count": layouts,
        "paired_requests": seeds * layouts * requests,
        "warm_samples": seeds * layouts * requests *
                        contract["warm_page_cache"]["measured_passes"],
        "fresh_process_samples": seeds * layouts *
                                 contract["process_cold"]["paired_requests_per_seed"],
        "full_corpus_physical_files": seeds * 2 + 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).resolve().parent /
                        "neuroute-r4-layout-benchmark.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        value = plan(load_contract(args.contract))
        if args.self_test:
            require(value["warm_samples"] == 4104
                    and value["fresh_process_samples"] == 135,
                    "R4 layout planner self-test differs")
            print("NeuRoute R4 layout planner self-test passed")
        else:
            print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-r4-layout-benchmark: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
