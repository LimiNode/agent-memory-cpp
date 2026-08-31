#!/usr/bin/env python3
"""Validate the R4 nonlinear INT5 routing-kernel closure matrix."""
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
            "neuroute_r4_int5_kernel_frontier",
            "R4 INT5 kernel contract identity differs")
    require([row["id"] for row in value["kernels"]] == [
        "homogeneous_int8", "int5_direct_square_legacy",
        "int5_direct_square", "int5_fused_sse", "int5_fused_avx2",
        "int5_fused_avx2_q8", "int5_direct_q8_integer",
        "int5_direct_q16_integer"],
        "R4 INT5 kernel ladder differs")
    require(value["conditions"] == ["resident", "working_set_cap"] and
            value["workers"] == [1, 8, 16] and
            value["route"]["seeds"] ==
                [2026082701, 2026082702, 2026082703] and
            value["trace"] == {"repetitions": 2, "warmup_batches": 1,
                "measured_batches": 2,
                "working_set_cap_bytes": 268435456},
            "R4 INT5 kernel execution matrix differs")
    require(value["memory_crossover"] == {
                "caps_bytes": [134217728, 201326592, 268435456,
                    335544320, 402653184, 536870912, 805306368,
                    1073741824],
                "include_resident": True, "workers": 8,
                "trace_repetitions": 2, "warmup_batches": 1,
                "measured_batches": 3},
            "R4 INT5 memory-crossover matrix differs")
    require(all(isinstance(item, str) and len(item) == 64
                for item in value["activation"].values()),
            "R4 INT5 kernel activation differs")
    require(value["system_gates"][
                "maximum_selected_resident_w1_total_p95_ratio_vs_int8"] ==
            1.02 and value["system_gates"][
                "maximum_direct_integer_resident_w1_representative_p95_ratio_vs_int8"] ==
            1.10 and value["selection"][
                "sensitivity_kernel_never_exact_implementation_control"] is
            True and value["selection"][
                "aosoa_followup_only_if_direct_integer_gate_passes"] is True,
            "R4 INT5 kernel decision boundary differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, int]:
    invocations = (len(contract["route"]["seeds"]) *
                   len(contract["kernels"]) * len(contract["conditions"]) *
                   len(contract["workers"]))
    trace_queries = (contract["route"]["queries_per_seed"] *
                     contract["trace"]["repetitions"])
    crossover_conditions = (len(contract["memory_crossover"]["caps_bytes"]) +
        int(contract["memory_crossover"]["include_resident"]))
    crossover_invocations = (len(contract["route"]["seeds"]) * 2 *
                             crossover_conditions)
    return {"bitsliced_full_store_materializations": 3,
            "avx2_full_store_materializations": 3,
            "native_invocations": invocations,
            "trace_queries_per_batch": trace_queries,
            "measured_query_rows": invocations *
                contract["trace"]["measured_batches"] * trace_queries,
            "crossover_native_invocations": crossover_invocations,
            "crossover_measured_query_rows": crossover_invocations *
                contract["memory_crossover"]["measured_batches"] *
                trace_queries}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-int5-kernel-frontier.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        result = plan(load_contract(args.contract))
        if args.self_test:
            require(result == {"bitsliced_full_store_materializations": 3,
                    "avx2_full_store_materializations": 3,
                    "native_invocations": 144,
                    "trace_queries_per_batch": 152,
                    "measured_query_rows": 43776,
                    "crossover_native_invocations": 54,
                    "crossover_measured_query_rows": 24624},
                    "R4 INT5 kernel plan differs")
            print("NeuRoute R4 INT5 kernel-frontier planner self-test passed")
        else:
            print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"plan-neuroute-r4-int5-kernel-frontier: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
