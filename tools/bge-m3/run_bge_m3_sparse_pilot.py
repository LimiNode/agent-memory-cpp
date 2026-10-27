#!/usr/bin/env python3
"""Run a fail-closed, research-only BGE-M3 sparse retrieval pilot."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import statistics
import subprocess
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
MODEL_REPOSITORY = "BAAI/bge-m3"
MODEL_BASE_URL = f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}"
LANGUAGES = {"en", "ru", "code"}
DECISIONS = {"GO_TO_LARGER_EVALUATION", "NO_GO", "INSUFFICIENT_EVIDENCE"}
COMPLETED = "EVALUATION_COMPLETED"
TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
TOKEN_ID_RE = re.compile(r"0|[1-9][0-9]*")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

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


class EvidenceError(ValueError):
    """A fail-closed artifact, inference, policy, or evaluation violation."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest().upper()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"{path} must contain a JSON object")
    return value


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
        row["size"] = path.stat().st_size if path.is_file() else None
        row["sha256"] = sha256_file(path) if path.is_file() else None
        row["verified"] = row["size"] == expected_size and row["sha256"] == expected_sha256
        rows.append(row)
    return rows


def require_verified_artifacts(rows: list[dict[str, Any]]) -> None:
    failures = [row["path"] for row in rows if not row.get("verified")]
    if failures:
        raise EvidenceError("artifact inventory is incomplete or hash-mismatched: " + ", ".join(failures))


def require_unique_ids(rows: list[dict[str, Any]], label: str) -> list[str]:
    ids = [row.get("id") for row in rows]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise EvidenceError(f"{label} IDs must be unique non-empty strings")
    return ids


def validate_data(
    corpus: dict[str, Any], queries: dict[str, Any], qrels: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, int]], dict[str, Any]]:
    documents, query_rows, qrel_rows = corpus.get("documents"), queries.get("queries"), qrels.get("qrels")
    if not all(isinstance(value, list) and value for value in (documents, query_rows, qrel_rows)):
        raise EvidenceError("corpus, queries and qrels must expose non-empty lists")
    if any(not isinstance(row, dict) for row in documents + query_rows + qrel_rows):
        raise EvidenceError("corpus, queries and qrels rows must be objects")
    for label, value in (("corpus", corpus), ("query", queries), ("qrels", qrels)):
        if not isinstance(value.get("revision"), str) or not value["revision"]:
            raise EvidenceError(f"{label} revision is required")
    provenance = qrels.get("judgment_provenance")
    if not isinstance(provenance, dict) or provenance.get("kind") != "independent_human_judgment" \
            or not isinstance(provenance.get("revision"), str) or not provenance["revision"]:
        raise EvidenceError("independent qrels judgment provenance is required")
    document_ids = require_unique_ids(documents, "document")
    query_ids = require_unique_ids(query_rows, "query")
    for row in documents:
        if not isinstance(row.get("text"), str) or not row["text"] or row.get("language") not in LANGUAGES:
            raise EvidenceError("documents require text and en/ru/code language")
    for row in query_rows:
        if not isinstance(row.get("text"), str) or not row["text"] or row.get("language") not in LANGUAGES:
            raise EvidenceError("queries require text and en/ru/code language")
        if row.get("answer_mode") not in {"judged", "no_answer"}:
            raise EvidenceError("query answer_mode must be judged or no_answer")
    if {row["language"] for row in documents} != LANGUAGES or {row["language"] for row in query_rows} != LANGUAGES:
        raise EvidenceError("evaluation requires complete en/ru/code document and query slices")
    if not any(row["answer_mode"] == "no_answer" for row in query_rows):
        raise EvidenceError("evaluation requires at least one no-answer query")
    document_set, query_set = set(document_ids), set(query_ids)
    mapped: dict[str, dict[str, int]] = {query_id: {} for query_id in query_ids}
    for row in qrel_rows:
        query_id, doc_id, grade = row.get("query_id"), row.get("doc_id"), row.get("grade")
        if not isinstance(query_id, str) or not isinstance(doc_id, str) \
                or query_id not in query_set or doc_id not in document_set or isinstance(grade, bool) \
                or not isinstance(grade, int) or grade < 0:
            raise EvidenceError("qrel identity or grade is invalid")
        if doc_id in mapped[query_id]:
            raise EvidenceError("duplicate qrel judgment")
        mapped[query_id][doc_id] = grade
    positive_languages: set[str] = set()
    for row in query_rows:
        judgments = mapped[row["id"]]
        if row["answer_mode"] == "no_answer" and judgments:
            raise EvidenceError("no-answer query must not have qrels")
        if row["answer_mode"] == "judged":
            if not judgments or not any(grade > 0 for grade in judgments.values()):
                raise EvidenceError("judged query must have at least one positive qrel")
            positive_languages.add(row["language"])
    if positive_languages != LANGUAGES:
        raise EvidenceError("each en/ru/code slice requires a positive judged query")
    return documents, query_rows, mapped, provenance


def normalize_weights(value: Any, label: str, vocabulary_size: int) -> dict[int, float]:
    if isinstance(value, dict):
        items: Iterable[tuple[Any, Any]] = value.items()
    elif isinstance(value, list):
        items = value
    else:
        raise EvidenceError(f"{label} sparse weights must be a mapping or token/weight pairs")
    result: dict[int, float] = {}
    for item in items:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise EvidenceError(f"{label} sparse entry must be a token/weight pair")
        raw_token, raw_weight = item
        if isinstance(raw_token, bool) or not (
            isinstance(raw_token, int) or isinstance(raw_token, str) and TOKEN_ID_RE.fullmatch(raw_token)
        ):
            raise EvidenceError(f"{label} token ID is not a canonical integer")
        token = int(raw_token)
        if token < 0 or token >= vocabulary_size or token in result:
            raise EvidenceError(f"{label} token ID is duplicate or outside tokenizer vocabulary")
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
            raise EvidenceError(f"{label} sparse weight is not numeric")
        weight = float(raw_weight)
        if not math.isfinite(weight) or weight < 0.0:
            raise EvidenceError(f"{label} sparse weight is invalid")
        result[token] = weight
    if not result:
        raise EvidenceError(f"{label} sparse weights are empty")
    return result


def normalize_inference_output(output: Any, expected_count: int, label: str, vocabulary_size: int) -> list[dict[int, float]]:
    if not isinstance(output, dict) or not isinstance(output.get("lexical_weights"), (list, tuple)):
        raise EvidenceError(f"{label} inference output misses lexical_weights")
    values = output["lexical_weights"]
    if len(values) != expected_count:
        raise EvidenceError(f"{label} inference cardinality mismatch: {len(values)} != {expected_count}")
    return [normalize_weights(value, f"{label}[{index}]", vocabulary_size) for index, value in enumerate(values)]


