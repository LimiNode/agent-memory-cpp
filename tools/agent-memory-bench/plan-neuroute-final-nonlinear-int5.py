#!/usr/bin/env python3
"""Validate the frozen nonlinear INT5 final-rerank protocol."""
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
            "neuroute_final_nonlinear_int5",
            "final nonlinear INT5 contract identity differs")
    require([(row["id"], row["queries"]) for row in value["datasets"]] == [
        ("de-25k", 76), ("fr-25k", 85), ("ja-25k", 215),
        ("de-1m", 76)], "final nonlinear INT5 datasets differ")
    require([(row["id"], row["kind"], row["parameter"])
             for row in value["treatments"]] == [
        ("int5_uniform", "uniform", 1.0),
        ("int5_power_050", "power", .5),
        ("int5_power_075", "power", .75),
        ("int5_mulaw_15", "mulaw", 15.0),
        ("int5_mulaw_63", "mulaw", 63.0)],
        "final nonlinear INT5 treatment ladder differs")
    require(value["query_partition"] == {
        "parameter_selection": "even_local_query_indices",
        "heldout_confirmation": "odd_local_query_indices",
        "selection_opened_before_confirmation": True},
        "final nonlinear INT5 query partition differs")
    require(value["quality"] == {
        "maximum_cross_dataset_mean_ndcg_loss_vs_fp32": .003,
        "maximum_per_dataset_ndcg_loss_vs_fp32": .0075,
        "maximum_confirmation_mean_ndcg_regression_vs_uniform": 0.0,
        "maximum_confirmation_per_dataset_regression_vs_uniform": .002},
        "final nonlinear INT5 quality gates differ")
    representation = value["representation"]
    require((representation["bits"], representation["dimensions"],
             representation["vectors_per_query"],
             representation["result_k"],
             representation["bytes_per_document"]) == (5, 384, 64, 10, 244),
            "final nonlinear INT5 representation differs")
    require(value["activation"]["native_executable_sha256"] is None and
            value["native_timing"]["run_condition"] ==
            "selected_nonlinear_passes_heldout_quality",
            "final nonlinear INT5 conditional native timing differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, int]:
    queries = sum(row["queries"] for row in contract["datasets"])
    seeds = 3
    treatments = len(contract["treatments"])
    return {"datasets": len(contract["datasets"]), "seeds": seeds,
            "treatments": treatments,
            "query_seed_treatment_rows": queries * seeds * treatments,
            "native_rows": len(contract["datasets"]) * seeds * treatments * 2}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-final-nonlinear-int5.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        value = plan(load_contract(args.contract))
        if args.self_test:
            require(value == {"datasets": 4, "seeds": 3, "treatments": 5,
                    "query_seed_treatment_rows": 6780, "native_rows": 120},
                    "final nonlinear INT5 plan differs")
            print("NeuRoute final nonlinear INT5 planner self-test passed")
        else:
            print(json.dumps(value, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"plan-neuroute-final-nonlinear-int5: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
