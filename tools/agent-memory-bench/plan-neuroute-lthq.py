#!/usr/bin/env python3
"""Validate the retrieval-supervised LTHQ research contract."""

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
    require(value.get("schema_version") == 1, "LTHQ schema differs")
    require(value.get("family") == "neuroute_lthq_retrieval_supervised",
            "LTHQ family differs")
    require(value.get("levels") == [3, 4, 5], "LTHQ levels differ")
    require(value["train"]["source"] == "independent_teacher_cache_required",
            "LTHQ train source is not independent")
    require(value["objective"]["threshold_order_constraint"] == "strict_sorted",
            "LTHQ threshold ordering differs")
    require(value["evaluation"]["partitions"] == ["config", "internal"],
            "LTHQ partitions differ")
    require(value["decision"]["native_confirmation_forbidden"] is True,
            "LTHQ activation boundary differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    levels = contract["levels"]
    return {
        "fit_count": len(levels) * len(contract["seeds"]),
        "levels": levels,
        "payload_bytes_per_vector": {
            str(level): (384 * (level - 1) + 7) // 8 for level in levels
        },
        "controls": contract["evaluation"]["controls"],
        "independent_teacher_cache_required": True,
        "native_confirmation_forbidden": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=Path(__file__).with_name("neuroute-lthq.example.json"))
    args = parser.parse_args()
    try:
        print(json.dumps(plan(load_contract(args.contract)), indent=2,
                         sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-lthq: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
