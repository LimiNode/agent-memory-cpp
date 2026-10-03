#!/usr/bin/env python3
"""Fail-closed aggregate audit for the fresh packed-quality receipt.

The evaluator is intentionally not trusted for quality arithmetic: this audit
reloads the canonical IDs/qrels and recomputes nDCG@10 and the available MRR
identity from the ranked evidence stored in the result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

Q = 305
CODECS = ("int8", "lsq32", "lsq48", "tq1", "tq1-pq8", "plsq8x6x8", "rslm1")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_qrels(source_root: Path) -> tuple[list[str], list[str], dict[str, dict[str, int]]]:
    payload = source_root / "payload"
    query_ids = [json.loads(line)["id"] for line in (payload / "evaluation-query-ids.jsonl").read_text(encoding="utf-8").splitlines()]
    doc_ids = [json.loads(line)["id"] for line in (payload / "evaluation-document-ids.jsonl").read_text(encoding="utf-8").splitlines()]
    if len(query_ids) != Q or len(set(query_ids)) != Q or len(doc_ids) != 1_000_000 or len(set(doc_ids)) != len(doc_ids):
        raise ValueError("canonical ID coverage differs")
    known_docs = set(doc_ids)
    qrels: dict[str, dict[str, int]] = {qid: {} for qid in query_ids}
    for line_number, line in enumerate((payload / "evaluation-qrels.tsv").read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split()
        if len(fields) != 4:
            raise ValueError(f"qrels line {line_number} has invalid field count")
        qid, _iteration, did, grade = fields
        if qid not in qrels or did not in known_docs:
            raise ValueError(f"qrels line {line_number} references an unknown ID")
        qrels[qid][did] = int(grade)
    if any(not values for values in qrels.values()):
        raise ValueError("qrels do not cover every query")
    return query_ids, doc_ids, qrels


def ndcg_at_10(ids: list[str], qrels: dict[str, int]) -> float:
    grades = [qrels.get(value, 0) for value in ids[:10]]
    ideal = sorted(qrels.values(), reverse=True)[:10]

    def dcg(values: list[int]) -> float:
        return sum((2.0 ** value - 1.0) / np.log2(index + 2.0) for index, value in enumerate(values))

    denominator = dcg(ideal)
    return 0.0 if denominator == 0.0 else dcg(grades) / denominator


def check_stage(stage: dict[str, Any], query_ids: list[str], known_docs: set[str], qrels: dict[str, dict[str, int]]) -> dict[str, Any]:
    rows = stage.get("per_query")
    if not isinstance(rows, list) or len(rows) != Q:
        raise ValueError("per-query coverage differs")
    nd_values: list[float] = []
    mr_values: list[float] = []
    mr10_values: list[float] = []
    for index, row in enumerate(rows):
        top10 = row.get("top10_ids")
        if not isinstance(top10, list) or len(top10) != 10 or any(str(value) not in known_docs for value in top10) or len(set(map(str, top10))) != 10:
            raise ValueError(f"invalid top10 evidence at query {index}")
        top10 = [str(value) for value in top10]
        expected_ndcg = ndcg_at_10(top10, qrels[query_ids[index]])
        if abs(float(row["ndcg_at_10"]) - expected_ndcg) > 1e-12:
            raise ValueError(f"nDCG recomputation differs at query {index}")
        rank = row.get("first_relevant_rank")
        first_doc = row.get("first_relevant_doc_id")
        if rank is None:
            if first_doc is not None or row.get("first_relevant_score") is not None or row.get("higher_score_count") is not None or row.get("tied_lower_id_count") is not None or float(row["mrr"]) != 0.0:
                raise ValueError(f"missing first relevant identity at query {index}")
            expected_mrr = 0.0
        else:
            rank = int(rank)
            if rank < 1 or first_doc is None or str(first_doc) not in known_docs or qrels[query_ids[index]].get(str(first_doc), 0) <= 0:
                raise ValueError(f"invalid first relevant identity at query {index}")
            score = row.get("first_relevant_score")
            higher = row.get("higher_score_count")
            tied_lower = row.get("tied_lower_id_count")
            if not isinstance(score, (int, float)) or not np.isfinite(float(score)) or not isinstance(higher, int) or higher < 0 or not isinstance(tied_lower, int) or tied_lower < 0 or 1 + higher + tied_lower != rank:
                raise ValueError(f"rank proof differs at query {index}")
            expected_mrr = 1.0 / rank
            # For ranks represented in top10, independently verify the exact
            # position and that no earlier top10 item is relevant.
            if rank <= 10:
                if top10[rank - 1] != str(first_doc) or any(qrels[query_ids[index]].get(value, 0) > 0 for value in top10[: rank - 1]):
                    raise ValueError(f"first relevant rank/order differs at query {index}")
        if abs(float(row["mrr"]) - expected_mrr) > 1e-12:
            raise ValueError(f"MRR identity differs at query {index}")
        expected_mrr10 = next((1.0 / (position + 1) for position, value in enumerate(top10) if qrels[query_ids[index]].get(value, 0) > 0), 0.0)
        if abs(float(row["mrr_at_10"]) - expected_mrr10) > 1e-12:
            raise ValueError(f"MRR@10 identity differs at query {index}")
        nd_values.append(expected_ndcg)
        mr_values.append(expected_mrr)
        mr10_values.append(expected_mrr10)
    nd = np.asarray(nd_values)
    mr = np.asarray(mr_values)
    mr10 = np.asarray(mr10_values)
    if abs(float(stage["mean_ndcg_at_10"]) - float(nd.mean())) > 1e-12:
        raise ValueError("nDCG aggregate differs")
    if abs(float(stage["mean_mrr"]) - float(mr.mean())) > 1e-12:
        raise ValueError("full-rank MRR aggregate differs")
    if abs(float(stage["mean_mrr_at_10"]) - float(mr10.mean())) > 1e-12:
        raise ValueError("MRR@10 aggregate differs")
    return {"mean_ndcg_at_10": float(nd.mean()), "mean_mrr": float(mr.mean()), "mean_mrr_at_10": float(mr10.mean()), "p05_ndcg_at_10": float(np.sort(nd)[14]), "worst_ndcg_at_10": float(nd.min())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", type=Path, required=True)
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--measured-code-commit", required=True)
    ap.add_argument("--receipt-commit", required=True)
    ap.add_argument("--release-target-commit", required=True)
    ap.add_argument("--bundle-sha256", required=True)
    args = ap.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    if result.get("query_count") != Q or result.get("candidate_count") != 5000:
        raise ValueError("fresh matrix shape differs")
    if set(result.get("stages", {})) != {"prototype_ivf", "modern_r4"}:
        raise ValueError("serving-mode coverage differs")
    query_ids, doc_ids, qrels = load_qrels(args.source_root)
    known_docs = set(doc_ids)
    result_hash = sha256(args.result)
    evidence_binding = hashlib.sha256((result_hash + args.bundle_sha256).encode("ascii")).hexdigest()
    compact = {
        "schema_version": 2,
        "family": "fresh_packed_quality_decomposition_receipt_v1",
        "status": "PASS",
        "measured_code_commit": args.measured_code_commit,
        "receipt_commit": args.receipt_commit,
        "receipt_commit_semantics": "generation commit for this receipt; the containing commit is its successor because a commit cannot embed its own hash",
        "release_target_commit": args.release_target_commit,
        "result_sha256": result_hash,
        "bundle_sha256": args.bundle_sha256,
        "evidence_binding_sha256": evidence_binding,
        "evidence_binding_definition": "sha256(ascii(result_sha256 + bundle_sha256)); deterministic binding digest, not a content-tree root",
        "query_count": Q,
        "codec_count": len(CODECS),
        "metric_contract": {"ndcg": "nDCG@10 recomputed from top10_ids and canonical qrels", "mrr": "full ranked-stage MRR with first-relevant score/rank proof (higher-score and tied-lower-ID counts)", "mrr_at_10": "diagnostic only", "tie_policy": "score-desc-or-THQ-distance-asc then numeric-id-asc"},
        "source": result.get("source"),
        "payloads": result.get("payloads"),
        "stages": {},
    }
    compact["exact_oracle"] = check_stage(result["exact_oracle"], query_ids, known_docs, qrels)
    for mode, value in result["stages"].items():
        compact["stages"][mode] = {"route_exact_fp32": check_stage(value["route_exact_fp32"], query_ids, known_docs, qrels), "thq_top128_exact_fp32": check_stage(value["thq_top128_exact_fp32"], query_ids, known_docs, qrels), "packed": {codec: check_stage(value["packed"][codec], query_ids, known_docs, qrels) for codec in CODECS}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "result_sha256": result_hash, "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()
