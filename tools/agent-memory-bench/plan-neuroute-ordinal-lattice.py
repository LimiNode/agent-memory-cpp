#!/usr/bin/env python3
"""Validate the learned ordinal lattice router contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1, "ordinal lattice schema differs")
    require(value.get("family") == "neuroute_learned_ordinal_lattice_router",
            "ordinal lattice family differs")
    expected = [("binary12", 12, 2), ("ordinal8x3", 8, 3),
                ("ordinal6x4", 6, 4), ("ordinal4x8", 4, 8),
                ("ordinal10x3", 10, 3)]
    actual = [(row["id"], row["axes"], row["levels"])
              for row in value["routers"]]
    require(actual == expected, "ordinal lattice router matrix differs")
    require(value["training"]["teacher_labels_forbidden_in_projection_fit"],
            "ordinal lattice projection leakage gate differs")
    require(value["probing"]["replication"] == [1, 2, 3, 4],
            "ordinal lattice replication matrix differs")
    require(value["decision"]["global_scan_product_path"] is False,
            "ordinal lattice product boundary differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    points = sum(len(contract["probing"]["cell_budgets"])
                 * len(contract["probing"]["document_budgets"])
                 * len(contract["probing"]["replication"])
                 for _ in contract["routers"])
    return {"routers": contract["routers"], "replay_points": points,
            "cell_budgets": contract["probing"]["cell_budgets"],
            "document_budgets": contract["probing"]["document_budgets"],
            "routing_ceiling_required": True,
            "global_scan_product_path": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=Path(__file__).with_name(
        "neuroute-ordinal-lattice.example.json"))
    args = parser.parse_args()
    try:
        print(json.dumps(plan(load(args.contract)), indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-ordinal-lattice: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
