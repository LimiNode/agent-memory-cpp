#!/usr/bin/env python3
"""Validate and print the frozen R3 document-summary materialization plan."""

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
            and value.get("family") == "neuroute_r3_document_summary",
            "R3 document-summary contract family differs")
    require(all(isinstance(digest, str) and len(digest) == 64
                and all(character in "0123456789abcdef" for character in digest)
                for digest in value["activation"].values()),
            "R3 document-summary activation hashes differ")
    require(value["route"] == {
        "dataset": "de-1m", "documents": 1000000, "width": 16,
        "seeds": [2026082701, 2026082702, 2026082703]},
        "R3 document-summary route differs")
    require(value["prototype_partition"]["requested_prototypes_per_address"] == 8
            and value["prototype_partition"]["summary_is_query_independent"] is True
            and value["prototype_partition"]["summary_is_teacher_blind"] is True,
            "R3 document-summary partition differs")
    direction = value["summaries"]["top_direction"]
    require(value["summaries"]["embedding_dimensions"] == 384
            and direction["iterations"] == 4
            and direction["minimum_local_documents"] == 3
            and value["summaries"]["storage"] == "uncompressed_npy_outside_git",
            "R3 document-summary definition differs")
    require(value["audit"]["address_occupancy_buckets"] == [
        "le_8", "9_16", "17_32", "gt_32"]
            and value["audit"]["note_centroid_slot_is_not_a_document_representative"]
            is True, "R3 document-summary audit differs")
    require(value["decision"]["stateful_policy_forbidden"] is True
            and value["decision"]["native_confirmation_forbidden"] is True
            and value["decision"]["production_selection_forbidden"] is True,
            "R3 document-summary activation boundary differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    seeds = contract["route"]["seeds"]
    return {
        "dataset": "de-1m",
        "seed_count": len(seeds),
        "document_assignments": len(seeds) * contract["route"]["documents"],
        "requested_prototypes_per_address": 8,
        "summary_vector_fields": 3,
        "summary_scalar_fields": 3,
        "power_iterations": contract["summaries"]["top_direction"]["iterations"],
        "occupancy_buckets": contract["audit"]["address_occupancy_buckets"],
        "matched_r3_ladder_licensed_after_replay": True,
        "native_confirmation_forbidden": True,
    }


def self_test() -> None:
    current = plan(load_contract(Path(__file__).with_name(
        "neuroute-r3-document-summary.example.json")))
    require(current["seed_count"] == 3
            and current["document_assignments"] == 3000000
            and current["summary_vector_fields"] == 3,
            "R3 document-summary planner self-test differs")
    print("NeuRoute R3 document-summary planner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).with_name(
        "neuroute-r3-document-summary.example.json"))
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
        print(f"plan-neuroute-r3-document-summary: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
