#!/usr/bin/env python3
"""Materialize deterministic leakage-safe query partitions for semantic routing."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent


def load_planner() -> Any:
    spec = importlib.util.spec_from_file_location("direct_semantic_address_planner",
                                                   THIS / "plan-direct-learned-semantic-address.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load direct semantic address planner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load_planner()


def ordered_ids(query_ids: list[str], prefix: str) -> list[str]:
    require(len(query_ids) == len(set(query_ids)) and all(isinstance(value, str) for value in query_ids),
            "direct semantic address query IDs differ")
    return sorted(query_ids, key=lambda value: (hashlib.sha256(prefix.encode("utf-8") + value.encode("utf-8")).digest(), value))


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def materialize(query_ids: list[str], contract: dict[str, Any]) -> dict[str, Any]:
    partitions = contract["partitions"]
    ordered = ordered_ids(query_ids, partitions["prefix_utf8"])
    require(len(ordered) == partitions["query_count"], "direct semantic address query count differs")
    train_end = partitions["training"]
    select_end = train_end + partitions["configuration_selection"]
    result = {"schema_version": 1, "family": "direct_learned_semantic_address_splits_v1",
              "partition_algorithm": partitions["algorithm"], "partition_prefix_utf8": partitions["prefix_utf8"],
              "training_query_ids": ordered[:train_end],
              "configuration_selection_query_ids": ordered[train_end:select_end],
              "internal_evaluation_query_ids": ordered[select_end:]}
    require(len(result["internal_evaluation_query_ids"]) == partitions["internal_evaluation"],
            "direct semantic address internal split differs")
    return result


def self_test() -> None:
    contract = planner.load_contract(THIS / "direct-learned-semantic-address.example.json")
    contract = {**contract, "partitions": {**contract["partitions"], "query_count": 6,
                "training": 3, "configuration_selection": 2, "internal_evaluation": 1}}
    result = materialize(["q3", "q1", "q5", "q2", "q0", "q4"], contract)
    members = sum((result[key] for key in ("training_query_ids", "configuration_selection_query_ids", "internal_evaluation_query_ids")), [])
    require(len(members) == len(set(members)) == 6 and materialize(["q3", "q1", "q5", "q2", "q0", "q4"], contract) == result,
            "direct semantic address split materializer differs")
    print("direct semantic address split materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "direct-learned-semantic-address.example.json")
    parser.add_argument("--query-ids", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test(); return 0
        if args.query_ids is None or args.output is None:
            parser.error("--query-ids and --output are required")
        query_ids = json.loads(args.query_ids.read_text(encoding="utf-8"))
        require(isinstance(query_ids, list), "direct semantic address query ID payload differs")
        args.output.write_text(json.dumps(materialize(query_ids, planner.load_contract(args.contract)), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"materialize-direct-semantic-address-splits: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
