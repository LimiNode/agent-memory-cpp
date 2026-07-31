#!/usr/bin/env python3
"""Validate a prepared MIRACL autoencoder-study split and its provenance manifest.

The validator is deliberately dependency-free.  It verifies the output hashes,
split disjointness, qrels closure, and (when the original config and source root
are supplied) the configuration hash and hashes of the materialized source
files.  It does not download MIRACL and is therefore suitable for CTest.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator

sys.dont_write_bytecode = True


class ValidationError(ValueError):
    """Raised when a prepared study violates its content contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{field} must be an object")
    return value


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field} must be a non-empty string")
    return value


def require_non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(f"{field} must be a non-negative integer")
    return value


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        return require_mapping(json.loads(path.read_text(encoding="utf-8")), label)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot read {label}: {exc}") from exc


def normalized_input_config(config: dict[str, Any]) -> dict[str, Any]:
    """Adds explicit defaults before comparing a manifest with legacy input."""
    sampling = dict(require_mapping(config.get("sampling"), "config.sampling"))
    sampling.setdefault("evaluation_queries_per_language", 0)
    return {**config, "sampling": sampling}


def resolve_output_path(root: Path, raw_path: Any, label: str) -> Path:
    relative = Path(require_string(raw_path, label))
    if relative.is_absolute() or relative.name != str(relative):
        raise ValidationError(f"{label} must be a plain file name")
    path = root / relative
    if not path.is_file():
        raise ValidationError(f"missing output file: {path}")
    return path


def read_document_ids(path: Path, label: str) -> tuple[set[str], dict[str, int]]:
    identifiers: set[str] = set()
    language_counts: dict[str, int] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                row = require_mapping(json.loads(line), f"{label}:{line_number}")
            except json.JSONDecodeError as exc:
                raise ValidationError(f"{label}:{line_number}: invalid JSON: {exc.msg}") from exc
            identifier = require_string(row.get("id"), f"{label}:{line_number}: id")
            language = require_string(row.get("language"), f"{label}:{line_number}: language")
            if not identifier.startswith(f"{language}:"):
                raise ValidationError(f"{label}:{line_number}: id does not carry its language prefix")
            if identifier in identifiers:
                raise ValidationError(f"{label}:{line_number}: duplicate document id {identifier}")
            identifiers.add(identifier)
            language_counts[language] = language_counts.get(language, 0) + 1
    return identifiers, language_counts


def read_query_ids(path: Path) -> set[str]:
    identifiers: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, line in enumerate(handle, start=1):
            fields = line.rstrip("\r\n").split("\t", 1)
            if len(fields) != 2 or not fields[0] or not fields[1]:
                raise ValidationError(f"queries:{line_number}: expected id<TAB>text")
            if fields[0] in identifiers:
                raise ValidationError(f"queries:{line_number}: duplicate query id {fields[0]}")
            identifiers.add(fields[0])
    return identifiers


def read_qrels(path: Path, query_ids: set[str], evaluation_ids: set[str]) -> None:
    positive_queries: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, line in enumerate(handle, start=1):
            fields = line.split()
            if len(fields) != 4:
                raise ValidationError(f"qrels:{line_number}: expected four fields")
            query_id, iteration, document_id, raw_grade = fields
            if iteration != "Q0" or query_id not in query_ids or document_id not in evaluation_ids:
                raise ValidationError(f"qrels:{line_number}: query or document is not in the evaluation split")
            try:
                grade = int(raw_grade)
            except ValueError as exc:
                raise ValidationError(f"qrels:{line_number}: grade must be an integer") from exc
            if grade > 0:
                positive_queries.add(query_id)
    missing = query_ids - positive_queries
    if missing:
        raise ValidationError("every evaluation query must have a positive qrel")


