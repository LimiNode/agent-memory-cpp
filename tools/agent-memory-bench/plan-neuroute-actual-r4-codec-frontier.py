#!/usr/bin/env python3
"""Validate the preregistered actual-R4 stage-specific codec frontier."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and
            value.get("family") == "neuroute_actual_r4_codec_frontier",
            "actual-R4 codec frontier identity differs")
    require(value["scalar_grid"]["integer_bits"] ==
            [4, 5, 6, 7, 8, 9, 10, 12] and
            value["scalar_grid"]["references"] == ["fp32", "fp16"],
            "actual-R4 codec width grid differs")
    expected = [("uniform", 0.0), ("power", .5), ("power", .625),
                ("power", .75), ("power", .875), ("mulaw", 15.0),
                ("mulaw", 63.0)]
    actual = [(row["kind"], float(row["parameter"]))
              for row in value["scalar_grid"]["companders"]]
    require(actual == expected and value["routing_storage_modes"] ==
            ["int8", "nonlinear_int5_power_half"],
            "actual-R4 codec treatment grid differs")
    require(value["partitions"] == {
        "configuration": 76, "internal_locked_replay": 76,
        "internal_is_not_new_untouched_heldout": True,
        "new_external_confirmation_required_for_production_license": True},
        "actual-R4 codec partition claim differs")
    return value


def treatment_id(bits: int, kind: str, parameter: float) -> str:
    suffix = "uniform" if kind == "uniform" else (
        f"power_{int(round(parameter * 1000)):03d}" if kind == "power"
        else f"mulaw_{int(parameter)}")
    return f"int{bits}_{suffix}"


def treatments(contract: dict[str, Any]) -> list[dict[str, Any]]:
    result = [{"id": "fp32", "kind": "fp32", "record_bytes": 1536},
              {"id": "fp16", "kind": "fp16", "record_bytes": 768}]
    for bits in contract["scalar_grid"]["integer_bits"]:
        for compander in contract["scalar_grid"]["companders"]:
            kind = compander["kind"]
            parameter = float(compander["parameter"])
            result.append({"id": treatment_id(bits, kind, parameter),
                "kind": "integer", "bits": bits,
                "compander": {"kind": kind, "parameter": parameter},
                "record_bytes": (384 * bits + 7) // 8 + 4})
    require(len(result) == 58 and len({row["id"] for row in result}) == 58,
            "actual-R4 codec expanded treatment grid differs")
    return result


def self_test() -> None:
    contract = load_contract(THIS /
        "neuroute-actual-r4-codec-frontier.example.json")
    rows = treatments(contract)
    require(next(row for row in rows if row["id"] == "int5_power_500")[
                "record_bytes"] == 244 and
            next(row for row in rows if row["id"] == "int12_uniform")[
                "record_bytes"] == 580,
            "actual-R4 codec record-byte expansion differs")
    print("NeuRoute actual-R4 codec frontier planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-actual-r4-codec-frontier.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
        else:
            load_contract(args.contract)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"plan-neuroute-actual-r4-codec-frontier: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