def sparse_score(query_weights: dict[int, float], document_weights: dict[int, float]) -> float:
    total = 0.0
    for token, weight in query_weights.items():
        product = weight * document_weights.get(token, 0.0)
        if not math.isfinite(product):
            raise EvidenceError("sparse score product is not finite")
        total += product
        if not math.isfinite(total):
            raise EvidenceError("sparse score is not finite")
    return total


def build_postings(documents: list[dict[str, Any]], weights: list[dict[int, float]]) -> dict[int, list[tuple[str, float]]]:
    if len(documents) != len(weights):
        raise EvidenceError("document inference cardinality mismatch before indexing")
    postings: dict[int, list[tuple[str, float]]] = defaultdict(list)
    for index in range(len(documents)):
        for token, weight in weights[index].items():
            postings[token].append((documents[index]["id"], weight))
    for values in postings.values():
        values.sort(key=lambda item: item[0])
    return postings


def score_candidates(postings: dict[int, list[tuple[str, float]]], query_weights: dict[int, float]) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for token, query_weight in query_weights.items():
        for document_id, document_weight in postings.get(token, []):
            product = query_weight * document_weight
            if not math.isfinite(product):
                raise EvidenceError("sparse score product is not finite")
            scores[document_id] += product
            if not math.isfinite(scores[document_id]):
                raise EvidenceError("sparse score is not finite")
    return dict(scores)


def rank_scores(scores: dict[str, float], limit: int) -> list[dict[str, Any]]:
    if any(not isinstance(score, (int, float)) or not math.isfinite(float(score)) for score in scores.values()):
        raise EvidenceError("ranking received a non-finite sparse score")
    return [
        {"doc_id": document_id, "score": score}
        for document_id, score in sorted(
            ((document_id, score) for document_id, score in scores.items() if score > 0.0),
            key=lambda item: (-item[1], item[0]),
        )[:limit]
    ]


def rank_sparse(postings: dict[int, list[tuple[str, float]]], query_weights: dict[int, float], limit: int) -> list[dict[str, Any]]:
    return rank_scores(score_candidates(postings, query_weights), limit)


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.casefold())


def build_bm25(documents: list[dict[str, Any]]) -> dict[str, Any]:
    document_terms = [tokens(row["text"]) for row in documents]
    counts: list[dict[str, int]] = []
    df: dict[str, int] = defaultdict(int)
    for terms in document_terms:
        term_counts: dict[str, int] = defaultdict(int)
        for term in terms:
            term_counts[term] += 1
        counts.append(dict(term_counts))
        for term in term_counts:
            df[term] += 1
    lengths = [len(terms) for terms in document_terms]
    return {
        "document_ids": [row["id"] for row in documents],
        "counts": counts,
        "df": dict(df),
        "lengths": lengths,
        "average_length": statistics.mean(lengths) if lengths else 1.0,
    }


def bm25_rank(index: dict[str, Any], query: str, limit: int) -> list[dict[str, Any]]:
    scores: list[dict[str, Any]] = []
    document_count = len(index["document_ids"])
    for position, document_id in enumerate(index["document_ids"]):
        counts, length = index["counts"][position], index["lengths"][position]
        score = 0.0
        for term in set(tokens(query)):
            if term not in counts:
                continue
            df = index["df"][term]
            inverse = math.log(1.0 + (document_count - df + 0.5) / (df + 0.5))
            norm = 1.2 * (0.25 + 0.75 * length / max(index["average_length"], 1.0))
            score += inverse * counts[term] * 2.2 / (counts[term] + norm)
        if score > 0.0:
            scores.append({"doc_id": document_id, "score": score})
    return sorted(scores, key=lambda item: (-item["score"], item["doc_id"]))[:limit]


def recall_at_k(hits: list[dict[str, Any]], relevant: dict[str, int], k: int) -> float:
    positives = {doc_id for doc_id, grade in relevant.items() if grade > 0}
    return len({hit["doc_id"] for hit in hits[:k]} & positives) / len(positives)


def ndcg_at_k(hits: list[dict[str, Any]], relevant: dict[str, int], k: int) -> float:
    gain = lambda grade: float((2 ** grade) - 1)
    actual = sum(gain(relevant.get(hit["doc_id"], 0)) / math.log2(index + 2) for index, hit in enumerate(hits[:k]))
    ideal_grades = sorted((grade for grade in relevant.values() if grade > 0), reverse=True)
    ideal = sum(gain(grade) / math.log2(index + 2) for index, grade in enumerate(ideal_grades[:k]))
    return actual / ideal if ideal else 0.0


def metrics(rows: list[dict[str, Any]], qrels: dict[str, dict[str, int]], arm: str, limit: int) -> dict[str, Any]:
    judged = [row for row in rows if row["answer_mode"] == "judged"]
    no_answer = [row for row in rows if row["answer_mode"] == "no_answer"]
    recall = {
        str(k): sum(recall_at_k(row[f"{arm}_hits"], qrels[row["query_id"]], k) for row in judged) / len(judged)
        if k <= limit else None for k in (1, 5, 10, 50)
    }
    by_language = {
        language: {
            "positive_query_count": sum(row["language"] == language for row in judged),
            "positive_relevant_document_count": sum(
                sum(grade > 0 for grade in qrels[row["query_id"]].values())
                for row in judged if row["language"] == language
            ),
        } for language in sorted(LANGUAGES)
    }
    return {
        "query_count": len(rows),
        "judged_query_count": len(judged),
        "positive_relevant_document_count": sum(sum(grade > 0 for grade in qrels[row["query_id"]].values()) for row in judged),
        "slice_counts": by_language,
        "recall_at": recall,
        "ndcg_at_10": sum(ndcg_at_k(row[f"{arm}_hits"], qrels[row["query_id"]], 10) for row in judged) / len(judged) if limit >= 10 else None,
        "mrr_at_limit": sum(next((1.0 / (index + 1) for index, hit in enumerate(row[f"{arm}_hits"][:limit]) if qrels[row["query_id"]].get(hit["doc_id"], 0) > 0), 0.0) for row in judged) / len(judged),
        "no_answer_nonempty_hit_rate_diagnostic": sum(bool(row[f"{arm}_hits"]) for row in no_answer) / len(no_answer),
        "abstention_accuracy": None,
        "abstention_note": "UNAVAILABLE: no calibrated score threshold is registered",
    }