def open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def resolve_source_paths(
    root: Path,
    template: str,
    language: str,
    *,
    allow_glob: bool,
) -> list[Path]:
    try:
        relative = Path(template.format(language=language))
    except KeyError as exc:
        raise ValidationError(f"source layout uses unsupported placeholder: {exc}") from exc
    if relative.is_absolute():
        raise ValidationError("source layout paths must be relative")
    if ".." in relative.parts:
        raise ValidationError("source layout paths must not contain parent traversal")
    resolved_root = root.resolve()
    candidate = resolved_root / relative
    if not allow_glob and any(character in relative.name for character in "*?["):
        raise ValidationError("this source layout path must not contain a glob")
    paths = sorted(candidate.parent.glob(candidate.name)) if allow_glob else [candidate]
    if not paths:
        raise ValidationError(f"missing source file: {candidate}")
    resolved_paths: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        try:
            resolved.relative_to(resolved_root)
        except ValueError as exc:
            raise ValidationError("source layout path escapes source root") from exc
        if not resolved.is_file():
            raise ValidationError(f"missing source file: {resolved}")
        resolved_paths.append(resolved)
    return resolved_paths


def source_file_hashes(root: Path, paths: list[Path]) -> list[dict[str, str]]:
    resolved_root = root.resolve()
    return [
        {"path": path.resolve().relative_to(resolved_root).as_posix(), "sha256": sha256_file(path)}
        for path in paths
    ]


