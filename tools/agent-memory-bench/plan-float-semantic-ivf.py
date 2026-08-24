#!/usr/bin/env python3
"""Validate the predeclared float semantic IVF routing-control matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
FAMILY = "float_semantic_ivf_routing_control_v1"


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY and value.get("purpose") == "external_calibration_only_float_semantic_ivf_routing_quality_control_before_binary_centroid_surrogates", "float semantic IVF contract identity differs")
    require(value.get("faiss_version") == "1.13.2" and value.get("training") == {"algorithm": "spherical_kmeans_faiss_cpu_v1", "source": "frozen_train_vectors_only", "seed": 20260824, "iterations": 25, "redoes": 1, "normalized_vectors": True}, "float semantic IVF training contract differs")
    require(value.get("target_candidate_fractions") == [0.05, 0.10, 0.25] and value.get("centroid_search") == "exact_float_inner_product_scan_all_centroids_v1" and value.get("cascade") == {"hamming_limit": 768, "adc_limit": 256, "exact_limit": 256, "oracle_k": 10}, "float semantic IVF routing contract differs")
    require(value.get("binary_surrogate") == "forbidden_in_this_control; requires_frozen_centroid_artifacts_from_this_protocol" and value.get("confirmation") == "forbidden" and value.get("library_dependency") == "forbidden_faiss_is_external_benchmark_only", "float semantic IVF scope differs")
    require(value.get("scales") == [{"id": "es-100k", "documents": 100000, "evaluation_manifest_sha256": "1e89e90596ca8dbf6ea87c4a3f4ff78775d3f2841395bef883bb727539c4e112", "train_vectors_sha256": "cf200ca28e0c7a8c3793bd359543b20353f516daa6fdb843f67b8da9f7b08bbb", "centroid_counts": [1024, 4096]}, {"id": "es-1m", "documents": 1000000, "evaluation_manifest_sha256": "616e70a3d0e21a561967b382bebc463b10038280f02ca77477b0b01331c73536", "train_vectors_sha256": "cf200ca28e0c7a8c3793bd359543b20353f516daa6fdb843f67b8da9f7b08bbb", "centroid_counts": [4096, 16384]}], "float semantic IVF scale matrix differs")
    return value


def plan(contract: dict[str, Any], contract_path: Path) -> dict[str, Any]:
    rows = [{"scale": scale["id"], "centroid_count": count, "target_candidate_fraction": fraction} for scale in contract["scales"] for count in scale["centroid_counts"] for fraction in contract["target_candidate_fractions"]]
    return {"schema_version": 1, "family": "float_semantic_ivf_routing_plan_v1", "contract_sha256": sha256(contract_path), "row_count": len(rows), "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "float-semantic-ivf.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        result = plan(load_contract(args.contract), args.contract)
        require(result["row_count"] == 12, "float semantic IVF matrix row count differs")
        print("float semantic IVF planner self-test passed" if args.self_test else json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-float-semantic-ivf: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
