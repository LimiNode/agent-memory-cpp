#!/usr/bin/env python3
"""Full-corpus THQ quality gate with candidate-local FP32 control.

This runner deliberately separates product qrels quality from exact-teacher
overlap. It never treats a query-specific candidate file as a persistent
index, and it validates every input manifest/receipt before measuring.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

TOP_K = 10
QUERIES = 152
RECORD = 148  # int32 doc id + 144-byte THQ4 research payload


def require(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def top(ids: np.ndarray, scores: np.ndarray, k: int, *, lower: bool) -> np.ndarray:
    order = np.lexsort((ids, scores if lower else -scores))
    return ids[order[: min(k, len(order))]]


def ndcg(ids: np.ndarray, qrel_ids: np.ndarray, qrel_scores: np.ndarray) -> float:
    grades = {int(doc): float(score) for doc, score in zip(qrel_ids, qrel_scores)
              if int(doc) >= 0 and float(score) > 0}
    gains = np.asarray([2.0 ** grades.get(int(doc), 0.0) - 1.0 for doc in ids[:TOP_K]])
    discounts = np.log2(np.arange(2, 2 + len(gains), dtype=np.float64))
    ideal = np.sort(np.asarray([2.0 ** s - 1.0 for s in grades.values()]))[::-1][:TOP_K]
    ideal_dcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal), dtype=np.float64))))
    return float(np.sum(gains / discounts) / ideal_dcg) if ideal_dcg else 0.0


def aggregate(values: list[float]) -> dict[str, float]:
    a = np.asarray(values, dtype=np.float64)
    return {"min": float(a.min()), "mean": float(a.mean()),
            "p05": float(np.percentile(a, 5)), "p50": float(np.percentile(a, 50)),
            "p95": float(np.percentile(a, 95)), "max": float(a.max())}


def packed_lut(thresholds: np.ndarray, query: np.ndarray) -> np.ndarray:
    # Four ordinal levels are represented by two bits. Cost is squared
    # distance to the corresponding scalar interval, matching the THQ oracle.
    t1, t2, t3 = thresholds[:, 0], thresholds[:, 1], thresholds[:, 2]
    return np.stack((np.maximum(query - t1, 0.0) ** 2,
                     np.where(query < t1, (t1 - query) ** 2,
                              np.where(query >= t2, (query - t2) ** 2, 0.0)),
                     np.where(query < t2, (t2 - query) ** 2,
                              np.where(query >= t3, (query - t3) ** 2, 0.0)),
                     np.maximum(t3 - query, 0.0) ** 2), axis=1).astype(np.float32)


def full_thq_top10(codes: np.memmap, lut: np.ndarray, n: int,
                   chunk: int) -> np.ndarray:
    # Score the canonical 96-byte packed ordinal payload. The byte-cost table
    # is query-specific but the document representation is persistent and
    # query-independent.
    byte_levels = np.asarray([(np.arange(256, dtype=np.uint16) >> shift) & 3
                              for shift in (0, 2, 4, 6)]).T
    byte_cost = np.empty((96, 256), dtype=np.float32)
    for byte in range(96):
        coord = np.arange(4) + byte * 4
        byte_cost[byte] = lut[coord, byte_levels].sum(axis=1)
    best_ids = np.empty(TOP_K, dtype=np.int64)
    best_scores = np.full(TOP_K, np.inf, dtype=np.float32)
    for start in range(0, n, chunk):
        stop = min(n, start + chunk)
        score = np.zeros(stop - start, dtype=np.float32)
        block = np.asarray(codes[start:stop])
        for byte in range(96):
            score += byte_cost[byte, block[:, byte]]
        ids = np.arange(start, stop, dtype=np.int64)
        merged_ids = np.concatenate((best_ids, ids))
        merged_scores = np.concatenate((best_scores, score))
        order = np.lexsort((merged_ids, merged_scores))[:TOP_K]
        best_ids, best_scores = merged_ids[order], merged_scores[order]
    return best_ids[np.lexsort((best_ids, best_scores))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thq-manifest", type=Path, required=True)
    parser.add_argument("--candidate-receipt", type=Path, required=True)
    parser.add_argument("--candidate-raw", type=Path, required=True)
    parser.add_argument("--candidate-flat", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--packed-thq", type=Path, required=True)
    parser.add_argument("--chunk", type=int, default=32768)
    args = parser.parse_args()
    require(args.chunk > 0, "chunk must be positive")
    manifest = json.loads(args.thq_manifest.read_text())
    receipt = json.loads(args.candidate_receipt.read_text())
    candidate_raw = json.loads(args.candidate_raw.read_text())
    require(manifest["documents"] == 1_000_000 and manifest["queries"] == QUERIES,
            "unexpected corpus shape")
    require(receipt["raw_sha256"] == sha(args.candidate_raw), "candidate raw SHA mismatch")
    require(receipt["flat_file"]["sha256"] == sha(args.candidate_flat), "candidate flat SHA mismatch")
    total = sum(int(row["candidate_count"]) for row in candidate_raw["rows"])
    require(args.candidate_flat.stat().st_size == total * RECORD, "candidate flat shape mismatch")
    refs = manifest["references"]
    queries = np.memmap(Path(refs["queries"]["path"]), mode="r", dtype="<f4", shape=(QUERIES, 384))
    teachers = np.memmap(Path(refs["teacher_ids"]["path"]), mode="r", dtype="<i8", shape=(QUERIES, 10))
    qrel_ids = np.memmap(Path(refs["qrel_ids"]["path"]), mode="r", dtype="<i8", shape=(QUERIES, 20))
    qrel_scores = np.memmap(Path(refs["qrel_scores"]["path"]), mode="r", dtype="<f4", shape=(QUERIES, 20))
    require(args.packed_thq.is_file() and args.packed_thq.stat().st_size == 1_000_000 * 96,
            "canonical packed THQ payload shape differs")
    thq_codes = np.memmap(args.packed_thq, mode="r", dtype=np.uint8, shape=(1_000_000, 96))
    thresholds = np.memmap(Path(manifest["outputs"]["thq4_thresholds"]["path"]), mode="r",
                           dtype="<f4", shape=(384, 3))
    flat = np.memmap(args.candidate_flat, mode="r", dtype=np.uint8, shape=(total, RECORD))
    docs = np.memmap(Path(refs["document_vectors"]["path"]), mode="r", dtype="<f4", shape=(1_000_000, 384))
    rows = []
    offset = 0
    for qi in range(QUERIES):
        lut = packed_lut(np.asarray(thresholds), np.asarray(queries[qi]))
        direct = full_thq_top10(thq_codes, lut, 1_000_000, args.chunk)
        teacher = np.asarray(teachers[qi])
        exact = teacher.copy()
        count = int(candidate_raw["rows"][qi]["candidate_count"])
        payload = np.asarray(flat[offset: offset + count]); offset += count
        ids = np.frombuffer(payload[:, :4].tobytes(), dtype="<i4").astype(np.int64)
        levels = np.unpackbits(payload[:, 4:], axis=1, bitorder="little")[:, : 384 * 3]
        levels = levels.reshape(count, 384, 3).sum(axis=2).astype(np.uint8)
        thq_score = lut[np.arange(384)[None, :], levels].sum(axis=1, dtype=np.float32)
        candidate_thq = top(ids, thq_score, TOP_K, lower=True)
        # Candidate-local FP32 is intentionally a control, never a stored
        # production representation.
        fp32 = np.asarray(docs[ids]) @ np.asarray(queries[qi])
        candidate_fp32 = top(ids, fp32, TOP_K, lower=False)
        for name, result in (("direct_packed_thq", direct),
                             ("candidate_thq", candidate_thq),
                             ("candidate_fp32_rerank", candidate_fp32),
                             ("exact_e5_teacher", exact)):
            rows.append({"query": qi, "representation": name,
                         "candidate_count": count,
                         "candidate_survival": float(np.isin(teacher, ids).sum() / len(teacher)),
                         "teacher_overlap": float(np.isin(teacher, result).sum() / len(teacher)),
                         "qrels_ndcg10": ndcg(result, np.asarray(qrel_ids[qi]), np.asarray(qrel_scores[qi]))})
    summaries = []
    for name in ("direct_packed_thq", "candidate_thq", "candidate_fp32_rerank", "exact_e5_teacher"):
        selected = [row for row in rows if row["representation"] == name]
        summaries.append({"representation": name, "query_count": len(selected),
                          **{key: aggregate([float(row[key]) for row in selected])
                             for key in ("candidate_survival", "teacher_overlap", "qrels_ndcg10")}})
    raw = {"schema_version": 1, "family": "semantic_thq_quality_gate_v1",
           "execution_status": "EXECUTED", "rows": rows,
           "protocol": {"primary_metric": "qrels_nDCG@10",
                        "teacher_overlap": "diagnostic_only",
                        "direct_thq_scope": "full 1M corpus",
                        "candidate_scope": "whole-posting routed stream",
                        "fp32": "offline candidate-local control"}}
    payload = (json.dumps(raw, separators=(",", ":"), sort_keys=True) + "\n").encode()
    args.raw_output.parent.mkdir(parents=True, exist_ok=True); args.raw_output.write_bytes(payload)
    receipt_out = {"schema_version": 1, "family": raw["family"], "execution_status": "EXECUTED",
                   "production_activation": False, "runner_sha256": sha(Path(__file__)),
                   "thq_manifest_sha256": sha(args.thq_manifest),
                   "candidate_receipt_sha256": sha(args.candidate_receipt),
                   "candidate_raw_sha256": sha(args.candidate_raw),
                   "candidate_flat_sha256": sha(args.candidate_flat), "summaries": summaries,
                   "raw_output": {"path": str(args.raw_output), "bytes": len(payload),
                                  "sha256": hashlib.sha256(payload).hexdigest(), "rows": len(rows)}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt_out, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