def validate_source_provenance(
    manifest: dict[str, Any],
    config_path: Path,
    source_root: Path,
    actual_train_ids: set[str],
    actual_evaluation_ids: set[str],
    actual_query_ids: set[str],
) -> None:
    config = normalized_input_config(load_json(config_path, "input config"))
    input_hash = hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest()
    if manifest.get("input_config_hash") != input_hash:
        raise ValidationError("input_config_hash does not match the supplied config")
    if manifest.get("dataset") != config.get("dataset"):
        raise ValidationError("manifest.dataset does not match the supplied config")
    if manifest.get("languages") != config.get("languages"):
        raise ValidationError("manifest.languages does not match the supplied config")
    if manifest.get("sampling") != config.get("sampling"):
        raise ValidationError("manifest.sampling does not match the supplied config")
    if manifest.get("embedding") != config.get("embedding"):
        raise ValidationError("manifest.embedding does not match the supplied config")
    config_split = require_mapping(config.get("split"), "config.split")
    manifest_split = require_mapping(manifest.get("split"), "manifest.split")
    if manifest_split.get("evaluation_qrels_split") != config_split.get("evaluation_qrels_split"):
        raise ValidationError("manifest evaluation_qrels_split does not match the supplied config")

    preparer = load_preparer_module()
    preparer_info = require_mapping(manifest.get("preparer"), "manifest.preparer")
    if preparer_info.get("id") != preparer.PREPARER_ID or preparer_info.get("version") != preparer.PREPARER_VERSION:
        raise ValidationError("manifest preparer identity does not match the local preparer")
    if preparer_info.get("source_hash") != preparer.sha256_file(Path(preparer.__file__)):
        raise ValidationError("manifest preparer source_hash does not match the local preparer")

    layout = require_mapping(config.get("layout"), "config.layout")
    per_language = manifest.get("per_language")
    if not isinstance(per_language, list):
        raise ValidationError("manifest.per_language must be an array")
    expected_train_ids: set[str] = set()
    expected_evaluation_ids: set[str] = set()
    expected_qrels_excluded_ids: set[str] = set()
    expected_query_ids: set[str] = set()
    for row in per_language:
        record = require_mapping(row, "manifest.per_language entry")
        language = require_string(record.get("language"), "manifest language")
        hashes = require_mapping(record.get("source_hashes"), "manifest source_hashes")
        corpus_paths = resolve_source_paths(
            source_root,
            require_string(layout.get("corpus"), "config.layout.corpus"),
            language,
            allow_glob=True,
        )
        if hashes.get("corpus") != source_file_hashes(source_root, corpus_paths):
            raise ValidationError(f"source hash mismatch for {language}:corpus")
        for kind in ("queries", "qrels"):
            path = resolve_source_paths(
                source_root,
                require_string(layout.get(kind), f"config.layout.{kind}"),
                language,
                allow_glob=False,
            )[0]
            if hashes.get(kind) != sha256_file(path):
                raise ValidationError(f"source hash mismatch for {language}:{kind}")

        all_queries = preparer.load_queries(resolve_source_paths(
            source_root,
            require_string(layout.get("queries"), "config.layout.queries"),
            language,
            allow_glob=False,
        )[0])
        all_qrels, _ = preparer.load_qrels(resolve_source_paths(
            source_root,
            require_string(layout.get("qrels"), "config.layout.qrels"),
            language,
            allow_glob=False,
        )[0], set(all_queries))
        queries = preparer.choose_evaluation_queries(
            all_queries,
            limit=config["sampling"].get("evaluation_queries_per_language", 0),
            seed=config["sampling"]["seed"],
            language=language,
        )
        qrels = [row for row in all_qrels if row[0] in queries]
        qrel_document_ids = {document_id for _, document_id, _ in qrels}
        documents_by_id = {
            document.docid: document
            for document in preparer.iter_corpora(corpus_paths, language)
            if document.docid in qrel_document_ids
        }
        if set(documents_by_id) != qrel_document_ids:
            raise ValidationError(f"source corpus does not close qrels for {language}")
        selected_train = preparer.choose_lowest_ranked(
            (document for document in preparer.iter_corpora(corpus_paths, language) if document.docid not in qrel_document_ids),
            limit=config["sampling"]["train_documents_per_language"],
            seed=config["sampling"]["seed"],
            language=language,
            purpose="train",
        )
        selected_train_ids = {document.docid for document in selected_train}
        selected_distractors = preparer.choose_lowest_ranked(
            (
                document
                for document in preparer.iter_corpora(corpus_paths, language)
                if document.docid not in qrel_document_ids and document.docid not in selected_train_ids
            ),
            limit=config["sampling"]["evaluation_distractors_per_language"],
            seed=config["sampling"]["seed"],
            language=language,
            purpose="evaluation-distractor",
        )
        expected_train_ids.update(document.global_id for document in selected_train)
        expected_evaluation_ids.update(documents_by_id[document_id].global_id for document_id in qrel_document_ids)
        expected_evaluation_ids.update(document.global_id for document in selected_distractors)
        expected_qrels_excluded_ids.update(f"{language}:{document_id}" for document_id in qrel_document_ids)
        expected_query_ids.update(f"{language}:{query_id}" for query_id in queries)

    if actual_train_ids != expected_train_ids:
        raise ValidationError("train document IDs do not match balanced_stable_hash selection")
    if actual_evaluation_ids != expected_evaluation_ids:
        raise ValidationError("evaluation document IDs do not match balanced_stable_hash selection")
    if actual_query_ids != expected_query_ids:
        raise ValidationError("evaluation query IDs do not match deterministic selection")
    if manifest_split.get("qrels_excluded_document_ids_sha256") != preparer.sorted_id_set_sha256(expected_qrels_excluded_ids):
        raise ValidationError("qrels excluded document ID digest is invalid")
    if manifest_split.get("evaluation_query_ids_sha256") != preparer.sorted_id_set_sha256(expected_query_ids):
        raise ValidationError("evaluation query ID digest is invalid")
    if manifest_split.get("evaluation_document_ids_sha256") != preparer.sorted_id_set_sha256(expected_evaluation_ids):
        raise ValidationError("evaluation document ID digest is invalid")


