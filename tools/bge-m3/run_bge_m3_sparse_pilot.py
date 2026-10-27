#!/usr/bin/env python3
"""Run an evidence-first BGE-M3 sparse retrieval pilot.

The runner is intentionally optional and read-only. It never changes the C++
build or storage layout. Missing or mismatched evidence produces an explicit
INSUFFICIENT_EVIDENCE receipt instead of a synthetic evaluation result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
MODEL_REPOSITORY = "BAAI/bge-m3"
MODEL_BASE_URL = f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}"

# SHA-256 values are verified from the exact revision, not from a mutable main
# branch or from a model-card claim.
REQUIRED_ARTIFACTS: dict[str, tuple[int, str]] = {
    "pytorch_model.bin": (2271145830, "B5E0CE3470ABF5EF3831AA1BD5553B486803E83251590AB7FF35A117CF6AAD38"),
    "config.json": (687, "26159E7AD065073448460117EB24B7A4572F6F4E78EADFF65DC0A11C052449FA"),
    "config_sentence_transformers.json": (123, "1EEF72430E7194A1E59680E635AED81FFA083F05668DBC5BB1C56C04C0999C38"),
    "modules.json": (349, "84E40C8E006C9B1D6C122E02CBA9B02458120B5FB0C87B746C41E0207CF642CF"),
    "sentence_bert_config.json": (54, "EB9B44B13C0F52A3B3685C3B1CBDEA1BA8B04BEA123B98F61610048940776EB1"),
    "sentencepiece.bpe.model": (5069051, "CFC8146ABE2A0488E9E2A0C56DE7952F7C11AB059ECA145A0A727AFCE0DB2865"),
    "sparse_linear.pt": (3516, "45C93804D2142B8F6D7EC6914AE23A1EEE9C6A1D27D83D908A20D2AFB3595AD9"),
    "special_tokens_map.json": (964, "8C785ABEBEA9AE3257B61681B4E6FD8365CEAFDE980C21970D001E834CF10835"),
    "tokenizer.json": (17098108, "21106B6D7DAB2952C1D496FB21D5DC9DB75C28ED361A05F5020BBBA27810DD08"),
    "tokenizer_config.json": (444, "A62B2B6784F990259FDDEF5F16388693A8043BE4F69179E6A5257EEB3F9ABAC4"),
}

TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)


class EvidenceError(ValueError):
    """A fail-closed artifact or evaluation-data violation."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest().upper()


def artifact_inventory(artifact_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, (expected_size, expected_sha256) in REQUIRED_ARTIFACTS.items():
        path = artifact_dir / name
        row: dict[str, Any] = {
            "path": name,
            "source_url": f"{MODEL_BASE_URL}/{name}",
            "revision": MODEL_REVISION,
            "expected_size": expected_size,
            "expected_sha256": expected_sha256,
            "present": path.is_file(),
            "loaded": False,
        }
        if path.is_file():
            row["size"] = path.stat().st_size
            row["sha256"] = sha256_file(path)
            row["verified"] = row["size"] == expected_size and row["sha256"] == expected_sha256
        else:
            row["size"] = None
            row["sha256"] = None
            row["verified"] = False
        rows.append(row)
    return rows


def require_verified_artifacts(rows: list[dict[str, Any]]) -> None:
    failures = [row["path"] for row in rows if not row["verified"]]
    if failures:
        raise EvidenceError("artifact inventory is incomplete or hash-mismatched: " + ", ".join(failures))


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"{path} must contain a JSON object")
    return value


