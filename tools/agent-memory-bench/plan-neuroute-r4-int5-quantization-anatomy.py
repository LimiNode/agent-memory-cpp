#!/usr/bin/env python3
"""Validate the frozen R4 INT5 quantization-anatomy protocol."""
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
            "neuroute_r4_int5_quantization_anatomy",
            "R4 INT5 anatomy contract identity differs")
    require(value["route"] == {"dataset": "de-1m",
            "seeds": [2026082701, 2026082702, 2026082703],
            "partition": "internal", "layout_request_offset": 76,
            "queries_per_seed": 76, "shortlist_addresses": 1024,
            "candidate_limit": 5000},
            "R4 INT5 anatomy route differs")
    require([row["id"] for row in value["codecs"]] ==
            ["int5_uniform", "int5_power_050"] and
            value["codecs"][1]["parameter"] == .5,
            "R4 INT5 anatomy codec matrix differs")
    diagnostics = value["component_diagnostics"]
    require(diagnostics["normalized_magnitude_histogram_bins"] == 4096 and
            diagnostics["equal_frequency_deciles"] == 10,
            "R4 INT5 anatomy component diagnostics differ")
    require(value["routing_diagnostics"]["fp32_margin_bins"] ==
            [.0001, .001, .01] and
            value["routing_diagnostics"]["stable_margin_threshold"] == .01 and
            value["routing_diagnostics"]["top_address_count"] == 128,
            "R4 INT5 anatomy routing diagnostics differ")
    require(value["learned_codebook_license"] == {
            "maximum_normalized_code_entropy": .75,
            "minimum_single_decile_query_error_fraction": .50,
            "minimum_stable_margin_argmax_disagreement": .01,
            "minimum_stable_boundary_query_count": 20,
            "maximum_material_boundary_jaccard": .80,
            "minimum_stable_boundary_query_difference_fraction": .05,
            "production_selection_forbidden": True},
            "R4 INT5 anatomy learned-codebook license differs")
    return value


def plan(value: dict[str, Any]) -> dict[str, int]:
    seeds = len(value["route"]["seeds"])
    return {"seeds": seeds, "codecs": len(value["codecs"]),
            "representative_component_passes": 2,
            "query_address_pairs": seeds *
                value["route"]["queries_per_seed"] *
                value["route"]["shortlist_addresses"],
            "decoded_scratch_stores": seeds * len(value["codecs"])}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
        "neuroute-r4-int5-quantization-anatomy.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        result = plan(load_contract(args.contract))
        if args.self_test:
            require(result == {"seeds": 3, "codecs": 2,
                    "representative_component_passes": 2,
                    "query_address_pairs": 233472,
                    "decoded_scratch_stores": 6},
                    "R4 INT5 anatomy plan differs")
            print("NeuRoute R4 INT5 quantization-anatomy planner self-test passed")
        else:
            print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"plan-neuroute-r4-int5-quantization-anatomy: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
