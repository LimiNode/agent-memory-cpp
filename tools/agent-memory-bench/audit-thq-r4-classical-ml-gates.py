#!/usr/bin/env python3
"""Fail-closed audit for the THQ classical, ML sanity and teacher diagnostics."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def top_ids(scores: np.ndarray, ids: np.ndarray, limit: int,
            ascending: bool = False) -> np.ndarray:
    order = np.lexsort((ids, scores if ascending else -scores))
    return ids[order[:limit]]


def ndcg(ids: np.ndarray, qrel_ids: np.ndarray, qrel_scores: np.ndarray) -> float:
    grades = {int(doc): float(score) for doc, score in zip(qrel_ids, qrel_scores)
              if int(doc) >= 0 and float(score) > 0.0}
    gains = np.asarray([2.0 ** grades.get(int(doc), 0.0) - 1.0 for doc in ids[:10]])
    discounts = np.log2(np.arange(2, 2 + len(gains), dtype=np.float64))
    ideal = np.sort(np.asarray([2.0 ** score - 1.0 for score in grades.values()]))[::-1][:10]
    ideal_dcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal), dtype=np.float64))))
    return float(np.sum(gains / discounts) / ideal_dcg) if ideal_dcg else 0.0


def check_primary_metrics(rows: list[dict], candidate_ids: np.ndarray,
                          candidate_offsets: np.ndarray, documents: np.ndarray,
                          queries: np.ndarray, qrel_ids: np.ndarray,
                          qrel_scores: np.ndarray, teacher_ids: np.ndarray,
                          arms: set[str]) -> None:
    by_query_arm = {(int(row["query"]), row["arm"]): row for row in rows}
    require(len(by_query_arm) == 152 * len(arms),
            "classical rows contain duplicate query/arm keys")
    for query in range(152):
        shell = candidate_ids[candidate_offsets[query]:candidate_offsets[query + 1]]
        exact_top = top_ids(
            np.asarray(documents[shell], dtype=np.float32) @
            np.asarray(queries[query], dtype=np.float32), shell, 10)
        candidate_row = by_query_arm[(query, "candidate-fp32")]
        require(candidate_row["top10_ids"] == exact_top.astype(int).tolist(),
                f"candidate FP32 top10 differs for query {query}")
        for arm in arms:
            row = by_query_arm[(query, arm)]
            selected = np.asarray(row["top10_ids"], dtype=np.int64)
            require(len(selected) == 10, f"top10 length differs: {arm}/{query}")
            require(len(np.unique(selected)) == 10,
                    f"top10 IDs duplicate: {arm}/{query}")
            require(np.all(np.isin(selected, shell)),
                    f"top10 ID outside candidate shell: {arm}/{query}")
            expected = {
                "qrels_ndcg10": ndcg(selected, qrel_ids[query], qrel_scores[query]),
                "teacher_overlap": float(np.isin(teacher_ids[query], selected).sum() / 10.0),
                "candidate_fp32_overlap": float(np.isin(exact_top, selected).sum() / 10.0),
            }
            for metric, value in expected.items():
                require(abs(float(row[metric]) - value) < 1e-12,
                        f"primary metric differs: {arm}/{query}/{metric}")


def check_summary(rows: list[dict], summaries: dict, arms: set[str]) -> None:
    for arm in arms:
        selected = [row for row in rows if row["arm"] == arm]
        require(len(selected) == 152, f"classical row count differs for {arm}")
        for metric in ("qrels_ndcg10", "teacher_overlap", "candidate_fp32_overlap"):
            values = np.asarray([row[metric] for row in selected], dtype=np.float64)
            expected = {"mean": float(np.mean(values)), "p05": float(np.quantile(values, 0.05)),
                        "min": float(np.min(values))}
            for statistic, value in expected.items():
                require(abs(float(summaries[arm][metric][statistic]) - value) < 1e-12,
                        f"classical summary differs: {arm}/{metric}/{statistic}")


def self_test() -> None:
    candidate_ids = np.tile(np.arange(10, dtype=np.int64), 152)
    candidate_offsets = np.arange(0, 152 * 10 + 1, 10, dtype=np.int64)
    documents = np.zeros((10, 384), dtype=np.float32)
    documents[:, 0] = np.arange(10, 0, -1, dtype=np.float32)
    queries = np.zeros((152, 384), dtype=np.float32)
    queries[:, 0] = 1.0
    qrel_ids = np.full((152, 20), -1, dtype=np.int64)
    qrel_scores = np.zeros((152, 20), dtype=np.float32)
    qrel_ids[:, 0] = 0
    qrel_scores[:, 0] = 1.0
    teacher_ids = np.zeros((152, 10), dtype=np.int64)
    rows = [{
        "query": query,
        "arm": "candidate-fp32",
        "top10_ids": list(range(10)),
        "qrels_ndcg10": 1.0,
        "teacher_overlap": 1.0,
        "candidate_fp32_overlap": 1.0,
    } for query in range(152)]
    check_primary_metrics(rows, candidate_ids, candidate_offsets, documents, queries,
                          qrel_ids, qrel_scores, teacher_ids, {"candidate-fp32"})
    rows[0]["qrels_ndcg10"] = 0.0
    try:
        check_primary_metrics(rows, candidate_ids, candidate_offsets, documents, queries,
                              qrel_ids, qrel_scores, teacher_ids, {"candidate-fp32"})
    except RuntimeError as error:
        if "primary metric differs" not in str(error):
            raise
    else:
        raise RuntimeError("primary metric self-test did not fail closed")
    print("audit-thq-r4-classical-ml-gates self-test PASS")


def main() -> None:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--classical", type=Path, required=True)
    parser.add_argument("--ml-sanity", type=Path, required=True)
    parser.add_argument("--teacher", type=Path, required=True)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--training", type=Path, required=True)
    parser.add_argument("--thq4-codes", type=Path, required=True)
    parser.add_argument("--thq4-thresholds", type=Path, required=True)
    parser.add_argument("--int8-codes", type=Path, required=True)
    parser.add_argument("--int8-scales", type=Path, required=True)
    parser.add_argument("--candidate-flat", type=Path, required=True)
    parser.add_argument("--candidate-raw", type=Path, required=True)
    parser.add_argument("--candidate-receipt", type=Path, required=True)
    parser.add_argument("--classical-runner", type=Path, required=True)
    parser.add_argument("--ml-runner", type=Path, required=True)
    parser.add_argument("--teacher-runner", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--qrel-ids", type=Path, required=True)
    parser.add_argument("--qrel-scores", type=Path, required=True)
    parser.add_argument("--teacher-ids", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    classical = json.loads(args.classical.read_text(encoding="utf-8"))
    ml = json.loads(args.ml_sanity.read_text(encoding="utf-8"))
    teacher = json.loads(args.teacher.read_text(encoding="utf-8"))
    candidate_raw = json.loads(args.candidate_raw.read_text(encoding="utf-8"))
    candidate_receipt = json.loads(args.candidate_receipt.read_text(encoding="utf-8"))
    require(candidate_receipt.get("family") == "semantic_r4_fused_candidate_materialization_v1",
            "candidate receipt family differs")
    require(candidate_receipt.get("raw_sha256") == sha(args.candidate_raw),
            "candidate receipt/raw binding differs")
    flat_entry = candidate_receipt.get("flat_file", {})
    require(flat_entry.get("sha256") == sha(args.candidate_flat),
            "candidate receipt/flat binding differs")
    require(int(flat_entry.get("bytes", -1)) == args.candidate_flat.stat().st_size,
            "candidate receipt/flat byte count differs")
    require(candidate_receipt.get("execution_status") == "EXECUTED",
            "candidate receipt execution status differs")
    for field in ("runner_sha256", "thq_manifest_sha256", "layout_manifest_sha256", "native_receipt_sha256"):
        require(candidate_receipt.get(field), f"candidate receipt missing {field}")
    candidate_rows = candidate_raw["rows"]
    require(len(candidate_rows) == 152, "candidate raw query count differs")
    candidate_counts = [int(row["candidate_count"]) for row in candidate_rows]
    require(all(5000 <= count <= 5099 for count in candidate_counts),
            "candidate raw count range differs")
    candidate_offsets = np.concatenate(([0], np.cumsum(candidate_counts)))
    candidate_records = np.memmap(args.candidate_flat, mode="r", dtype=np.uint8,
                                  shape=(int(candidate_offsets[-1]), 148))
    candidate_ids = np.asarray(candidate_records[:, :4]).copy().view("<i4").reshape(-1)
    require(np.all((candidate_ids >= 0) & (candidate_ids < 1_000_000)),
            "candidate flat ID range differs")
    for query in range(152):
        values = candidate_ids[candidate_offsets[query]:candidate_offsets[query + 1]]
        require(len(np.unique(values)) == len(values),
                f"candidate flat IDs duplicate for query {query}")
    document_count = args.documents.stat().st_size // (4 * 384)
    query_count = args.queries.stat().st_size // (4 * 384)
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(document_count, 384))
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_count, 384))
    qrel_ids = np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(query_count, 20))
    qrel_scores = np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(query_count, 20))
    teacher_ids = np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(query_count, 10))
    require(query_count == 152, "query input count differs")
    bindings = {
        "documents_sha256": args.documents,
        "training_sha256": args.training,
        "thq4_codes_sha256": args.thq4_codes,
        "thq4_thresholds_sha256": args.thq4_thresholds,
        "int8_codes_sha256": args.int8_codes,
        "int8_scales_sha256": args.int8_scales,
        "candidate_flat_sha256": args.candidate_flat,
        "candidate_raw_sha256": args.candidate_raw,
        "candidate_receipt_sha256": args.candidate_receipt,
        "queries_sha256": args.queries,
        "qrel_ids_sha256": args.qrel_ids,
        "qrel_scores_sha256": args.qrel_scores,
        "teacher_ids_sha256": args.teacher_ids,
    }
    digest_cache = {path: sha(path) for path in set(bindings.values())}
    for field, path in bindings.items():
        if field in classical:
            require(classical[field] == digest_cache[path], f"classical binding differs: {field}")
        if field in teacher:
            require(teacher[field] == digest_cache[path], f"teacher binding differs: {field}")
    require(ml["documents_sha256"] == digest_cache[args.documents], "ML document binding differs")
    require(ml["training_sha256"] == digest_cache[args.training], "ML training binding differs")
    require(ml["thresholds_sha256"] == digest_cache[args.thq4_thresholds], "ML threshold binding differs")

    require(classical["status"] == "EXECUTED" and classical["query_count"] == 152,
            "classical execution contract differs")
    arms = set(classical["summaries"])
    expected_arms = {"candidate-fp32", "thq4-fp32", "direct-int8", "thq4-centroid",
                     "pca32x8", "pq32x8", "opq32x4", "hierarchical-1bit",
                     "hierarchical-2bit", "hierarchical-3bit", "rslm2", "rslm3", "rslm4"}
    require(arms == expected_arms, "classical arm set differs")
    require(classical.get("family") == "thq_r4_codec_function_gate_v1",
            "classical family differs")
    require(classical.get("runner_sha256") == sha(args.classical_runner),
            "classical runner binding differs")
    require(ml.get("runner_sha256") == sha(args.ml_runner), "ML runner binding differs")
    require(teacher.get("runner_sha256") == sha(args.teacher_runner),
            "teacher runner binding differs")
    require(classical.get("candidate_receipt_sha256") == sha(args.candidate_receipt),
            "classical candidate receipt binding differs")
    require(teacher.get("candidate_receipt_sha256") == sha(args.candidate_receipt),
            "teacher candidate receipt binding differs")
    check_summary(classical["rows"], classical["summaries"], arms)
    check_primary_metrics(classical["rows"], candidate_ids, candidate_offsets, documents,
                          queries, qrel_ids, qrel_scores, teacher_ids, arms)
    by_query = {(row["query"], row["arm"]): row for row in classical["rows"]}
    for query in range(152):
        require(by_query[(query, "candidate-fp32")]["candidate_count"] == candidate_counts[query],
                f"candidate count differs for query {query}")
        require(by_query[(query, "candidate-fp32")]["top10_ids"] ==
                by_query[(query, "thq4-fp32")]["top10_ids"],
                f"THQ4 top128 FP32 ceiling differs for query {query}")
    for arm in arms:
        for baseline in ("candidate-fp32", "direct-int8"):
            if arm == baseline:
                continue
            deltas = np.asarray([
                by_query[(query, arm)]["qrels_ndcg10"] -
                by_query[(query, baseline)]["qrels_ndcg10"]
                for query in range(152)
            ], dtype=np.float64)
            rng = np.random.default_rng(20260918)
            bootstrap = deltas[rng.integers(0, len(deltas), size=(2000, len(deltas)))].mean(axis=1)
            observed = classical["summaries"][arm]["qrels_ndcg10_paired"][baseline]
            expected = {
                "mean_delta": float(np.mean(deltas)),
                "p05_delta": float(np.quantile(deltas, 0.05)),
                "min_delta": float(np.min(deltas)),
                "worst_query_loss": float(np.min(deltas)),
                "bootstrap_ci95": [float(np.quantile(bootstrap, 0.025)),
                                    float(np.quantile(bootstrap, 0.975))],
            }
            for key, value in expected.items():
                if isinstance(value, list):
                    require(np.allclose(observed[key], value, atol=1e-12),
                            f"paired summary differs: {arm}/{baseline}/{key}")
                else:
                    require(abs(float(observed[key]) - value) < 1e-12,
                            f"paired summary differs: {arm}/{baseline}/{key}")

    require(ml["status"] == "EXECUTED" and ml["heldout_count"] == 10000,
            "ML sanity execution contract differs")
    ae = ml["results"]["linear_ae32_random_init"]
    pca = ml["results"]["pca32"]
    require(float(ae["train_mse"]) <= float(pca["train_mse"]) * 1.01,
            "linear AE32 does not approach PCA32 on train")
    require(float(ae["heldout_mse"]) <= float(pca["heldout_mse"]) * 1.01,
            "linear AE32 does not approach PCA32 on held-out")

    require(teacher["status"] == "EXECUTED" and teacher["query_count"] == 152,
            "teacher execution contract differs")
    require(teacher["train_query_count"] == 120 and teacher["heldout_query_count"] == 32,
            "teacher split differs")
    counts = Counter((row["split"], row["arm"]) for row in teacher["rows"])
    for arm in ("centroid", "ridge", "decoder"):
        require(counts[("train", arm)] == 120, f"teacher train rows differ: {arm}")
        require(counts[("heldout", arm)] == 32, f"teacher held-out rows differ: {arm}")
    for split in ("train", "heldout"):
        for arm in ("centroid", "ridge", "decoder"):
            rows = [row for row in teacher["rows"] if row["split"] == split and row["arm"] == arm]
            for metric in ("score_mse", "top10_overlap"):
                values = np.asarray([row[metric] for row in rows], dtype=np.float64)
                expected = {"mean": float(np.mean(values)), "p05": float(np.quantile(values, 0.05)),
                            "min": float(np.min(values))}
                for statistic, value in expected.items():
                    require(abs(float(teacher["summaries"][split][arm][metric][statistic]) - value) < 1e-12,
                            f"teacher summary differs: {split}/{arm}/{metric}/{statistic}")

    output = {
        "schema_version": 1,
        "family": "thq_r4_classical_ml_gate_audit_v1",
        "status": "PASS",
        "runner_sha256": sha(Path(__file__)),
        "classical_sha256": sha(args.classical),
        "ml_sanity_sha256": sha(args.ml_sanity),
        "teacher_sha256": sha(args.teacher),
        "candidate_receipt_sha256": sha(args.candidate_receipt),
        "checks": [
            "all external input SHA-256 bindings",
            "classical arm/query cardinality and independently recomputed primary metrics and aggregates",
            "THQ4 top128 FP32 ceiling parity with the candidate FP32 ceiling",
            "linear AE32 within one percent of PCA32 on train and held-out",
            "teacher split cardinality and independently recomputed aggregates",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("THQ R4 classical/ML evidence audit PASS")


if __name__ == "__main__":
    main()