def validate_policy(policy: dict[str, Any], repository_root: Path | None = None) -> None:
    required = {
        "schema_version", "revision", "registered_before_evaluation",
        "minimum_judged_queries_per_language", "minimum_recall_at_10_delta",
        "minimum_ndcg_at_10_delta", "no_go_recall_at_10_delta",
        "no_go_ndcg_at_10_delta", "registration_manifest",
    }
    if set(policy) != required or policy["schema_version"] != 1 \
            or policy["registered_before_evaluation"] is not True \
            or not isinstance(policy["revision"], str) or not policy["revision"]:
        raise EvidenceError("decision policy schema or preregistration is invalid")
    if isinstance(policy["minimum_judged_queries_per_language"], bool) \
            or not isinstance(policy["minimum_judged_queries_per_language"], int) \
            or policy["minimum_judged_queries_per_language"] <= 0:
        raise EvidenceError("decision policy cohort threshold is invalid")
    for field in required - {"schema_version", "revision", "registered_before_evaluation", "minimum_judged_queries_per_language", "registration_manifest"}:
        if isinstance(policy[field], bool) or not isinstance(policy[field], (int, float)) or not math.isfinite(float(policy[field])):
            raise EvidenceError(f"decision policy {field} is invalid")
    registration = policy["registration_manifest"]
    if not isinstance(registration, dict) or set(registration) != {"repository", "commit", "path", "policy_sha256"} \
            or not all(isinstance(registration.get(field), str) and registration[field] for field in registration):
        raise EvidenceError("decision policy registration manifest is invalid")
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", registration["commit"]) \
            or not re.fullmatch(r"[0-9A-Fa-f]{64}", registration["policy_sha256"]):
        raise EvidenceError("decision policy registration identity is invalid")
    unsigned = {key: value for key, value in policy.items() if key != "registration_manifest"}
    if sha256_json(unsigned) != registration["policy_sha256"].upper():
        raise EvidenceError("decision policy registration digest does not match policy contents")
    if repository_root is None:
        return
    registered_path = registration["path"]
    if Path(registered_path).is_absolute() or ".." in Path(registered_path).parts \
            or Path(registered_path).as_posix() != registered_path:
        raise EvidenceError("decision policy registration path is not repository-relative")
    object_name = f"{registration['commit']}:{registered_path}"
    try:
        subprocess.run(
            ["git", "-C", str(repository_root), "cat-file", "-e", f"{registration['commit']}^{{commit}}"],
            check=True, capture_output=True, text=True,
        )
        registered_file = subprocess.run(
            ["git", "-C", str(repository_root), "show", object_name],
            check=True, capture_output=True, text=True,
        ).stdout
        registered_value = json.loads(registered_file)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise EvidenceError("decision policy registration Git object/path cannot be verified") from exc
    if not isinstance(registered_value, dict):
        raise EvidenceError("registered decision policy is not a JSON object")
    registered_unsigned = {key: value for key, value in registered_value.items() if key != "registration_manifest"}
    if sha256_json(registered_unsigned) != sha256_json(unsigned):
        raise EvidenceError("decision policy differs from the registered Git policy body")


def decide(quality: dict[str, Any], policy: dict[str, Any], limit: int) -> str:
    validate_policy(policy)
    if limit < 10:
        return "INSUFFICIENT_EVIDENCE"
    sparse, bm25 = quality["bge_m3_sparse"], quality["bm25"]
    minimum = policy["minimum_judged_queries_per_language"]
    if any(sparse["slice_counts"][language]["positive_query_count"] < minimum for language in LANGUAGES):
        return "INSUFFICIENT_EVIDENCE"
    recall_delta = sparse["recall_at"]["10"] - bm25["recall_at"]["10"]
    ndcg_delta = sparse["ndcg_at_10"] - bm25["ndcg_at_10"]
    if recall_delta >= policy["minimum_recall_at_10_delta"] and ndcg_delta >= policy["minimum_ndcg_at_10_delta"]:
        return "GO_TO_LARGER_EVALUATION"
    if recall_delta <= policy["no_go_recall_at_10_delta"] or ndcg_delta <= policy["no_go_ndcg_at_10_delta"]:
        return "NO_GO"
    return "INSUFFICIENT_EVIDENCE"


def validate_quality(quality: Any, limit: int) -> None:
    if not isinstance(quality, dict) or set(quality) != {"bge_m3_sparse", "bm25"}:
        raise EvidenceError("completed evaluation receipt quality schema is incomplete")
    for arm_name, arm in quality.items():
        if not isinstance(arm, dict) or not isinstance(arm.get("recall_at"), dict) \
                or not isinstance(arm.get("slice_counts"), dict):
            raise EvidenceError(f"{arm_name} quality schema is incomplete")
        required_fields = {
            "query_count", "judged_query_count", "positive_relevant_document_count",
            "mrr_at_limit", "no_answer_nonempty_hit_rate_diagnostic", "abstention_accuracy",
            "abstention_note", "recall_at", "ndcg_at_10", "slice_counts",
        }
        if not required_fields.issubset(arm):
            raise EvidenceError(f"{arm_name} quality schema is incomplete")
        recall = arm["recall_at"]
        if set(recall) != {"1", "5", "10", "50"}:
            raise EvidenceError(f"{arm_name} recall schema is incomplete")
        for value in recall.values():
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0):
                raise EvidenceError(f"{arm_name} recall value is invalid")
        if limit >= 10 and (recall["10"] is None or arm.get("ndcg_at_10") is None):
            raise EvidenceError(f"{arm_name} lacks required top-10 quality evidence")
        for field in ("ndcg_at_10", "mrr_at_limit"):
            value = arm.get(field)
            if value is None:
                if field == "mrr_at_limit":
                    raise EvidenceError(f"{arm_name} quality is missing {field}")
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
                raise EvidenceError(f"{arm_name} {field} is invalid")
        for field in ("query_count", "judged_query_count", "positive_relevant_document_count"):
            value = arm[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise EvidenceError(f"{arm_name} {field} is invalid")
        diagnostic = arm["no_answer_nonempty_hit_rate_diagnostic"]
        if isinstance(diagnostic, bool) or not isinstance(diagnostic, (int, float)) \
                or not math.isfinite(float(diagnostic)) or not 0.0 <= float(diagnostic) <= 1.0:
            raise EvidenceError(f"{arm_name} no-answer diagnostic is invalid")
        if arm["abstention_accuracy"] is not None:
            abstention = arm["abstention_accuracy"]
            if isinstance(abstention, bool) or not isinstance(abstention, (int, float)) \
                    or not math.isfinite(float(abstention)) or not 0.0 <= float(abstention) <= 1.0:
                raise EvidenceError(f"{arm_name} abstention accuracy is invalid")
        if not isinstance(arm["abstention_note"], str) or not arm["abstention_note"]:
            raise EvidenceError(f"{arm_name} abstention note is invalid")
        if set(arm["slice_counts"]) != LANGUAGES:
            raise EvidenceError(f"{arm_name} language slice evidence is incomplete")
        for language, counts in arm["slice_counts"].items():
            if not isinstance(counts, dict) or any(
                isinstance(counts.get(field), bool) or not isinstance(counts.get(field), int) or counts[field] < 0
                for field in ("positive_query_count", "positive_relevant_document_count")
            ):
                raise EvidenceError(f"{arm_name} {language} slice counts are invalid")


def _quality_matches(actual: Any, expected: Any) -> bool:
    if isinstance(actual, dict) and isinstance(expected, dict):
        return set(actual) == set(expected) and all(_quality_matches(actual[key], expected[key]) for key in actual)
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(_quality_matches(left, right) for left, right in zip(actual, expected))
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)) \
            and not isinstance(actual, bool) and not isinstance(expected, bool):
        return math.isclose(float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-12)
    return actual == expected


