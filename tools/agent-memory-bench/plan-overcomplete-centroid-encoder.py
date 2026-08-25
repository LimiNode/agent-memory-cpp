#!/usr/bin/env python3
"""Validate the overcomplete centroid-encoder follow-up matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
FAMILY = "overcomplete_centroid_encoder_v1"


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY and value.get("purpose") == "calibration_only_overcomplete_itq_ensemble_follow_up_after_strict_dimension_gain", "overcomplete centroid contract identity differs")
    require(value.get("faiss_version") == "1.13.2" and value.get("parent_intrinsic_evidence_sha256") == "9f68ed73d3e7ad0494f6f86848fb59a5e2c458ba38ab49ce93868dc253d49321", "overcomplete centroid parent evidence differs")
    require(value.get("scale") == {"id": "es-1m", "documents": 1000000, "input_manifest_sha256": "697f81bc66b37feb47b413fa168f4ae5efd030b9dbbaeb8d0c67ac8d224a9ae7", "evaluation_manifest_sha256": "616e70a3d0e21a561967b382bebc463b10038280f02ca77477b0b01331c73536"} and value.get("centroid_count") == 4096, "overcomplete centroid scale differs")
    require(value.get("encoders") == ["itq_centroids_ensemble", "itq_centroids_plus_calibration_queries_ensemble"] and value.get("bit_counts") == [512, 768, 1024] and value.get("shortlist_sizes") == [16, 32, 64, 128] and value.get("seed") == 20260825 and value.get("itq_iterations") == 50, "overcomplete centroid matrix differs")
    require(value.get("selection") == {"primary_metric": "mean_recall_of_float_top16_in_binary_top64_v1", "secondary_metric": "mean_recall_of_float_top16_in_binary_top32_v1", "maximum_selected_configurations": 3, "minimum_top64_recall": 0.95, "minimum_top32_recall": 0.85, "tie_rule": "top64_desc_top32_desc_bits_asc_encoder_id_asc_v1"} and value.get("interpretation") == "overcomplete_ensemble_of_multiple_strict_itq_rotations_not_a_single_orthogonal_1024_bit_transform_v1" and value.get("confirmation") == "forbidden" and value.get("library_dependency") == "forbidden_faiss_is_external_benchmark_only", "overcomplete centroid scope differs")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "overcomplete-centroid-encoder.example.json"); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract); rows = [{"encoder": encoder, "bit_count": bits} for encoder in contract["encoders"] for bits in contract["bit_counts"]]
        require(len(rows) == 6, "overcomplete centroid row count differs")
        payload = {"schema_version": 1, "family": "overcomplete_centroid_encoder_plan_v1", "contract_sha256": sha256(args.contract), "row_count": len(rows), "rows": rows}
        print("overcomplete centroid encoder planner self-test passed" if args.self_test else json.dumps(payload, indent=2, sort_keys=True)); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-overcomplete-centroid-encoder: {error}"); return 1


if __name__ == "__main__":
    raise SystemExit(main())
