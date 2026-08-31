#!/usr/bin/env python3
"""Validate the frozen R4 fused INT8 kernel frontier."""
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
            "neuroute_r4_int8_kernel_frontier", "R4 INT8 kernel identity differs")
    require(value["kernels"] == ["decode_fp32_scalar_dot", "fused_int8_scalar",
            "fused_int8_avx2", "fused_int8_avx2_ordered"],
            "R4 INT8 kernel matrix differs")
    require(value["frozen_store"] == {"layout": "address_major_int8",
            "record_bytes": 388, "dimensions": 384,
            "representatives": "ff32_prefix"}, "R4 INT8 store differs")
    require(value["route"]["queries_per_seed"] == 152 and
            value["route"]["addresses_per_query"] == 1024 and
            value["warm_page_cache"]["measured_passes"] == 3,
            "R4 INT8 kernel matrix differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    samples = (len(contract["route"]["seeds"]) * len(contract["kernels"]) *
               contract["route"]["queries_per_seed"] *
               contract["warm_page_cache"]["measured_passes"])
    return {"seed_count": len(contract["route"]["seeds"]),
            "kernel_count": len(contract["kernels"]),
            "warm_samples": samples}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).resolve().parent /
                        "neuroute-r4-int8-kernel.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        value = plan(load_contract(args.contract))
        if args.self_test:
            require(value["warm_samples"] == 5472, "R4 INT8 kernel sample count differs")
            print("NeuRoute R4 INT8 kernel planner self-test passed")
        else:
            print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-r4-int8-kernel: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
