#!/usr/bin/env python3
"""Candidate-local query-dependent LSQ diagnostics.

The query-dot greedy arm preserves the historical negative control.  The
oracle arm minimizes reconstructed-cosine score error relative to each exact
document score.  Neither arm is AAQ or a deployable document codec: both make
query-time substitutions in otherwise frozen Faiss LSQ codes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

D = 384
THQ_BYTES = 96
QUERY_COUNT = 152
TOP = 128
PAYLOADS = (32, 48)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    v = np.asarray(codes, dtype=np.uint8)
    out = np.empty((len(v), D), dtype=np.uint8)
    for b in range(THQ_BYTES):
        x = v[:, b]
        out[:, 4 * b : 4 * b + 4] = np.stack(
            (x & 3, (x >> 2) & 3, (x >> 4) & 3, (x >> 6) & 3), axis=1
        )
    return out


def top_ids(scores: np.ndarray, ids: np.ndarray) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:10]]


def cosine(values: np.ndarray, query: np.ndarray) -> np.ndarray:
    x = np.asarray(values, dtype=np.float64)
    q = np.asarray(query, dtype=np.float64)
    return (x @ q) / np.maximum(np.linalg.norm(x, axis=1) * np.linalg.norm(q), 1e-30)


def ndcg10(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    rel = {int(d): float(g) for d, g in zip(qids, grades) if int(d) >= 0 and float(g) > 0}
    gains = np.asarray([2.0 ** rel.get(int(d), 0.0) - 1.0 for d in ids])
    ideal = np.sort(np.asarray([2.0 ** g - 1.0 for g in rel.values()]))[::-1][:10]
    discounts = np.log2(np.arange(2, 2 + len(gains)))
    dcg = float(np.sum(gains / discounts))
    idcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal))))) if len(ideal) else 0.0
    return dcg / idcg if idcg else 0.0


def neighbours(codebook: np.ndarray, width: int) -> np.ndarray:
    """Return nearest alternate codewords for every codeword."""
    norm = np.sum(codebook * codebook, axis=1)
    dist = norm[:, None] + norm[None, :] - 2.0 * (codebook @ codebook.T)
    dist[np.diag_indices_from(dist)] = np.inf
    return np.argsort(dist, axis=1)[:, :width]


def decode_codes(codes: np.ndarray, codebooks: np.ndarray, offsets: np.ndarray) -> np.ndarray:
    decoded = np.zeros((len(codes), D), dtype=np.float32)
    for stage in range(len(offsets) - 1):
        decoded += codebooks[offsets[stage] + codes[:, stage]]
    return decoded


def query_dot_greedy_codes(
    codes: np.ndarray,
    codebooks: np.ndarray,
    offsets: np.ndarray,
    query: np.ndarray,
    neighbor_tables: list[np.ndarray],
    passes: int,
) -> np.ndarray:
    """Historical control: independently maximize q dot codeword per stage."""
    out = np.asarray(codes, dtype=np.uint8).copy()
    qdot_books = [query @ codebooks[offsets[s] : offsets[s + 1]].T for s in range(len(offsets) - 1)]
    for _ in range(passes):
        for stage in range(len(offsets) - 1):
            current = out[:, stage].astype(np.int64)
            local = np.concatenate((current[:, None], neighbor_tables[stage][current]), axis=1)
            choices = qdot_books[stage][local]
            best = local[np.arange(len(out)), np.argmax(choices, axis=1)]
            out[:, stage] = best.astype(np.uint8)
    return out


def oracle_score_error_codes(
    base: np.ndarray,
    exact: np.ndarray,
    codes: np.ndarray,
    codebooks: np.ndarray,
    offsets: np.ndarray,
    query: np.ndarray,
    neighbor_tables: list[np.ndarray],
    passes: int,
    reconstruction_lambda: float,
) -> np.ndarray:
    """Minimize exact-document score error by local coordinate substitution."""
    out = np.asarray(codes, dtype=np.uint8).copy()
    reconstruction = np.asarray(base + decode_codes(out, codebooks, offsets), dtype=np.float64)
    exact64 = np.asarray(exact, dtype=np.float64)
    query64 = np.asarray(query, dtype=np.float64)
    query_norm = max(float(np.linalg.norm(query64)), 1e-30)
    exact_scores = cosine(exact64, query64)
    row = np.arange(len(out))
    for _ in range(passes):
        for stage in range(len(offsets) - 1):
            current = out[:, stage].astype(np.int64)
            book = np.asarray(codebooks[offsets[stage] : offsets[stage + 1]], dtype=np.float64)
            candidates = np.concatenate((current[:, None], neighbor_tables[stage][current]), axis=1)
            partial = reconstruction - book[current]
            choices = book[candidates]
            dots = partial @ query64
            dots = dots[:, None] + np.einsum("nkd,d->nk", choices, query64)
            partial_norm2 = np.einsum("nd,nd->n", partial, partial)
            norms2 = partial_norm2[:, None] + 2.0 * np.einsum("nd,nkd->nk", partial, choices)
            norms2 += np.einsum("nkd,nkd->nk", choices, choices)
            estimated = dots / np.maximum(np.sqrt(np.maximum(norms2, 0.0)) * query_norm, 1e-30)
            loss = np.square(estimated - exact_scores[:, None])
            if reconstruction_lambda:
                target_minus_partial = exact64 - partial
                reconstruction_error = np.einsum(
                    "nkd,nkd->nk",
                    target_minus_partial[:, None, :] - choices,
                    target_minus_partial[:, None, :] - choices,
                )
                loss += reconstruction_lambda * reconstruction_error
            best = candidates[row, np.argmin(loss, axis=1)]
            reconstruction = partial + book[best]
            out[:, stage] = best.astype(np.uint8)
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    for name in (
        "documents", "queries", "qrel-ids", "qrel-scores", "teacher-ids",
        "thq4-codes", "lsq-result", "lsq-models", "lsq-codes", "output",
    ):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    p.add_argument("--neighbors", type=int, default=8)
    p.add_argument("--passes", type=int, default=1)
    p.add_argument("--reconstruction-lambda", type=float, default=0.0)
    a = p.parse_args()
    if a.neighbors < 1 or a.passes < 1 or a.reconstruction_lambda < 0.0:
        p.error("neighbors/passes must be positive and reconstruction lambda non-negative")
    docs = np.memmap(a.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    queries = np.memmap(a.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D))
    qrel_ids = np.memmap(a.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20))
    qrel_scores = np.memmap(a.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20))
    teacher = np.memmap(a.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10))
    thq = np.memmap(a.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    lsq_result = json.loads(a.lsq_result.read_text(encoding="utf-8"))
    if lsq_result.get("query_count") != QUERY_COUNT or lsq_result.get("status") != "EXECUTED":
        raise RuntimeError("LSQ result is not the expected executed 152-query replay")
    z = np.load(a.lsq_codes, allow_pickle=False)
    models = np.load(a.lsq_models, allow_pickle=False)
    selected = np.asarray(z["selected_ids"], dtype=np.int64)
    if selected.shape != (QUERY_COUNT, TOP):
        raise RuntimeError("selected shell is not 152x128")
    rows = []
    greedy_codes = {}
    oracle_codes = {}
    norm_controls = {}
    for payload in PAYLOADS:
        codes = np.asarray(z[f"codes_{payload}"], dtype=np.uint8)
        books = np.asarray(models[f"lsq{payload}_codebooks"], dtype=np.float32)
        offsets = np.asarray(models[f"lsq{payload}_offsets"], dtype=np.int64)
        greedy_codes[payload] = np.empty_like(codes)
        oracle_codes[payload] = np.empty_like(codes)
        neighbor_tables = [neighbours(books[offsets[s] : offsets[s + 1]], a.neighbors) for s in range(payload)]
        fp16_top10_mismatches = {
            f"faiss_lsq{payload}_baseline": 0,
            f"query_dot_greedy_lsq{payload}": 0,
            f"oracle_score_error_lsq{payload}": 0,
        }
        fp16_max_relative_error = 0.0
        for qi, query in enumerate(np.asarray(queries, dtype=np.float32)):
            ids = selected[qi]
            exact = np.asarray(docs[ids], dtype=np.float32)
            levels = unpack_thq(np.asarray(thq[ids]))
            base = np.asarray(models["centroids"], dtype=np.float32)[np.arange(D)[None, :], levels]
            baseline_values = base + decode_codes(codes[qi], books, offsets)
            greedy = query_dot_greedy_codes(codes[qi], books, offsets, query, neighbor_tables, a.passes)
            oracle = oracle_score_error_codes(base, exact, codes[qi], books, offsets, query, neighbor_tables, a.passes, a.reconstruction_lambda)
            greedy_codes[payload][qi] = greedy
            oracle_codes[payload][qi] = oracle
            arm_values = (
                (f"faiss_lsq{payload}_baseline", baseline_values, codes[qi]),
                (f"query_dot_greedy_lsq{payload}", base + decode_codes(greedy, books, offsets), greedy),
                (f"oracle_score_error_lsq{payload}", base + decode_codes(oracle, books, offsets), oracle),
            )
            exact_scores = cosine(exact, query)
            for arm, values, code in arm_values:
                estimated_scores = cosine(values, query)
                rank = top_ids(estimated_scores, ids)
                norms32 = np.linalg.norm(np.asarray(values, dtype=np.float32), axis=1).astype(np.float32)
                norms16 = norms32.astype(np.float16).astype(np.float32)
                fp16_scores = (np.asarray(values, dtype=np.float64) @ np.asarray(query, dtype=np.float64)) / np.maximum(norms16.astype(np.float64) * float(np.linalg.norm(query)), 1e-30)
                fp16_rank = top_ids(fp16_scores, ids)
                fp16_top10_mismatches[arm] += int(not np.array_equal(rank, fp16_rank))
                fp16_max_relative_error = max(fp16_max_relative_error, float(np.max(np.abs(norms16 - norms32) / np.maximum(norms32, 1e-30))))
                rows.append({
                    "query": qi, "arm": arm, "side_payload_bytes": payload,
                    "cascade_total_bytes": THQ_BYTES + payload, "neighbor_count": a.neighbors,
                    "passes": a.passes, "top10_ids": rank.astype(int).tolist(),
                    "fp16_norm_top10_ids": fp16_rank.astype(int).tolist(),
                    "thq4_top128_ids": ids.astype(int).tolist(),
                    "teacher_overlap": float(np.isin(teacher[qi], rank).sum() / 10.0),
                    "qrels_ndcg10": ndcg10(rank, qrel_ids[qi], qrel_scores[qi]),
                    "fp16_norm_qrels_ndcg10": ndcg10(fp16_rank, qrel_ids[qi], qrel_scores[qi]),
                    "mean_score_squared_error": float(np.mean(np.square(estimated_scores - exact_scores))),
                    "mean_score_absolute_error": float(np.mean(np.abs(estimated_scores - exact_scores))),
                    "changed_code_fraction": float(np.mean(code != codes[qi])),
                })
        norm_controls[payload] = {"storage_bytes_per_document": 2, "ordered_top10_mismatch_count_by_arm": fp16_top10_mismatches, "max_relative_norm_error": fp16_max_relative_error}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    artifact = a.output.with_suffix(".codes.npz")
    np.savez_compressed(
        artifact,
        selected_ids=selected,
        **{f"greedy_codes_{m}": greedy_codes[m] for m in PAYLOADS},
        **{f"oracle_codes_{m}": oracle_codes[m] for m in PAYLOADS},
    )
    summaries = {}
    for arm in sorted({r["arm"] for r in rows}):
        rr = [r for r in rows if r["arm"] == arm]
        summaries[arm] = {"mean_qrels_ndcg10": float(np.mean([r["qrels_ndcg10"] for r in rr])), "p05_qrels_ndcg10": float(np.percentile([r["qrels_ndcg10"] for r in rr], 5)), "worst_qrels_ndcg10": float(np.min([r["qrels_ndcg10"] for r in rr])), "mean_teacher_overlap": float(np.mean([r["teacher_overlap"] for r in rr])), "mean_changed_code_fraction": float(np.mean([r["changed_code_fraction"] for r in rr])), "mean_score_squared_error": float(np.mean([r["mean_score_squared_error"] for r in rr])), "mean_score_absolute_error": float(np.mean([r["mean_score_absolute_error"] for r in rr])), "mean_fp16_norm_qrels_ndcg10": float(np.mean([r["fp16_norm_qrels_ndcg10"] for r in rr]))}
    result = {"schema_version": 2, "family": "thq_query_local_lsq_diagnostics_v2", "status": "EXECUTED", "source_replay": True, "metric": "cosine", "query_count": QUERY_COUNT, "candidate_shell": "persisted LSQ frozen THQ top128", "candidate_stream_hash": lsq_result.get("source_hashes", {}).get("candidate-flat"), "diagnostic_semantics": {"query_dot_greedy": "independently maximize q dot codeword in each frozen-codebook neighbourhood", "oracle_score_error": "minimize squared reconstructed-cosine error relative to exact document cosine", "deployable": False}, "config": {"neighbors": a.neighbors, "passes": a.passes, "reconstruction_lambda": a.reconstruction_lambda}, "source_hashes": {n: sha256(getattr(a, n.replace("-", "_"))) for n in ("documents", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "lsq-result", "lsq-models", "lsq-codes")}, "artifact_sha256": sha256(artifact), "runner_sha256": sha256(Path(__file__)), "global_model_bytes_by_payload": {str(payload): int(lsq_result["summaries"][f"faiss_lsq{payload}"]["global_codebook_bytes"]) for payload in PAYLOADS}, "final_norm_control": {"fp32_oracle_bytes_per_document": 4, "fp16_candidate_bytes_per_document": 2, "payloads": norm_controls}, "summaries": summaries, "rows": rows, "limitations": ["not official AAQ", "query-dependent code substitutions are optimistic diagnostics, not deployable document codes", "frozen Faiss LSQ codebooks and candidate shell", "no production latency claim"]}
    a.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
