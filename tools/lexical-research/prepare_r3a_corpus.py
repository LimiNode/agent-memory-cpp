#!/usr/bin/env python3
"""Prepare a deterministic, provenance-bound R3A corpus from this repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path


ALLOWED_EXTENSIONS = frozenset({
    ".cpp", ".h", ".hpp", ".json", ".md", ".py", ".tsv", ".txt", ".yml", ".yaml"
})
ROOTS = ("guides", "src", "tests", "tools")
EXCLUDED_PARTS = frozenset({".git", "external", "build", "build-r2", "build-mdbx-benchmark"})
EXCLUDED_PATHS = frozenset({
    "guides/experiments/2026-10-31-lexical-retrieval-r3a-acquisition.md",
    "tools/lexical-research/fixtures/lexical_retrieval_r3a_manifest.json",
    "tools/lexical-research/prepare_r3a_corpus.py",
})
MAX_BYTES = 1024 * 1024


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _iter_documents(root: Path) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    documents: list[dict[str, object]] = []
    excluded: list[dict[str, str]] = []
    for source_root in ROOTS:
        directory = root / source_root
        if not directory.is_dir():
            excluded.append({"path": source_root, "reason": "missing_root"})
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative in EXCLUDED_PATHS:
                excluded.append({"path": relative, "reason": "acquisition_metadata"})
                continue
            directory_parts = path.relative_to(root).parts[:-1]
            if any(part in EXCLUDED_PARTS or part.startswith("build") for part in directory_parts):
                excluded.append({"path": relative, "reason": "excluded_path"})
                continue
            if path.suffix.lower() not in ALLOWED_EXTENSIONS:
                excluded.append({"path": relative, "reason": "extension"})
                continue
            payload = path.read_bytes()
            if not payload:
                excluded.append({"path": relative, "reason": "empty"})
                continue
            if len(payload) > MAX_BYTES:
                excluded.append({"path": relative, "reason": "size_limit"})
                continue
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError:
                excluded.append({"path": relative, "reason": "not_utf8"})
                continue
            normalized = text.replace("\r\n", "\n").replace("\r", "\n")
            canonical = normalized.encode("utf-8")
            documents.append({
                "id": "r3a:" + relative,
                "path": relative,
                "sha256": _sha256(canonical),
                "bytes": len(canonical),
                "text": normalized,
            })
    documents.sort(key=lambda row: str(row["path"]))
    return documents, excluded


def prepare(root: Path, output: Path, source_commit: str) -> dict[str, object]:
    documents, excluded = _iter_documents(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    corpus_path = output.with_suffix(".jsonl")
    with corpus_path.open("w", encoding="utf-8", newline="\n") as stream:
        for document in documents:
            stream.write(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            stream.write("\n")
    digest_payload = "\n".join(
        f"{document['id']}\t{document['sha256']}\t{document['bytes']}" for document in documents
    ).encode("utf-8")
    manifest = {
        "schema_version": 1,
        "protocol": "lexical_retrieval_r3a_acquisition",
        "source": {
            "repository": "LimiNode/agent-memory-cpp",
            "license": "MIT",
            "commit": source_commit,
            "roots": list(ROOTS),
        },
        "selection": {
            "allowed_extensions": sorted(ALLOWED_EXTENSIONS),
            "max_document_bytes": MAX_BYTES,
            "excluded_path_parts": sorted(EXCLUDED_PARTS),
            "uses_qrels_for_selection": False,
        },
        "corpus": {
            "document_count": len(documents),
            "excluded_count": len(excluded),
            "digest_algorithm": "sha256(path/id/digest/bytes-v1)",
            "corpus_digest": _sha256(digest_payload),
            "documents_file": corpus_path.name,
        },
        "judgments": {
            "status": "JUDGMENT_READINESS_ONLY",
            "qrels_present": False,
            "planned_query_count": 60,
            "planned_judgment_pool_per_query": 20,
            "independent_judges_per_pair": 2,
            "adjudication_on_disagreement": True,
        },
        "excluded": excluded,
    }
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="r3a-corpus-") as directory:
        root = Path(directory)
        (root / "guides").mkdir()
        (root / "guides" / "example.md").write_text("Русский\r\nEnglish\n", encoding="utf-8")
        output = root / "manifest.json"
        manifest = prepare(root, output, "self-test")
        if manifest["corpus"]["document_count"] != 1:
            raise AssertionError("R3A self-test document count mismatch")
        if manifest["judgments"]["qrels_present"]:
            raise AssertionError("R3A acquisition must not invent qrels")
        if not output.with_suffix(".jsonl").read_text(encoding="utf-8").strip():
            raise AssertionError("R3A self-test corpus is empty")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--source-commit",
        default="c71e275fd876f64f6dfb50dd76ed284b42fce9b1",
        help="commit pinned by the acquisition manifest",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print('{"status":"PASS","protocol":"lexical_retrieval_r3a_acquisition"}')
        return 0
    if args.output is None:
        parser.error("--output is required unless --self-test is used")
    manifest = prepare(args.root.resolve(), args.output.resolve(), args.source_commit)
    print(json.dumps({"status": "PASS", **manifest["corpus"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
