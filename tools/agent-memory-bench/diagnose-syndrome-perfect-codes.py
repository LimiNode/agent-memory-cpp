#!/usr/bin/env python3
"""Compute exact feasibility bounds for coarse syndrome and perfect-code locators."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
FAMILY = "syndrome_perfect_code_locator_feasibility_v1"


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value == {
        "schema_version": 1,
        "family": FAMILY,
        "purpose": "mathematical_feasibility_diagnostic_not_a_retrieval_measurement_or_index_implementation",
        "ambient_bits": 256,
        "coarse_center_counts": [256, 1024, 4096, 16384, 65536],
        "generic_linear_code_case": {"center_count": 4096, "center_dimension": 12, "syndrome_bits": 244},
        "perfect_code_controls": [
            {"id": "hamming-7-4-3", "length": 7, "dimension": 4, "radius": 1},
            {"id": "hamming-15-11-3", "length": 15, "dimension": 11, "radius": 1},
            {"id": "golay-23-12-7", "length": 23, "dimension": 12, "radius": 3},
        ],
        "interpretation": "covering_bound_is_a_lower_bound_over_the_full_binary_cube_and_does_not_measure_the_observed_document_manifold",
    }, "syndrome/perfect-code contract differs")
    return value


def hamming_ball_volume(length: int, radius: int) -> int:
    require(0 <= radius <= length, "Hamming-ball radius differs")
    return sum(math.comb(length, index) for index in range(radius + 1))


def sphere_covering_radius_lower_bound(length: int, center_count: int) -> int:
    require(length > 0 and center_count > 0, "covering-bound dimensions differ")
    target = 1 << length
    for radius in range(length + 1):
        if center_count * hamming_ball_volume(length, radius) >= target:
            return radius
    raise RuntimeError("covering radius is unreachable")


def perfect_control(value: dict[str, Any]) -> dict[str, Any]:
    length, dimension, radius = int(value["length"]), int(value["dimension"]), int(value["radius"])
    centers = 1 << dimension; volume = hamming_ball_volume(length, radius); space = 1 << length
    return {**value, "center_count": centers, "syndrome_bits": length - dimension, "hamming_ball_volume": volume, "sphere_packing_product": centers * volume, "perfect_sphere_identity_holds": centers * volume == space}


def diagnose(contract: dict[str, Any]) -> dict[str, Any]:
    ambient = int(contract["ambient_bits"])
    covering = [{"center_count": value, "sphere_covering_radius_lower_bound": sphere_covering_radius_lower_bound(ambient, value)} for value in contract["coarse_center_counts"]]
    generic = contract["generic_linear_code_case"]
    require((1 << int(generic["center_dimension"])) == int(generic["center_count"]) and ambient - int(generic["center_dimension"]) == int(generic["syndrome_bits"]), "generic syndrome duality differs")
    return {
        "schema_version": 1,
        "family": FAMILY,
        "contract_sha256": None,
        "ambient_bits": ambient,
        "coarse_covering_lower_bounds": covering,
        "generic_linear_code": {**generic, "coset_leader_table_entries": f"2^{generic['syndrome_bits']}", "reverse_small_syndrome_case": {"syndrome_bits": int(generic["center_dimension"]), "center_dimension": ambient - int(generic["center_dimension"]), "center_count": f"2^{ambient - int(generic['center_dimension'])}"}},
        "perfect_code_controls": [perfect_control(item) for item in contract["perfect_code_controls"]],
        "interpretation": contract["interpretation"],
    }


def self_test() -> None:
    contract = load_contract(THIS / "syndrome-perfect-code-diagnostic.example.json")
    require(hamming_ball_volume(7, 1) == 8 and hamming_ball_volume(23, 3) == 2048, "Hamming-ball calculation differs")
    require([sphere_covering_radius_lower_bound(256, count) for count in contract["coarse_center_counts"]] == [107, 103, 100, 97, 95], "coarse covering lower bounds differ")
    result = diagnose(contract)
    require(all(item["perfect_sphere_identity_holds"] for item in result["perfect_code_controls"]), "perfect-code control identity differs")
    print("syndrome/perfect-code diagnostic self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "syndrome-perfect-code-diagnostic.example.json"); parser.add_argument("--output", type=Path); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    try:
        if args.self_test: self_test(); return 0
        if args.output is None: parser.error("--output is required unless --self-test is used")
        result = diagnose(load_contract(args.contract)); result["contract_sha256"] = sha256(args.contract); args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_bytes(canonical(result)); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"diagnose-syndrome-perfect-codes: {error}", file=__import__("sys").stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
