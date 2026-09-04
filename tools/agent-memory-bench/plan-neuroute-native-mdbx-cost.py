#!/usr/bin/env python3
"""Validate the frozen native MDBX cost-frontier protocol."""

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
            and value.get("family") == "neuroute_native_mdbx_cost_protocol",
            "native MDBX cost identity differs")
    require(value.get("claim_scope")
            == "observed_languages_native_warm_cost_characterization_no_confirmation",
            "native MDBX cost claim scope differs")
    activation = value.get("activation", {})
    require(activation == {
        "training_sanity_result_sha256":
            "02985871f9fd70d3a8634ef42840f7e799957f24fd0797278560a22dc848c5d6",
        "training_sanity_evidence_sha256":
            "80e1de6387379666a1b914067714172e481a3ae084a9906351fa1ba293f5d673",
        "training_sanity_model_set_sha256":
            "5cfdb8d055ddd32334ab68135e9c6e267c635ab1f29ec56766bdcdd11ebf5729",
    }, "native MDBX cost activation differs")
    require([(row.get("id"), row.get("language"), row.get("configuration_queries"))
             for row in value.get("datasets", [])]
            == [("de-25k", "de", 76), ("fr-25k", "fr", 85),
                ("ja-25k", "ja", 215)],
            "native MDBX cost datasets differ")
    routes = value.get("routes", {})
    require(routes.get("learned") == {
        "treatment": "raw_euclidean_mined_pairs",
        "seeds": [2026082701, 2026082702, 2026082703],
        "bits": 12,
        "document_replication": 1,
        "probe_budgets": [16, 32, 64, 128, 256, 512],
    }, "native MDBX learned route differs")
    require(routes.get("pca") == {
        "treatment": "symmetric_pca_control",
        "bits": 8,
        "document_replication": 4,
        "probe_budgets": [16],
    }, "native MDBX PCA route differs")
    require(value.get("candidate_pipeline") == {
        "candidate_mass_target": 0.1,
        "oversize_bucket_policy": "read_then_skip_without_mutating_generation_set_v1",
        "hamming_limit": 768,
        "adc_limit": 256,
        "exact_e5_rerank": False,
    }, "native MDBX candidate pipeline differs")
    require(value.get("storage") == {
        "backend": "libmdbx_via_mdbx_containers_key_value_table",
        "table": "neuroute_postings",
        "key_layout": "route_u8_address_u16be_kind_u8_page_u32be_v1",
        "posting_encoding": "packed_u32le",
        "page_entries": 256,
        "transaction_scope": "one_read_only_transaction_per_query",
        "directory_entry": "u32le_posting_count_and_u32le_page_count",
    }, "native MDBX storage layout differs")
    timing = value.get("timing", {})
    require(timing.get("cache_regime") == "warm_os_and_mdbx_page_cache"
            and timing.get("warmup_full_query_passes") == 2
            and timing.get("measured_full_query_passes") == 9
            and timing.get("clock") == "std_chrono_steady_clock"
            and timing.get("query_aggregation")
                == "median_per_query_across_passes_then_p50_p95_p99_across_queries_v1"
            and timing.get("stages") == [
                "address_generation", "mdbx_lookup_and_decode",
                "generation_array_dedup_and_ceiling", "hamming_and_top_k",
                "binary_adc_and_top_k", "total"],
            "native MDBX timing protocol differs")
    require(value.get("interpretation") == {
        "primary_comparison": "raw_euclidean_512_vs_pca_16_total_p50_and_p95_per_language",
        "secondary_comparison": "lowest_learned_budget_not_slower_than_pca_16_per_language",
        "old_probe_gate_reopened": False,
        "confirmation_claims_permitted": False,
        "scale_transfer_permitted": False,
        "next": "use_measured_native_cost_frontier_for_relevance_aware_v4_selection",
    }, "native MDBX interpretation differs")
    return value


def rows(contract: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for dataset in contract["datasets"]:
        for seed in contract["routes"]["learned"]["seeds"]:
            for probes in contract["routes"]["learned"]["probe_budgets"]:
                result.append({"dataset": dataset["id"], "route": "learned",
                               "seed": seed, "probes": probes})
        result.append({"dataset": dataset["id"], "route": "pca",
                       "seed": None, "probes": 16})
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-native-mdbx-cost.example.json")
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract)
        print(json.dumps({
            "family": contract["family"],
            "dataset_count": len(contract["datasets"]),
            "model_index_count": 12,
            "timing_row_count": len(rows(contract)),
            "measured_query_passes": contract["timing"]["measured_full_query_passes"],
            "claim_scope": contract["claim_scope"],
        }, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-native-mdbx-cost: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
