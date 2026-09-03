#!/usr/bin/env python3
"""Validate and print the frozen teacher-objective ablation matrix."""

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
    require(value.get("schema_version") == 1, "teacher-objective schema differs")
    require(value.get("family") == "neuroute_teacher_objective_ablation",
            "teacher-objective family differs")
    require(all(isinstance(digest, str) and len(digest) == 64
                and all(character in "0123456789abcdef" for character in digest)
                for digest in value["activation"].values()),
            "teacher-objective activation hashes differ")
    require(value["route"] == {"dataset": "de-1m", "documents": 1000000,
                                "width": 16,
                                "seeds": [2026082701, 2026082702, 2026082703]},
            "teacher-objective frozen route differs")
    require(value["prototype_shortlist"]["requested_prototypes_per_address"] == 8
            and value["prototype_shortlist"]["address_shortlist"] == 1024
            and value["prototype_shortlist"]["shortlist_frozen_before_reranking"] is True,
            "teacher-objective shortlist differs")
    require(value["teachers"]["variants"] == [
        "static_gain_density", "cascade_independent_density",
        "conditional_marginal_sequence_distillation"],
        "teacher-objective variants differ")
    require(value["evaluation"]["address_budgets"] == [128, 256, 512]
            and value["evaluation"]["headline_address_budget"] == 256,
            "teacher-objective budgets differ")
    require(value["frozen_selection"][
        "architecture_and_training_query_count_per_seed_are_frozen"] is True,
        "teacher-objective parent selection is not frozen")
    require(value["decision"]["native_confirmation_forbidden"] is True
            and value["decision"]["production_selection_forbidden"] is True,
            "teacher-objective activation boundary differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset": contract["route"]["dataset"],
        "seed_count": len(contract["route"]["seeds"]),
        "teacher_variants": contract["teachers"]["variants"],
        "model_fits": len(contract["route"]["seeds"])
        * len(contract["teachers"]["variants"]),
        "internal_rows": len(contract["route"]["seeds"])
        * (len(contract["teachers"]["variants"]) + 2),
        "address_budgets": contract["evaluation"]["address_budgets"],
        "native_confirmation_forbidden": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).with_name(
        "neuroute-teacher-objective-ablation.example.json"))
    args = parser.parse_args()
    try:
        print(json.dumps(plan(load_contract(args.contract)), indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-teacher-objective-ablation: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
