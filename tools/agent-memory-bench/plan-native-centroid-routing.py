#!/usr/bin/env python3
"""Validate the predeclared native centroid-routing calibration matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
FAMILY = "native_centroid_routing_calibration_v1"


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY and value.get("purpose") == "calibration_only_native_cost_and_matched_mass_quality_comparison_for_frozen_float_semantic_ivf_centroid_routing", "native centroid routing contract identity differs")
    require(value.get("faiss_version") == "1.13.2" and value.get("scales") == [{"id": "es-100k", "documents": 100000, "centroid_counts": [1024, 4096], "input_manifest_sha256": "720fead487f3a7caec62ad190cd93fa79969effd1d0fe825c865ab5d0d437d15"}, {"id": "es-1m", "documents": 1000000, "centroid_counts": [4096, 16384], "input_manifest_sha256": "697f81bc66b37feb47b413fa168f4ae5efd030b9dbbaeb8d0c67ac8d224a9ae7"}], "native centroid routing frozen scales differ")
    require(value.get("evaluation") == {"query_count": 648, "target_candidate_fractions": [.05, .10, .25], "matched_mass_rule": "ranked_centroid_lists_until_target_document_count_v1", "float_oracle": "exact_fp32_inner_product_centroid_order_descending_then_centroid_id_ascending_v1"}, "native centroid routing evaluation differs")
    require(value.get("routing_treatments") == ["exact_fp32_scan", "fp16_centroids_fp32_accumulation", "int8_per_centroid_symmetric_quantized_scan", "rademacher512_symmetric_hamming_then_exact_fp32_rerank", "rademacher512_asymmetric_sign_dot_then_exact_fp32_rerank", "hnsw_fp32_inner_product_then_exact_fp32_rerank"] and value.get("binary_code") == {"bits": 512, "construction": "centroid_component_sign_after_deterministic_rademacher_projection_v1", "seed": 20260825} and value.get("shortlist_multipliers") == [2, 4], "native centroid routing treatments differ")
    require(value.get("hnsw") == {"connectivity": [8, 16], "ef_construction": 200, "ef_search": [16, 32, 64, 128]} and value.get("timing") == {"warmup_repeats": 2, "measured_repeats": 7, "scope": "warm_in_memory_native_centroid_routing_only_excludes_query_embedding_document_cascade_and_index_build_v1"}, "native centroid routing timing matrix differs")
    require(value.get("selection") == "forbidden_results_describe_a_calibration_frontier_only" and value.get("confirmation") == "forbidden" and value.get("library_dependency") == "forbidden_hnswlib_is_external_benchmark_only", "native centroid routing scope differs")
    return value


def plan(contract: dict[str, Any], contract_path: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for scale in contract["scales"]:
        for centroid_count in scale["centroid_counts"]:
            for fraction in contract["evaluation"]["target_candidate_fractions"]:
                rows.extend({"scale": scale["id"], "centroid_count": centroid_count, "target_candidate_fraction": fraction, "treatment": treatment, "shortlist_multiplier": multiplier if "rerank" in treatment else None, "hnsw_connectivity": connectivity if treatment.startswith("hnsw") else None, "hnsw_ef_search": ef_search if treatment.startswith("hnsw") else None} for treatment in contract["routing_treatments"] for multiplier in (contract["shortlist_multipliers"] if "rerank" in treatment else [None]) for connectivity in (contract["hnsw"]["connectivity"] if treatment.startswith("hnsw") else [None]) for ef_search in (contract["hnsw"]["ef_search"] if treatment.startswith("hnsw") else [None]))
    return {"schema_version": 1, "family": "native_centroid_routing_plan_v1", "contract_sha256": sha256(contract_path), "row_count": len(rows), "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "native-centroid-routing.example.json"); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        result = plan(load_contract(args.contract), args.contract)
        require(result["row_count"] == 276, "native centroid routing row count differs")
        print("native centroid routing planner self-test passed" if args.self_test else json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-native-centroid-routing: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
