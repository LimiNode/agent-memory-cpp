#!/usr/bin/env python3
"""Independent audit for the score-aware LSQ control."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

D, THQ_BYTES, QUERY_COUNT, TOP = 384, 96, 152, 128
PAYLOADS = (32, 48)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def top_ids(scores, ids):
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:10]]


def unpack(codes):
    v = np.asarray(codes, dtype=np.uint8)
    out = np.empty((len(v), D), dtype=np.uint8)
    for b in range(THQ_BYTES):
        x = v[:, b]
        out[:, 4 * b : 4 * b + 4] = np.stack((x & 3, (x >> 2) & 3, (x >> 4) & 3, (x >> 6) & 3), axis=1)
    return out


def cosine(x, q):
    x, q = np.asarray(x, dtype=np.float64), np.asarray(q, dtype=np.float64)
    return (x @ q) / np.maximum(np.linalg.norm(x, axis=1) * np.linalg.norm(q), 1e-30)


def replay_codes(base, codes, books, offsets):
    out = np.zeros((len(codes), D), dtype=np.float32)
    for stage in range(len(offsets) - 1):
        out += books[offsets[stage] + codes[:, stage]]
    return out


def ndcg10(ids, qids, grades):
    rel = {int(d): float(g) for d, g in zip(qids, grades) if int(d) >= 0 and float(g) > 0}
    gains = np.asarray([2.0 ** rel.get(int(d), 0.0) - 1.0 for d in ids])
    ideal = np.sort(np.asarray([2.0 ** g - 1.0 for g in rel.values()]))[::-1][:10]
    dcg = float(np.sum(gains / np.log2(np.arange(2, 2 + len(gains)))))
    idcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal))))) if len(ideal) else 0.0
    return dcg / idcg if idcg else 0.0


def main():
    p = argparse.ArgumentParser()
    for name in ("documents", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "result", "lsq-result", "models", "codes", "refined-codes", "output"):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    a = p.parse_args()
    result = json.loads(a.result.read_text(encoding="utf-8"))
    if result.get("family") != "thq_score_aware_lsq_control_v1" or result.get("source_replay") is not True:
        raise RuntimeError("unexpected score-aware result")
    queries = np.memmap(a.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D))
    qids = np.memmap(a.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20))
    grades = np.memmap(a.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20))
    teacher = np.memmap(a.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10))
    thq = np.memmap(a.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    models, source_codes, refined_codes = (np.load(x, allow_pickle=False) for x in (a.models, a.codes, a.refined_codes))
    selected = np.asarray(source_codes["selected_ids"], dtype=np.int64)
    if selected.shape != (QUERY_COUNT, TOP):
        raise RuntimeError("invalid selected shell")
    rows = result.get("rows", [])
    if len(rows) != QUERY_COUNT * len(PAYLOADS) * 2:
        raise RuntimeError("unexpected row count")
    expected_hashes = {n: sha256(getattr(a, n.replace("-", "_"))) for n in ("documents", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "lsq-result")}
    expected_hashes["lsq-models"] = sha256(a.models)
    expected_hashes["lsq-codes"] = sha256(a.codes)
    if result.get("source_hashes") != expected_hashes:
        raise RuntimeError("result source hash binding differs")
    query_ids = sorted({int(r.get("query", -1)) for r in rows})
    if query_ids != list(range(QUERY_COUNT)):
        raise RuntimeError("result query identity/order is not exactly 0..151")
    mismatches = 0
    checks = []
    for payload in PAYLOADS:
        books = np.asarray(models[f"lsq{payload}_codebooks"], dtype=np.float32)
        offsets = np.asarray(models[f"lsq{payload}_offsets"], dtype=np.int64)
        source = np.asarray(source_codes[f"codes_{payload}"], dtype=np.uint8)
        refined = np.asarray(refined_codes[f"codes_{payload}"], dtype=np.uint8)
        for qi in range(QUERY_COUNT):
            ids = selected[qi]
            levels = unpack(np.asarray(thq[ids]))
            base = np.asarray(models["centroids"], dtype=np.float32)[np.arange(D)[None, :], levels]
            rank = top_ids(cosine(base + replay_codes(base, refined[qi], books, offsets), queries[qi]), ids)
            row = next(r for r in rows if r["query"] == qi and r["arm"] == f"score_aware_lsq{payload}")
            if rank.tolist() != row["top10_ids"]:
                mismatches += 1
            checks.append({"query": qi, "payload": payload, "top10_match": rank.tolist() == row["top10_ids"], "teacher_overlap": float(np.isin(teacher[qi], rank).sum() / 10.0), "ndcg10": ndcg10(rank, qids[qi], grades[qi])})
    if mismatches:
        raise RuntimeError(f"{mismatches} independent top10 mismatches")
    audit = {"schema_version": 1, "family": "thq_score_aware_lsq_control_audit_v1", "status": "PASS", "source_replay": True, "persisted_code_decode_replay": True, "query_count": QUERY_COUNT, "row_count": len(rows), "result_sha256": sha256(a.result), "input_hashes": {n: sha256(getattr(a, n.replace("-", "_"))) for n in ("documents", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "lsq-result", "models", "codes", "refined-codes")}, "checks": ["source and artifact SHA binding", "result source-hash equality", "152-query identity and 128-document shell", "independent additive decode", "score-aware top10 replay", "teacher/nDCG recomputation"], "mismatch_count": mismatches, "rows": checks, "audit_runner_sha256": sha256(Path(__file__))}
    a.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
