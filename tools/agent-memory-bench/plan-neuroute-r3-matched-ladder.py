#!/usr/bin/env python3
"""Validate and print the frozen matched R0/R3a/R3b/R3c ladder."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1
            and value.get("family") == "neuroute_r3_matched_ladder",
            "R3 matched-ladder contract family differs")
    require(all(isinstance(digest, str) and len(digest) == 64
                and all(character in "0123456789abcdef" for character in digest)
                for digest in value["activation"].values()),
            "R3 matched-ladder activation hashes differ")
    require(value["route"] == {
        "dataset": "de-1m", "documents": 1000000, "width": 16,
        "seeds": [2026082701, 2026082702, 2026082703]},
        "R3 matched-ladder route differs")
    require(value["query_partitions"] == {
        "training_queries": 8141, "configuration_queries": 76,
        "internal_evaluation_queries": 76,
        "training_then_configuration_then_internal_required": True},
        "R3 matched-ladder partitions differ")
    require(value["representations"]["variants"] == [
        "r0_scalar", "r3a_occupancy", "r3b_residual_mean",
        "r3c_residual_shape"]
            and value["representations"]["additive_ladder"] is True
            and value["representations"]["random_projection_forbidden"] is True,
            "R3 matched representation ladder differs")
    require(value["prototype_shortlist"]["requested_prototypes_per_address"] == 8
            and value["prototype_shortlist"]["address_shortlist"] == 1024
            and value["prototype_shortlist"]["shortlist_frozen_before_reranking"]
            is True, "R3 matched shortlist differs")
    require(value["training"][
        "same_teacher_data_optimizer_seeds_for_every_variant"] is True
            and value["evaluation"]["address_budgets"] == [128, 256, 512]
            and value["evaluation"]["headline_address_budget"] == 256,
            "R3 matched comparison differs")
    require(value["decision"]["stateful_policy_forbidden"] is True
            and value["decision"]["native_confirmation_forbidden"] is True
            and value["decision"]["production_selection_forbidden"] is True,
            "R3 matched activation boundary differs")
    return value


def parameter_counts(contract: dict[str, Any]) -> dict[str, int]:
    query_parameters = 384 * 32 + 32
    joined = 32 * 5
    result = {}
    for variant in contract["representations"]["variants"]:
        local = int(contract["models"]["local_input_dimensions"][variant])
        hidden = int(contract["models"]["score_hidden_dimensions"][variant])
        result[variant] = (query_parameters + local * 32 + 32
                           + joined * hidden + hidden + hidden + 1)
    return result


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    counts = parameter_counts(contract)
    ratio = max(counts.values()) / min(counts.values())
    require(ratio <= contract["models"]["maximum_parameter_count_ratio"],
            "R3 matched parameter budget differs")
    variants = contract["representations"]["variants"]
    seeds = contract["route"]["seeds"]
    return {
        "dataset": "de-1m",
        "seed_count": len(seeds),
        "variants": variants,
        "model_fits": len(seeds) * len(variants),
        "configuration_rows": len(seeds) * len(variants),
        "internal_rows": len(seeds) * (len(variants) + 2),
        "parameter_counts": counts,
        "parameter_count_ratio": ratio,
        "address_budgets": contract["evaluation"]["address_budgets"],
        "full_384d_interactions": True,
        "stateful_policy_forbidden": True,
        "native_confirmation_forbidden": True,
    }


def self_test() -> None:
    current = plan(load_contract(Path(__file__).with_name(
        "neuroute-r3-matched-ladder.example.json")))
    require(current["model_fits"] == 12
            and current["configuration_rows"] == 12
            and current["internal_rows"] == 18
            and current["parameter_count_ratio"] < 1.01,
            "R3 matched-ladder planner self-test differs")
    print("NeuRoute R3 matched-ladder planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).with_name(
        "neuroute-r3-matched-ladder.example.json"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
        else:
            print(json.dumps(plan(load_contract(args.contract)), indent=2,
                             sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-r3-matched-ladder: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
