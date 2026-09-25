#!/usr/bin/env python3
"""Independent source replay for the five-seed Gaussian QJL diagnostic.

This deliberately regenerates the Gaussian matrices, packed sign codes, and
the score formula without importing ``qjl_reference.py``.  It is therefore a
replay of the persisted experiment result rather than a second invocation of
the producer API.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np

Q, D = 152, 384


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def ndcg(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    relevance = {int(doc): float(grade) for doc, grade in zip(qids, grades)
                 if int(doc) >= 0 and float(grade) > 0.0}
    gains = np.asarray([2.0 ** relevance.get(int(doc), 0.0) - 1.0 for doc in ids[:10]])
    ideal = np.sort(np.asarray([2.0 ** grade - 1.0 for grade in relevance.values()]))[::-1][:10]
    denominator = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal))))) if len(ideal) else 0.0
    return float(np.sum(gains / np.log2(np.arange(2, 2 + len(gains))) / denominator)) if denominator else 0.0


def direct_gaussian_qjl(query: np.ndarray, packed_signs: np.ndarray,
                        residual_norms: np.ndarray, projection: np.ndarray) -> np.ndarray:
    """Evaluate the Gaussian QJL formula without the reference scorer API."""
    signs = np.unpackbits(packed_signs, axis=1, bitorder="little").astype(np.float32) * 2.0 - 1.0
    projected_query = np.asarray(projection, dtype=np.float32) @ np.asarray(query, dtype=np.float32)
    return (math.sqrt(math.pi / 2.0) * np.asarray(residual_norms, dtype=np.float32)
            * np.mean(signs * projected_query[None, :], axis=1)).astype(np.float32)


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("result", "documents", "queries", "qrel-ids", "qrel-scores", "artifact", "tq-payload", "output"):
        parser.add_argument("--" + name, dest=name.replace("-", "_"), type=Path, required=True)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    assert result["family"] == "thq_qjl_m384_seed_stability_v1"
    assert result["status"] == "EXECUTED" and result["schema_version"] >= 2
    assert result["width"] == D and result["distribution"] == "gaussian"
    assert result["query_count"] == Q and result["seed_count"] == len(result["seeds"])
    assert result["side_payload_bytes"] == D // 8 + 4
    assert result["global_model_bytes"] == D * D * 4
    assert result["runner_sha256"] == sha(Path(__file__).with_name("run-thq-qjl-seed-stability.py"))
    expected_hashes = {
        name: sha(getattr(args, name.replace("-", "_")))
        for name in ("documents", "queries", "qrel-ids", "qrel-scores", "artifact", "tq-payload")
    }
    expected_hashes["qjl-reference"] = sha(Path(__file__).with_name("qjl_reference.py"))
    assert result["source_hashes"] == expected_hashes

    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    queries = np.asarray(np.memmap(args.queries, mode="r", dtype="<f4", shape=(Q, D)))
    qrel_ids = np.asarray(np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(Q, 20)))
    qrel_scores = np.asarray(np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(Q, 20)))
    with np.load(args.tq_payload, allow_pickle=False) as payload:
        candidate_ids = np.asarray(payload["row_ids"], dtype=np.int64)
        offsets = np.asarray(payload["row_offsets"], dtype=np.int64)
    assert offsets.shape == (Q + 1,) and int(offsets[0]) == 0 and int(offsets[-1]) == len(candidate_ids)
    with np.load(args.artifact, allow_pickle=False) as artifact:
        ids = np.asarray(artifact["document_ids"], dtype=np.int64)
        base = np.asarray(artifact["base"], dtype=np.float32)
        decoded = np.asarray(artifact["decoded1"], dtype=np.float32)
    assert ids.ndim == 1 and base.shape == decoded.shape == (len(ids), D)
    residual = np.asarray(documents[ids], dtype=np.float32) - base - decoded
    residual_norms = np.linalg.norm(residual.astype(np.float64), axis=1).astype(np.float32)
    positions = {int(doc): index for index, doc in enumerate(ids)}
    assert len(positions) == len(ids)

    replay_rows = []
    for seed in result["seeds"]:
        # This is intentionally a direct NumPy reconstruction of the producer's
        # RNG and Gaussian matrix, not a call into qjl_reference.py.
        projection = np.random.default_rng(int(seed)).standard_normal((D, D)).astype(np.float32)
        packed_signs = np.packbits((residual @ projection.T) >= 0.0, axis=1, bitorder="little")
        values = []
        for query_index, query in enumerate(queries):
            ids_q = candidate_ids[offsets[query_index]:offsets[query_index + 1]]
            indexes = np.asarray([positions[int(doc)] for doc in ids_q], dtype=np.int64)
            scores = (np.einsum("ij,j->i", base[indexes] + decoded[indexes], query, dtype=np.float32)
                      + direct_gaussian_qjl(query, packed_signs[indexes], residual_norms[indexes], projection))
            scores /= max(float(np.linalg.norm(query)), 1e-30)
            ranked = ids_q[np.lexsort((ids_q, -scores))]
            values.append(ndcg(ranked, qrel_ids[query_index], qrel_scores[query_index]))
        replay_rows.append({"seed": int(seed), "mean_qrels_ndcg10": float(np.mean(values)),
                            "p05_qrels_ndcg10": float(np.percentile(values, 5)),
                            "worst_qrels_ndcg10": float(np.min(values))})
    assert replay_rows == result["rows"]
    replay_summaries = {
        "mean_of_seed_means": float(np.mean([row["mean_qrels_ndcg10"] for row in replay_rows])),
        "min_seed_mean": float(np.min([row["mean_qrels_ndcg10"] for row in replay_rows])),
        "max_seed_mean": float(np.max([row["mean_qrels_ndcg10"] for row in replay_rows])),
    }
    assert replay_summaries == result["summaries"]
    audit = {
        "schema_version": 1,
        "family": "thq_qjl_m384_seed_stability_audit_v1",
        "status": "PASS",
        "source_replay": True,
        "independent_projection_replay": True,
        "independent_packed_sign_replay": True,
        "independent_score_top10_replay": True,
        "audit_runner_sha256": sha(Path(__file__)),
        "input_hashes": expected_hashes,
        "result_sha256": sha(args.result),
        "seed_count": len(replay_rows),
        "query_count": Q,
        "checks": ["source and producer SHA binding", "deterministic Gaussian projection replay",
                   "packed-sign and residual-norm replay", "direct QJL score/top10 replay",
                   "per-seed nDCG and min/max/mean summary replay"],
        "limitations": ["candidate-local canonical THQ top128 shell", "historical 152-query reproduction"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("QJL five-seed stability audit PASS")


if __name__ == "__main__":
    main()
