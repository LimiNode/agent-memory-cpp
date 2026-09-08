#!/usr/bin/env python3
"""Validate and print the frozen 12-bit A@256 scale-transfer matrix."""

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
    require(value.get("schema_version") == 1 and value.get("family") == "neuroute_frozen_scale_transfer",
            "frozen scale-transfer contract family differs")
    require(value.get("claim_scope") == "same_language_nested_scale_transfer_only_no_width_or_budget_selection",
            "frozen scale-transfer claim scope differs")
    require([(row.get("id"), row.get("documents")) for row in value.get("scales", [])]
            == [("de-25k", 25000), ("de-100k", 100000), ("de-1m", 1000000)],
            "frozen scale-transfer scales differ")
    require(value.get("route") == {
        "bits": 12, "probes": 256, "candidate_mass_target": 0.1,
        "threshold_policies": ["per_scale_document_median", "frozen_de_25k_document_median"],
        "primary_threshold_policy": "per_scale_document_median",
    }, "frozen scale-transfer route differs")
    require(value.get("cascade") == {
        "oracle_k": 10, "hamming_limit": 768, "adc_limit": 64, "exact_limit": 64,
        "result_k": 10, "serving_modes": ["binary_adc_only", "quality_exact_e5_64"],
    }, "frozen scale-transfer cascade differs")
    require(value.get("storage", {}).get("dependency_provenance") == {
        "libmdbx_commit": "fc8b8e4697e0ef8b2cd5aee1f2d9fb0974fc665f",
        "mdbx_containers_commit": "e9e9f2fd5139f7fb386afd458fcdd8e20d7ec6e3",
        "required_resolution": "repository_pinned_external_submodules",
    }, "frozen scale-transfer storage provenance differs")
    require(value.get("native_timing") == {
        "cache_state": "warm", "warmup_passes": 2, "measured_passes": 9,
        "required_hamming_backend": "hardware_popcount",
        "stages": ["address_generation", "mdbx_lookup_and_decode",
                   "generation_array_dedup_and_ceiling", "hamming_score_materialization",
                   "hamming_sparse_distance", "hamming_top_k_partition",
                   "hamming_top_k_sort", "hamming_result_materialization",
                   "hamming_and_top_k", "binary_adc_and_top_k", "exact_e5_and_top_k", "total"],
        "native_python_sequence_replay_required": True,
    }, "frozen scale-transfer native timing differs")
    require([row.get("seed") for row in value.get("frozen_models", [])]
            == [2026082701, 2026082702, 2026082703], "frozen scale-transfer seeds differ")
    return value


def plan(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"scale": scale["id"], "documents": scale["documents"], "seed": model["seed"],
             "threshold_policy": policy, "bits": 12, "probes": 256,
             "hamming_limit": 768, "adc_limit": 64, "exact_limit": 64}
            for scale in contract["scales"] for model in contract["frozen_models"]
            for policy in contract["route"]["threshold_policies"]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-frozen-scale-transfer.example.json")
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract)
        print(json.dumps({"schema_version": 1, "family": "neuroute_frozen_scale_transfer_plan",
                          "rows": plan(contract)}, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-frozen-scale-transfer: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
