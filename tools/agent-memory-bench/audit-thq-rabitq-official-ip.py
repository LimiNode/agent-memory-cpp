#!/usr/bin/env python3
"""Source-bound parity and quality audit for the official one-bit RaBitQ IP form.

The reduced centered form is algebraically equivalent to the public RaBitQ
one-bit estimator.  This audit evaluates the public factors explicitly
(``F_add``, ``F_rescale`` and ``c_B S_q``) and compares them with the existing
packed scorer before reporting the matched cascade quality.  It is an IP
experiment; it must not be mixed with reconstructed-vector cosine results.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
from pathlib import Path

import numpy as np

from binary_code_references import RabitQReference, _packed_signed_dot

D, TOP, QUERY_COUNT = 384, 128, 152
K_VALUES = (32, 64, 128, 256, 512)
SEED = 20260922
SOURCE_URL = "https://vectordb-ntu.github.io/RaBitQ-Library/rabitq/estimator/"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def load_f32(path: Path, rows: int | None = None) -> np.ndarray:
    require(path.stat().st_size % (D * 4) == 0, f"not FP32x{D}: {path}")
    actual = path.stat().st_size // (D * 4)
    require(rows is None or actual == rows, f"unexpected row count: {path}")
    return np.asarray(np.memmap(path, mode="r", dtype="<f4", shape=(actual, D)), dtype=np.float32)


def top_ids(scores: np.ndarray, ids: np.ndarray, count: int) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:min(count, len(ids))]]


def ndcg10(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    rel = {int(doc): float(score) for doc, score in zip(qids, grades) if int(doc) >= 0 and float(score) > 0}
    gains = np.asarray([2.0 ** rel.get(int(doc), 0.0) - 1.0 for doc in ids[:10]], dtype=np.float64)
    ideal = np.sort(np.asarray([2.0 ** score - 1.0 for score in rel.values()], dtype=np.float64))[::-1][:10]
    dcg = np.sum(gains / np.log2(np.arange(2, 2 + len(gains))))
    idcg = np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal)))) if len(ideal) else 0.0
    return float(dcg / idcg) if idcg else 0.0


def load_candidates(flat: Path, raw: Path, receipt: Path) -> tuple[np.ndarray, np.ndarray]:
    metadata = json.loads(raw.read_text(encoding="utf-8")); rows = metadata.get("rows")
    require(isinstance(rows, list) and len(rows) == QUERY_COUNT, "candidate rows differ")
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64))); total = int(offsets[-1])
    require(flat.stat().st_size == total * 148, "candidate flat size differs")
    records = np.memmap(flat, mode="r", dtype=np.uint8, shape=(total, 148))
    ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    rec = json.loads(receipt.read_text(encoding="utf-8"))
    require(rec.get("execution_status") == "EXECUTED" and rec.get("raw_sha256") == sha256(raw) and rec.get("flat_file", {}).get("sha256") == sha256(flat), "candidate provenance differs")
    return ids, offsets


def official_ip_scores(model: RabitQReference, query: np.ndarray, vectors: np.ndarray) -> np.ndarray:
    """Evaluate the public B=1 IP estimator with its explicit factors."""
    residual = (vectors - model.mean) @ model.rotation
    qrot = (query - model.mean) @ model.rotation
    bits = residual >= 0.0
    xu_cb = bits.astype(np.float32) - 0.5
    ip_resi = np.sum(residual * xu_cb, axis=1, dtype=np.float64)
    l2_sqr = np.sum(residual * residual, axis=1, dtype=np.float64)
    f_rescale = -np.divide(l2_sqr, ip_resi, out=np.zeros_like(l2_sqr), where=ip_resi > 0.0)
    ip_code = bits.astype(np.float32) @ qrot
    c_b_sum_q = -0.5 * float(np.sum(qrot, dtype=np.float64))
    # For the centered transformed residual, F_add=1 and G_add=0.  The
    # constant 1 is retained to mirror the public distance estimator.
    estimated_distance = 1.0 + f_rescale * (ip_code + c_b_sum_q)
    return 1.0 - estimated_distance + float(model.mean @ query)


def materialize_candidate_model(model: RabitQReference, vectors: np.ndarray) -> RabitQReference:
    residual = (vectors - model.mean) @ model.rotation
    abs_sum = np.sum(np.abs(residual), axis=1, dtype=np.float32)
    l2_sqr = np.sum(residual * residual, axis=1, dtype=np.float32)
    gains = np.divide(l2_sqr, abs_sum, out=np.zeros_like(l2_sqr), where=abs_sum > 0.0)
    return dataclasses.replace(model, codes=np.packbits(residual >= 0.0, axis=1, bitorder="little"), gains=gains, source_norm_sq=np.sum(vectors * vectors, axis=1, dtype=np.float32))


def self_test() -> None:
    rng = np.random.default_rng(SEED); train = rng.normal(size=(128, D)).astype(np.float32); docs = rng.normal(size=(17, D)).astype(np.float32); query = rng.normal(size=D).astype(np.float32)
    model = materialize_candidate_model(RabitQReference.fit(train, bits=D, seed=SEED, metric="ip"), docs)
    explicit = official_ip_scores(model, query, docs)
    packed = model.scores_subset(query, np.arange(len(docs)))
    require(np.max(np.abs(explicit - packed)) < 2e-4, "official one-bit IP parity failed")
    print("official RaBitQ IP self-test: PASS")


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true")
    for name in ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "candidate-flat", "candidate-raw", "candidate-receipt", "output"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path)
    args = parser.parse_args()
    if args.self_test: self_test(); return
    required = (args.documents, args.train_vectors, args.queries, args.qrel_ids, args.qrel_scores, args.candidate_flat, args.candidate_raw, args.candidate_receipt, args.output)
    if any(value is None for value in required): parser.error("all source paths and --output are required")
    docs = load_f32(args.documents, 1_000_000); train = load_f32(args.train_vectors); queries = load_f32(args.queries, QUERY_COUNT)
    qids = np.asarray(np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20))); grades = np.asarray(np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20)))
    ids, offsets = load_candidates(args.candidate_flat, args.candidate_raw, args.candidate_receipt); unique = np.unique(ids); candidate_docs = np.asarray(docs[unique], dtype=np.float32); positions = {int(doc): pos for pos, doc in enumerate(unique)}
    model = materialize_candidate_model(RabitQReference.fit(train, bits=D, seed=SEED, metric="ip"), candidate_docs)
    rows, parity_errors = [], []; topk_mismatches = 0
    explicit_quality, packed_quality, rerank_quality = [], [], []
    explicit_top10_mismatches = 0
    for qi, query in enumerate(queries):
        row_ids = ids[offsets[qi]:offsets[qi + 1]]; pos = np.asarray([positions[int(doc)] for doc in row_ids], dtype=np.int64); vectors = candidate_docs[pos]
        explicit = official_ip_scores(model, query, vectors); packed = model.scores_subset(query, pos)
        parity_errors.append(float(np.max(np.abs(explicit - packed))))
        for k in K_VALUES:
            selected = top_ids(explicit, row_ids, k)
            packed_selected = top_ids(packed, row_ids, k)
            if not np.array_equal(selected, packed_selected):
                topk_mismatches += 1
            explicit_top10 = selected[:10]
            packed_top10 = packed_selected[:10]
            explicit_ndcg = ndcg10(explicit_top10, qids[qi], grades[qi])
            packed_ndcg = ndcg10(packed_top10, qids[qi], grades[qi])
            if not np.array_equal(explicit_top10, packed_top10):
                explicit_top10_mismatches += 1
            final = top_ids(np.asarray(docs[selected]) @ query, selected, 10)
            rerank_ndcg = ndcg10(final, qids[qi], grades[qi])
            rows.append({
                "query": qi, "K": k,
                "explicit_ip_top10_ids": explicit_top10.astype(int).tolist(),
                "packed_ip_top10_ids": packed_top10.astype(int).tolist(),
                "explicit_ip_qrels_ndcg10": float(explicit_ndcg),
                "packed_ip_qrels_ndcg10": float(packed_ndcg),
                "explicit_vs_packed_top10_equal": bool(np.array_equal(explicit_top10, packed_top10)),
                "selected_ids": selected.astype(int).tolist(),
                "packed_selected_ids": packed_selected.astype(int).tolist(),
                "packed_topk_equal": bool(np.array_equal(selected, packed_selected)),
                "final_ids": final.astype(int).tolist(),
                "fp32_rerank_qrels_ndcg10": float(rerank_ndcg),
                "explicit_vs_packed_max_abs_error": parity_errors[-1],
            })
            explicit_quality.append(float(explicit_ndcg)); packed_quality.append(float(packed_ndcg)); rerank_quality.append(float(rerank_ndcg))
    def summary(values: list[float]) -> dict[str, float]:
        return {"mean_qrels_ndcg10": float(np.mean(values)), "p05_qrels_ndcg10": float(np.percentile(values, 5)), "worst_qrels_ndcg10": float(np.min(values))}
    result = {"schema_version": 2, "family": "thq_faithful_rabitq_official_ip_v1", "status": "EXECUTED", "source_replay": True, "source_scope": "full 1M documents, frozen 152-query R4 candidate shell", "metric": "ip", "estimator_source": SOURCE_URL, "bits": D, "seed": SEED, "candidate_count": int(len(ids)), "candidate_unique_documents": int(len(unique)), "query_count": QUERY_COUNT, "K_values": list(K_VALUES), "tie_policy": "score descending, document ID ascending", "runner_sha256": sha256(Path(__file__)), "source_hashes": {name: sha256(path) for name, path in {"documents": args.documents, "train_vectors": args.train_vectors, "queries": args.queries, "qrel_ids": args.qrel_ids, "qrel_scores": args.qrel_scores, "candidate_flat": args.candidate_flat, "candidate_raw": args.candidate_raw, "candidate_receipt": args.candidate_receipt}.items()}, "storage": {"sign_bits_bytes_per_document": 48, "per_document_factor_bytes": 4, "side_payload_bytes_per_document": 52, "thq4_plus_side_bytes_per_document": 148, "global_model_bytes": 384 * 4 + 384 * 384 * 4, "one_million_side_payload_bytes": 1_000_000 * 52, "one_million_thq4_plus_side_bytes": 1_000_000 * 148}, "factor_audit": {"max_abs_explicit_vs_packed_ip_error": float(max(parity_errors)), "mean_abs_explicit_vs_packed_ip_error": float(np.mean(parity_errors)), "topk_mismatch_count": int(topk_mismatches), "top10_mismatch_count": int(explicit_top10_mismatches), "formula": "distance=F_add+G_add+F_rescale*(ip_code+c_B*S_q), B=1, centered transformed residual"}, "summaries": {"explicit_ip": summary(explicit_quality), "packed_ip": summary(packed_quality), "fp32_rerank": summary(rerank_quality), "packed_minus_explicit_mean_ndcg10": float(np.mean(packed_quality) - np.mean(explicit_quality)), "fp32_rerank_minus_packed_mean_ndcg10": float(np.mean(rerank_quality) - np.mean(packed_quality))}, "rows": rows, "limitations": ["IP ordering only; not reconstructed-vector cosine", "explicit public factors and packed serving scorer are separate evidence lanes because the fast path quantizes the query", "one-bit full-dimensional RaBitQ estimator; no B>1 extended code", "candidate-local replay, not native serving latency"]}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__": main()
