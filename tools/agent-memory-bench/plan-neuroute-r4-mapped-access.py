#!/usr/bin/env python3
"""Validate the frozen R4 mapped address-access frontier."""
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
            "neuroute_r4_mapped_address_access_frontier",
            "R4 mapped access identity differs")
    require(value["treatments"] == ["seek_read_staging", "mmap_copy_staging",
            "mmap_direct_shortlist", "mmap_direct_offset_order"],
            "R4 mapped access matrix differs")
    require(value["frozen_execution"] == {"kernel": "fused_int8_scalar",
            "scorer": "batched_avx2_r0",
            "store": "address_major_int8_ff32_prefix"},
            "R4 mapped execution differs")
    require(value["route"]["queries_per_seed"] == 152 and
            value["process_cold"]["paired_requests_per_seed"] == 15,
            "R4 mapped request matrix differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    seeds = len(contract["route"]["seeds"])
    treatments = len(contract["treatments"])
    return {"seed_count": seeds, "treatment_count": treatments,
            "warm_samples": seeds * treatments * contract["route"][
                "queries_per_seed"] * contract["warm_page_cache"]["measured_passes"],
            "fresh_process_samples": seeds * treatments * contract[
                "process_cold"]["paired_requests_per_seed"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).resolve().parent /
                        "neuroute-r4-mapped-access.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        value = plan(load_contract(args.contract))
        if args.self_test:
            require(value["warm_samples"] == 5472 and
                    value["fresh_process_samples"] == 180,
                    "R4 mapped access sample count differs")
            print("NeuRoute R4 mapped access planner self-test passed")
        else:
            print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-r4-mapped-access: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
