#!/usr/bin/env python3
"""Write a compact result and fail-closed provenance receipt for THQ residual runs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_frontier_runner() -> Path:
    return Path(__file__).with_name("run-thq4-residual-frontier.py")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontier-result", type=Path, required=True)
    parser.add_argument("--stage-result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    frontier = json.loads(args.frontier_result.read_text(encoding="utf-8"))
    stage = json.loads(args.stage_result.read_text(encoding="utf-8"))
    runner = load_frontier_runner()
    stage_runner = Path(__file__).with_name("run-thq-residual-stage-local.py")
    frontier_summary = {}
    for name in ("interval", "centroid", "pca8", "pca16", "pca32",
                 "pq8", "pq16", "pq32", "int8"):
        overlaps = [float(row[f"{name}_teacher_overlap"])
                    for row in frontier.get("rows", [])
                    if f"{name}_teacher_overlap" in row]
        ndcgs = [float(row[f"{name}_qrels_ndcg10"])
                 for row in frontier.get("rows", [])
                 if f"{name}_qrels_ndcg10" in row]
        if overlaps:
            frontier_summary[name] = {
                "teacher_overlap_mean": sum(overlaps) / len(overlaps),
                "teacher_overlap_min": min(overlaps),
                "qrels_ndcg10_mean": sum(ndcgs) / len(ndcgs),
            }
    compact = {
        "schema_version": 1,
        "family": "thq_residual_frontier_compact_v1",
        "status": "EXECUTED",
        "frontier_evidence_status": frontier.get("evidence_status"),
        "stage_evidence_status": stage.get("evidence_status"),
        "query_count": stage.get("query_count"),
        "documents": stage.get("documents"),
        "logical_payload_bytes_per_document": frontier.get("logical_payload_bytes_per_document"),
        "reconstruction": frontier.get("reconstruction"),
        "frontier_summaries": frontier_summary,
        "stage_local_summaries": stage.get("summaries"),
        "limitations": stage.get("limitations"),
    }
    args.output.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt = {
        "schema_version": 1,
        "family": "thq_residual_frontier_receipt_v1",
        "status": "EXECUTED",
        "runner_sha256": sha256(runner),
        "stage_runner_sha256": sha256(stage_runner),
        "frontier_result_sha256": sha256(args.frontier_result),
        "stage_result_sha256": sha256(args.stage_result),
        "compact_result_sha256": sha256(args.output),
        "input_hashes": {key: frontier.get(key) for key in (
            "document_vectors_sha256", "train_vectors_sha256", "queries_sha256",
            "query_ids_sha256", "document_ids_sha256", "qrels_sha256",
            "thq4_codes_sha256", "thq4_thresholds_sha256")},
        "stage_input_hashes": {key: stage.get(key) for key in (
            "documents_sha256", "training_sha256", "queries_sha256", "thq_sha256")},
        "frontier_artifacts": {
            "pca_basis_sha256": frontier.get("pca_basis_sha256"),
            "thq4_centroids_sha256": frontier.get("thq4_centroids_sha256"),
            "materialized_residual_codes": frontier.get("materialized_residual_codes"),
            "materialized_residual_pq": frontier.get("materialized_residual_pq"),
        },
        "runtime": {"numpy_version": frontier.get("numpy_version")},
        "quality_scope": {"query_count": stage.get("query_count"),
                          "canonical_152_query_payload": False,
                          "native_latency": False},
    }
    try:
        import faiss
        receipt["runtime"]["faiss_version"] = faiss.__version__
    except Exception:
        receipt["runtime"]["faiss_version"] = None
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