def validate_prepared_study(
    prepared_root: Path,
    *,
    config_path: Path | None = None,
    source_root: Path | None = None,
) -> None:
    manifest_path = prepared_root / "manifest.json"
    manifest = load_json(manifest_path, "manifest")
    if manifest.get("schema_version") != 1:
        raise ValidationError("manifest.schema_version must equal 1")
    dataset = require_mapping(manifest.get("dataset"), "manifest.dataset")
    for source_name in ("corpus", "judgments"):
        source = require_mapping(dataset.get(source_name), f"manifest.dataset.{source_name}")
        require_string(source.get("id"), f"manifest.dataset.{source_name}.id")
        require_string(source.get("revision"), f"manifest.dataset.{source_name}.revision")
    preparer = require_mapping(manifest.get("preparer"), "manifest.preparer")
    require_string(preparer.get("id"), "manifest.preparer.id")
    require_string(preparer.get("version"), "manifest.preparer.version")
    source_hash = require_string(preparer.get("source_hash"), "manifest.preparer.source_hash")
    if len(source_hash) != 64 or any(character not in "0123456789abcdef" for character in source_hash):
        raise ValidationError("manifest.preparer.source_hash must be a lowercase SHA-256 hash")
    split = require_mapping(manifest.get("split"), "manifest.split")
    require_string(split.get("policy"), "manifest.split.policy")
    require_string(split.get("evaluation_qrels_split"), "manifest.split.evaluation_qrels_split")
    for field in (
        "qrels_excluded_document_ids_sha256",
        "evaluation_document_ids_sha256",
        "evaluation_query_ids_sha256",
    ):
        digest = require_string(split.get(field), f"manifest.split.{field}")
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValidationError(f"manifest.split.{field} must be a lowercase SHA-256 hash")

    outputs = require_mapping(manifest.get("outputs"), "manifest.outputs")
    files: dict[str, Path] = {}
    for name in ("train_documents", "evaluation_documents", "evaluation_queries", "evaluation_qrels"):
        entry = require_mapping(outputs.get(name), f"manifest.outputs.{name}")
        path = resolve_output_path(prepared_root, entry.get("path"), f"manifest.outputs.{name}.path")
        if entry.get("sha256") != sha256_file(path):
            raise ValidationError(f"output hash mismatch: {name}")
        files[name] = path

    train_ids, train_language_counts = read_document_ids(files["train_documents"], "train documents")
    evaluation_ids, evaluation_language_counts = read_document_ids(
        files["evaluation_documents"], "evaluation documents"
    )
    if train_ids & evaluation_ids:
        raise ValidationError("training and evaluation document IDs overlap")
    query_ids = read_query_ids(files["evaluation_queries"])
    read_qrels(files["evaluation_qrels"], query_ids, evaluation_ids)
    actual_query_digest = hashlib.sha256(
        ("\n".join(sorted(query_ids)) + "\n").encode("utf-8")
    ).hexdigest()
    if split.get("evaluation_query_ids_sha256") != actual_query_digest:
        raise ValidationError("evaluation query ID digest does not match output")

    counts = {
        "train_documents": len(train_ids),
        "evaluation_documents": len(evaluation_ids),
        "evaluation_queries": len(query_ids),
        "evaluation_qrels": sum(1 for _ in files["evaluation_qrels"].open("r", encoding="utf-8")),
    }
    for name, count in counts.items():
        entry = require_mapping(outputs.get(name), f"manifest.outputs.{name}")
        if require_non_negative_int(entry.get("count"), f"manifest.outputs.{name}.count") != count:
            raise ValidationError(f"output count mismatch: {name}")

    languages = manifest.get("languages")
    if not isinstance(languages, list) or not languages or any(not isinstance(item, str) or not item for item in languages):
        raise ValidationError("manifest.languages must be a non-empty string array")
    if len(set(languages)) != len(languages):
        raise ValidationError("manifest.languages must not contain duplicates")
    if set(train_language_counts) - set(languages) or set(evaluation_language_counts) - set(languages):
        raise ValidationError("output documents contain a language absent from manifest.languages")
    if set(train_language_counts) != set(languages) or set(evaluation_language_counts) != set(languages):
        raise ValidationError("every manifest language must appear in both output document splits")

    raw_per_language = manifest.get("per_language")
    if not isinstance(raw_per_language, list) or len(raw_per_language) != len(languages):
        raise ValidationError("manifest.per_language must contain one entry per language")
    per_language = {
        require_string(require_mapping(row, "manifest.per_language entry").get("language"), "manifest language"): row
        for row in raw_per_language
    }
    if set(per_language) != set(languages):
        raise ValidationError("manifest.per_language languages do not match manifest.languages")
    for language in languages:
        row = require_mapping(per_language[language], f"manifest.per_language.{language}")
        if require_non_negative_int(
            row.get("train_document_count"),
            f"manifest.per_language.{language}.train_document_count",
        ) != train_language_counts[language]:
            raise ValidationError(f"per-language train count mismatch: {language}")
        if require_non_negative_int(
            row.get("evaluation_document_count"),
            f"manifest.per_language.{language}.evaluation_document_count",
        ) != evaluation_language_counts[language]:
            raise ValidationError(f"per-language evaluation count mismatch: {language}")

    if (config_path is None) != (source_root is None):
        raise ValidationError("--config and --source-root must be supplied together")
    if config_path is not None and source_root is not None:
        validate_source_provenance(
            manifest,
            config_path,
            source_root,
            train_ids,
            evaluation_ids,
            query_ids,
        )