def validate_data(corpus: dict[str, Any], queries: dict[str, Any], qrels: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, int]]]:
    documents = corpus.get("documents")
    query_rows = queries.get("queries")
    qrel_rows = qrels.get("qrels")
    if not isinstance(documents, list) or not isinstance(query_rows, list) or not isinstance(qrel_rows, list):
        raise EvidenceError("corpus, queries and qrels must expose lists")
    if not isinstance(corpus.get("revision"), str) or not corpus["revision"]:
        raise EvidenceError("corpus revision is required")
    if not isinstance(queries.get("revision"), str) or not queries["revision"]:
        raise EvidenceError("query revision is required")
    if not isinstance(qrels.get("revision"), str) or not qrels["revision"]:
        raise EvidenceError("qrels revision is required")
    if any(not isinstance(row, dict) for row in documents + query_rows + qrel_rows):
        raise EvidenceError("corpus, queries and qrels rows must be objects")
    document_ids = [row.get("id") for row in documents]
    if any(not isinstance(value, str) or not value for value in document_ids) or len(set(document_ids)) != len(document_ids):
        raise EvidenceError("document IDs must be unique non-empty strings")
    for row in documents:
        if not isinstance(row.get("text"), str) or not row["text"]:
            raise EvidenceError("document text is required")
        if row.get("language") not in {"en", "ru", "code"}:
            raise EvidenceError("documents must declare en, ru or code language")
    query_ids = [row.get("id") for row in query_rows]
    if any(not isinstance(value, str) or not value for value in query_ids) or len(set(query_ids)) != len(query_ids):
        raise EvidenceError("query IDs must be unique non-empty strings")
    for row in query_rows:
        if not isinstance(row.get("text"), str) or not row["text"]:
            raise EvidenceError("query text is required")
        if row.get("language") not in {"en", "ru", "code"}:
            raise EvidenceError("queries must declare en, ru or code language")
        if row.get("answer_mode") not in {"judged", "no_answer"}:
            raise EvidenceError("query answer_mode must be judged or no_answer")
    document_set = set(document_ids)
    query_set = set(query_ids)
    mapped: dict[str, dict[str, int]] = {query_id: {} for query_id in query_set}
    for row in qrel_rows:
        query_id, doc_id, grade = row.get("query_id"), row.get("doc_id"), row.get("grade")
        if query_id not in query_set or doc_id not in document_set or isinstance(grade, bool) or not isinstance(grade, int) or grade < 0:
            raise EvidenceError("qrel identity or grade is invalid")
        if doc_id in mapped[query_id]:
            raise EvidenceError("duplicate qrel judgment")
        mapped[query_id][doc_id] = grade
    for row in query_rows:
        judgments = mapped[row["id"]]
        if row["answer_mode"] == "no_answer" and judgments:
            raise EvidenceError("no-answer query must not have qrels")
        if row["answer_mode"] == "judged" and not judgments:
            raise EvidenceError("judged query must have qrels")
    return documents, query_rows, mapped


def normalize_weights(value: Any, label: str) -> dict[int, float]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} sparse weights must be a mapping")
    result: dict[int, float] = {}
    for raw_token, raw_weight in value.items():
        try:
            token = int(raw_token)
        except (TypeError, ValueError) as exc:
            raise EvidenceError(f"{label} token ID is not an integer") from exc
        weight = float(raw_weight)
        if token < 0 or not math.isfinite(weight) or weight < 0.0:
            raise EvidenceError(f"{label} sparse weight is invalid")
        if token in result:
            raise EvidenceError(f"{label} contains duplicate token IDs")
        result[token] = weight
    return result


def sparse_score(query_weights: dict[int, float], document_weights: dict[int, float]) -> float:
    return sum(weight * document_weights.get(token, 0.0) for token, weight in query_weights.items())


def rank_sparse(postings: dict[int, list[tuple[str, float]]], query_weights: dict[int, float], limit: int) -> list[dict[str, Any]]:
    scores: dict[str, float] = defaultdict(float)
    for token, query_weight in query_weights.items():
        for document_id, document_weight in postings.get(token, []):
            scores[document_id] += query_weight * document_weight
    return [
        {"doc_id": document_id, "score": score}
        for document_id, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
        if score > 0.0
    ]


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.casefold())


def bm25_rank(documents: list[dict[str, Any]], query: str, limit: int) -> list[dict[str, Any]]:
    document_terms = [tokens(row["text"]) for row in documents]
    query_terms = set(tokens(query))
    df: dict[str, int] = defaultdict(int)
    for terms in document_terms:
        for term in set(terms):
            df[term] += 1
    lengths = [len(terms) for terms in document_terms]
    average_length = statistics.mean(lengths) if lengths else 1.0
    scores: list[dict[str, Any]] = []
    for row, terms in zip(documents, document_terms):
        counts: dict[str, int] = defaultdict(int)
        for term in terms:
            counts[term] += 1
        score = 0.0
        for term in query_terms:
            if term not in counts:
                continue
            idf = math.log(1.0 + (len(documents) - df[term] + 0.5) / (df[term] + 0.5))
            norm = 1.2 * (1.0 - 0.75 + 0.75 * len(terms) / max(average_length, 1.0))
            score += idf * counts[term] * 2.2 / (counts[term] + norm)
        if score > 0.0:
            scores.append({"doc_id": row["id"], "score": score})
    return sorted(scores, key=lambda item: (-item["score"], item["doc_id"]))[:limit]


