#!/usr/bin/env python3
"""Validate and summarize the semantic-anchor Hamming relocation contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


FAMILY = "neuroute_semantic_anchor_relocation_ceiling"
THIS = Path(__file__).resolve().parent


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY,
            "semantic-anchor contract identity differs")
    require(value.get("code_bits") in (256, 384, 512), "code width differs")
    require(value.get("anchor_sets") == ["centroid", "prototype", "oracle_prototype"],
            "anchor sets differ")
    require(value.get("controls") == ["q_global", "q_restricted", "c_seeded",
                                        "p_seeded", "p_oracle"],
            "matched controls differ")
    require(value.get("anchor_counts") == [1, 2, 4, 8], "anchor counts differ")
    require(value.get("radii") == [0, 1, 2, 3, 4, 6, 8, 12, 16], "radius grid differs")
    require(value.get("budgets") == [128, 256, 512, 1024], "budget grid differs")
    require(value.get("selection") == {
        "partition": "configuration_only",
        "primary": "final_exact_e5_top10_survival_at_fixed_unique_candidate_budget",
        "qrels_role": "terminal_utility_only",
        "production_selection": False,
    }, "selection rule differs")
    return value


def plan(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "family": FAMILY,
        "control_count": len(contract["controls"]),
        "anchor_count_grid": contract["anchor_counts"],
        "radius_count": len(contract["radii"]),
        "budget_count": len(contract["budgets"]),
        "required_diagnostics": [
            "per_query_r50_r90_r95_r99",
            "bucket_probes",
            "raw_posting_entries_scanned",
            "unique_documents_after_union",
            "unique_candidates",
            "exact_e5_top10_survival",
            "frozen_r4_boundary_retention",
            "terminal_ndcg",
        ],
    }


def self_test(contract_path: Path) -> int:
    try:
        contract = load_contract(contract_path)
        result = plan(contract)
        require(result["control_count"] == 5 and result["radius_count"] == 9
                and result["budget_count"] == 4,
                "semantic-anchor plan expansion differs")
        changed = json.loads(json.dumps(contract))
        changed["controls"].remove("q_restricted")
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "changed.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            try:
                load_contract(path)
            except ValueError:
                pass
            else:
                raise ValueError("missing Q-restricted control was accepted")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"semantic-anchor planner self-test failed: {error}", file=sys.stderr)
        return 1
    print("Semantic-anchor relocation planner self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-semantic-anchor-relocation.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract)
        if args.self_test:
            return self_test(args.contract)
        print(json.dumps(plan(contract), indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-semantic-anchor-relocation: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
