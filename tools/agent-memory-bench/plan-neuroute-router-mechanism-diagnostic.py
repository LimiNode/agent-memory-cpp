#!/usr/bin/env python3
"""Validate and summarize the frozen router mechanism diagnostic protocol."""

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
    require(value.get("schema_version") == 1 and
            value.get("family") == "neuroute_router_mechanism_diagnostic",
            "router mechanism contract identity differs")
    old = value["old_strong_routes"]
    scalable = value["scalable_controls"]
    evaluation = value["evaluation"]
    decision = value["decision"]
    require(old == {
        "regime": "raw_euclidean_dynamic_mining",
        "widths": [12, 14, 16],
        "seeds": [2026082701, 2026082702, 2026082703],
        "scales": ["de-25k", "de-100k", "de-1m"],
    }, "router mechanism old-route matrix differs")
    require(scalable == {
        "regimes": ["matched_25k", "expanded_100k"],
        "widths": [14, 16],
        "seeds": [2026082701, 2026082702, 2026082703],
        "scale": "de-25k",
    }, "router mechanism scalable-control matrix differs")
    require(evaluation.get("queries") == 76 and
            evaluation.get("exact_e5_top_k") == [10, 100] and
            evaluation.get("coverage_targets") == [0.5, 0.75, 0.9, 0.95] and
            evaluation.get("hamming_radii") == [0, 1, 2, 3] and
            evaluation.get("early_probe_budget") == 256,
            "router mechanism evaluation matrix differs")
    require(decision.get("scale") == "de-1m" and
            decision.get("top_k") == 10 and
            decision.get("coverage_target") == 0.9 and
            decision.get("scheduler_followup_is_conditional") is True and
            decision.get("production_selection_forbidden") is True,
            "router mechanism decision differs")
    require(set(value.get("activation", {})) == {
        "width_result_sha256", "width_evidence_sha256",
        "width_materialization_sha256", "wider_result_sha256",
        "wider_evidence_sha256",
    }, "router mechanism activation differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    old = contract["old_strong_routes"]
    scalable = contract["scalable_controls"]
    return {
        "old_route_rows": len(old["widths"]) * len(old["seeds"]) * len(old["scales"]),
        "scalable_control_rows": (len(scalable["regimes"]) * len(scalable["widths"]) *
                                    len(scalable["seeds"])),
        "queries_per_row": contract["evaluation"]["queries"],
        "exact_neighbour_sets": len(old["scales"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-router-mechanism-diagnostic.example.json")
    args = parser.parse_args()
    try:
        print(json.dumps(plan(load_contract(args.contract)), sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-router-mechanism-diagnostic: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
