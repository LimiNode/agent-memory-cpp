#!/usr/bin/env python3
"""Validate the R4 nonlinear representative-quantization protocol."""
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
            "neuroute_r4_nonlinear_representative_quantization_frontier",
            "R4 nonlinear protocol identity differs")
    require(len(value["representations"]) == 16 and
            value["representations"][0]["id"] == "fp32",
            "R4 nonlinear representation ladder differs")
    for bits, size in ((8, 388), (6, 292), (5, 244)):
        rows = [row for row in value["representations"]
                if row["bits"] == bits]
        require(len(rows) == 5 and
                all(row["record_bytes"] == size for row in rows),
                f"R4 nonlinear INT{bits} ladder differs")
        require({row["compander"]["kind"] for row in rows} ==
                {"uniform", "power", "mulaw"},
                f"R4 nonlinear INT{bits} companders differ")
    require(value["query_partitions"]["configuration_queries"] == 76 and
            value["query_partitions"]["internal_evaluation_queries"] == 76,
            "R4 nonlinear query partitions differ")
    return value


def plan(value: dict[str, Any]) -> dict[str, Any]:
    seeds = len(value["route"]["seeds"])
    return {"seeds": seeds, "configuration_treatments": 16,
            "configuration_rows": seeds * 16,
            "nonlinear_physical_stores": seeds * 12,
            "internal_treatments_after_selection": 7,
            "internal_rows_after_selection": seeds * 7,
            "native_benchmark_maximum_treatments": 6}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("contract", nargs="?", type=Path, default=THIS /
        "neuroute-r4-nonlinear-representative-quantization.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        result = plan(load_contract(args.contract))
        if args.self_test:
            require(result["nonlinear_physical_stores"] == 36 and
                    result["internal_rows_after_selection"] == 21,
                    "R4 nonlinear planner self-test differs")
            print("NeuRoute R4 nonlinear quantization planner self-test passed")
        else:
            print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-r4-nonlinear-representative-quantization: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
