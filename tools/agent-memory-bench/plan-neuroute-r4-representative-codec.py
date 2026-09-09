#!/usr/bin/env python3
"""Validate the frozen R4 representative physical-codec ladder."""
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
            "neuroute_r4_representative_codec_frontier",
            "R4 representative-codec contract identity differs")
    require(value["route"] == {"dataset": "de-1m", "documents": 1000000,
            "width": 16, "seeds": [2026082701, 2026082702, 2026082703]},
            "R4 representative-codec route differs")
    require([(row["id"], row["record_bytes"]) for row in value["representations"]]
            == [("fp32", 1536), ("fp16", 768), ("int8", 388),
                ("int6", 292), ("int5", 244)],
            "R4 representative-codec ladder differs")
    require(value["frozen_algorithm"]["representatives_per_address"] == 32
            and value["frozen_algorithm"]["headline_candidate_fraction"] == 0.005
            and value["frozen_algorithm"]["candidate_fraction_budgets"]
            == [0.003, 0.004, 0.005],
            "R4 representative-codec frozen algorithm differs")
    require(value["configuration_gates"] == {
        "maximum_mean_actionable_loss": 0.003,
        "maximum_every_seed_actionable_loss": 0.006,
        "maximum_mean_ndcg_loss": 0.002,
        "maximum_every_seed_ndcg_loss": 0.004,
        "selection": "smallest_record_bytes_passing_all_gates_else_fp32",
    }, "R4 representative-codec gates differ")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    seeds = len(contract["route"]["seeds"])
    codecs = len(contract["representations"])
    return {
        "dataset": "de-1m",
        "seed_count": seeds,
        "codec_count": codecs,
        "physical_stores": seeds * codecs,
        "configuration_rows": seeds * codecs,
        "internal_rows": seeds * codecs,
        "model_fits": 0,
        "partitions_opened": ["configuration", "internal"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).resolve().parent /
                        "neuroute-r4-representative-codec.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        value = plan(load_contract(args.contract))
        if args.self_test:
            require(value["physical_stores"] == 15 and value["model_fits"] == 0,
                    "R4 representative-codec planner self-test differs")
            print("NeuRoute R4 representative-codec planner self-test passed")
        else:
            print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-r4-representative-codec: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