def recall_at_k(hits: list[dict[str, Any]], relevant: dict[str, int], k: int) -> float:
    positives = {doc_id for doc_id, grade in relevant.items() if grade > 0}
    if not positives:
        return 0.0
    return len({hit["doc_id"] for hit in hits[:k]} & positives) / len(positives)


def ndcg_at_k(hits: list[dict[str, Any]], relevant: dict[str, int], k: int) -> float:
    def gain(grade: int) -> float:
        return float((2 ** grade) - 1)

    actual = sum(gain(relevant.get(hit["doc_id"], 0)) / math.log2(index + 2) for index, hit in enumerate(hits[:k]))
    ideal_grades = sorted((grade for grade in relevant.values() if grade > 0), reverse=True)
    ideal = sum(gain(grade) / math.log2(index + 2) for index, grade in enumerate(ideal_grades[:k]))
    return actual / ideal if ideal else 0.0


def metrics(rows: list[dict[str, Any]], qrels: dict[str, dict[str, int]], arm: str, limit: int) -> dict[str, Any]:
    judged = [row for row in rows if row["answer_mode"] == "judged"]
    positive = [row for row in judged if any(grade > 0 for grade in qrels[row["query_id"]].values())]
    no_answer = [row for row in rows if row["answer_mode"] == "no_answer"]
    return {
        "query_count": len(rows),
        "judged_query_count": len(judged),
        "positive_judged_query_count": len(positive),
        "recall_at": {
            str(k): (sum(recall_at_k(row[f"{arm}_hits"], qrels[row["query_id"]], k) for row in positive) / len(positive) if positive and k <= limit else None)
            for k in (1, 5, 10, 50)
        },
        "ndcg_at_10": (sum(ndcg_at_k(row[f"{arm}_hits"], qrels[row["query_id"]], 10) for row in positive) / len(positive) if positive else None),
        "mrr": (sum(next((1.0 / (index + 1) for index, hit in enumerate(row[f"{arm}_hits"]) if qrels[row["query_id"]].get(hit["doc_id"], 0) > 0), 0.0) for row in positive) / len(positive) if positive else None),
        "no_answer_accuracy": (sum(not row[f"{arm}_hits"] for row in no_answer) / len(no_answer) if no_answer else None),
    }


