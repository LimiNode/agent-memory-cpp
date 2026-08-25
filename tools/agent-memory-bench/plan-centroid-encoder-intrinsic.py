#!/usr/bin/env python3
"""Validate the calibration-only centroid-encoder intrinsic matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
FAMILY = "centroid_encoder_intrinsic_v1"


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY and value.get("purpose") == "calibration_only_encoder_selection_for_frozen_float_semantic_ivf_centroid_routing", "centroid encoder contract identity differs")
    require(value.get("faiss_version") == "1.13.2" and value.get("scale") == {"id": "es-1m", "documents": 1000000, "input_manifest_sha256": "697f81bc66b37feb47b413fa168f4ae5efd030b9dbbaeb8d0c67ac8d224a9ae7", "evaluation_manifest_sha256": "616e70a3d0e21a561967b382bebc463b10038280f02ca77477b0b01331c73536"}, "centroid encoder frozen scale differs")
    require(value.get("frozen_float_semantic_ivf") == {"centroid_count": 4096, "evidence_family": "float_semantic_ivf_evidence_v1", "selection_oracle": "exact_float_inner_product_top16_centroids_v1"}, "centroid encoder frozen IVF source differs")
    require(value.get("calibration_queries") == {"dataset": "miracl/miracl", "revision": "5be20db9509754dadad47689368639fcec739c00", "path": "miracl-v1.0-es/topics/topics.miracl-v1.0-es-train.tsv", "sha256": "d19226d18d198d740623deefd2f618e9bc5ed589c48b31e6e717e986a856c4d3", "count": 2162, "usage": "encoder_fitting_and_configuration_selection_only_never_evaluation"}, "centroid encoder calibration-query contract differs")
    require(value.get("encoders") == ["rademacher_sign_control", "random_orthogonal_sign", "pca_sign", "itq_centroids", "itq_centroids_plus_calibration_queries"] and value.get("bit_counts") == [128, 256, 384] and value.get("shortlist_sizes") == [16, 32, 64, 128] and value.get("seed") == 20260825 and value.get("itq_iterations") == 50, "centroid encoder matrix differs")
    require(value.get("selection") == {"primary_metric": "mean_recall_of_float_top16_in_binary_top64_v1", "secondary_metric": "mean_recall_of_float_top16_in_binary_top32_v1", "maximum_selected_configurations": 3, "minimum_top64_recall": 0.95, "minimum_top32_recall": 0.85, "tie_rule": "top64_desc_top32_desc_bits_asc_encoder_id_asc_v1"}, "centroid encoder selection gate differs")
    require(value.get("overcomplete_follow_up") == {"permitted_only_if": "a_non_rademacher_encoder_strictly_improves_top64_recall_from_256_to_384_bits_v1", "not_in_this_matrix": True} and value.get("confirmation") == "forbidden" and value.get("library_dependency") == "forbidden_faiss_is_external_benchmark_only", "centroid encoder scope differs")
    return value


def plan(contract: dict[str, Any], path: Path) -> dict[str, Any]:
    rows = [{"encoder": encoder, "bit_count": bits} for encoder in contract["encoders"] for bits in contract["bit_counts"]]
    return {"schema_version": 1, "family": "centroid_encoder_intrinsic_plan_v1", "contract_sha256": sha256(path), "row_count": len(rows), "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "centroid-encoder-intrinsic.example.json"); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        result = plan(load_contract(args.contract), args.contract)
        require(result["row_count"] == 15, "centroid encoder row count differs")
        print("centroid encoder intrinsic planner self-test passed" if args.self_test else json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-centroid-encoder-intrinsic: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
