#!/usr/bin/env python3
"""Prepare deterministic, leakage-safe MIRACL slices for AE experiments.

The tool has no runtime dependency beyond Python's standard library. It accepts
an already materialized input root so CI never downloads an external corpus.
The input layout is configured with relative path templates, for example:

{
  "schema_version": 1,
  "dataset": {
    "corpus": {"id": "miracl/miracl-corpus", "revision": "<immutable revision>"},
    "judgments": {"id": "miracl/miracl", "revision": "<immutable revision>"}
  },
  "languages": ["ru", "en"],
  "layout": {
    "corpus": "miracl-corpus-v1.0-{language}/docs-*.jsonl.gz",
    "queries": "{language}/queries.dev.tsv",
    "qrels": "{language}/qrels.dev.tsv"
  },
  "sampling": {
    "strategy": "balanced_stable_hash",
    "seed": 42,
    "train_documents_per_language": 25000,
    "evaluation_distractors_per_language": 10000,
    "evaluation_queries_per_language": 0
  },
  "embedding": {
    "model_id": "intfloat/multilingual-e5-small",
    "model_revision": "<immutable revision>",
    "document_prefix": "passage: ",
    "query_prefix": "query: ",
    "normalized": true
  }
}

The resulting output keeps all qrels-referenced documents in the evaluation
corpus and samples both training records and evaluation distractors by stable
SHA-256 rank. Training and evaluation document IDs are therefore disjoint.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import heapq
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

sys.dont_write_bytecode = True

PREPARER_ID = "agent-memory-cpp:miracl-ae-preparer"
PREPARER_VERSION = "v1"
REQUIRED_CONFIG_KEYS = {
    "schema_version",
    "dataset",
    "languages",
    "layout",
    "sampling",
    "split",
    "embedding",
}


class PreparationError(ValueError):
    """Raised when input data cannot satisfy the experiment contract."""


@dataclass(frozen=True)
class StudyConfig:
    dataset: dict[str, dict[str, str]]
    languages: tuple[str, ...]
    corpus_template: str
    queries_template: str
    qrels_template: str
    evaluation_qrels_split: str
    seed: int
    train_documents_per_language: int
    evaluation_distractors_per_language: int
    evaluation_queries_per_language: int
    embedding: dict[str, Any]
    canonical_json: str


@dataclass(frozen=True)
class Document:
    language: str
    docid: str
    title: str
    text: str

    @property
    def global_id(self) -> str:
        return f"{self.language}:{self.docid}"

    def as_json(self) -> dict[str, str]:
        return {
            "id": self.global_id,
            "language": self.language,
            "source_id": self.docid,
            "title": self.title,
            "text": self.text,
        }


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sorted_id_set_sha256(ids: Iterable[str]) -> str:
    return sha256_bytes(("\n".join(sorted(ids)) + "\n").encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PreparationError(f"{field} must be a non-empty string")
    return value


def non_negative_int(value: Any, field: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PreparationError(f"{field} must be an integer")
    if value < 0 or (positive and value == 0):
        qualifier = "positive" if positive else "non-negative"
        raise PreparationError(f"{field} must be {qualifier}")
    return value


def require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PreparationError(f"{field} must be an object")
    return value


def load_config(path: Path) -> StudyConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreparationError(f"cannot read config: {exc}") from exc

    root = require_mapping(raw, "config")
    missing = sorted(REQUIRED_CONFIG_KEYS - root.keys())
    if missing:
        raise PreparationError(f"config is missing required fields: {', '.join(missing)}")
    if root.get("schema_version") != 1:
        raise PreparationError("schema_version must equal 1")

    raw_dataset = require_mapping(root["dataset"], "dataset")
    layout = require_mapping(root["layout"], "layout")
    sampling = dict(require_mapping(root["sampling"], "sampling"))
    split = require_mapping(root["split"], "split")
    embedding = require_mapping(root["embedding"], "embedding")

    raw_languages = root["languages"]
    if not isinstance(raw_languages, list) or not raw_languages:
        raise PreparationError("languages must be a non-empty array")
    languages = tuple(non_empty_string(item, "language") for item in raw_languages)
    if len(set(languages)) != len(languages):
        raise PreparationError("languages must not contain duplicates")
    if sampling.get("strategy") != "balanced_stable_hash":
        raise PreparationError("sampling.strategy must equal balanced_stable_hash")
    sampling.setdefault("evaluation_queries_per_language", 0)

    dataset: dict[str, dict[str, str]] = {}
    for source_name in ("corpus", "judgments"):
        source = require_mapping(raw_dataset.get(source_name), f"dataset.{source_name}")
        dataset[source_name] = {
            "id": non_empty_string(source.get("id"), f"dataset.{source_name}.id"),
            "revision": non_empty_string(
                source.get("revision"),
                f"dataset.{source_name}.revision",
            ),
        }

    for field in ("model_id", "model_revision", "document_prefix", "query_prefix"):
        non_empty_string(embedding.get(field), f"embedding.{field}")
    if not isinstance(embedding.get("normalized"), bool):
        raise PreparationError("embedding.normalized must be a boolean")

    return StudyConfig(
        dataset=dataset,
        languages=languages,
        corpus_template=non_empty_string(layout.get("corpus"), "layout.corpus"),
        queries_template=non_empty_string(layout.get("queries"), "layout.queries"),
        qrels_template=non_empty_string(layout.get("qrels"), "layout.qrels"),
        evaluation_qrels_split=non_empty_string(
            split.get("evaluation_qrels_split"),
            "split.evaluation_qrels_split",
        ),
        seed=non_negative_int(sampling.get("seed"), "sampling.seed"),
        train_documents_per_language=non_negative_int(
            sampling.get("train_documents_per_language"),
            "sampling.train_documents_per_language",
            positive=True,
        ),
        evaluation_distractors_per_language=non_negative_int(
            sampling.get("evaluation_distractors_per_language"),
            "sampling.evaluation_distractors_per_language",
        ),
        evaluation_queries_per_language=non_negative_int(
            sampling.get("evaluation_queries_per_language", 0),
            "sampling.evaluation_queries_per_language",
        ),
        embedding=embedding,
        canonical_json=canonical_json({**root, "sampling": sampling}),
    )


def resolve_input_paths(
    root: Path,
    template: str,
    language: str,
    *,
    allow_glob: bool,
) -> list[Path]:
    try:
        relative = Path(template.format(language=language))
    except KeyError as exc:
        raise PreparationError(f"path template has unsupported placeholder: {exc}") from exc
    if relative.is_absolute():
        raise PreparationError("input path templates must be relative")
    if ".." in relative.parts:
        raise PreparationError("input path templates must not contain parent traversal")
    resolved_root = root.resolve()
    candidate = resolved_root / relative
    if not allow_glob and any(character in relative.name for character in "*?["):
        raise PreparationError("this input path template must not contain a glob")
    paths = sorted(candidate.parent.glob(candidate.name)) if allow_glob else [candidate]
    if not paths:
        raise PreparationError(f"input file does not exist: {candidate}")
    resolved_paths: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        try:
            resolved.relative_to(resolved_root)
        except ValueError as exc:
            raise PreparationError("input path template must stay within input root") from exc
        if not resolved.is_file():
            raise PreparationError(f"input path is not a file: {resolved}")
        resolved_paths.append(resolved)
    return resolved_paths


def resolve_input_path(root: Path, template: str, language: str) -> Path:
    return resolve_input_paths(root, template, language, allow_glob=False)[0]


def source_file_hashes(root: Path, paths: Iterable[Path]) -> list[dict[str, str]]:
    resolved_root = root.resolve()
    return [
        {
            "path": path.resolve().relative_to(resolved_root).as_posix(),
            "sha256": sha256_file(path),
        }
        for path in paths
    ]


def open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def iter_corpus(path: Path, language: str) -> Iterator[Document]:
    with open_text(path) as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PreparationError(
                    f"{path}:{line_number}: invalid corpus JSON: {exc.msg}"
                ) from exc
            if not isinstance(raw, dict):
                raise PreparationError(f"{path}:{line_number}: corpus row must be an object")
            yield Document(
                language=language,
                docid=non_empty_string(raw.get("docid"), f"{path}:{line_number}: docid"),
                title=non_empty_string(raw.get("title"), f"{path}:{line_number}: title"),
                text=non_empty_string(raw.get("text"), f"{path}:{line_number}: text"),
            )


def iter_corpora(paths: Iterable[Path], language: str) -> Iterator[Document]:
    for path in paths:
        yield from iter_corpus(path, language)


def load_queries(path: Path) -> dict[str, str]:
    queries: dict[str, str] = {}
    with open_text(path) as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.rstrip("\r\n")
            if not line:
                continue
            fields = line.split("\t", 1)
            if len(fields) != 2:
                raise PreparationError(f"{path}:{line_number}: query row must be qid<TAB>text")
            query_id = non_empty_string(fields[0], f"{path}:{line_number}: query id")
            query_text = non_empty_string(fields[1], f"{path}:{line_number}: query text")
            if query_id in queries:
                raise PreparationError(f"{path}:{line_number}: duplicate query id {query_id}")
            queries[query_id] = query_text
    if not queries:
        raise PreparationError(f"{path}: queries must not be empty")
    return queries


def load_qrels(path: Path, query_ids: set[str]) -> tuple[list[tuple[str, str, int]], set[str]]:
    rows: list[tuple[str, str, int]] = []
    document_ids: set[str] = set()
    positive_queries: set[str] = set()
    with open_text(path) as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            fields = line.split()
            if len(fields) != 4:
                raise PreparationError(f"{path}:{line_number}: qrels row must have four fields")
            query_id, iteration, document_id, raw_grade = fields
            if iteration != "Q0":
                raise PreparationError(f"{path}:{line_number}: qrels iteration must equal Q0")
            if query_id not in query_ids:
                raise PreparationError(f"{path}:{line_number}: qrels query is absent from topics")
            try:
                grade = int(raw_grade)
            except ValueError as exc:
                raise PreparationError(f"{path}:{line_number}: qrels grade must be an integer") from exc
            rows.append((query_id, document_id, grade))
            document_ids.add(document_id)
            if grade > 0:
                positive_queries.add(query_id)
    if not rows:
        raise PreparationError(f"{path}: qrels must not be empty")
    missing_positive = query_ids - positive_queries
    if missing_positive:
        raise PreparationError(
            f"{path}: queries without positive qrels: {', '.join(sorted(missing_positive)[:3])}"
        )
    return rows, document_ids


def stable_rank(seed: int, language: str, document_id: str, purpose: str) -> int:
    payload = f"{seed}\0{language}\0{purpose}\0{document_id}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:16], byteorder="big")


def choose_evaluation_queries(
    queries: dict[str, str],
    *,
    limit: int,
    seed: int,
    language: str,
) -> dict[str, str]:
    """Returns all queries or a deterministic, qrels-safe subset of them."""
    if limit == 0:
        return queries
    if limit > len(queries):
        raise PreparationError(
            f"{language}: found only {len(queries)} queries; need {limit}"
        )
    selected_ids = sorted(
        queries,
        key=lambda query_id: (
            stable_rank(seed, language, query_id, "evaluation-query"),
            query_id,
        ),
    )[:limit]
    return {query_id: queries[query_id] for query_id in selected_ids}


def choose_lowest_ranked(
    records: Iterable[Document],
    *,
    limit: int,
    seed: int,
    language: str,
    purpose: str,
) -> list[Document]:
    if limit == 0:
        return []
    heap: list[tuple[int, str, Document]] = []
    for record in records:
        rank = stable_rank(seed, language, record.docid, purpose)
        item = (-rank, record.docid, record)
        if len(heap) < limit:
            heapq.heappush(heap, item)
        elif item > heap[0]:
            heapq.heapreplace(heap, item)
    if len(heap) != limit:
        raise PreparationError(
            f"{language}: found only {len(heap)} eligible documents for {purpose}; need {limit}"
        )
    return [item[2] for item in sorted(heap, key=lambda item: (item[0], item[1]), reverse=True)]


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def prepare_study(config: StudyConfig, input_root: Path, output_root: Path) -> dict[str, Any]:
    if output_root.exists():
        raise PreparationError(f"output directory already exists: {output_root}")
    output_root.mkdir(parents=True)

    train_documents: list[Document] = []
    evaluation_documents: list[Document] = []
    qrels_excluded_ids: list[str] = []
    evaluation_query_ids: list[str] = []
    output_queries: list[tuple[str, str, str]] = []
    output_qrels: list[tuple[str, str, int]] = []
    per_language: list[dict[str, Any]] = []

    for language in config.languages:
        corpus_paths = resolve_input_paths(
            input_root,
            config.corpus_template,
            language,
            allow_glob=True,
        )
        queries_path = resolve_input_path(input_root, config.queries_template, language)
        qrels_path = resolve_input_path(input_root, config.qrels_template, language)
        all_queries = load_queries(queries_path)
        all_qrels, _ = load_qrels(qrels_path, set(all_queries))
        queries = choose_evaluation_queries(
            all_queries,
            limit=config.evaluation_queries_per_language,
            seed=config.seed,
            language=language,
        )
        qrels = [row for row in all_qrels if row[0] in queries]
        qrel_document_ids = {document_id for _, document_id, _ in qrels}
        if not qrels:
            raise PreparationError(f"{language}: selected evaluation queries have no qrels")
        qrels_excluded_ids.extend(f"{language}:{document_id}" for document_id in qrel_document_ids)
        evaluation_query_ids.extend(f"{language}:{query_id}" for query_id in queries)

        evaluation_by_id: dict[str, Document] = {}
        for document in iter_corpora(corpus_paths, language):
            if document.docid in qrel_document_ids:
                if document.docid in evaluation_by_id:
                    raise PreparationError(f"{language}: duplicate qrels document id {document.docid}")
                evaluation_by_id[document.docid] = document
        missing_documents = qrel_document_ids - set(evaluation_by_id)
        if missing_documents:
            first = next(iter(sorted(missing_documents)))
            raise PreparationError(f"{language}: qrels document is absent from corpus: {first}")

        selected_train = choose_lowest_ranked(
            (
                document
                for document in iter_corpora(corpus_paths, language)
                if document.docid not in qrel_document_ids
            ),
            limit=config.train_documents_per_language,
            seed=config.seed,
            language=language,
            purpose="train",
        )
        selected_train_ids = {document.docid for document in selected_train}
        selected_distractors = choose_lowest_ranked(
            (
                document
                for document in iter_corpora(corpus_paths, language)
                if document.docid not in qrel_document_ids
                and document.docid not in selected_train_ids
            ),
            limit=config.evaluation_distractors_per_language,
            seed=config.seed,
            language=language,
            purpose="evaluation-distractor",
        )
        selected_evaluation = sorted(
            [*evaluation_by_id.values(), *selected_distractors],
            key=lambda document: document.docid,
        )
        if selected_train_ids & {document.docid for document in selected_evaluation}:
            raise PreparationError(f"{language}: train and evaluation document IDs overlap")

        train_documents.extend(sorted(selected_train, key=lambda document: document.docid))
        evaluation_documents.extend(selected_evaluation)
        output_queries.extend(
            (language, query_id, query_text)
            for query_id, query_text in sorted(queries.items())
        )
        output_qrels.extend(
            (f"{language}:{query_id}", f"{language}:{document_id}", grade)
            for query_id, document_id, grade in qrels
        )
        per_language.append(
            {
                "language": language,
                "train_document_count": len(selected_train),
                "evaluation_document_count": len(selected_evaluation),
                "qrels_document_count": len(evaluation_by_id),
                "evaluation_distractor_count": len(selected_distractors),
                "query_count": len(queries),
                "evaluation_query_ids_sha256": sorted_id_set_sha256(
                    f"{language}:{query_id}" for query_id in queries
                ),
                "qrels_count": len(qrels),
                "source_hashes": {
                    "corpus": source_file_hashes(input_root, corpus_paths),
                    "queries": sha256_file(queries_path),
                    "qrels": sha256_file(qrels_path),
                },
            }
        )

    train_path = output_root / "train-documents.jsonl"
    evaluation_path = output_root / "evaluation-documents.jsonl"
    queries_path = output_root / "evaluation-queries.tsv"
    qrels_path = output_root / "evaluation-qrels.tsv"
    write_jsonl(train_path, (document.as_json() for document in train_documents))
    write_jsonl(evaluation_path, (document.as_json() for document in evaluation_documents))
    with queries_path.open("w", encoding="utf-8", newline="\n") as handle:
        for language, query_id, query_text in output_queries:
            handle.write(f"{language}:{query_id}\t{query_text}\n")
    with qrels_path.open("w", encoding="utf-8", newline="\n") as handle:
        for query_id, document_id, grade in output_qrels:
            handle.write(f"{query_id} Q0 {document_id} {grade}\n")

    manifest = {
        "schema_version": 1,
        "preparer": {
            "id": PREPARER_ID,
            "version": PREPARER_VERSION,
            "source_hash": sha256_file(Path(__file__)),
        },
        "dataset": config.dataset,
        "languages": list(config.languages),
        "sampling": {
            "strategy": "balanced_stable_hash",
            "seed": config.seed,
            "train_documents_per_language": config.train_documents_per_language,
            "evaluation_distractors_per_language": config.evaluation_distractors_per_language,
            "evaluation_queries_per_language": config.evaluation_queries_per_language,
        },
        "split": {
            "policy": "held_out_document_ids",
            "evaluation_qrels_split": config.evaluation_qrels_split,
            "query_usage": "evaluation_only",
            "qrels_usage": "evaluation_only",
            "qrels_excluded_document_ids_sha256": sorted_id_set_sha256(qrels_excluded_ids),
            "evaluation_query_ids_sha256": sorted_id_set_sha256(evaluation_query_ids),
            "evaluation_document_ids_sha256": sorted_id_set_sha256(
                document.global_id for document in evaluation_documents
            ),
        },
        "embedding": config.embedding,
        "input_config_hash": sha256_bytes(config.canonical_json.encode("utf-8")),
        "per_language": per_language,
        "outputs": {
            "train_documents": {
                "path": train_path.name,
                "sha256": sha256_file(train_path),
                "count": len(train_documents),
            },
            "evaluation_documents": {
                "path": evaluation_path.name,
                "sha256": sha256_file(evaluation_path),
                "count": len(evaluation_documents),
            },
            "evaluation_queries": {
                "path": queries_path.name,
                "sha256": sha256_file(queries_path),
                "count": len(output_queries),
            },
            "evaluation_qrels": {
                "path": qrels_path.name,
                "sha256": sha256_file(qrels_path),
                "count": len(output_qrels),
            },
        },
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


def write_test_input(root: Path) -> Path:
    config = {
        "schema_version": 1,
        "dataset": {
            "corpus": {"id": "miracl/miracl-corpus", "revision": "test-corpus-revision"},
            "judgments": {"id": "miracl/miracl", "revision": "test-judgments-revision"},
        },
        "languages": ["ru", "en"],
        "layout": {
            "corpus": "{language}/corpus-*.jsonl",
            "queries": "{language}/queries.dev.tsv",
            "qrels": "{language}/qrels.dev.tsv",
        },
        "sampling": {
            "strategy": "balanced_stable_hash",
            "seed": 42,
            "train_documents_per_language": 2,
            "evaluation_distractors_per_language": 1,
            "evaluation_queries_per_language": 0,
        },
        "split": {"evaluation_qrels_split": "dev"},
        "embedding": {
            "model_id": "intfloat/multilingual-e5-small",
            "model_revision": "test-model-revision",
            "document_prefix": "passage: ",
            "query_prefix": "query: ",
            "normalized": True,
        },
    }
    for language in config["languages"]:
        language_root = root / language
        language_root.mkdir(parents=True)
        corpus = [
            {
                "docid": f"{language}-{index}",
                "title": f"{language} title {index}",
                "text": f"{language} text {index}",
            }
            for index in range(1, 6)
        ]
        write_jsonl(language_root / "corpus-a.jsonl", corpus[:3])
        write_jsonl(language_root / "corpus-b.jsonl", corpus[3:])
        (language_root / "queries.dev.tsv").write_text(
            f"q1\t{language} query\n", encoding="utf-8", newline="\n"
        )
        (language_root / "qrels.dev.tsv").write_text(
            f"q1 Q0 {language}-1 2\nq1 Q0 {language}-2 0\n",
            encoding="utf-8",
            newline="\n",
        )
    config_path = root / "study.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return config_path


def run_self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-memory-miracl-preparer-") as raw_root:
        root = Path(raw_root)
        config_path = write_test_input(root / "input")
        config = load_config(config_path)
        first = prepare_study(config, root / "input", root / "first")
        second = prepare_study(config, root / "input", root / "second")

        if first["outputs"] != second["outputs"]:
            print("self-test failed: repeated output hashes differ", file=sys.stderr)
            return 1
        if first["outputs"]["train_documents"]["count"] != 4:
            print("self-test failed: unexpected balanced train count", file=sys.stderr)
            return 1
        if first["outputs"]["evaluation_documents"]["count"] != 6:
            print("self-test failed: qrels and distractor documents were not preserved", file=sys.stderr)
            return 1

        train_ids = {
            json.loads(line)["id"]
            for line in (root / "first" / "train-documents.jsonl").read_text(encoding="utf-8").splitlines()
        }
        evaluation_ids = {
            json.loads(line)["id"]
            for line in (root / "first" / "evaluation-documents.jsonl").read_text(encoding="utf-8").splitlines()
        }
        if train_ids & evaluation_ids:
            print("self-test failed: train and evaluation documents overlap", file=sys.stderr)
            return 1

        query_limited = json.loads(config_path.read_text(encoding="utf-8"))
        query_limited["sampling"]["evaluation_queries_per_language"] = 1
        query_limited_path = root / "query-limited.json"
        query_limited_path.write_text(
            json.dumps(query_limited), encoding="utf-8", newline="\n"
        )
        query_limited_manifest = prepare_study(
            load_config(query_limited_path), root / "input", root / "query-limited"
        )
        if query_limited_manifest["outputs"]["evaluation_queries"]["count"] != 2:
            print("self-test failed: deterministic query limit was not applied", file=sys.stderr)
            return 1
        if ("evaluation_query_ids_sha256" not in query_limited_manifest["split"] or
                query_limited_manifest["outputs"]["evaluation_qrels"]["count"] != 4):
            print("self-test failed: query-limited qrels provenance", file=sys.stderr)
            return 1

        invalid = json.loads(config_path.read_text(encoding="utf-8"))
        invalid["sampling"]["train_documents_per_language"] = 4
        invalid_path = root / "invalid.json"
        invalid_path.write_text(json.dumps(invalid), encoding="utf-8", newline="\n")
        try:
            prepare_study(load_config(invalid_path), root / "input", root / "invalid-output")
        except PreparationError:
            pass
        else:
            print("self-test failed: insufficient corpus was accepted", file=sys.stderr)
            return 1

    print("MIRACL AE preparer self-test ok")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        if args.config or args.input_root or args.output_root:
            parser.error("--self-test cannot be combined with preparation arguments")
        return run_self_test()
    if not args.config or not args.input_root or not args.output_root:
        parser.error("--config, --input-root, and --output-root are required")

    try:
        manifest = prepare_study(load_config(args.config), args.input_root, args.output_root)
    except PreparationError as exc:
        print(f"prepare-miracl-ae-study: {exc}", file=sys.stderr)
        return 1
    print(
        f"prepared {manifest['outputs']['train_documents']['count']} train and "
        f"{manifest['outputs']['evaluation_documents']['count']} evaluation documents"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
