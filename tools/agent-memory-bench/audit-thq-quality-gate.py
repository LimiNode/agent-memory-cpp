#!/usr/bin/env python3
"""Fail-closed audit for the North Star full-corpus quality receipt."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def sha(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            d.update(chunk)
    return d.hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def ndcg(ids: list[int], qrel_ids: np.ndarray, qrel_scores: np.ndarray) -> float:
    grades = {int(doc): float(score) for doc, score in zip(qrel_ids, qrel_scores)
              if int(doc) >= 0 and float(score) > 0}
    gains = np.asarray([2.0 ** grades.get(int(doc), 0.0) - 1.0 for doc in ids[:10]])
    discounts = np.log2(np.arange(2, 2 + len(gains), dtype=np.float64))
    ideal = np.sort(np.asarray([2.0 ** score - 1.0 for score in grades.values()]))[::-1][:10]
    ideal_dcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal), dtype=np.float64))))
    return float(np.sum(gains / discounts) / ideal_dcg) if ideal_dcg else 0.0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--receipt", type=Path, required=True)
    p.add_argument("--raw", type=Path, required=True)
    p.add_argument("--runner", type=Path)
    p.add_argument("--thq-manifest", type=Path)
    p.add_argument("--candidate-receipt", type=Path)
    p.add_argument("--candidate-raw", type=Path)
    p.add_argument("--candidate-flat", type=Path)
    p.add_argument("--packed-thq", type=Path)
    a = p.parse_args()
    receipt = json.loads(a.receipt.read_text())
    raw = json.loads(a.raw.read_text())
    rows = raw["rows"]
    require(receipt["family"] == "semantic_thq_quality_gate_v1", "family differs")
    require(receipt["execution_status"] == "EXECUTED", "receipt is not executed")
    require(receipt["raw_output"]["sha256"] == sha(a.raw), "raw SHA differs")
    if a.runner:
        require(receipt.get("runner_sha256") == sha(a.runner), "runner SHA differs")
    for key, path in (("thq_manifest_sha256", a.thq_manifest),
                      ("candidate_receipt_sha256", a.candidate_receipt),
                      ("candidate_raw_sha256", a.candidate_raw),
                      ("candidate_flat_sha256", a.candidate_flat)):
        if path:
            require(receipt.get(key) == sha(path), f"{key} differs")
    if a.packed_thq:
        packed = receipt.get("packed_thq", {})
        require(packed.get("sha256") == sha(a.packed_thq), "packed THQ SHA differs")
        require(int(packed.get("bytes", -1)) == a.packed_thq.stat().st_size, "packed THQ size differs")
    independent = bool(a.candidate_raw and a.candidate_flat and a.thq_manifest)
    if independent:
        manifest = json.loads(a.thq_manifest.read_text())
        refs = manifest["references"]
        qrels_ids = np.memmap(refs["qrel_ids"]["path"], mode="r", dtype="<i8", shape=(152, 20))
        qrels_scores = np.memmap(refs["qrel_scores"]["path"], mode="r", dtype="<f4", shape=(152, 20))
        teachers = np.memmap(refs["teacher_ids"]["path"], mode="r", dtype="<i8", shape=(152, 10))
        candidate_raw = json.loads(a.candidate_raw.read_text())
        counts = [int(row["candidate_count"]) for row in candidate_raw["rows"]]
        flat = np.memmap(a.candidate_flat, mode="r", dtype=np.uint8, shape=(sum(counts), 148))
        offsets = np.cumsum([0, *counts[:-1]])
        for row in rows:
            ids = [int(value) for value in row["top10_ids"]]
            qi = int(row["query"])
            candidate_ids = np.frombuffer(np.asarray(flat[offsets[qi]:offsets[qi] + counts[qi], :4]).tobytes(), dtype="<i4").astype(np.int64)
            expected_survival = float(np.isin(np.asarray(teachers[qi]), candidate_ids).sum() / 10.0)
            expected_overlap = float(np.isin(np.asarray(teachers[qi]), ids).sum() / 10.0)
            require(abs(float(row["candidate_survival"]) - expected_survival) <= 1e-12, "candidate survival recomputation differs")
            require(abs(float(row["teacher_overlap"]) - expected_overlap) <= 1e-12, "teacher overlap recomputation differs")
            require(abs(float(row["qrels_ndcg10"]) - ndcg(ids, np.asarray(qrels_ids[qi]), np.asarray(qrels_scores[qi]))) <= 1e-12,
                    "nDCG recomputation differs")
    require(len(rows) == 152 * 4, "row count differs")
    names = {row["representation"] for row in rows}
    require(names == {"direct_packed_thq", "candidate_thq", "candidate_fp32_rerank", "exact_e5_teacher"}, "representations differ")
    for name in names:
        group = [row for row in rows if row["representation"] == name]
        require(len(group) == 152, f"query count differs for {name}")
        require(all(0.0 <= float(row["qrels_ndcg10"]) <= 1.0 for row in group), f"invalid nDCG for {name}")
        require(all(len(row.get("top10_ids", [])) == 10 for row in group), f"top10 ids missing for {name}")
    exact = [row for row in rows if row["representation"] == "exact_e5_teacher"]
    require(all(float(row["teacher_overlap"]) == 1.0 for row in exact), "exact teacher is not identity")
    print("THQ North Star quality receipt audit passed")


if __name__ == "__main__":
    main()
