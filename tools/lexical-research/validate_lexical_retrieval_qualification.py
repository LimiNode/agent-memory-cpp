#!/usr/bin/env python3
"""Validate the frozen Lexical Retrieval Qualification R1 protocol.

This is a small protocol guard, not a second benchmark framework.  It checks
the committed manifest and fixture, then delegates the BM25 smoke execution to
``run_lexical_protocol.py``.  C++ backends and neural pilots remain external
adapters whose receipts must use the same metric and eligibility vocabulary.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from run_lexical_protocol import run as run_bm25
from run_lexical_protocol import validate_dataset


ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT / "fixtures" / "lexical_retrieval_qualification_r1_manifest.json"


class ProtocolError(ValueError):
    """Raised when a protocol or fixture invariant is violated."""


BACKEND_IDS = {
    "bm25",
    "flat-v2",
    "segmented-v2",
    "learned-sparse-weighted-postings",
    "bge-m3-pilot",
}
REQUIRED_METRICS = {
    "candidate_recall_at_k",
    "ndcg_at_k",
    "mrr",
    "hydrated_recall_at_k",
    "provenance_correctness",
    "no_answer_accuracy",
    "latency_ms_p50",
    "latency_ms_p95",
    "latency_ms_p99",
    "index_build_ms",
    "update_ms",
    "reopen_ms",
    "decoded_bytes",
    "index_bytes",
}


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProtocolError(f"{path} must contain an object")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_manifest(manifest: dict[str, Any], manifest_path: Path) -> Path:
    if manifest.get("schema_version") != 1:
        raise ProtocolError("unsupported protocol schema")
    if manifest.get("protocol") != "lexical_retrieval_qualification_r1":
        raise ProtocolError("unexpected protocol name")
    if not isinstance(manifest.get("protocol_revision"), str) or not manifest["protocol_revision"]:
        raise ProtocolError("protocol revision is required")
    if not isinstance(manifest.get("source_frontier"), str) or not manifest["source_frontier"]:
        raise ProtocolError("source frontier is required")
    revisions = manifest.get("revisions")
    if not isinstance(revisions, dict) or revisions != {
        "corpus": "r1-corpus-2026-10-30",
        "qrels": "r1-qrels-2026-10-30",
        "query_split": "r1-split-2026-10-30",
        "tokenizer": "code_aware_fixture_v1",
    }:
        raise ProtocolError("manifest revision identities are incomplete")
    corpus = manifest.get("corpus")
    if not isinstance(corpus, dict) or corpus.get("access_policy") != "committed_fixture_only":
        raise ProtocolError("corpus access policy must be committed_fixture_only")
    corpus_name, declared_sha = corpus.get("path"), corpus.get("sha256")
    if not isinstance(corpus_name, str) or Path(corpus_name).name != corpus_name:
        raise ProtocolError("corpus path must be a fixture basename")
    if not isinstance(declared_sha, str) or len(declared_sha) != 64:
        raise ProtocolError("corpus SHA-256 is missing")
    corpus_path = manifest_path.parent / corpus_name
    if not corpus_path.is_file() or sha256(corpus_path) != declared_sha:
        raise ProtocolError("frozen corpus digest mismatch")

    tokenizer = manifest.get("tokenizer")
    if not isinstance(tokenizer, dict) or tokenizer.get("name") != "code_aware_fixture_v1":
        raise ProtocolError("tokenizer identity is incomplete")
    if tokenizer.get("revision") != tokenizer["name"]:
        raise ProtocolError("tokenizer revision must match its frozen name")

    split = manifest.get("query_split")
    if not isinstance(split, dict) or split.get("name") != "r1-split-2026-10-30":
        raise ProtocolError("query split identity is incomplete")
    policy = manifest.get("eligibility_policy")
    if not isinstance(policy, dict) or policy.get("evaluation_mode") != "fixture_eligibility_simulation" \
            or "not an MDBX canonical read" not in str(policy.get("hydration_evidence")) \
            or not all(policy.get(key) for key in (
        "canonical_hydration", "stale_revision", "duplicate_hits", "unjudged_or_incomplete", "no_answer"
    )):
        raise ProtocolError("eligibility policy is incomplete")

    backends = manifest.get("backends")
    if not isinstance(backends, list) or {row.get("id") for row in backends} != BACKEND_IDS:
        raise ProtocolError("baseline backend matrix is incomplete")
    rows = {row["id"]: row for row in backends}
    for row in backends:
        if row.get("status") not in {"production_baseline", "experimental", "research_only"}:
            raise ProtocolError(f"invalid status for {row.get('id')}")
        if not row.get("kind") or not row.get("scoring") or not row.get("index"):
            raise ProtocolError(f"backend row is incomplete: {row.get('id')}")
    if rows["flat-v2"]["scoring"] != rows["segmented-v2"]["scoring"]:
        raise ProtocolError("flat-v2 and segmented-v2 must share the BM25 scoring identity")
    metrics = manifest.get("metrics")
    if not isinstance(metrics, dict):
        raise ProtocolError("metrics declaration is missing")
    declared = set(metrics.get("quality", [])) | set(metrics.get("cost", []))
    if declared != REQUIRED_METRICS:
        raise ProtocolError("metric matrix is incomplete or has unexpected fields")
    if metrics.get("measurement_unit") != "one_query_repeat":
        raise ProtocolError("timing measurement unit is not explicit")
    if "repeated queries are observations" not in str(metrics.get("timing_semantics")):
        raise ProtocolError("timing independence warning is missing")
    if not metrics.get("tail_claim"):
        raise ProtocolError("tail-latency limitation is missing")
    receipt = manifest.get("run_receipt")
    required_receipt_sections = {
        "required_top_level", "required_query_fields", "required_hit_fields",
        "required_timing_fields", "required_lifecycle_fields",
    }
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1 \
            or set(receipt) != required_receipt_sections | {"schema_version"}:
        raise ProtocolError("run receipt contract is incomplete")
    if any(not isinstance(receipt[field], list) or not receipt[field] for field in required_receipt_sections - {"schema_version"}):
        raise ProtocolError("run receipt field requirements are empty")
    if not isinstance(manifest.get("required_gates"), list) or len(manifest["required_gates"]) < 5:
        raise ProtocolError("required gates are incomplete")
    return corpus_path


def validate_fixture(corpus_path: Path, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    dataset = load_json(corpus_path)
    try:
        validate_dataset(dataset)
    except ValueError as exc:
        raise ProtocolError(f"dataset contract failed: {exc}") from exc
    corpus_ids = {row["id"] for row in dataset["corpus"]}
    queries = {row["id"]: row for row in dataset["queries"]}
    if manifest is not None:
        frontier = dataset.get("source_frontier")
        if not isinstance(frontier, dict) or frontier.get("revision") != manifest.get("source_frontier"):
            raise ProtocolError("fixture source frontier does not match manifest")
        revisions = dataset.get("revisions")
        if revisions != manifest.get("revisions"):
            raise ProtocolError("fixture revision identities do not match manifest")
    if len(dataset["corpus"]) < 10:
        raise ProtocolError("R1 corpus must contain at least ten documents")
    required_languages = {"en", "ru"}
    if {row.get("language") for row in dataset["corpus"]} != required_languages:
        raise ProtocolError("R1 corpus must contain English and Russian documents")
    required_classes = {"exact_identifier", "russian_morphology", "rare_fact", "correction", "contradictory_sources", "hard_negative", "lexical_empty_control"}
    classes = {row.get("query_class") for row in dataset["queries"]}
    if not required_classes <= classes:
        raise ProtocolError(f"fixture misses query classes: {sorted(required_classes - classes)}")
    if {row.get("split") for row in dataset["queries"]} != {"train", "dev", "test"}:
        raise ProtocolError("fixture must declare train/dev/test query splits")
    if any(not isinstance(row.get("split"), str) for row in dataset["queries"]):
        raise ProtocolError("every query must have a split assignment")
    if queries["q_no_answer"]["answer_mode"] != "no_answer":
        raise ProtocolError("no-answer control is not explicit")
    qrel_pairs = {(row["query_id"], row["doc_id"]) for row in dataset["qrels"]}
    if len(qrel_pairs) != len(dataset["qrels"]):
        raise ProtocolError("fixture qrels contain duplicate pairs")
    if not any(row["answer_mode"] == "incomplete_qrels" for row in dataset["queries"]):
        raise ProtocolError("fixture must exercise incomplete-qrels reporting")
    if not any(row["answer_mode"] == "unjudged" for row in dataset["queries"]):
        raise ProtocolError("fixture must exercise unjudged reporting")
    expected = dataset.get("expected_top_ids")
    if not isinstance(expected, dict):
        raise ProtocolError("expected_top_ids is required")
    if set(expected) - set(queries):
        raise ProtocolError("expected_top_ids references an unknown query")
    for query_id, doc_ids in expected.items():
        if not isinstance(doc_ids, list) or len(set(doc_ids)) != len(doc_ids) \
                or any(doc_id not in corpus_ids for doc_id in doc_ids):
            raise ProtocolError(f"expected_top_ids has invalid documents for {query_id}")
    if any(row["answer_mode"] == "no_answer" and any(
            judgment["query_id"] == row["id"] for judgment in dataset["qrels"])
            for row in dataset["queries"]):
        raise ProtocolError("no-answer query must not have qrels")
    if not corpus_ids:
        raise ProtocolError("fixture corpus is empty")
    return dataset


def self_test(manifest_path: Path) -> None:
    manifest = load_json(manifest_path)
    corpus_path = validate_manifest(manifest, manifest_path)
    dataset = validate_fixture(corpus_path, manifest)
    report = run_bm25(corpus_path, None, limit=50, repeats=1)
    if report["quality"]["evaluation_status"] != "partial_qrels":
        raise AssertionError("incomplete/unjudged queries must prevent complete quality status")
    if report["quality"]["no_answer_accuracy"] != 1.0:
        raise AssertionError("no-answer control must remain empty")
    actual = {row["query_id"]: [hit["doc_id"] for hit in row["hits"]] for row in report["queries"]}
    for query_id, expected in dataset.get("expected_top_ids", {}).items():
        if actual.get(query_id, [])[: len(expected)] != expected:
            raise AssertionError(f"{query_id}: expected {expected}, got {actual.get(query_id, [])}")

    mutations: list[tuple[str, Any]] = []
    bad_manifest = copy.deepcopy(load_json(manifest_path))
    bad_manifest["backends"][1]["scoring"] = "bm25_variant"
    mutations.append(("backend scoring split", bad_manifest))
    bad_fixture = copy.deepcopy(dataset)
    bad_fixture["qrels"].append({"query_id": "q_api_symbol", "doc_id": "d_api_exact", "grade": 0})
    mutations.append(("duplicate qrel", bad_fixture))
    bad_fixture = copy.deepcopy(dataset)
    bad_fixture["queries"] = [row for row in bad_fixture["queries"] if row["id"] != "q_no_answer"]
    mutations.append(("missing no-answer control", bad_fixture))
    bad_fixture = copy.deepcopy(dataset)
    bad_fixture["source_frontier"]["revision"] = "stale-frontier"
    mutations.append(("source frontier mismatch", bad_fixture))
    for label, candidate in mutations:
        try:
            if label == "backend scoring split":
                validate_manifest(candidate, manifest_path)
            else:
                validate_fixture_from_value(candidate, load_json(manifest_path))
        except ProtocolError:
            continue
        raise AssertionError(f"mutation was accepted: {label}")
    print(json.dumps({"status": "PASS", "negative_controls": len(mutations),
                      "positive_controls": 1,
                      "mutation_controls": len(mutations),
                      "dataset": dataset["dataset_name"],
                      "quality_status": report["quality"]["evaluation_status"]}, indent=2, sort_keys=True))


def validate_fixture_from_value(dataset: dict[str, Any], manifest: dict[str, Any]) -> None:
    # Keep the same checks as the on-disk path without introducing another parser.
    with tempfile.TemporaryDirectory(prefix="lexical-r1-fixture-") as directory:
        path = Path(directory) / "fixture.json"
        path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
        validate_fixture(path, manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--run-bm25", action="store_true", help="run the existing BM25 smoke and print its receipt")
    args = parser.parse_args()
    try:
        manifest = load_json(args.manifest)
        corpus_path = validate_manifest(manifest, args.manifest)
        validate_fixture(corpus_path, manifest)
        if args.self_test:
            self_test(args.manifest)
        elif args.run_bm25:
            print(json.dumps(run_bm25(corpus_path, None, limit=50, repeats=1), indent=2, sort_keys=True))
        else:
            print(json.dumps({"status": "PASS", "manifest": str(args.manifest),
                              "corpus_sha256": sha256(corpus_path)}, indent=2, sort_keys=True))
        return 0
    except (OSError, ProtocolError, ValueError, AssertionError) as exc:
        print(f"lexical qualification validation failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