def status_receipt(status: str, reason: str, inventory: list[dict[str, Any]], output: Path) -> int:
    receipt = {
        "status": status,
        "decision": status,
        "reason": reason,
        "model": {"repository": MODEL_REPOSITORY, "revision": MODEL_REVISION},
        "artifacts": inventory,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def run(args: argparse.Namespace) -> int:
    inventory = artifact_inventory(args.artifact_dir)
    try:
        require_verified_artifacts(inventory)
    except EvidenceError as exc:
        return status_receipt("INSUFFICIENT_EVIDENCE", str(exc), inventory, args.output)
    if not args.corpus or not args.queries or not args.qrels:
        return status_receipt("INSUFFICIENT_EVIDENCE", "frozen corpus, queries and independent qrels are required", inventory, args.output)
    try:
        documents, query_rows, qrels = validate_data(
            load_json(args.corpus), load_json(args.queries), load_json(args.qrels)
        )
    except EvidenceError as exc:
        return status_receipt("INSUFFICIENT_EVIDENCE", str(exc), inventory, args.output)
    try:
        from FlagEmbedding import BGEM3FlagModel
    except ImportError as exc:
        return status_receipt("INSUFFICIENT_EVIDENCE", f"FlagEmbedding is unavailable in the isolated pilot environment: {exc}", inventory, args.output)
    model_dir = args.artifact_dir
    try:
        model = BGEM3FlagModel(str(model_dir), use_fp16=False)
        document_encoding_started = time.perf_counter_ns()
        document_output = model.encode([row["text"] for row in documents], return_dense=False, return_sparse=True, return_colbert_vecs=False, batch_size=args.batch_size)
        document_encoding_ms = (time.perf_counter_ns() - document_encoding_started) / 1_000_000.0
        query_encoding_started = time.perf_counter_ns()
        query_output = model.encode([row["text"] for row in query_rows], return_dense=False, return_sparse=True, return_colbert_vecs=False, batch_size=args.batch_size)
        query_encoding_ms = (time.perf_counter_ns() - query_encoding_started) / 1_000_000.0
        document_weights = [normalize_weights(value, "document") for value in document_output["lexical_weights"]]
        query_weights = [normalize_weights(value, "query") for value in query_output["lexical_weights"]]
    except Exception as exc:  # model backends expose heterogeneous exception types
        return status_receipt("INSUFFICIENT_EVIDENCE", f"BGE-M3 sparse inference failed closed: {exc}", inventory, args.output)
    for row in inventory:
        row["loaded"] = True
    build_started = time.perf_counter_ns()
    postings: dict[int, list[tuple[str, float]]] = defaultdict(list)
    for document, weights in zip(documents, document_weights):
        for token, weight in weights.items():
            postings[token].append((document["id"], weight))
    for values in postings.values():
        values.sort(key=lambda item: item[0])
    build_ms = (time.perf_counter_ns() - build_started) / 1_000_000.0
    rows: list[dict[str, Any]] = []
    traversal_samples: list[float] = []
    ranking_samples: list[float] = []
    total_samples: list[float] = []
    for query, weights in zip(query_rows, query_weights):
        traversal_started = time.perf_counter_ns()
        candidate_scores: dict[str, float] = defaultdict(float)
        for token, query_weight in weights.items():
            for document_id, document_weight in postings.get(token, []):
                candidate_scores[document_id] += query_weight * document_weight
        traversal_samples.append((time.perf_counter_ns() - traversal_started) / 1_000_000.0)
        ranking_started = time.perf_counter_ns()
        sparse_hits = [
            {"doc_id": doc_id, "score": score}
            for doc_id, score in sorted(
                ((doc_id, score) for doc_id, score in candidate_scores.items() if score > 0.0),
                key=lambda item: (-item[1], item[0]),
            )[:args.limit]
        ]
        ranking_samples.append((time.perf_counter_ns() - ranking_started) / 1_000_000.0)
        total_samples.append(traversal_samples[-1] + ranking_samples[-1])
        rows.append({
            "query_id": query["id"], "answer_mode": query["answer_mode"],
            "sparse_hits": sparse_hits,
            "bm25_hits": bm25_rank(documents, query["text"], args.limit),
        })
    # Verify the local posting scorer against the library's reference function
    # on a bounded Cartesian sample before trusting the full ranking.
    for query_weights_row in query_weights[: min(3, len(query_weights))]:
        for document_weights_row in document_weights[: min(3, len(document_weights))]:
            reference = float(model.compute_lexical_matching_score(query_weights_row, document_weights_row))
            if not math.isclose(reference, sparse_score(query_weights_row, document_weights_row), rel_tol=1e-6, abs_tol=1e-7):
                return status_receipt("INSUFFICIENT_EVIDENCE", "posting scorer disagrees with BGE-M3 reference lexical matching", inventory, args.output)
    receipt = {
        "status": "PILOT_COMPLETE",
        "decision": "NO_GO",
        "model": {"repository": MODEL_REPOSITORY, "revision": MODEL_REVISION},
        "artifacts": inventory,
        "corpus_sha256": sha256_json(documents),
        "queries_sha256": sha256_json(query_rows),
        "qrels_sha256": sha256_json(qrels),
        "query_count": len(rows),
        "quality": {"bge_m3_sparse": metrics(rows, qrels, "sparse", args.limit), "bm25": metrics(rows, qrels, "bm25", args.limit)},
        "timing": {
            "document_encoding_ms": document_encoding_ms,
            "query_encoding_ms": query_encoding_ms,
            "sparse_index_build_ms": build_ms,
            "traversal_p50_ms": statistics.median(traversal_samples) if traversal_samples else None,
            "ranking_p50_ms": statistics.median(ranking_samples) if ranking_samples else None,
            "query_total_p50_ms": statistics.median(total_samples) if total_samples else None,
            "sample_count": len(total_samples),
            "sample_count_semantics": "one_query_observation_per_frozen_query_not_independent_repeats",
            "measured_status": "directional",
        },
        "queries": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--queries", type=Path)
    parser.add_argument("--qrels", type=Path)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--output", type=Path, default=Path("build/bge-m3-pilot-receipt.json"))
    args = parser.parse_args()
    if args.limit <= 0 or args.batch_size <= 0:
        parser.error("limit and batch-size must be positive")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
