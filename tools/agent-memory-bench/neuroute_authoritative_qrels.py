#!/usr/bin/env python3
"""Validate the authoritative E5 identity and qrels payload chain."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable


REQUIRED_OUTPUTS = (
    "evaluation_document_ids",
    "evaluation_query_ids",
    "evaluation_qrels",
    "prepared_study_manifest",
)


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def plain_relative_path(value: Any, field: str) -> Path:
    require(isinstance(value, str) and value and "\\" not in value,
            f"authoritative root path differs: {field}")
    path = Path(value)
    require(not path.is_absolute() and ".." not in path.parts,
            f"authoritative root path escapes root: {field}")
    return path


def output_receipt(root: Path, outputs: dict[str, Any], name: str) -> dict[str, Any]:
    entry = outputs.get(name)
    require(isinstance(entry, dict), f"authoritative output is absent: {name}")
    path = root / plain_relative_path(entry.get("path"), f"outputs.{name}.path")
    expected = entry.get("sha256")
    require(isinstance(expected, str) and len(expected) == 64,
            f"authoritative output SHA differs: {name}")
    require(path.is_file() and sha256(path) == expected,
            f"authoritative output bytes differ: {name}")
    count = entry.get("count")
    require(isinstance(count, int) and count > 0,
            f"authoritative output count differs: {name}")
    return {"path": entry["path"], "sha256": expected, "count": count}


def validate_qrels(path: Path, expected_count: int) -> tuple[int, int]:
    rows = 0
    query_ids: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            fields = line.split()
            require(len(fields) == 4,
                    f"authoritative qrels row differs at line {line_number}")
            query_id, iteration, document_id, grade = fields
            require(iteration in ("0", "Q0") and query_id and document_id,
                    f"authoritative qrels identity differs at line {line_number}")
            try:
                relevance = int(grade)
            except ValueError as error:
                raise ValueError(
                    f"authoritative qrels grade differs at line {line_number}") from error
            require(relevance >= 0,
                    f"authoritative qrels grade is negative at line {line_number}")
            require((query_id, document_id) not in pairs,
                    f"authoritative qrels pair is duplicated at line {line_number}")
            pairs.add((query_id, document_id))
            query_ids.add(query_id)
            rows += 1
    require(rows == expected_count, "authoritative qrels count differs")
    return rows, len(query_ids)


def validate_e5_root(label: str, root: Path) -> dict[str, Any]:
    manifest_path = root / "manifest.json"
    require(manifest_path.is_file(), f"authoritative E5 manifest is absent: {label}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("schema_version") == 1 and isinstance(manifest.get("outputs"), dict),
            f"authoritative E5 manifest identity differs: {label}")
    outputs = manifest["outputs"]
    receipts = {name: output_receipt(root, outputs, name) for name in REQUIRED_OUTPUTS}

    prepared = receipts["prepared_study_manifest"]
    require(manifest.get("prepared_study_manifest_sha256") == prepared["sha256"],
            f"authoritative prepared-manifest binding differs: {label}")
    prepared_path = root / prepared["path"]
    prepared_manifest = json.loads(prepared_path.read_text(encoding="utf-8"))
    prepared_outputs = prepared_manifest.get("outputs")
    require(isinstance(prepared_outputs, dict),
            f"authoritative prepared outputs differ: {label}")
    prepared_qrels = prepared_outputs.get("evaluation_qrels")
    require(isinstance(prepared_qrels, dict)
            and prepared_qrels.get("sha256") == receipts["evaluation_qrels"]["sha256"]
            and prepared_qrels.get("count") == receipts["evaluation_qrels"]["count"],
            f"authoritative prepared-to-E5 qrels binding differs: {label}")

    qrels_path = root / receipts["evaluation_qrels"]["path"]
    qrels_rows, qrels_queries = validate_qrels(
        qrels_path, receipts["evaluation_qrels"]["count"])
    return {
        "id": label,
        "e5_manifest_sha256": sha256(manifest_path),
        "prepared_study_manifest_sha256": prepared["sha256"],
        "outputs": receipts,
        "qrels_row_count": qrels_rows,
        "qrels_query_count": qrels_queries,
    }


def validate_roots(roots: Iterable[tuple[str, Path]]) -> list[dict[str, Any]]:
    values = list(roots)
    require(values and len({label for label, _ in values}) == len(values),
            "authoritative root labels differ")
    return [validate_e5_root(label, root) for label, root in values]


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="neuroute-authoritative-qrels-") as directory:
        root = Path(directory)
        payloads = {
            "evaluation-document-ids.jsonl": b'{"id":"d0"}\n',
            "evaluation-query-ids.jsonl": b'{"id":"q0"}\n',
            "evaluation-qrels.tsv": b"q0 0 d0 1\n",
        }
        prepared = {
            "schema_version": 1,
            "outputs": {"evaluation_qrels": {
                "path": "evaluation-qrels.tsv",
                "sha256": hashlib.sha256(payloads["evaluation-qrels.tsv"]).hexdigest(),
                "count": 1,
            }},
        }
        payloads["prepared-study-manifest.json"] = json.dumps(
            prepared, sort_keys=True).encode("utf-8")
        for name, value in payloads.items():
            (root / name).write_bytes(value)
        names = {
            "evaluation_document_ids": "evaluation-document-ids.jsonl",
            "evaluation_query_ids": "evaluation-query-ids.jsonl",
            "evaluation_qrels": "evaluation-qrels.tsv",
            "prepared_study_manifest": "prepared-study-manifest.json",
        }
        manifest = {
            "schema_version": 1,
            "outputs": {name: {"path": path, "sha256": sha256(root / path), "count": 1}
                        for name, path in names.items()},
            "prepared_study_manifest_sha256": sha256(root / "prepared-study-manifest.json"),
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        receipt = validate_e5_root("fixture", root)
        require(receipt["qrels_row_count"] == 1 and receipt["qrels_query_count"] == 1,
                "authoritative qrels self-test receipt differs")
        (root / "evaluation-qrels.tsv").write_text("q0 0 d0 0\n", encoding="utf-8")
        try:
            validate_e5_root("fixture", root)
        except ValueError:
            return
        raise ValueError("authoritative qrels tamper self-test did not fail")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", action="append", default=[], metavar="ID=PATH")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            print("NeuRoute authoritative qrels validator self-test passed")
            return 0
        roots = []
        for value in args.root:
            require("=" in value, "authoritative root argument must be ID=PATH")
            label, path = value.split("=", 1)
            require(label and path, "authoritative root argument must be ID=PATH")
            roots.append((label, Path(path)))
        print(json.dumps(validate_roots(roots), indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"neuroute_authoritative_qrels: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
