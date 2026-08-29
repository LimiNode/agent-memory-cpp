#!/usr/bin/env python3
"""Validate and print the frozen R0 ambiguity diagnostic matrix."""

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
            and value.get("family")
            == "neuroute_representation_ambiguity_diagnostic",
            "representation-ambiguity contract family differs")
    require(all(isinstance(digest, str) and len(digest) == 64
                and all(character in "0123456789abcdef" for character in digest)
                for digest in value["activation"].values()),
            "representation-ambiguity activation hashes differ")
    require(value["route"] == {
        "dataset": "de-1m", "documents": 1000000, "width": 16,
        "seeds": [2026082701, 2026082702, 2026082703]},
        "representation-ambiguity route differs")
    representation = value["representation"]
    require(representation["id"] == "r0"
            and representation["feature_count"] == 22
            and representation["scope"] == "within_query_top1024"
            and representation["quantization_bits"] == [8, 12],
            "representation-ambiguity R0 definition differs")
    diagnostic = value["diagnostic"]
    require(diagnostic["sampled_queries_per_seed"] == 256
            and diagnostic["nearest_neighbor_counts"] == [1, 4, 8, 16]
            and diagnostic["cross_validation_folds"] == 5,
            "representation-ambiguity diagnostic matrix differs")
    require(set(value["cache_manifest_sha256"])
            == {str(seed) for seed in value["route"]["seeds"]},
            "representation-ambiguity cache bindings differ")
    require(value["decision"]["native_confirmation_forbidden"] is True
            and value["decision"]["production_selection_forbidden"] is True,
            "representation-ambiguity activation boundary differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset": contract["route"]["dataset"],
        "seed_count": len(contract["route"]["seeds"]),
        "collision_query_count": 8141 * len(contract["route"]["seeds"]),
        "sampled_query_count": contract["diagnostic"]["sampled_queries_per_seed"]
        * len(contract["route"]["seeds"]),
        "quantization_bits": contract["representation"]["quantization_bits"],
        "nearest_neighbor_counts": contract["diagnostic"][
            "nearest_neighbor_counts"],
        "cross_validation_folds": contract["diagnostic"][
            "cross_validation_folds"],
        "representation_ladder_predeclared": True,
        "native_confirmation_forbidden": True,
    }


def self_test() -> None:
    contract = load_contract(Path(__file__).with_name(
        "neuroute-representation-ambiguity.example.json"))
    current = plan(contract)
    require(current["collision_query_count"] == 24423
            and current["sampled_query_count"] == 768,
            "representation-ambiguity planner self-test differs")
    print("NeuRoute representation-ambiguity planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).with_name(
        "neuroute-representation-ambiguity.example.json"))
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
        print(f"plan-neuroute-representation-ambiguity: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
