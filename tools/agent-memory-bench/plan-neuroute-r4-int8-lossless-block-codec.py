#!/usr/bin/env python3
"""Validate and summarize the R4 INT8 lossless block-codec protocol."""
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
    require(value["schema_version"] == 1 and value["family"] ==
            "neuroute_r4_int8_lossless_block_codec_frontier",
            "R4 lossless block protocol identity differs")
    require(value["treatments"] == ["raw_int8", "zstd_block",
            "zstd_dictionary_block", "vbyte_zigzag"],
            "R4 lossless block treatment order differs")
    require(value["zstd"]["commit"] ==
            "f8745da6ff1ad1e7bab384bd1f9d742439278e99" and
            value["zstd"]["level"] == 3 and
            value["zstd"]["dictionary_capacity_bytes"] == 65536 and
            value["zstd"]["dictionary_training_blocks"] == 4096,
            "R4 lossless Zstd contract differs")
    require(value["route"]["documents"] == 1000000 and
            value["route"]["queries_per_seed"] == 152 and
            value["route"]["addresses_per_query"] == 1024 and
            len(value["route"]["seeds"]) == 3,
            "R4 lossless route contract differs")
    return value


def plan(value: dict[str, Any]) -> dict[str, Any]:
    seeds = len(value["route"]["seeds"])
    treatments = len(value["treatments"])
    queries = value["route"]["queries_per_seed"]
    return {"seeds": seeds, "treatments": treatments,
            "warm_samples": seeds * treatments * queries *
                value["warm_page_cache"]["measured_passes"],
            "fresh_process_samples": seeds * treatments *
                value["process_cold"]["paired_requests_per_seed"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("contract", nargs="?", type=Path, default=THIS /
                        "neuroute-r4-int8-lossless-block-codec.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        value = load_contract(args.contract)
        result = plan(value)
        if args.self_test:
            require(result["warm_samples"] == 5472 and
                    result["fresh_process_samples"] == 180,
                    "R4 lossless planner self-test differs")
            print("NeuRoute R4 lossless block planner self-test passed")
        else:
            print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-r4-int8-lossless-block-codec: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