def load_preparer_module() -> Any:
    path = Path(__file__).with_name("prepare-miracl-ae-study.py")
    spec = importlib.util.spec_from_file_location("miracl_ae_preparer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load MIRACL preparer for validator self-test")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_self_test() -> int:
    preparer = load_preparer_module()
    with tempfile.TemporaryDirectory(prefix="agent-memory-miracl-validator-") as raw_root:
        root = Path(raw_root)
        input_root = root / "input"
        config_path = preparer.write_test_input(input_root)
        prepared_root = root / "prepared"
        preparer.prepare_study(preparer.load_config(config_path), input_root, prepared_root)
        validate_prepared_study(prepared_root, config_path=config_path, source_root=input_root)

        train_path = prepared_root / "train-documents.jsonl"
        train_rows = [json.loads(line) for line in train_path.read_text(encoding="utf-8").splitlines()]
        evaluation_path = prepared_root / "evaluation-documents.jsonl"
        evaluation_rows = [json.loads(line) for line in evaluation_path.read_text(encoding="utf-8").splitlines()]
        train_index = next(index for index, row in enumerate(train_rows) if row["id"].startswith("ru:"))
        evaluation_index = next(
            index
            for index, row in enumerate(evaluation_rows)
            if row["id"].startswith("ru:ru-") and row["source_id"] not in {"ru-1", "ru-2"}
        )
        train_rows[train_index], evaluation_rows[evaluation_index] = (
            evaluation_rows[evaluation_index],
            train_rows[train_index],
        )
        train_path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in train_rows),
            encoding="utf-8",
            newline="\n",
        )
        evaluation_path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in evaluation_rows),
            encoding="utf-8",
            newline="\n",
        )
        manifest_path = prepared_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["outputs"]["train_documents"]["sha256"] = sha256_file(train_path)
        manifest["outputs"]["evaluation_documents"]["sha256"] = sha256_file(evaluation_path)
        manifest["split"]["evaluation_document_ids_sha256"] = preparer.sorted_id_set_sha256(
            row["id"] for row in evaluation_rows
        )
        manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        try:
            validate_prepared_study(prepared_root, config_path=config_path, source_root=input_root)
        except ValidationError:
            pass
        else:
            print("self-test failed: alternate balanced split was accepted", file=sys.stderr)
            return 1

    print("MIRACL AE manifest validator self-test ok")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        if args.prepared_root or args.config or args.source_root:
            parser.error("--self-test cannot be combined with validation arguments")
        return run_self_test()
    if args.prepared_root is None:
        parser.error("--prepared-root is required")

    try:
        validate_prepared_study(
            args.prepared_root,
            config_path=args.config,
            source_root=args.source_root,
        )
    except ValidationError as exc:
        print(f"validate-miracl-ae-study: {exc}", file=sys.stderr)
        return 1
    print("MIRACL AE study manifest is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
