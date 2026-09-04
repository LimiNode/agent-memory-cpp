#!/usr/bin/env python3
"""Validate and summarize the routing-architecture bake-off contract."""

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
    require(value.get("schema_version") == 1,
            "routing bake-off schema differs")
    require(value.get("family") == "neuroute_routing_architecture_bakeoff",
            "routing bake-off family differs")
    require(value["architectures"] == [
        "direct_document_ivf", "learned_semantic_router_replication",
        "lthq_ordinal_router", "float_ivf_local_residual_k8"],
            "routing architecture matrix differs")
    require(value["learned_router"]["bits"] == [12, 14, 16]
            and value["learned_router"]["replication"] == [1, 2, 3, 4],
            "learned router matrix differs")
    require(value["evaluation"]["routing_ceiling_required"] is True
            and value["decision"]["global_prototype_scan_product_path"] is False,
            "routing bake-off decision boundary differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    ivf = contract["ivf"]
    return {
        "architectures": contract["architectures"],
        "ivf_points": len(ivf["nlist"]) * len(ivf["nprobe"])
                       * len(ivf["candidate_budgets"]),
        "learned_router_points": len(contract["learned_router"]["bits"])
                                 * len(contract["learned_router"]["replication"]),
        "ordinal_points": len(contract["ordinal_router"]["levels"])
                          * len(contract["ordinal_router"]["thresholds"]),
        "routing_ceiling_required": True,
        "global_prototype_scan_product_path": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).with_name(
        "neuroute-routing-architecture-bakeoff.example.json"))
    args = parser.parse_args()
    try:
        print(json.dumps(plan(load_contract(args.contract)), indent=2,
                         sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-routing-architecture-bakeoff: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
