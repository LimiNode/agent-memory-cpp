#!/usr/bin/env python3
"""Independent fail-closed audit for the THQ ADC OOF research receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ndcg(ids: list[int], qrel_ids: np.ndarray, qrel_scores: np.ndarray) -> float:
    grades = {int(doc): float(score) for doc, score in zip(qrel_ids, qrel_scores)
              if int(doc) >= 0 and float(score) > 0.0}
    gains = np.asarray([2.0 ** grades.get(int(doc), 0.0) - 1.0 for doc in ids[:10]])
    discounts = np.log2(np.arange(2, 2 + len(gains), dtype=np.float64))
    ideal = np.sort(np.asarray([2.0 ** value - 1.0 for value in grades.values()]))[::-1][:10]
    ideal_dcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal), dtype=np.float64))))
    return float(np.sum(gains / discounts) / ideal_dcg) if ideal_dcg else 0.0


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def self_test() -> None:
    ids = [3, 2, 1]
    qids = np.asarray([1, 2, 3], dtype=np.int64)
    scores = np.asarray([3.0, 2.0, 1.0], dtype=np.float32)
    require(ndcg(ids, qids, scores) < 1.0, "audit nDCG self-test did not exercise ordering")
    print("THQ ADC research audit self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--runner", type=Path)
    parser.add_argument("--qrel-ids", type=Path)
    parser.add_argument("--qrel-scores", type=Path)
    parser.add_argument("--teacher-ids", type=Path)
    parser.add_argument("--candidate-flat", type=Path)
    parser.add_argument("--candidate-raw", type=Path)
    parser.add_argument("--documents", type=Path)
    parser.add_argument("--thq4-codes", type=Path)
    parser.add_argument("--thq4-thresholds", type=Path)
    parser.add_argument("--queries", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    required_inputs = (args.result, args.runner, args.qrel_ids, args.qrel_scores, args.teacher_ids,
                       args.candidate_flat, args.candidate_raw, args.documents, args.thq4_codes,
                       args.thq4_thresholds, args.queries)
    require(all(required_inputs) and args.output is not None,
            "result, runner, qrels, teacher IDs, source replay inputs and output are required")
    for path in required_inputs:
        require(path.is_file(), f"required audit input is not a file: {path}")
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("status") == "EXECUTED", "ADC result is not executed")
    require(result.get("runner_sha256") == sha(args.runner), "runner/result SHA binding differs")
    if result.get("family", "").endswith("production_shaped_crossfit_v1"):
        require(len(result.get("model_hashes", {})) == int(result.get("fold_count", 0)),
                "production model hashes missing")
    if result.get("family", "").endswith("pairwise_crossfit_v1"):
        diagnostics = result.get("training_diagnostics", {})
        require(diagnostics and all("initial_model_sha256" in value and "final_model_sha256" in value
                                    for value in diagnostics.values()),
                "pairwise model hashes missing")
    query_count = int(result["query_count"])
    qrel_ids = np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(query_count, 20))
    qrel_scores = np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(query_count, 20))
    teacher_ids = np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(query_count, 10))
    recompute_shell = True
    input_paths = {"candidate_flat": args.candidate_flat, "candidate_raw": args.candidate_raw,
                   "documents": args.documents, "thq4_codes": args.thq4_codes,
                   "thq4_thresholds": args.thq4_thresholds, "queries": args.queries,
                   "qrel_ids": args.qrel_ids, "qrel_scores": args.qrel_scores,
                   "teacher_ids": args.teacher_ids}
    if recompute_shell:
        raw_rows = json.loads(args.candidate_raw.read_text(encoding="utf-8"))["rows"]
        counts = [int(row["candidate_count"]) for row in raw_rows]
        offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
        candidate_ids = np.memmap(args.candidate_flat, mode="r", dtype="<i4",
                                  shape=(int(offsets[-1]), 37))[:, 0]
        document_count = args.documents.stat().st_size // (4 * 384)
        documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(document_count, 384))
        codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(document_count, 96))
        thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(384, 3)
        query_values = np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_count, 384))
        for field, path in (("candidate_flat_sha256", args.candidate_flat),
                            ("candidate_raw_sha256", args.candidate_raw),
                            ("documents_sha256", args.documents),
                            ("thq4_codes_sha256", args.thq4_codes),
                            ("thq4_thresholds_sha256", args.thq4_thresholds),
                            ("queries_sha256", args.queries),
                            ("qrel_ids_sha256", args.qrel_ids),
                            ("qrel_scores_sha256", args.qrel_scores),
                            ("teacher_ids_sha256", args.teacher_ids)):
            require(field in result, f"result missing source SHA: {field}")
            require(result[field] == sha(path), f"source SHA differs: {field}")
    folds = [set(map(int, fold)) for fold in result.get("fold_queries", [])]
    require(len(folds) == int(result.get("fold_count", 0)), "fold list missing")
    require(set().union(*folds) == set(range(query_count)), "folds do not cover all queries")
    require(sum(map(len, folds)) == query_count and sum(len(a & b) for i, a in enumerate(folds) for b in folds[i + 1:]) == 0,
            "folds overlap")
    rows = result.get("rows", [])
    require(rows, "result has no rows")
    family = result.get("family", "")
    fold_by_query = {}
    for fold_index, fold in enumerate(folds):
        for query in fold:
            require(query not in fold_by_query, "query appears in multiple folds")
            fold_by_query[query] = fold_index
    if family.endswith("production_shaped_crossfit_v1"):
        require(len(rows) == query_count * 4, "production row cardinality differs")
        expected_arms = {"thq4-fp32", "thq4-int8", "thq4-rslm4", "adc48-3bit"}
        require({row.get("arm") for row in rows} == expected_arms, "production arms differ")
        keys = [(int(row["query"]), row.get("arm")) for row in rows]
    elif family.endswith("cutoff_aware_crossfit_v1"):
        require(len(rows) == query_count, "cutoff row cardinality differs")
        keys = [int(row["query"]) for row in rows]
    elif family.endswith("pairwise_crossfit_v1"):
        require(len(rows) == query_count * 3, "pairwise row cardinality differs")
        require({int(row["seed"]) for row in rows} == {11, 22, 33}, "pairwise seeds differ")
        keys = [(int(row["query"]), int(row["seed"])) for row in rows]
    else:
        raise RuntimeError(f"unsupported ADC result family: {family}")
    require(len(set(keys)) == len(keys), "duplicate family row key")
    for row in rows:
        query = int(row["query"])
        require(query in fold_by_query, "row query is outside declared folds")
        require(int(row["fold"]) == fold_by_query[query], "row fold does not match fold membership")
        selected = list(map(int, row["top10_ids"]))
        candidate = list(map(int, row["candidate_fp32_top10_ids"]))
        top128 = set(map(int, row["thq4_top128_ids"]))
        require(len(selected) == 10 and len(set(selected)) == 10, "top10 cardinality/uniqueness differs")
        require(set(selected).issubset(top128), "selected ID escaped recorded THQ4 top128")
        require(abs(float(row["qrels_ndcg10"]) - ndcg(selected, qrel_ids[query], qrel_scores[query])) < 1e-12,
                "qrels nDCG differs")
        expected_teacher = float(np.isin(teacher_ids[query], selected).sum() / 10.0)
        expected_candidate = float(np.isin(candidate, selected).sum() / 10.0)
        require(abs(float(row["teacher_overlap"]) - expected_teacher) < 1e-12, "teacher overlap differs")
        require(abs(float(row["candidate_fp32_overlap"]) - expected_candidate) < 1e-12,
                "candidate-FP32 overlap differs")
        if recompute_shell:
            ids = np.asarray(candidate_ids[offsets[query]:offsets[query + 1]], dtype=np.int64)
            docs = np.asarray(documents[ids], dtype=np.float32)
            exact_top = ids[np.lexsort((ids, -(docs @ query_values[query])))[:10]]
            levels = ((np.asarray(codes[ids])[:, :, None] >>
                       np.asarray((0, 2, 4, 6), dtype=np.uint8)) & 3).reshape(len(ids), 384)
            query_value = np.asarray(query_values[query], dtype=np.float32)
            interval_lut = np.empty((384, 4), dtype=np.float32)
            for coordinate in range(384):
                for level in range(4):
                    low = -np.inf if level == 0 else thresholds[coordinate, level - 1]
                    high = np.inf if level == 3 else thresholds[coordinate, level]
                    delta = low - query_value[coordinate] if query_value[coordinate] < low else (
                        query_value[coordinate] - high if query_value[coordinate] > high else 0.0)
                    interval_lut[coordinate, level] = delta * delta
            interval = np.sum(interval_lut[np.arange(384)[None, :], levels], axis=1)
            expected_top128 = ids[np.lexsort((ids, interval))[:min(128, len(ids))]]
            require(candidate == exact_top.tolist(), "independent candidate FP32 top10 differs")
            require(list(map(int, row["thq4_top128_ids"])) == expected_top128.tolist(),
                    "independent THQ4 top128 differs")
    audit = {"schema_version": 1, "family": "thq_adc_research_audit_v1", "status": "PASS",
             "result_sha256": sha(args.result), "runner_sha256": sha(args.runner),
             "source_replay": True,
             "input_hashes": {name: sha(path) for name, path in input_paths.items()},
             "query_count": query_count, "row_count": len(rows), "fold_count": len(folds),
             "checks": ["runner/result SHA binding", "disjoint fold coverage",
                        "independent qrels nDCG", "independent teacher overlap",
                         "independent candidate-FP32 overlap", "independent candidate/THQ4 source replay",
                        "unique top10 IDs"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("THQ ADC research audit PASS")


if __name__ == "__main__":
    main()
