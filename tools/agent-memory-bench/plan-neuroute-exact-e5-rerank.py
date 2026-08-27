#!/usr/bin/env python3
"""Validate and print the frozen exact-E5 rerank ablation matrix."""

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
    require(value.get("schema_version") == 1
            and value.get("family") == "neuroute_exact_e5_rerank_ablation",
            "exact-E5 ablation contract family differs")
    require(value.get("claim_scope") == "configuration_only_frozen_A_at_256_no_training_no_scale_claim",
            "exact-E5 ablation claim scope differs")
    require(value.get("datasets") == [
        {"id": "de-25k", "language": "de", "configuration_queries": 76},
        {"id": "fr-25k", "language": "fr", "configuration_queries": 85},
        {"id": "ja-25k", "language": "ja", "configuration_queries": 215},
    ], "exact-E5 ablation datasets differ")
    route = value.get("frozen_route", {})
    require(route == {
        "treatment": "raw_euclidean_document_mining_control",
        "source_treatment": "raw_euclidean_mined_pairs",
        "seeds": [2026082701, 2026082702, 2026082703],
        "bits": 12, "probes": 256, "candidate_mass_target": 0.1,
    }, "exact-E5 ablation route differs")
    cascade = value.get("cascade", {})
    require(cascade.get("oracle_k") == 10 and cascade.get("hamming_limit") == 768
            and cascade.get("adc_limits") == [64, 128, 256, 512]
            and cascade.get("result_k") == 10,
            "exact-E5 ablation cascade differs")
    require(value.get("native_timing", {}).get("scope")
            == "resident_contiguous_fp32_exact_rerank_lower_bound",
            "exact-E5 native timing scope differs")
    return value


def plan(contract: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dataset in contract["datasets"]:
        for seed in contract["frozen_route"]["seeds"]:
            rows.append({
                "dataset": dataset["id"], "seed": seed,
                "probes": contract["frozen_route"]["probes"],
                "hamming_limit": contract["cascade"]["hamming_limit"],
                "adc_limits": contract["cascade"]["adc_limits"],
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-exact-e5-rerank.example.json")
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract)
        print(json.dumps({"schema_version": 1, "family": "neuroute_exact_e5_rerank_plan",
                          "rows": plan(contract)}, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-exact-e5-rerank: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
