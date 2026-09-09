#!/usr/bin/env python3
"""Validate the frozen R4 batched scorer frontier."""
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
            "neuroute_r4_batched_scorer_frontier", "R4 scorer identity differs")
    require(value["frozen_kernel"] == "fused_int8_scalar" and
            value["scorers"] == ["scalar_r0", "batched_avx2_r0"],
            "R4 scorer matrix differs")
    require(value["batching"] == {"address_lanes": 8,
            "preserve_per_address_accumulation_order": True,
            "scalar_tanh": True}, "R4 scorer batching differs")
    require(value["route"]["queries_per_seed"] == 152 and
            value["route"]["addresses_per_query"] == 1024,
            "R4 scorer request matrix differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    samples = (len(contract["route"]["seeds"]) * len(contract["scorers"]) *
               contract["route"]["queries_per_seed"] *
               contract["warm_page_cache"]["measured_passes"])
    return {"seed_count": len(contract["route"]["seeds"]),
            "scorer_count": len(contract["scorers"]),
            "warm_samples": samples}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).resolve().parent /
                        "neuroute-r4-batched-scorer.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        value = plan(load_contract(args.contract))
        if args.self_test:
            require(value["warm_samples"] == 2736, "R4 scorer sample count differs")
            print("NeuRoute R4 batched scorer planner self-test passed")
        else:
            print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-r4-batched-scorer: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
