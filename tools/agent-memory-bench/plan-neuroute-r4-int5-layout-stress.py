#!/usr/bin/env python3
"""Validate the frozen R4 INT5 layout stress protocol."""
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
            "neuroute_r4_int5_layout_stress",
            "R4 INT5 stress contract identity differs")
    require(value["route"] == {"dataset": "de-1m",
            "seeds": [2026082701, 2026082702, 2026082703],
            "partition": "internal", "queries_per_seed": 76},
            "R4 INT5 stress route differs")
    require(value["treatments"] == ["homogeneous_int8", "int5_mixed"] and
            value["conditions"] == ["resident", "working_set_cap"] and
            value["workers"] == [1, 2, 4, 8, 16],
            "R4 INT5 stress matrix differs")
    require(value["trace_repetitions"] == 4 and
            value["warmup_batches"] == 1 and
            value["measured_batches"] == 2 and
            value["working_set_cap_bytes"] == 256 * 1024 * 1024,
            "R4 INT5 stress load differs")
    return value


def plan(value: dict[str, Any]) -> dict[str, int]:
    invocations = (len(value["route"]["seeds"]) *
        len(value["treatments"]) * len(value["conditions"]) *
        len(value["workers"]))
    trace_queries = (value["route"]["queries_per_seed"] *
                     value["trace_repetitions"])
    return {"native_invocations": invocations,
            "trace_queries_per_batch": trace_queries,
            "measured_batch_samples": invocations *
                value["measured_batches"],
            "measured_query_executions": invocations *
                value["measured_batches"] * trace_queries}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
        "neuroute-r4-int5-layout-stress.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        result = plan(load_contract(args.contract))
        if args.self_test:
            require(result == {"native_invocations": 60,
                    "trace_queries_per_batch": 304,
                    "measured_batch_samples": 120,
                    "measured_query_executions": 36480},
                    "R4 INT5 stress plan differs")
            print("NeuRoute R4 INT5 layout-stress planner self-test passed")
        else:
            print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"plan-neuroute-r4-int5-layout-stress: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
