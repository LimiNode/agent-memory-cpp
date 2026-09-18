#!/usr/bin/env python3
"""Fail-closed audit for the THQ classical, ML sanity and teacher diagnostics."""
from __future__ import annotations

import argparse
import hashlib
import json
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


def main() -> None:
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
    bindings = {
        "documents_sha256": args.documents,
        "training_sha256": args.training,
        "thq4_codes_sha256": args.thq4_codes,
        "thq4_thresholds_sha256": args.thq4_thresholds,
        "int8_codes_sha256": args.int8_codes,
        "int8_scales_sha256": args.int8_scales,
        "candidate_flat_sha256": args.candidate_flat,
        "candidate_raw_sha256": args.candidate_raw,
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
                     "hierarchical-2bit", "hierarchical-3bit", "rslm2", "rslm3"}
    require(arms == expected_arms, "classical arm set differs")
    check_summary(classical["rows"], classical["summaries"], arms)
    by_query = {(row["query"], row["arm"]): row for row in classical["rows"]}
    for query in range(152):
        require(by_query[(query, "candidate-fp32")]["candidate_count"] == candidate_counts[query],
                f"candidate count differs for query {query}")
        require(by_query[(query, "candidate-fp32")]["top10_ids"] ==
                by_query[(query, "thq4-fp32")]["top10_ids"],
                f"THQ4 top128 FP32 ceiling differs for query {query}")

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
        "classical_sha256": sha(args.classical),
        "ml_sanity_sha256": sha(args.ml_sanity),
        "teacher_sha256": sha(args.teacher),
        "checks": [
            "all external input SHA-256 bindings",
            "classical arm/query cardinality and independently recomputed aggregates",
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
