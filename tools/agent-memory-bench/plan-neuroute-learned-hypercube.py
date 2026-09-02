#!/usr/bin/env python3
"""Validate the prototype-only learned binary-hypercube ceiling contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


FAMILY = "neuroute_learned_hypercube_prototype_ceiling"
THIS = Path(__file__).resolve().parent


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY,
            "learned-hypercube contract identity differs")
    require(value.get("code_bits") == 256 and value.get("iterations") == [0, 2, 4, 8],
            "learned-hypercube training grid differs")
    require(value.get("positive_prototypes") == [1, 2, 4, 8],
            "learned-hypercube teacher positives differ")
    require(value.get("balance") == "per_bit_median_with_deterministic_tie_break",
            "learned-hypercube balance rule differs")
    require(value.get("decorrelation_penalty") == 0.2,
            "learned-hypercube decorrelation rule differs")
    require(value.get("router") == "none_prototype_only",
            "learned-hypercube router scope differs")
    require(value.get("production_selection") is False,
            "learned-hypercube production selection must be forbidden")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "family": FAMILY,
        "iterations": contract["iterations"],
        "positive_prototypes": contract["positive_prototypes"],
        "required_diagnostics": [
            "teacher_topk_recall",
            "hamming_radius_quantiles",
            "per_bit_entropy",
            "subcode_bucket_occupancy",
            "bit_correlation",
            "prototype_code_bytes",
        ],
        "followup": "metric_router_only_if_prototype_ceiling_is_positive",
    }


def self_test(contract_path: Path) -> int:
    try:
        contract = load_contract(contract_path)
        result = plan(contract)
        require(result["iterations"] == [0, 2, 4, 8]
                and result["positive_prototypes"] == [1, 2, 4, 8],
                "learned-hypercube plan expansion differs")
        changed = json.loads(json.dumps(contract))
        changed["router"] = "neural"
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "changed.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            try:
                load_contract(path)
            except ValueError:
                pass
            else:
                raise ValueError("neural router was accepted in prototype ceiling")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"learned-hypercube planner self-test failed: {error}", file=sys.stderr)
        return 1
    print("Learned-hypercube planner self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-learned-hypercube.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        return self_test(args.contract) if args.self_test else (
            print(json.dumps(plan(load_contract(args.contract)), indent=2, sort_keys=True)) or 0)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-learned-hypercube: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