def validate_replay_evidence(receipt: dict[str, Any], quality: dict[str, Any], limit: int) -> None:
    inputs = receipt["inputs"]
    replay = receipt.get("evidence_replay")
    required = {"corpus_ids", "queries", "qrels", "rows"}
    if not isinstance(replay, dict) or set(replay) != required:
        raise EvidenceError("completed evaluation receipt lacks replayable evidence bundle")
    paths = {field: Path(inputs[f"{field}_path"]) for field in ("corpus", "queries", "qrels")}
    try:
        source_values = {field: load_json(path) for field, path in paths.items()}
        source_hashes = {field: sha256_file(path) for field, path in paths.items()}
    except (EvidenceError, OSError) as exc:
        raise EvidenceError("replay inputs are unavailable") from exc
    for field in ("corpus", "queries", "qrels"):
        if source_hashes[field] != inputs[f"{field}_sha256"]:
            raise EvidenceError(f"{field} replay input hash does not match receipt")
    if replay["queries"] != source_values["queries"].get("queries") \
            or replay["qrels"] != source_values["qrels"].get("qrels"):
        raise EvidenceError("replay bundle does not match frozen query/qrel inputs")
    documents, query_rows, qrels, _ = validate_data(
        source_values["corpus"], source_values["queries"], source_values["qrels"]
    )
    document_ids = [row["id"] for row in documents]
    if replay["corpus_ids"] != document_ids:
        raise EvidenceError("replay bundle does not match frozen corpus identities")
    rows = replay["rows"]
    if not isinstance(rows, list) or len(rows) != len(query_rows):
        raise EvidenceError("replay row cardinality does not match frozen queries")
    query_ids = [row["id"] for row in query_rows]
    if [row.get("query_id") for row in rows if isinstance(row, dict)] != query_ids:
        raise EvidenceError("replay row identities do not match frozen queries")
    document_set = set(document_ids)
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"query_id", "language", "answer_mode", "sparse_hits", "bm25_hits"}:
            raise EvidenceError("replay row schema is invalid")
        if row["language"] not in LANGUAGES or row["answer_mode"] not in {"judged", "no_answer"}:
            raise EvidenceError("replay row query metadata is invalid")
        if not isinstance(row["sparse_hits"], list) or not isinstance(row["bm25_hits"], list):
            raise EvidenceError("replay ranked hits are invalid")
        for hits in (row["sparse_hits"], row["bm25_hits"]):
            seen: set[str] = set()
            for hit in hits:
                if not isinstance(hit, dict) or set(hit) != {"doc_id", "score"} \
                        or not isinstance(hit["doc_id"], str) or hit["doc_id"] not in document_set \
                        or hit["doc_id"] in seen or isinstance(hit["score"], bool) \
                        or not isinstance(hit["score"], (int, float)) or not math.isfinite(float(hit["score"])) \
                        or hit["score"] < 0.0:
                    raise EvidenceError("replay ranked hit is invalid")
                seen.add(hit["doc_id"])
    expected = {
        "bge_m3_sparse": metrics(rows, qrels, "sparse", limit),
        "bm25": metrics(rows, qrels, "bm25", limit),
    }
    if not _quality_matches(quality, expected):
        raise EvidenceError("receipt quality does not match replayed ranked hits and qrels")


def validate_receipt(receipt: dict[str, Any]) -> None:
    if receipt.get("decision") not in DECISIONS:
        raise EvidenceError("receipt decision is invalid")
    status = receipt.get("execution_status")
    if not isinstance(status, str):
        raise EvidenceError("receipt execution status is missing")
    if status != COMPLETED:
        if receipt["decision"] != "INSUFFICIENT_EVIDENCE":
            raise EvidenceError("incomplete execution cannot publish GO or NO_GO")
        return
    model = receipt.get("model")
    if not isinstance(model, dict) or model.get("loaded") is not True:
        raise EvidenceError("completed evaluation receipt lacks loaded-model evidence")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts or any(
        not isinstance(row, dict) or row.get("verified") is not True or row.get("loaded") is not True
        for row in artifacts
    ):
        raise EvidenceError("completed evaluation receipt lacks verified artifact evidence")
    policy_record = receipt.get("decision_policy")
    if not isinstance(policy_record, dict) or not isinstance(policy_record.get("policy"), dict) \
            or not isinstance(policy_record.get("sha256"), str) \
            or policy_record["sha256"] != sha256_json(policy_record["policy"]):
        raise EvidenceError("completed evaluation receipt lacks bound decision policy evidence")
    policy = policy_record["policy"]
    validate_policy(policy, REPOSITORY_ROOT)
    if policy_record.get("revision") != policy.get("revision"):
        raise EvidenceError("receipt decision policy revision mismatch")
    inputs = receipt.get("inputs")
    if not isinstance(inputs, dict) or not all(isinstance(inputs.get(field), str) and inputs[field] for field in (
        "corpus_revision", "queries_revision", "qrels_revision", "corpus_sha256", "queries_sha256", "qrels_sha256",
        "corpus_path", "queries_path", "qrels_path",
    )) or not isinstance(inputs.get("judgment_provenance"), dict):
        raise EvidenceError("completed evaluation receipt lacks frozen input evidence")
    limit = receipt.get("retrieval_limit")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise EvidenceError("completed evaluation receipt retrieval limit is invalid")
    quality = receipt.get("quality")
    validate_quality(quality, limit)
    validate_replay_evidence(receipt, quality, limit)
    if receipt["decision"] != decide(quality, policy, limit):
        raise EvidenceError("receipt decision does not match validated quality and policy")


