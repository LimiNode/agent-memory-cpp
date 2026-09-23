#!/usr/bin/env python3
"""Bounded held-out ranking-aware scalar LSQ correction pilot.

This is a diagnostic MA-LSQ control: it learns one residual scale per payload
from the first half of the frozen queries using qrels pairwise hinge loss and
evaluates the untouched second half. It is intentionally not a query-local
codec or a production claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

D, Q, TOP = 384, 152, 128


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ndcg(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    rel = {int(d): float(g) for d, g in zip(qids, grades) if int(d) >= 0 and float(g) > 0}
    gains = np.asarray([2.0 ** rel.get(int(d), 0.0) - 1.0 for d in ids[:10]])
    ideal = np.sort(np.asarray([2.0 ** g - 1.0 for g in rel.values()]))[::-1][:10]
    if not len(ideal): return 0.0
    den = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal)))))
    return float(np.sum(gains / np.log2(np.arange(2, 2 + len(gains))) / den))


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    out = np.empty((len(codes), D), dtype=np.uint8)
    for b in range(96):
        x = codes[:, b]
        out[:, 4 * b:4 * b + 4] = np.stack((x & 3, (x >> 2) & 3, (x >> 4) & 3, (x >> 6) & 3), axis=1)
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    for name in ("documents", "queries", "qrel-ids", "qrel-scores", "thq4-codes", "lsq-models", "lsq-codes", "output"):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    p.add_argument("--train-queries", type=int, default=76)
    args = p.parse_args()
    if not 1 <= args.train_queries < Q: p.error("train query split must be non-empty and held out")
    docs = np.memmap(args.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    queries = np.asarray(np.memmap(args.queries, mode="r", dtype="<f4", shape=(Q, D)), dtype=np.float32)
    qids = np.asarray(np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(Q, 20)))
    grades = np.asarray(np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(Q, 20)))
    thq = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, 96))
    z, m = np.load(args.lsq_codes, allow_pickle=False), np.load(args.lsq_models, allow_pickle=False)
    selected = np.asarray(z["selected_ids"], dtype=np.int64)
    if selected.shape != (Q, TOP): raise RuntimeError("expected 152x128 frozen shell")
    rows, summaries = [], {}
    for payload in (32, 48):
        codes = np.asarray(z[f"codes_{payload}"], dtype=np.uint8)
        books = np.asarray(m[f"lsq{payload}_codebooks"], dtype=np.float32)
        offsets = np.asarray(m[f"lsq{payload}_offsets"], dtype=np.int64)
        alpha_values = np.asarray([0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0], dtype=np.float32)
        losses = np.zeros(len(alpha_values), dtype=np.float64)
        for qi in range(args.train_queries):
            ids = selected[qi]; exact = np.asarray(docs[ids], dtype=np.float32)
            levels = unpack_thq(np.asarray(thq[ids]))
            base = np.asarray(m["centroids"], dtype=np.float32)[np.arange(D)[None, :], levels]
            residual = np.zeros_like(exact)
            for stage in range(payload): residual += books[offsets[stage] + codes[qi, :, stage]]
            rel = {int(d): float(g) for d, g in zip(qids[qi], grades[qi])}
            positive = [i for i, d in enumerate(ids) if rel.get(int(d), 0.0) > 0]
            negative = [i for i, d in enumerate(ids) if rel.get(int(d), 0.0) <= 0]
            if not positive or not negative: continue
            for ai, alpha in enumerate(alpha_values):
                values = base + alpha * residual
                scores = (values @ queries[qi]) / np.maximum(np.linalg.norm(values, axis=1) * np.linalg.norm(queries[qi]), 1e-30)
                losses[ai] += sum(max(0.0, 0.05 - float(scores[i] - scores[j])) for i in positive for j in negative)
        best_alpha = float(alpha_values[int(np.argmin(losses))])
        for qi in range(Q):
            ids = selected[qi]; levels = unpack_thq(np.asarray(thq[ids]))
            base = np.asarray(m["centroids"], dtype=np.float32)[np.arange(D)[None, :], levels]
            residual = np.zeros((TOP, D), dtype=np.float32)
            for stage in range(payload): residual += books[offsets[stage] + codes[qi, :, stage]]
            values = base + best_alpha * residual
            scores = (values @ queries[qi]) / np.maximum(np.linalg.norm(values, axis=1) * np.linalg.norm(queries[qi]), 1e-30)
            ranked = ids[np.lexsort((ids, -scores))]
            rows.append({"query": qi, "arm": f"ma_lsq{payload}_alpha{best_alpha:g}", "split": "train" if qi < args.train_queries else "heldout", "alpha": best_alpha, "top10_ids": ranked[:10].astype(int).tolist(), "qrels_ndcg10": ndcg(ranked, qids[qi], grades[qi])})
        held = [r["qrels_ndcg10"] for r in rows if r["arm"] == f"ma_lsq{payload}_alpha{best_alpha:g}" and r["split"] == "heldout"]
        summaries[f"ma_lsq{payload}"] = {"alpha": best_alpha, "train_pairwise_hinge": float(np.min(losses)), "heldout_mean_ndcg10": float(np.mean(held)), "heldout_p05_ndcg10": float(np.percentile(held, 5)), "quality_status": "BOUNDED_HELDOUT_DIAGNOSTIC"}
    result = {"schema_version": 1, "family": "thq_ma_lsq_ranking_scalar_pilot_v1", "status": "EXECUTED", "metric": "cosine", "train_queries": args.train_queries, "heldout_queries": Q - args.train_queries, "source_hashes": {name: sha256(getattr(args, name.replace("-", "_"))) for name in ("documents", "queries", "qrel-ids", "qrel-scores", "thq4-codes", "lsq-models", "lsq-codes")}, "runner_sha256": sha256(Path(__file__)), "summaries": summaries, "rows": rows, "limitations": ["single global residual scale per payload, not full MA-LSQ codebook training", "qrels are used only on the first query fold", "diagnostic decode uses fixed LSQ residual codes and is not a deployable query-conditioned codec"]}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__": main()
