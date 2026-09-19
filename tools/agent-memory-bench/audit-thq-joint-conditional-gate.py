#!/usr/bin/env python3
"""Independent source-replay audit for the persistable RSLM reference gate."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

D = 384
TOP = 128
ARMS = ("thq-joint2", "thq-joint3")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def top_ids(scores: np.ndarray, ids: np.ndarray, limit: int) -> np.ndarray:
    return ids[np.lexsort((ids, -scores))[:limit]]


def ndcg(ids: list[int], qrel_ids: np.ndarray, qrel_scores: np.ndarray) -> float:
    grades = {int(doc): float(score) for doc, score in zip(qrel_ids, qrel_scores)
              if int(doc) >= 0 and float(score) > 0.0}
    gains = np.asarray([2.0 ** grades.get(int(doc), 0.0) - 1.0 for doc in ids[:10]])
    discounts = np.log2(np.arange(2, 2 + len(gains), dtype=np.float64))
    ideal = np.sort(np.asarray([2.0 ** value - 1.0 for value in grades.values()]))[::-1][:10]
    ideal_dcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal), dtype=np.float64))))
    return float(np.sum(gains / discounts) / ideal_dcg) if ideal_dcg else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("result", "runner", "candidate-flat", "candidate-raw", "documents", "thq4-codes",
                 "thq4-thresholds", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "artifact-dir", "output"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    args = parser.parse_args()
    paths = (args.result, args.runner, args.candidate_flat, args.candidate_raw, args.documents,
             args.thq4_codes, args.thq4_thresholds, args.queries, args.qrel_ids, args.qrel_scores,
             args.teacher_ids, args.artifact_dir)
    require(all(path.is_file() if path.suffix else path.is_dir() for path in paths), "audit input missing")
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("family") == "thq_joint_conditional_gate_v1" and result.get("status") == "EXECUTED", "result identity differs")
    require(result.get("runner_sha256") == sha(args.runner), "runner/result SHA binding differs")
    query_count = int(result["query_count"])
    document_count = args.documents.stat().st_size // (4 * D)
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(document_count, D))
    codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(len(documents), 96))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_count, D))
    qrel_ids = np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(query_count, 20))
    qrel_scores = np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(query_count, 20))
    teacher_ids = np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(query_count, 10))
    raw = json.loads(args.candidate_raw.read_text(encoding="utf-8"))["rows"]
    counts = np.asarray([int(row["candidate_count"]) for row in raw], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    records = np.memmap(args.candidate_flat, mode="r", dtype=np.uint8, shape=(int(offsets[-1]), 148))
    candidate_ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    unique_ids = np.fromfile(args.artifact_dir / "candidate.ids.i4", dtype="<i4").astype(np.int64)
    require(sha(args.artifact_dir / "candidate.ids.i4") == result["candidate_ids_sha256"], "candidate ID artifact hash differs")
    require(len(unique_ids) == int(result["candidate_unique_documents"]) and np.array_equal(unique_ids, np.unique(candidate_ids)), "candidate ID artifact differs")
    symbols = {}
    centers = {}
    for arm in ARMS:
        bits = int(arm[-1])
        symbol_path = args.artifact_dir / f"{arm}.candidate.u8"
        center_path = args.artifact_dir / f"{arm}.codebook.f32"
        symbols[arm] = None
        centers[arm] = None
        require(sha(symbol_path) == result["models"][str(bits)]["symbols_sha256"], f"{arm} symbol hash differs")
        require(sha(center_path) == result["models"][str(bits)]["codebook_sha256"], f"{arm} codebook hash differs")
    folds = [set(map(int, fold)) for fold in result["fold_queries"]]
    fold_by_query = {query: fold for fold, values in enumerate(folds) for query in values}
    rows = result.get("rows", [])
    require(len(rows) == query_count * 2 and len({(int(row["query"]), row["arm"]) for row in rows}) == len(rows), "row cardinality differs")
    require({row["arm"] for row in rows} == set(ARMS), "arm set differs")
    id_to_row = {int(doc): i for i, doc in enumerate(unique_ids)}
    checked = 0
    for row in rows:
        query = int(row["query"])
        require(int(row["fold"]) == fold_by_query[query], "row fold differs")
        ids = candidate_ids[offsets[query]:offsets[query + 1]]
        values = np.asarray(documents[ids], dtype=np.float32)
        exact = values @ np.asarray(queries[query], dtype=np.float32)
        levels = ((np.asarray(codes[ids])[:, :, None] >> np.asarray((0, 2, 4, 6), dtype=np.uint8)) & 3).reshape(len(ids), D)
        interval_lut = np.empty((D, 4), dtype=np.float32)
        query_value = np.asarray(queries[query], dtype=np.float32)
        for coordinate in range(D):
            for level in range(4):
                low = -np.inf if level == 0 else thresholds[coordinate, level - 1]
                high = np.inf if level == 3 else thresholds[coordinate, level]
                delta = low - query_value[coordinate] if query_value[coordinate] < low else (query_value[coordinate] - high if query_value[coordinate] > high else 0.0)
                interval_lut[coordinate, level] = delta * delta
        interval = np.sum(interval_lut[np.arange(D)[None, :], levels], axis=1)
        thq_top = ids[np.lexsort((ids, interval))[:min(TOP, len(ids))]]
        require(list(map(int, row["thq4_top128_ids"])) == thq_top.tolist(), "THQ top128 differs")
        thq_positions = np.asarray([int(np.flatnonzero(ids == doc)[0]) for doc in thq_top])
        exact_top = top_ids(exact[thq_positions], thq_top, 10).tolist()
        require(row["candidate_fp32_top10_ids"] == exact_top, "candidate FP32 top10 differs")
        selected = list(map(int, row["top10_ids"]))
        require(len(selected) == 10 and len(set(selected)) == 10 and set(selected).issubset(set(map(int, row["thq4_top128_ids"]))), "top10 containment differs")
        require(abs(float(row["qrels_ndcg10"]) - ndcg(selected, qrel_ids[query], qrel_scores[query])) < 1e-12, "nDCG differs")
        require(abs(float(row["teacher_overlap"]) - np.isin(teacher_ids[query], selected).sum() / 10.0) < 1e-12, "teacher overlap differs")
        checked += 1
    audit = {"schema_version": 1, "family": "thq_joint_conditional_audit_v1", "status": "PASS", "source_replay": True,
             "result_sha256": sha(args.result), "runner_sha256": sha(args.runner), "row_count": checked,
             "input_hashes": {"candidate_flat": sha(args.candidate_flat), "candidate_raw": sha(args.candidate_raw),
                              "documents": sha(args.documents), "thq4_codes": sha(args.thq4_codes),
                              "thq4_thresholds": sha(args.thq4_thresholds), "queries": sha(args.queries),
                              "qrel_ids": sha(args.qrel_ids), "qrel_scores": sha(args.qrel_scores),
                              "teacher_ids": sha(args.teacher_ids), "candidate_ids": sha(args.artifact_dir / "candidate.ids.i4"),
                              "thq-joint2_symbols": sha(args.artifact_dir / "thq-joint2.candidate.u8"),
                              "thq-joint3_symbols": sha(args.artifact_dir / "thq-joint3.candidate.u8")},
             "query_count": query_count, "checks": ["source SHA binding", "persisted ID/symbol/center hashes", "family cardinality", "fold membership", "candidate FP32 top10", "qrels nDCG", "teacher overlap"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("THQ joint conditional audit PASS")


if __name__ == "__main__":
    main()


