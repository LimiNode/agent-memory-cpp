#!/usr/bin/env python3
"""Validate the frozen-matrix binary centroid routing surrogate plan."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
FAMILY = "binary_centroid_routing_surrogate_v1"


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY and value.get("purpose") == "external_calibration_only_binary_centroid_routing_surrogate_against_frozen_float_semantic_ivf_control", "binary centroid routing contract identity differs")
    require(value.get("faiss_version") == "1.13.2" and value.get("centroid_source") == "frozen_serialized_float_semantic_ivf_centroid_indexes_and_document_assignments_only", "binary centroid routing source differs")
    require(value.get("binary_code") == {"lengths": [128, 256, 512], "construction": "centroid_component_sign_after_deterministic_orthogonal_projection_v1", "seed": 20260825} and value.get("binary_shortlist_multipliers") == [2, 4] and value.get("binary_routing") == "full_hamming_scan_all_centroid_codes_then_exact_float_rerank_v1" and value.get("float_rerank") == "exact_inner_product_on_binary_shortlist_only_v1", "binary centroid routing algorithm differs")
    require(value.get("scales") == [{"id": "es-100k", "documents": 100000, "centroid_counts": [1024, 4096]}, {"id": "es-1m", "documents": 1000000, "centroid_counts": [4096, 16384]}] and value.get("target_candidate_fractions") == [0.05, 0.10, 0.25] and value.get("cascade") == {"hamming_limit": 768, "adc_limit": 256, "exact_limit": 256, "oracle_k": 10}, "binary centroid routing matrix differs")
    require(value.get("confirmation") == "forbidden" and value.get("library_dependency") == "forbidden_faiss_is_external_benchmark_only", "binary centroid routing scope differs")
    return value


def plan(contract: dict[str, Any], contract_path: Path) -> dict[str, Any]:
    rows = [{"scale": scale["id"], "centroid_count": count, "target_candidate_fraction": fraction, "code_length": length, "binary_shortlist_multiplier": multiplier} for scale in contract["scales"] for count in scale["centroid_counts"] for fraction in contract["target_candidate_fractions"] for length in contract["binary_code"]["lengths"] for multiplier in contract["binary_shortlist_multipliers"]]
    return {"schema_version": 1, "family": "binary_centroid_routing_plan_v1", "contract_sha256": sha256(contract_path), "row_count": len(rows), "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "binary-centroid-routing.example.json"); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        result = plan(load_contract(args.contract), args.contract)
        require(result["row_count"] == 72, "binary centroid routing row count differs")
        print("binary centroid routing planner self-test passed" if args.self_test else json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-binary-centroid-routing: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
