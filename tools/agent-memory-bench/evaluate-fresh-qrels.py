#!/usr/bin/env python3
"""Evaluate the untouched canonical 305-query qrels and routed candidates.

This is a NumPy reference harness for the post-serving study.  It deliberately
opens the canonical query/qrels materialization by path, computes an exact
FP32 cosine oracle with numeric document-ID tie breaking, and can then score a
candidate export against that oracle.  It is not a production ANN runner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


class EvaluationError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def percentile_nearest(values: list[float], p: float) -> float:
    if not values:
        raise EvaluationError("percentile input is empty")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(np.ceil(p * len(ordered))) - 1))
    return float(ordered[index])


def dcg(grades: list[int]) -> float:
    return sum((2.0 ** grade - 1.0) / np.log2(index + 2.0) for index, grade in enumerate(grades))


def ndcg_at_10(ids: list[Any], qrels: dict[Any, int]) -> float:
    values = [qrels.get(value, 0) for value in ids[:10]]
    ideal = sorted(qrels.values(), reverse=True)[:10]
    denominator = dcg(ideal)
    return 0.0 if denominator == 0.0 else dcg(values) / denominator


def mrr(ids: list[Any], qrels: dict[Any, int]) -> float:
    for index, value in enumerate(ids, 1):
        if qrels.get(value, 0) > 0:
            return 1.0 / index
    return 0.0


def load_root(root: Path) -> dict[str, Any]:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    outputs = manifest["outputs"]

    def output(name: str) -> Path:
        entry = outputs[name]
        path = root / entry["path"]
        if not path.is_file() or sha256(path) != entry["sha256"]:
            raise EvaluationError(f"materialization hash mismatch: {name}")
        return path

    dimension = int(manifest["vector_format"]["dimension"])
    document_ids = [json.loads(line)["id"] for line in output("evaluation_document_ids").read_text(encoding="utf-8").splitlines()]
    query_ids = [json.loads(line)["id"] for line in output("evaluation_query_ids").read_text(encoding="utf-8").splitlines()]
    if len(document_ids) != len(set(document_ids)) or len(query_ids) != len(set(query_ids)):
        raise EvaluationError("evaluation IDs are not unique")
    numeric = all(value.lstrip("-").isdigit() for value in document_ids)
    document_order_keys: np.ndarray = np.asarray(
        [int(value) for value in document_ids] if numeric else document_ids,
        dtype=np.int64 if numeric else np.str_,
    )
    qrels: dict[str, dict[str, int]] = {value: {} for value in query_ids}
    for line_number, line in enumerate(output("evaluation_qrels").read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split()
        if len(fields) == 4:
            query, _iteration, document, grade = fields
        elif len(fields) == 3:
            query, document, grade = fields
        else:
            raise EvaluationError(f"qrels line {line_number} has an invalid field count")
        if query not in qrels:
            raise EvaluationError(f"qrels line {line_number} references an unknown query")
        qrels[query][document] = int(grade)
    documents = np.memmap(output("evaluation_document_vectors"), dtype="<f4", mode="r").reshape(len(document_ids), dimension)
    queries = np.memmap(output("evaluation_query_vectors"), dtype="<f4", mode="r").reshape(len(query_ids), dimension)
    return {
        "root": root,
        "manifest": manifest,
        "manifest_sha256": sha256(manifest_path),
        "documents": documents,
        "queries": queries,
        "document_ids": np.asarray(document_ids, dtype=np.str_),
        "document_order_keys": document_order_keys,
        "query_ids": query_ids,
        "qrels": qrels,
        "qrels_sha256": outputs["evaluation_qrels"]["sha256"],
        "query_vectors_sha256": outputs["evaluation_query_vectors"]["sha256"],
        "document_vectors_sha256": outputs["evaluation_document_vectors"]["sha256"],
        "document_ids_sha256": outputs["evaluation_document_ids"]["sha256"],
    }


def exact_top(data: dict[str, Any], query_position: int, k: int) -> tuple[np.ndarray, np.ndarray]:
    scores = np.asarray(data["documents"] @ data["queries"][query_position], dtype=np.float32)
    # The small partition bounds work for the 1M-row corpus while the final
    # lexsort makes the result deterministic for equal scores.
    take = min(k, scores.size)
    positions = np.argpartition(-scores, take - 1)[:take]
    order = np.lexsort((data["document_order_keys"][positions], -scores[positions]))
    positions = positions[order]
    return positions, scores[positions]


def exact_run(args: argparse.Namespace) -> None:
    data = load_root(args.evaluation_root)
    if len(data["query_ids"]) != 305:
        raise EvaluationError("canonical fresh run must contain exactly 305 queries")
    max_k = max(args.top_k, 1000)
    raw_rows: list[dict[str, Any]] = []
    per_query: list[dict[str, Any]] = []
    for position, query_id in enumerate(data["query_ids"]):
        positions, _ = exact_top(data, position, max_k)
        ids = data["document_ids"][positions].tolist()
        grades = data["qrels"][query_id]
        row = {
            "query_position": position,
            "query_id": query_id,
            "top10_ids": ids[:10],
            "top128_ids": ids[:128],
            "ndcg_at_10": ndcg_at_10(ids, grades),
            "mrr": mrr(ids, grades),
            "relevant_in_top10": sum(grades.get(value, 0) > 0 for value in ids[:10]),
        }
        per_query.append(row)
        raw_rows.append({"query_position": position, "query_id": query_id, "top10_ids": ids[:10], "top128_ids": ids[:128]})
    ndcgs = [row["ndcg_at_10"] for row in per_query]
    mrrs = [row["mrr"] for row in per_query]
    result = {
        "schema_version": 1,
        "family": "fresh_exact_fp32_qrels_reference_v1",
        "status": "EXECUTED",
        "scope": "all canonical 305 queries; no historical candidate stream",
        "metric": "cosine",
        "query_count": len(per_query),
        "document_count": len(data["document_ids"]),
        "top_k": args.top_k,
        "p05_ndcg_at_10": percentile_nearest(ndcgs, 0.05),
        "mean_ndcg_at_10": float(np.mean(ndcgs)),
        "worst_ndcg_query": min(per_query, key=lambda row: (row["ndcg_at_10"], row["query_position"])),
        "p05_mrr": percentile_nearest(mrrs, 0.05),
        "mean_mrr": float(np.mean(mrrs)),
        "worst_mrr_query": min(per_query, key=lambda row: (row["mrr"], row["query_position"])),
        "source": {
            "materialization_manifest_sha256": data["manifest_sha256"],
            "document_vectors_sha256": data["document_vectors_sha256"],
            "document_ids_sha256": data["document_ids_sha256"],
            "query_vectors_sha256": data["query_vectors_sha256"],
            "qrels_sha256": data["qrels_sha256"],
        },
        "tie_policy": "score_desc_id_asc_numeric_when_numeric_else_lexical",
        "oracle_algorithm": "FP32 dot on normalized vectors; argpartition then lexsort",
        "runtime": {"python": platform.python_version(), "numpy": np.__version__},
        "per_query": per_query,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.raw is not None:
        args.raw.parent.mkdir(parents=True, exist_ok=True)
        args.raw.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in raw_rows), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "query_count": 305, "mean_ndcg_at_10": result["mean_ndcg_at_10"], "mean_mrr": result["mean_mrr"]}, sort_keys=True))


def candidate_run(args: argparse.Namespace) -> None:
    data = load_root(args.evaluation_root)
    export = json.loads(args.candidates.read_text(encoding="utf-8"))
    rows = export.get("rows")
    if not isinstance(rows, list) or len(rows) != len(data["query_ids"]):
        raise EvaluationError("candidate export must contain one row per fresh query")
    by_position: dict[int, list[str]] = {}
    for row in rows:
        position = int(row["query_position"])
        ids = [str(value) for value in row.get("candidate_ids", [])]
        if position in by_position or not ids or len(ids) != len(set(ids)):
            raise EvaluationError("candidate export has duplicate or empty query rows")
        by_position[position] = ids
    if set(by_position) != set(range(len(data["query_ids"]))):
        raise EvaluationError("candidate export query positions are incomplete")
    oracle = json.loads(args.oracle.read_text(encoding="utf-8"))
    oracle_rows = oracle.get("per_query")
    if oracle.get("source") != {"materialization_manifest_sha256": data["manifest_sha256"], "document_vectors_sha256": data["document_vectors_sha256"], "document_ids_sha256": data["document_ids_sha256"], "query_vectors_sha256": data["query_vectors_sha256"], "qrels_sha256": data["qrels_sha256"]} or len(oracle_rows) != len(data["query_ids"]):
        raise EvaluationError("oracle provenance differs")
    id_to_position = {str(value): index for index, value in enumerate(data["document_ids"].tolist())}
    rows_out: list[dict[str, Any]] = []
    for position, query_id in enumerate(data["query_ids"]):
        candidate_ids = [str(value) for value in by_position[position]]
        if any(value not in id_to_position for value in candidate_ids):
            raise EvaluationError("candidate references an unknown document")
        candidate_positions = np.asarray([id_to_position[value] for value in candidate_ids], dtype=np.int64)
        candidate_scores = np.asarray(data["documents"][candidate_positions] @ data["queries"][position], dtype=np.float32)
        candidate_keys = np.asarray(candidate_ids, dtype=np.str_)
        order = np.lexsort((candidate_keys, -candidate_scores))
        ranked = [candidate_ids[index] for index in order]
        exact_top128 = [str(value) for value in oracle_rows[position]["top128_ids"]]
        route_recall = sum(value in set(candidate_ids) for value in exact_top128) / len(exact_top128)
        rows_out.append({"query_position": position, "query_id": query_id, "candidate_count": len(candidate_ids), "route_recall_at_128": route_recall, "top10_ids": ranked[:10], "ndcg_at_10": ndcg_at_10(ranked, data["qrels"][query_id]), "mrr": mrr(ranked, data["qrels"][query_id])})
    ndcgs = [row["ndcg_at_10"] for row in rows_out]
    result = {"schema_version": 1, "family": "fresh_candidate_qrels_evaluation_v1", "status": "EXECUTED", "candidate_export_sha256": sha256(args.candidates), "oracle_sha256": sha256(args.oracle), "query_count": len(rows_out), "mean_route_recall_at_128": float(np.mean([row["route_recall_at_128"] for row in rows_out])), "p05_route_recall_at_128": percentile_nearest([row["route_recall_at_128"] for row in rows_out], 0.05), "mean_ndcg_at_10": float(np.mean(ndcgs)), "p05_ndcg_at_10": percentile_nearest(ndcgs, 0.05), "worst_ndcg_query": min(rows_out, key=lambda row: (row["ndcg_at_10"], row["query_position"])), "mean_mrr": float(np.mean([row["mrr"] for row in rows_out])), "source": {"materialization_manifest_sha256": data["manifest_sha256"], "qrels_sha256": data["qrels_sha256"]}, "per_query": rows_out}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "query_count": len(rows_out), "mean_route_recall_at_128": result["mean_route_recall_at_128"], "mean_ndcg_at_10": result["mean_ndcg_at_10"]}, sort_keys=True))


def self_test() -> None:
    if percentile_nearest([1.0, 2.0, 3.0, 4.0], 0.05) != 1.0:
        raise EvaluationError("nearest-rank percentile failed")
    if ndcg_at_10([2, 1], {1: 2, 2: 1}) <= 0.0 or mrr([3, 2], {2: 1}) != 0.5:
        raise EvaluationError("qrels metric self-test failed")
    print("evaluate-fresh-qrels self-test PASS")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("self-test")
    exact = commands.add_parser("exact")
    exact.add_argument("--evaluation-root", type=Path, required=True)
    exact.add_argument("--output", type=Path, required=True)
    exact.add_argument("--raw", type=Path)
    exact.add_argument("--top-k", type=int, default=128)
    candidate = commands.add_parser("candidates")
    candidate.add_argument("--evaluation-root", type=Path, required=True)
    candidate.add_argument("--candidates", type=Path, required=True)
    candidate.add_argument("--oracle", type=Path, required=True)
    candidate.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "self-test":
            self_test()
        elif args.command == "exact":
            exact_run(args)
        else:
            candidate_run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, EvaluationError) as error:
        print(f"evaluate-fresh-qrels: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
