#!/usr/bin/env python3
"""Prepare and verify a deterministic, provenance-bound R3A corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable


ALLOWED_EXTENSIONS = frozenset({
    ".cpp", ".h", ".hpp", ".json", ".md", ".py", ".tsv", ".txt", ".yml", ".yaml"
})
ROOTS = ("guides", "src", "tests", "tools")
EXCLUDED_PARTS = frozenset({".git", "external", "build", "build-r2", "build-mdbx-benchmark"})
EXCLUDED_PATHS = frozenset({
    "guides/experiments/2026-10-31-lexical-retrieval-r3a-acquisition.md",
    "tools/lexical-research/fixtures/lexical_retrieval_r3a_manifest.json",
    "tools/lexical-research/fixtures/lexical_retrieval_r3a_documents.json",
    "tools/lexical-research/prepare_r3a_corpus.py",
})
MAX_BYTES = 1024 * 1024
REPOSITORY_ID = "LimiNode/agent-memory-cpp"


class AcquisitionError(RuntimeError):
    """Raised for an invalid or unverifiable acquisition input."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _git(root: Path, *args: str, check: bool = True) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=False
    )
    if check and result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise AcquisitionError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def _resolved_commit(root: Path, requested: str) -> str:
    resolved = _git(root, "rev-parse", "--verify", f"{requested}^{{commit}}").decode().strip()
    if not resolved:
        raise AcquisitionError(f"source commit does not resolve: {requested}")
    return resolved


def _repository_identity(root: Path) -> str:
    remotes = _git(root, "remote", "-v").decode("utf-8", errors="replace")
    if not any("github.com/LimiNode/agent-memory-cpp" in line for line in remotes.splitlines()):
        raise AcquisitionError(
            f"repository identity is not {REPOSITORY_ID}; no matching GitHub remote"
        )
    return REPOSITORY_ID


