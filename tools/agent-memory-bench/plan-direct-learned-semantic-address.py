#!/usr/bin/env python3
"""Validate and enumerate the predeclared direct semantic-address study."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1
            and value.get("family") == "direct_learned_semantic_address_protocol_v1",
            "direct semantic address contract identity differs")
    partitions = value.get("partitions")
    require(isinstance(partitions, dict)
            and partitions.get("training") == 324
            and partitions.get("configuration_selection") == 162
            and partitions.get("internal_evaluation") == 162
            and sum(partitions[key] for key in ("training", "configuration_selection", "internal_evaluation"))
            == partitions.get("query_count") == 648,
            "direct semantic address partitions differ")
    addressing = value.get("addressing")
    require(isinstance(addressing, dict)
            and addressing.get("semantic_prefix_bits") == [8, 10, 12, 14, 16]
            and addressing.get("query_probes") == [1, 2, 4, 8, 16]
            and addressing.get("document_replication") == [1, 2, 4]
            and addressing.get("query_routing") == "confidence_ranked_subset_flip_addresses_v1"
            and addressing.get("document_placement") == "document_only_pca_median_nested_prefixes_with_margin_replication_v1",
            "direct semantic address routing grid differs")
    training = value.get("router_training")
    require(isinstance(training, dict)
            and training.get("family") == "deterministic_mlp_384_128_16_v1"
            and training.get("checkpoint") == "fixed_final_epoch"
            and training.get("target") == "mean_document_address_bit_probability_over_exact_e5_top10_v1",
            "direct semantic address training contract differs")
    require(value.get("candidate_mass_targets") == [0.05, 0.1, 0.25]
            and value.get("controls") == ["symmetric_document_head_control",
                                          "learned_direct_address_postings",
                                          "learned_address_then_float_bucket_centroid_refinement",
                                          "exact_float_bucket_centroid_scan_same_postings"],
            "direct semantic address controls differ")
    selection = value.get("selection")
    require(isinstance(selection, dict)
            and selection.get("headline_treatment") == "learned_direct_address_postings"
            and selection.get("candidate_mass_target") == 0.1
            and selection.get("internal_evaluation_may_not_select") is True,
            "direct semantic address selection contract differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    addressing = contract["addressing"]
    rows = [
        {"semantic_prefix_bits": depth, "query_probes": probes,
         "document_replication": replication, "candidate_mass_target": mass}
        for depth in addressing["semantic_prefix_bits"]
        for probes in addressing["query_probes"]
        for replication in addressing["document_replication"]
        for mass in contract["candidate_mass_targets"]
    ]
    return {"schema_version": 1, "family": "direct_learned_semantic_address_plan_v1",
            "row_count": len(rows), "rows": rows}


def self_test() -> None:
    contract = load_contract(THIS / "direct-learned-semantic-address.example.json")
    result = plan(contract)
    require(result["row_count"] == 225 and result["rows"][0]["semantic_prefix_bits"] == 8
            and result["rows"][-1]["document_replication"] == 4,
            "direct semantic address planner differs")
    print("direct semantic address planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "direct-learned-semantic-address.example.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        result = plan(load_contract(args.contract))
        if args.output is None:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-direct-learned-semantic-address: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