def publish_receipt(receipt: dict[str, Any], output: Path) -> None:
    validate_receipt(receipt)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=output.name + ".", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
        os.replace(temporary, output)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def failure_receipt(
    status: str,
    reason: str,
    inventory: list[dict[str, Any]],
    output: Path,
    exit_code: int,
    model_loaded: bool = False,
) -> int:
    receipt = {
        "execution_status": status,
        "decision": "INSUFFICIENT_EVIDENCE",
        "reason": reason,
        "model": {"repository": MODEL_REPOSITORY, "revision": MODEL_REVISION, "loaded": model_loaded},
        "artifacts": inventory,
    }
    publish_receipt(receipt, output)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _run(args: argparse.Namespace) -> int:
    args._model_loaded = False
    verification_started = time.perf_counter_ns()
    inventory = artifact_inventory(args.artifact_dir)
    artifact_verification_ms = (time.perf_counter_ns() - verification_started) / 1_000_000.0
    try:
        require_verified_artifacts(inventory)
    except EvidenceError as exc:
        return failure_receipt("ARTIFACT_VERIFICATION_FAILED", str(exc), inventory, args.output, 2)
    if not args.corpus or not args.queries or not args.qrels or not args.decision_policy:
        return failure_receipt("MISSING_EVALUATION_EVIDENCE", "frozen corpus, queries, independent qrels and preregistered decision policy are required", inventory, args.output, 0)
    try:
        corpus_value, queries_value, qrels_value = load_json(args.corpus), load_json(args.queries), load_json(args.qrels)
        documents, query_rows, qrels, judgment_provenance = validate_data(corpus_value, queries_value, qrels_value)
        policy = load_json(args.decision_policy)
        validate_policy(policy, REPOSITORY_ROOT)
        total_text_bytes = sum(len(row["text"].encode("utf-8")) for row in documents + query_rows)
        if len(documents) > args.max_documents or len(query_rows) > args.max_queries \
                or total_text_bytes > args.max_total_text_bytes:
            raise EvidenceError("evaluation exceeds registered document/query/text resource budget")
    except EvidenceError as exc:
        return failure_receipt("MISSING_EVALUATION_EVIDENCE", str(exc), inventory, args.output, 2)
    try:
        from FlagEmbedding import BGEM3FlagModel
    except ImportError as exc:
        return failure_receipt("MODEL_DEPENDENCY_UNAVAILABLE", f"FlagEmbedding is unavailable: {exc}", inventory, args.output, 2)
    try:
        model_load_started = time.perf_counter_ns()
        model = BGEM3FlagModel(str(args.artifact_dir), use_fp16=False, devices=args.device)
        args._model_loaded = True
        model_load_ms = (time.perf_counter_ns() - model_load_started) / 1_000_000.0
        tokenizer = getattr(model, "tokenizer", None)
        vocabulary_size = len(tokenizer) if tokenizer is not None else 0
        if vocabulary_size <= 0:
            raise EvidenceError("loaded model does not expose a non-empty tokenizer vocabulary")
        document_encoding_started = time.perf_counter_ns()
        document_output = model.encode(
            [row["text"] for row in documents], return_dense=False, return_sparse=True,
            return_colbert_vecs=False, batch_size=args.batch_size, max_length=args.max_length)
        document_encoding_ms = (time.perf_counter_ns() - document_encoding_started) / 1_000_000.0
        query_encoding_started = time.perf_counter_ns()
        query_output = model.encode(
            [row["text"] for row in query_rows], return_dense=False, return_sparse=True,
            return_colbert_vecs=False, batch_size=args.batch_size, max_length=args.max_length)
        query_encoding_ms = (time.perf_counter_ns() - query_encoding_started) / 1_000_000.0
        document_weights = normalize_inference_output(document_output, len(documents), "document", vocabulary_size)
        query_weights = normalize_inference_output(query_output, len(query_rows), "query", vocabulary_size)
    except EvidenceError as exc:
        return failure_receipt("MODEL_INFERENCE_FAILED", str(exc), inventory, args.output, 2)
    except Exception as exc:  # third-party backends expose heterogeneous exception types
        return failure_receipt("MODEL_INFERENCE_FAILED", f"BGE-M3 sparse inference failed closed: {exc}", inventory, args.output, 2)
    for row in inventory:
        row["loaded"] = True
    parity_count = 0
    try:
        for query_index, query_weight in enumerate(query_weights):
            for document_index, document_weight in enumerate(document_weights):
                if parity_count >= args.parity_pairs:
                    break
                reference = float(model.compute_lexical_matching_score(query_weight, document_weight))
                local = sparse_score(query_weight, document_weight)
                if not math.isfinite(reference) or not math.isclose(reference, local, rel_tol=1e-6, abs_tol=1e-7):
                    raise EvidenceError(f"scorer parity mismatch at query {query_index}, document {document_index}")
                parity_count += 1
            if parity_count >= args.parity_pairs:
                break
        if parity_count == 0:
            raise EvidenceError("no inference scorer parity pair was checked")
    except (EvidenceError, TypeError, ValueError) as exc:
        return failure_receipt("SCORER_PARITY_FAILED", str(exc), inventory, args.output, 2, model_loaded=True)
    sparse_build_started = time.perf_counter_ns()
    postings = build_postings(documents, document_weights)
    sparse_build_ms = (time.perf_counter_ns() - sparse_build_started) / 1_000_000.0
    bm25_build_started = time.perf_counter_ns()
    bm25_index = build_bm25(documents)
    bm25_build_ms = (time.perf_counter_ns() - bm25_build_started) / 1_000_000.0
    rows: list[dict[str, Any]] = []
    traversal_samples, ranking_samples, retrieval_samples, bm25_samples = [], [], [], []
    for index in range(len(query_rows)):
        query, weights = query_rows[index], query_weights[index]
        traversal_started = time.perf_counter_ns()
        scores = score_candidates(postings, weights)
        traversal_ms = (time.perf_counter_ns() - traversal_started) / 1_000_000.0
        ranking_started = time.perf_counter_ns()
        sparse_hits = rank_scores(scores, args.limit)
        ranking_ms = (time.perf_counter_ns() - ranking_started) / 1_000_000.0
        bm25_started = time.perf_counter_ns()
        bm25_hits = bm25_rank(bm25_index, query["text"], args.limit)
        bm25_ms = (time.perf_counter_ns() - bm25_started) / 1_000_000.0
        traversal_samples.append(traversal_ms)
        ranking_samples.append(ranking_ms)
        retrieval_samples.append(traversal_ms + ranking_ms)
        bm25_samples.append(bm25_ms)
        rows.append({
            "query_id": query["id"], "language": query["language"],
            "answer_mode": query["answer_mode"], "sparse_hits": sparse_hits, "bm25_hits": bm25_hits,
        })
    quality = {"bge_m3_sparse": metrics(rows, qrels, "sparse", args.limit), "bm25": metrics(rows, qrels, "bm25", args.limit)}
    decision = decide(quality, policy, args.limit)
    receipt = {
        "execution_status": COMPLETED,
        "decision": decision,
        "decision_policy": {"revision": policy["revision"], "sha256": sha256_json(policy), "policy": policy},
        "model": {
            "repository": MODEL_REPOSITORY, "revision": MODEL_REVISION, "loaded": True,
            "tokenizer_vocabulary_size": vocabulary_size, "device": args.device,
            "use_fp16": False, "max_length": args.max_length, "batch_size": args.batch_size,
        },
        "dependencies": {
            "python": platform.python_version(), "FlagEmbedding": package_version("FlagEmbedding"),
            "torch": package_version("torch"), "transformers": package_version("transformers"),
        },
        "resource_budgets": {
            "max_documents": args.max_documents, "max_queries": args.max_queries,
            "max_total_text_bytes": args.max_total_text_bytes,
        },
        "artifacts": inventory,
        "inputs": {
            "corpus_revision": corpus_value["revision"], "corpus_sha256": sha256_file(args.corpus),
            "queries_revision": queries_value["revision"], "queries_sha256": sha256_file(args.queries),
            "qrels_revision": qrels_value["revision"], "qrels_sha256": sha256_file(args.qrels),
            "corpus_path": str(args.corpus.resolve()), "queries_path": str(args.queries.resolve()),
            "qrels_path": str(args.qrels.resolve()),
            "judgment_provenance": judgment_provenance,
        },
        "scorer_parity_pairs": parity_count,
        "quality": quality,
        "retrieval_limit": args.limit,
        "evidence_replay": {
            "corpus_ids": [row["id"] for row in documents],
            "queries": queries_value["queries"], "qrels": qrels_value["qrels"], "rows": rows,
        },
        "timing": {
            "artifact_verification_ms": artifact_verification_ms, "model_loading_ms": model_load_ms,
            "document_encoding_ms": document_encoding_ms, "query_batch_encoding_ms": query_encoding_ms,
            "sparse_index_build_ms": sparse_build_ms, "bm25_index_build_ms": bm25_build_ms,
            "posting_traversal_p50_ms": statistics.median(traversal_samples),
            "ranking_materialization_p50_ms": statistics.median(ranking_samples),
            "post_encoding_retrieval_p50_ms": statistics.median(retrieval_samples),
            "bm25_query_p50_ms": statistics.median(bm25_samples),
            "end_to_end_query_p50_ms": None,
            "end_to_end_query_note": "UNAVAILABLE: query encoding was measured as one batch, not per query",
            "sample_count": len(rows),
            "sample_count_semantics": "one_observation_per_frozen_query_not_independent_repeats",
            "measured_status": "directional",
        },
        "queries": rows,
    }
    publish_receipt(receipt, args.output)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def run(args: argparse.Namespace) -> int:
    try:
        return _run(args)
    except (EvidenceError, TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
        inventory = artifact_inventory(args.artifact_dir)
        return failure_receipt(
            "EVALUATION_FAILED", str(exc), inventory, args.output, 2,
            model_loaded=bool(getattr(args, "_model_loaded", False)),
        )


def self_test() -> int:
    failures = 0

    class FakeEncoder:
        def encode(self, texts: list[str]) -> dict[str, list[dict[str, float]]]:
            table = {
                "doc-a": {"1": 4.0, "2": 2.0},
                "doc-b": {"1": 1.0},
                "query": {"1": 2.0, "2": 3.0},
                "english": {"3": 1.0},
                "code": {"5": 1.0},
                "none": {"7": 1.0},
            }
            return {"lexical_weights": [table.get(text, {"6": 1.0}) for text in texts]}

        @staticmethod
        def compute_lexical_matching_score(left: dict[int, float], right: dict[int, float]) -> float:
            return sum(left.get(token, 0.0) * right.get(token, 0.0) for token in set(left) | set(right))

    def reject(callable_value: Any, label: str) -> None:
        nonlocal failures
        try:
            callable_value()
        except EvidenceError:
            failures += 1
            return
        raise SystemExit(f"self-test accepted {label}")

    encoder = FakeEncoder()
    encoded_documents = normalize_inference_output(encoder.encode(["doc-a", "doc-b"]), 2, "fake-document", 8)
    encoded_queries = normalize_inference_output(encoder.encode(["query"]), 1, "fake-query", 8)
    if not math.isclose(
        sparse_score(encoded_queries[0], encoded_documents[0]),
        encoder.compute_lexical_matching_score(encoded_queries[0], encoded_documents[0]),
    ):
        raise SystemExit("self-test scorer parity differs from independent fake encoder")
    query = normalize_weights([[1, 2.0], [2, 3.0]], "query", 8)
    documents = [{"id": "b"}, {"id": "a"}, {"id": "z"}]
    weights = [normalize_weights({"1": 4.0}, "d0", 8), normalize_weights({"2": 2.0}, "d1", 8), normalize_weights({"7": 1.0}, "d2", 8)]
    postings = build_postings(documents, weights)
    hits = rank_sparse(postings, query, 2)
    if hits != [{"doc_id": "b", "score": 8.0}, {"doc_id": "a", "score": 6.0}]:
        raise SystemExit("self-test sparse multi-token/top-k result differs")
    if sparse_score({1: 2.0, 2: 3.0}, {1: 4.0, 2: 2.0}) != 14.0 or rank_sparse(postings, {6: 1.0}, 10):
        raise SystemExit("self-test arithmetic oracle or zero-overlap result differs")
    tied = rank_sparse({1: [("b", 1.0), ("a", 1.0)]}, {1: 1.0}, 2)
    if [row["doc_id"] for row in tied] != ["a", "b"]:
        raise SystemExit("self-test deterministic tie order differs")
    reject(lambda: normalize_weights([[1, 1.0], [1, 2.0]], "duplicate", 8), "duplicate token")
    reject(lambda: normalize_weights({"8": 1.0}, "range", 8), "out-of-range token")
    reject(lambda: normalize_weights({"1": float("nan")}, "nan", 8), "NaN weight")
    reject(lambda: normalize_inference_output({"lexical_weights": [{"1": 1.0}]}, 2, "short", 8), "short inference")
    reject(lambda: normalize_inference_output({"lexical_weights": [{"1": 1.0}, {"2": 1.0}]}, 1, "long", 8), "long inference")
    reject(lambda: build_postings([{"id": "a"}], []), "missing document identity")

    corpus = {"revision": "c1", "documents": [
        {"id": "de", "text": "english", "language": "en"},
        {"id": "dr", "text": "русский", "language": "ru"},
        {"id": "dc", "text": "code", "language": "code"},
    ]}
    queries = {"revision": "q1", "queries": [
        {"id": "qe", "text": "english", "language": "en", "answer_mode": "judged"},
        {"id": "qr", "text": "русский", "language": "ru", "answer_mode": "judged"},
        {"id": "qc", "text": "code", "language": "code", "answer_mode": "judged"},
        {"id": "qn", "text": "none", "language": "en", "answer_mode": "no_answer"},
    ]}
    qrels = {"revision": "r1", "judgment_provenance": {"kind": "independent_human_judgment", "revision": "j1"}, "qrels": [
        {"query_id": "qe", "doc_id": "de", "grade": 1},
        {"query_id": "qr", "doc_id": "dr", "grade": 1},
        {"query_id": "qc", "doc_id": "dc", "grade": 1},
    ]}
    validate_data(corpus, queries, qrels)
    duplicate_corpus = json.loads(json.dumps(corpus)); duplicate_corpus["documents"][1]["id"] = "de"
    reject(lambda: validate_data(duplicate_corpus, queries, qrels), "duplicate document ID")
    malformed_qrels = json.loads(json.dumps(qrels)); malformed_qrels["qrels"][0]["doc_id"] = "missing"
    reject(lambda: validate_data(corpus, queries, malformed_qrels), "qrels mismatch")
    missing_slice = json.loads(json.dumps(queries)); missing_slice["queries"] = [row for row in missing_slice["queries"] if row["language"] != "code"]
    reject(lambda: validate_data(corpus, missing_slice, qrels), "missing language slice")
    reject(lambda: require_verified_artifacts([{"path": "corrupt", "verified": False}]), "corrupted artifact metadata")

    policy = {
        "schema_version": 1, "revision": "self-test", "registered_before_evaluation": True,
        "minimum_judged_queries_per_language": 1, "minimum_recall_at_10_delta": 0.0,
        "minimum_ndcg_at_10_delta": 0.0, "no_go_recall_at_10_delta": -0.1,
        "no_go_ndcg_at_10_delta": -0.1,
    }
    policy["registration_manifest"] = {
        "repository": "LimiNode/agent-memory-cpp",
        "commit": "0123456789abcdef0123456789abcdef01234567",
        "path": "tools/bge-m3/decision-policy.example.json",
        "policy_sha256": sha256_json(policy),
    }
    slices = {language: {"positive_query_count": 1, "positive_relevant_document_count": 1} for language in LANGUAGES}
    quality = {
        "bge_m3_sparse": {"query_count": 4, "judged_query_count": 3, "positive_relevant_document_count": 3, "recall_at": {"1": 1.0, "5": 1.0, "10": 1.0, "50": 1.0}, "ndcg_at_10": 1.0, "mrr_at_limit": 1.0, "no_answer_nonempty_hit_rate_diagnostic": 0.0, "abstention_accuracy": None, "abstention_note": "UNAVAILABLE", "slice_counts": slices},
        "bm25": {"query_count": 4, "judged_query_count": 3, "positive_relevant_document_count": 3, "recall_at": {"1": 0.5, "5": 0.5, "10": 0.5, "50": 0.5}, "ndcg_at_10": 0.5, "mrr_at_limit": 0.5, "no_answer_nonempty_hit_rate_diagnostic": 0.0, "abstention_accuracy": None, "abstention_note": "UNAVAILABLE", "slice_counts": slices},
    }
    if decide(quality, policy, 10) != "GO_TO_LARGER_EVALUATION" or decide(quality, policy, 5) != "INSUFFICIENT_EVIDENCE":
        raise SystemExit("self-test decision policy or top-k coverage differs")
    reject(lambda: validate_receipt({"execution_status": "MODEL_INFERENCE_FAILED", "decision": "GO_TO_LARGER_EVALUATION"}), "false GO receipt")
    reject(lambda: validate_receipt({"execution_status": "SCORER_PARITY_FAILED", "decision": "NO_GO"}), "false NO_GO receipt")
    reject(lambda: validate_receipt({"execution_status": COMPLETED, "decision": "GO_TO_LARGER_EVALUATION", "quality": {}}), "empty completed GO receipt")
    reject(lambda: validate_receipt({"execution_status": COMPLETED, "decision": "NO_GO", "quality": {}}), "empty completed NO_GO receipt")
    overflow = {1: 1.0e200}
    reject(lambda: sparse_score(overflow, overflow), "overflowing sparse score")
    malformed_qrel_identity = json.loads(json.dumps(qrels)); malformed_qrel_identity["qrels"][0]["query_id"] = []
    reject(lambda: validate_data(corpus, queries, malformed_qrel_identity), "malformed qrel identity")
    registered_policy = load_json(REPOSITORY_ROOT / "tools/bge-m3/decision-policy.example.json")
    validate_policy(registered_policy, REPOSITORY_ROOT)
    forged_commit = json.loads(json.dumps(registered_policy)); forged_commit["registration_manifest"]["commit"] = "0" * 40
    reject(lambda: validate_policy(forged_commit, REPOSITORY_ROOT), "unresolvable policy commit")
    forged_path = json.loads(json.dumps(registered_policy)); forged_path["registration_manifest"]["path"] = "tools/bge-m3/missing-policy.json"
    reject(lambda: validate_policy(forged_path, REPOSITORY_ROOT), "unresolvable policy path")
    forged_digest = json.loads(json.dumps(registered_policy)); forged_digest["registration_manifest"]["policy_sha256"] = "0" * 64
    reject(lambda: validate_policy(forged_digest, REPOSITORY_ROOT), "forged policy body digest")
    posthoc_policy = json.loads(json.dumps(registered_policy)); posthoc_policy["minimum_recall_at_10_delta"] = -1.0
    reject(lambda: validate_policy(posthoc_policy, REPOSITORY_ROOT), "post-hoc policy threshold")
    with tempfile.TemporaryDirectory(prefix="bge-m3-self-test-") as directory:
        root = Path(directory)
        corpus_path, queries_path, qrels_path = root / "corpus.json", root / "queries.json", root / "qrels.json"
        corpus_path.write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")
        evaluation_queries = {"revision": "q-e2e", "queries": []}
        evaluation_qrels = {"revision": "r-e2e", "judgment_provenance": qrels["judgment_provenance"], "qrels": []}
        query_document = {"en": "de", "ru": "dr", "code": "dc"}
        for language in ("en", "ru", "code"):
            for index in range(5):
                query_id = f"q-{language}-{index}"
                evaluation_queries["queries"].append({"id": query_id, "text": language, "language": language, "answer_mode": "judged"})
                evaluation_qrels["qrels"].append({"query_id": query_id, "doc_id": query_document[language], "grade": 1})
        evaluation_queries["queries"].append({"id": "q-none", "text": "none", "language": "en", "answer_mode": "no_answer"})
        queries, qrels = evaluation_queries, evaluation_qrels
        queries_path.write_text(json.dumps(queries, ensure_ascii=False), encoding="utf-8")
        qrels_path.write_text(json.dumps(qrels, ensure_ascii=False), encoding="utf-8")

        def e2e_receipt(rows: list[dict[str, Any]], limit: int) -> dict[str, Any]:
            _, _, replay_qrels, provenance = validate_data(corpus, queries, qrels)
            quality = {
                "bge_m3_sparse": metrics(rows, replay_qrels, "sparse", limit),
                "bm25": metrics(rows, replay_qrels, "bm25", limit),
            }
            return {
                "execution_status": COMPLETED,
                "decision": decide(quality, registered_policy, limit),
                "decision_policy": {
                    "revision": registered_policy["revision"],
                    "sha256": sha256_json(registered_policy),
                    "policy": registered_policy,
                },
                "model": {"repository": MODEL_REPOSITORY, "revision": MODEL_REVISION, "loaded": True},
                "artifacts": [{"path": "fake-model", "verified": True, "loaded": True}],
                "inputs": {
                    "corpus_revision": corpus["revision"], "queries_revision": queries["revision"],
                    "qrels_revision": qrels["revision"], "corpus_sha256": sha256_file(corpus_path),
                    "queries_sha256": sha256_file(queries_path), "qrels_sha256": sha256_file(qrels_path),
                    "corpus_path": str(corpus_path), "queries_path": str(queries_path),
                    "qrels_path": str(qrels_path), "judgment_provenance": provenance,
                },
                "retrieval_limit": limit,
                "quality": quality,
                "evidence_replay": {
                    "corpus_ids": [row["id"] for row in corpus["documents"]],
                    "queries": queries["queries"], "qrels": qrels["qrels"], "rows": rows,
                },
            }

        good_rows, bad_rows = [], []
        for query in queries["queries"]:
            if query["answer_mode"] == "no_answer":
                good_rows.append({"query_id": query["id"], "language": query["language"], "answer_mode": query["answer_mode"], "sparse_hits": [], "bm25_hits": []})
                bad_rows.append(json.loads(json.dumps(good_rows[-1])))
                continue
            relevant = query_document[query["language"]]
            wrong = next(document_id for document_id in query_document.values() if document_id != relevant)
            good_rows.append({"query_id": query["id"], "language": query["language"], "answer_mode": query["answer_mode"], "sparse_hits": [{"doc_id": relevant, "score": 1.0}], "bm25_hits": [{"doc_id": wrong, "score": 1.0}]})
            bad_rows.append({"query_id": query["id"], "language": query["language"], "answer_mode": query["answer_mode"], "sparse_hits": [{"doc_id": wrong, "score": 1.0}], "bm25_hits": [{"doc_id": relevant, "score": 1.0}]})

        # Exercise the complete dependency-free path instead of feeding the
        # receipt validator precomputed rows.  This is intentionally a small
        # smoke fixture: the completed result is validated from the generated
        # rows, while the decision controls below cover qualified GO and NO_GO
        # outcomes with deliberately contrasting arms.
        fake_document_weights = normalize_inference_output(
            encoder.encode([row["text"] for row in corpus["documents"]]),
            len(corpus["documents"]), "fake-document", 8,
        )
        fake_query_weights = normalize_inference_output(
            encoder.encode([row["text"] for row in queries["queries"]]),
            len(queries["queries"]), "fake-query", 8,
        )
        fake_postings = build_postings(corpus["documents"], fake_document_weights)
        fake_bm25 = build_bm25(corpus["documents"])
        pipeline_rows = []
        for query_row, query_weights in zip(queries["queries"], fake_query_weights):
            pipeline_rows.append({
                "query_id": query_row["id"],
                "language": query_row["language"],
                "answer_mode": query_row["answer_mode"],
                "sparse_hits": rank_scores(score_candidates(fake_postings, query_weights), 10),
                "bm25_hits": bm25_rank(fake_bm25, query_row["text"], 10),
            })
        pipeline_receipt = e2e_receipt(pipeline_rows, 10)
        if pipeline_receipt["decision"] not in DECISIONS:
            raise SystemExit(f"self-test fake end-to-end path produced an invalid decision: {pipeline_receipt['decision']}")
        publish_receipt(pipeline_receipt, root / "receipt-pipeline.json")

        expected_decisions = ((good_rows, 10, "GO_TO_LARGER_EVALUATION"), (bad_rows, 10, "NO_GO"), (good_rows, 5, "INSUFFICIENT_EVIDENCE"))
        for index, (rows, limit, expected_decision) in enumerate(expected_decisions):
            output = root / f"receipt-{index}.json"
            receipt = e2e_receipt(rows, limit)
            if receipt["decision"] != expected_decision:
                raise SystemExit(f"self-test expected {expected_decision}, got {receipt['decision']}")
            publish_receipt(receipt, output)
            validate_receipt(load_json(output))
        forged_quality = e2e_receipt(good_rows, 10)
        forged_quality["quality"]["bge_m3_sparse"]["recall_at"]["10"] = 0.0
        reject(lambda: validate_receipt(forged_quality), "forged replay quality")
        failure_output = root / "failure.json"
        if failure_receipt("EVALUATION_FAILED", "synthetic post-inference failure", [{"path": "fake", "verified": True}], failure_output, 2, True) != 2 \
                or load_json(failure_output)["model"]["loaded"] is not True:
            raise SystemExit("self-test lost model-loaded state in failure receipt")
        output = root / "invalid.json"
        reject(lambda: publish_receipt({"execution_status": COMPLETED, "decision": "INVALID", "quality": {}}, output), "invalid decision publication")
        if output.exists():
            raise SystemExit("self-test published an invalid receipt")
    print(json.dumps({"status": "PASS", "negative_controls": failures}, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--queries", type=Path)
    parser.add_argument("--qrels", type=Path)
    parser.add_argument("--decision-policy", type=Path)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-length", type=int, default=8192)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--max-documents", type=int, default=10000)
    parser.add_argument("--max-queries", type=int, default=1000)
    parser.add_argument("--max-total-text-bytes", type=int, default=100_000_000)
    parser.add_argument("--parity-pairs", type=int, default=64)
    parser.add_argument("--output", type=Path, default=Path("build/bge-m3-pilot-receipt.json"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.artifact_dir is None:
        parser.error("--artifact-dir is required unless --self-test is used")
    if any(value <= 0 for value in (
        args.limit, args.batch_size, args.max_length, args.max_documents,
        args.max_queries, args.max_total_text_bytes, args.parity_pairs,
    )):
        parser.error("limits, budgets, batch size and parity pairs must be positive")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