def _tree_entries(root: Path, commit: str) -> list[tuple[str, str, str, str]]:
    raw = _git(root, "ls-tree", "-r", "-z", commit, "--", *ROOTS)
    entries: list[tuple[str, str, str, str]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        header, path_bytes = record.split(b"\t", 1)
        mode, kind, object_id = header.decode("ascii").split()
        entries.append((mode, kind, object_id, path_bytes.decode("utf-8")))
    return entries


def _selection_reason(path: str, mode: str, kind: str) -> str | None:
    if mode == "120000":
        return "symlink"
    if kind != "blob":
        return "non_blob"
    if path in EXCLUDED_PATHS:
        return "acquisition_metadata"
    parts = Path(path).parts
    if any(part in EXCLUDED_PARTS or part.startswith("build") for part in parts[:-1]):
        return "excluded_path"
    if Path(path).suffix.lower() not in ALLOWED_EXTENSIONS:
        return "extension"
    return None


def _documents_from_git(root: Path, commit: str) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    entries = _tree_entries(root, commit)
    present_roots = {path.split("/", 1)[0] for _, _, _, path in entries}
    missing = [name for name in ROOTS if name not in present_roots]
    if missing:
        raise AcquisitionError(f"source commit is missing required roots: {', '.join(missing)}")

    documents: list[dict[str, object]] = []
    excluded: list[dict[str, str]] = []
    selected: list[tuple[str, str, str, str]] = []
    for mode, kind, object_id, relative in sorted(entries, key=lambda item: item[3]):
        reason = _selection_reason(relative, mode, kind)
        if reason:
            excluded.append({"path": relative, "reason": reason})
            continue
        selected.append((mode, kind, object_id, relative))

    batch = subprocess.Popen(
        ["git", "-C", str(root), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert batch.stdin is not None and batch.stdout is not None
    payloads: list[bytes] = []
    for _, _, object_id, relative in selected:
        batch.stdin.write(f"{object_id}\n".encode("ascii"))
        batch.stdin.flush()
        header = batch.stdout.readline().decode("ascii", errors="replace").strip().split()
        if len(header) != 3 or header[0] != object_id or header[1] != "blob":
            batch.kill()
            raise AcquisitionError(f"cannot read Git blob for {relative}")
        size = int(header[2])
        payloads.append(batch.stdout.read(size))
        if batch.stdout.read(1) != b"\n":
            batch.kill()
            raise AcquisitionError(f"malformed Git blob stream for {relative}")
    batch.stdin.close()
    if batch.wait() != 0:
        detail = (batch.stderr.read() if batch.stderr else b"").decode("utf-8", errors="replace")
        raise AcquisitionError(f"git cat-file failed: {detail.strip()}")

    for (_, _, _, relative), payload in zip(selected, payloads):
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
    return documents, excluded


def _digest(documents: Iterable[dict[str, object]]) -> str:
    payload = "\n".join(
        f"{document['id']}\t{document['sha256']}\t{document['bytes']}" for document in documents
    ).encode("utf-8")
    return _sha256(payload)


def _index(documents: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {key: document[key] for key in ("id", "path", "sha256", "bytes")}
        for document in documents
    ]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def prepare(root: Path, output: Path, source_commit: str) -> dict[str, object]:
    resolved = _resolved_commit(root, source_commit)
    repository = _repository_identity(root)
    documents, excluded = _documents_from_git(root, resolved)
    output.parent.mkdir(parents=True, exist_ok=True)
    corpus_path = output.with_suffix(".jsonl")
    index_path = output.with_name(output.stem + ".documents.json")
    with corpus_path.open("w", encoding="utf-8", newline="\n") as stream:
        for document in documents:
            stream.write(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            stream.write("\n")
    frozen_index = _index(documents)
    _write_json(index_path, frozen_index)
    manifest = {
        "schema_version": 2,
        "protocol": "lexical_retrieval_r3a_acquisition",
        "source": {
            "repository": repository,
            "license": "MIT",
            "commit": resolved,
            "roots": list(ROOTS),
        },
        "selection": {
            "allowed_extensions": sorted(ALLOWED_EXTENSIONS),
            "max_document_bytes": MAX_BYTES,
            "excluded_path_parts": sorted(EXCLUDED_PARTS),
            "excluded_paths": sorted(EXCLUDED_PATHS),
            "uses_qrels_for_selection": False,
            "line_endings": "CRLF/CR normalized to LF before hashing",
            "source_mode": "pinned_git_tree",
        },
        "corpus": {
            "document_count": len(documents),
            "excluded_count": len(excluded),
            "digest_algorithm": "sha256(path/id/digest/bytes-v1)",
            "corpus_digest": _digest(documents),
            "documents_file": corpus_path.name,
            "documents_index": index_path.name,
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
    _write_json(output, manifest)
    return manifest


def verify(root: Path, manifest_path: Path) -> dict[str, object]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AcquisitionError(f"cannot read frozen manifest: {error}") from error
    source = manifest.get("source", {})
    selection = manifest.get("selection", {})
    corpus = manifest.get("corpus", {})
    if manifest.get("schema_version") != 2 or manifest.get("protocol") != "lexical_retrieval_r3a_acquisition":
        raise AcquisitionError("frozen manifest schema/protocol mismatch")
    if source.get("repository") != REPOSITORY_ID:
        raise AcquisitionError("frozen manifest repository identity mismatch")
    if tuple(source.get("roots", ())) != ROOTS:
        raise AcquisitionError("frozen manifest roots mismatch")
    if selection.get("source_mode") != "pinned_git_tree":
        raise AcquisitionError("frozen manifest is not bound to a Git tree")
    if selection.get("allowed_extensions") != sorted(ALLOWED_EXTENSIONS):
        raise AcquisitionError("frozen manifest extension rules mismatch")
    if selection.get("max_document_bytes") != MAX_BYTES:
        raise AcquisitionError("frozen manifest size rule mismatch")
    if selection.get("excluded_path_parts") != sorted(EXCLUDED_PARTS):
        raise AcquisitionError("frozen manifest excluded-path rules mismatch")
    if selection.get("excluded_paths") != sorted(EXCLUDED_PATHS):
        raise AcquisitionError("frozen manifest excluded-file rules mismatch")
    if selection.get("uses_qrels_for_selection") is not False:
        raise AcquisitionError("frozen manifest qrels-selection rule mismatch")
    if selection.get("line_endings") != "CRLF/CR normalized to LF before hashing":
        raise AcquisitionError("frozen manifest line-ending rule mismatch")
    resolved = _resolved_commit(root, str(source.get("commit", "")))
    if resolved != source.get("commit"):
        raise AcquisitionError("frozen manifest source commit is not exact")
    if _repository_identity(root) != source.get("repository"):
        raise AcquisitionError("repository identity mismatch")
    documents, excluded = _documents_from_git(root, resolved)
    if len(documents) != corpus.get("document_count"):
        raise AcquisitionError("frozen manifest document count mismatch")
    if len(excluded) != corpus.get("excluded_count"):
        raise AcquisitionError("frozen manifest excluded count mismatch")
    if _digest(documents) != corpus.get("corpus_digest"):
        raise AcquisitionError("frozen manifest corpus digest mismatch")
    index_name = corpus.get("documents_index")
    if not isinstance(index_name, str):
        raise AcquisitionError("frozen manifest has no document index")
    index_path = manifest_path.parent / index_name
    try:
        frozen_index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AcquisitionError(f"cannot read frozen document index: {error}") from error
    if frozen_index != _index(documents):
        raise AcquisitionError("frozen per-document index mismatch")
    return manifest


def _run_git_test(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="r3a-corpus-") as directory:
        root = Path(directory)
        for name in ROOTS:
            (root / name).mkdir()
        (root / "guides" / "example.md").write_bytes("Русский\r\nEnglish\n".encode())
        (root / "src" / "empty.txt").write_bytes(b"")
        (root / "tests" / "bad.txt").write_bytes(b"bad\xff")
        (root / "tools" / "large.txt").write_bytes(b"x" * (MAX_BYTES + 1))
        (root / "tools" / "generated.json").write_text("generated", encoding="utf-8")
        _run_git_test(root, "init", "-q")
        _run_git_test(root, "config", "user.email", "r3a@example.invalid")
        _run_git_test(root, "config", "user.name", "R3A self-test")
        _run_git_test(root, "remote", "add", "liminode", "https://github.com/LimiNode/agent-memory-cpp.git")
        _run_git_test(root, "add", ".")
        _run_git_test(root, "commit", "-qm", "fixture")
        link_id = subprocess.run(
            ["git", "-C", str(root), "hash-object", "-w", "--stdin"],
            input=b"../../outside",
            capture_output=True,
            check=True,
        ).stdout.decode("ascii").strip()
        _run_git_test(root, "update-index", "--add", "--cacheinfo", f"120000,{link_id},tools/escape.txt")
        _run_git_test(root, "commit", "-qm", "symlink fixture")
        commit = _resolved_commit(root, "HEAD")
        output = root / "manifest.json"
        first = prepare(root, output, commit)
        second = prepare(root, root / "manifest2.json", commit)
        if {
            key: first["corpus"][key] for key in ("document_count", "excluded_count", "corpus_digest")
        } != {
            key: second["corpus"][key] for key in ("document_count", "excluded_count", "corpus_digest")
        }:
            raise AssertionError("repeated acquisition is not deterministic")
        verify(root, output)
        reasons = {item["reason"] for item in first["excluded"]}
        if not {"empty", "not_utf8", "size_limit"}.issubset(reasons):
            raise AssertionError(f"missing exclusion controls: {reasons}")
        corpus_rows = [json.loads(line) for line in (root / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
        example = next(row for row in corpus_rows if row["path"] == "guides/example.md")
        if "\r" in example["text"] or example["bytes"] != len(example["text"].encode("utf-8")):
            raise AssertionError("line-ending normalization is not canonical")
        if not any(item == {"path": "tools/escape.txt", "reason": "symlink"} for item in first["excluded"]):
            raise AssertionError("Git symlink entries are not rejected")
        mutated = json.loads(output.read_text(encoding="utf-8"))
        mutated["corpus"]["corpus_digest"] = "0" * 64
        _write_json(root / "bad.json", mutated)
        try:
            verify(root, root / "bad.json")
        except AcquisitionError:
            pass
        else:
            raise AssertionError("digest mutation was accepted")
        mutated["corpus"]["document_count"] += 1
        _write_json(root / "bad-count.json", mutated)
        try:
            verify(root, root / "bad-count.json")
        except AcquisitionError:
            pass
        else:
            raise AssertionError("count mutation was accepted")
        frozen_index = json.loads((root / "manifest.documents.json").read_text(encoding="utf-8"))
        frozen_index[0]["sha256"] = "0" * 64
        _write_json(root / "manifest.documents.json", frozen_index)
        try:
            verify(root, output)
        except AcquisitionError:
            pass
        else:
            raise AssertionError("per-document mutation was accepted")
        prepare(root, output, commit)
        try:
            prepare(root, root / "wrong.json", "0" * 40)
        except AcquisitionError:
            pass
        else:
            raise AssertionError("source commit mismatch was accepted")
        (root / "generated.json").write_text("outside roots", encoding="utf-8")
        (root / "guides" / "example.md").write_text("working tree mutation", encoding="utf-8")
        verify(root, output)  # untracked/generated files cannot affect a pinned tree
        if first["judgments"]["qrels_present"]:
            raise AssertionError("R3A acquisition must not invent qrels")

        incomplete = root / "incomplete"
        (incomplete / "guides").mkdir(parents=True)
        (incomplete / "guides" / "only.md").write_text("one", encoding="utf-8")
        _run_git_test(incomplete, "init", "-q")
        _run_git_test(incomplete, "config", "user.email", "r3a@example.invalid")
        _run_git_test(incomplete, "config", "user.name", "R3A self-test")
        _run_git_test(incomplete, "remote", "add", "liminode", "https://github.com/LimiNode/agent-memory-cpp.git")
        _run_git_test(incomplete, "add", ".")
        _run_git_test(incomplete, "commit", "-qm", "incomplete")
        try:
            prepare(incomplete, incomplete / "manifest.json", "HEAD")
        except AcquisitionError:
            pass
        else:
            raise AssertionError("missing required roots were accepted")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--source-commit", default="c71e275fd876f64f6dfb50dd76ed284b42fce9b1")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            print('{"status":"PASS","protocol":"lexical_retrieval_r3a_acquisition"}')
            return 0
        if args.verify:
            manifest_path = args.manifest or args.output
            if manifest_path is None:
                parser.error("--manifest is required with --verify")
            verify(args.root.resolve(), manifest_path.resolve())
            print('{"status":"PASS","verification":"frozen_manifest"}')
            return 0
        if args.output is None:
            parser.error("--output is required unless --self-test or --verify is used")
        manifest = prepare(args.root.resolve(), args.output.resolve(), args.source_commit)
        print(json.dumps({"status": "PASS", **manifest["corpus"]}, sort_keys=True))
        return 0
    except AcquisitionError as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
