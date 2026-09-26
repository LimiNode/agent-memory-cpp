#!/usr/bin/env python3
"""Validate the recovered canonical DE-1M E5 source bundle.

The validator is deliberately read-only: it neither downloads nor copies the
1M payload.  It checks the persisted E5 manifest, exact byte sizes and the
SHA-256 values recorded by the materializer so research runners can bind their
inputs before fitting any codec.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _load_id_lines(path: Path, expected_count: int, label: str) -> list[str]:
    values: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        require(isinstance(value, dict) and isinstance(value.get("id"), str) and value["id"],
                f"{label}: every line must contain a non-empty string id")
        values.append(value["id"])
    require(len(values) == expected_count, f"{label}: expected {expected_count} IDs, got {len(values)}")
    require(len(values) == len(set(values)), f"{label}: IDs are not unique")
    return values


def _scan_norms(path: Path, count: int, dimension: int, chunk_rows: int = 100_000) -> dict[str, float]:
    vectors = np.memmap(path, mode="r", dtype="<f4", shape=(count, dimension))
    minimum = math.inf
    maximum = 0.0
    maximum_error = 0.0
    total = 0.0
    for start in range(0, count, chunk_rows):
        block = np.asarray(vectors[start:min(count, start + chunk_rows)], dtype=np.float64)
        norms = np.linalg.norm(block, axis=1)
        require(np.isfinite(norms).all(), f"non-finite vector norm in {path}")
        minimum = min(minimum, float(np.min(norms)))
        maximum = max(maximum, float(np.max(norms)))
        maximum_error = max(maximum_error, float(np.max(np.abs(norms - 1.0))))
        total += float(np.sum(norms))
    return {"count": count, "minimum": minimum, "maximum": maximum,
            "mean": total / count, "maximum_absolute_error_from_one": maximum_error}


def validate(root: Path, deep: bool = False) -> dict[str, object]:
    manifest_path = root / "manifest.json"
    require(manifest_path.is_file(), f"missing manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("schema_version") == 1, "unsupported canonical source manifest schema")
    vector_format = manifest.get("vector_format", {})
    require(vector_format == {"dimension": 384, "dtype": "float32_le", "endianness": "little"},
            "canonical vector format differs")
    embedding = manifest.get("embedding", {})
    require(embedding.get("model_id") == "intfloat/multilingual-e5-small",
            "canonical embedding model_id differs")
    require(embedding.get("model_revision") == "614241f622f53c4eeff9890bdc4f31cfecc418b3",
            "canonical embedding model_revision differs")
    require(embedding.get("document_prefix") == "passage: " and
            embedding.get("query_prefix") == "query: ",
            "canonical E5 prefixes differ")
    require(embedding.get("normalized") is True,
            "canonical E5 source must be L2-normalized")
    outputs = manifest.get("outputs", {})
    required = {
        "evaluation_document_vectors": ("evaluation-document-vectors.f32", 1_536_000_000),
        "evaluation_query_vectors": ("evaluation-query-vectors.f32", 305 * 384 * 4),
        "train_vectors": ("train-vectors.f32", 25_000 * 384 * 4),
        "evaluation_document_ids": ("evaluation-document-ids.jsonl", None),
        "evaluation_query_ids": ("evaluation-query-ids.jsonl", None),
        "evaluation_qrels": ("evaluation-qrels.tsv", None),
        "train_ids": ("train-document-ids.jsonl", None),
    }
    checks: dict[str, dict[str, object]] = {}
    for key, (filename, expected_bytes) in required.items():
        entry = outputs.get(key)
        require(isinstance(entry, dict), f"manifest missing output entry: {key}")
        path = root / filename
        require(path.is_file(), f"missing source file: {path}")
        require(entry.get("path") == filename, f"{key}: manifest path differs")
        actual_bytes = path.stat().st_size
        if expected_bytes is not None:
            require(actual_bytes == expected_bytes, f"{key}: expected {expected_bytes} bytes, got {actual_bytes}")
        require(actual_bytes == int(entry["bytes"]) if "bytes" in entry else True,
                f"{key}: manifest byte count differs")
        actual_sha = sha256(path)
        require(actual_sha == entry["sha256"], f"{key}: SHA-256 differs from manifest")
        checks[key] = {"path": str(path.resolve()), "bytes": actual_bytes, "sha256": actual_sha}
    require(outputs["evaluation_document_vectors"]["count"] == 1_000_000,
            "canonical document count differs")
    require(outputs["evaluation_query_vectors"]["count"] == 305,
            "canonical query count differs")
    require(outputs["train_vectors"]["count"] == 25_000,
            "canonical train count differs")
    query_ids = _load_id_lines(root / "evaluation-query-ids.jsonl", 305, "evaluation query IDs")
    document_ids = _load_id_lines(root / "evaluation-document-ids.jsonl", 1_000_000, "evaluation document IDs")
    _load_id_lines(root / "train-document-ids.jsonl", 25_000, "train IDs")
    query_set, document_set = set(query_ids), set(document_ids)
    qrels_path = root / "evaluation-qrels.tsv"
    qrel_pairs: set[tuple[str, str]] = set()
    qrel_count = 0
    for line in qrels_path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        require(len(fields) == 4 and fields[1] == "Q0", f"invalid qrels row: {line!r}")
        query_id, document_id = fields[0], fields[2]
        grade = float(fields[3])
        require(query_id in query_set, f"qrels query ID is unknown: {query_id}")
        require(document_id in document_set, f"qrels document ID is unknown: {document_id}")
        require(math.isfinite(grade) and grade >= 0.0, f"invalid qrels grade: {line!r}")
        require((query_id, document_id) not in qrel_pairs, f"duplicate qrels pair: {query_id}/{document_id}")
        qrel_pairs.add((query_id, document_id))
        qrel_count += 1
    require(qrel_count == 3144, f"qrels row count differs: {qrel_count}")
    checks["semantic"] = {"query_id_count": len(query_ids), "document_id_count": len(document_ids),
                           "train_id_count": 25_000, "qrels_row_count": qrel_count,
                           "qrels_unique_pair_count": len(qrel_pairs)}
    if deep:
        checks["deep_norms"] = {
            "documents": _scan_norms(root / "evaluation-document-vectors.f32", 1_000_000, 384),
            "queries": _scan_norms(root / "evaluation-query-vectors.f32", 305, 384),
            "train": _scan_norms(root / "train-vectors.f32", 25_000, 384),
            "tolerance": 5e-4,
        }
        require(all(value["maximum_absolute_error_from_one"] <= 5e-4
                    for key, value in checks["deep_norms"].items() if key != "tolerance"),
                "deep normalization scan exceeds tolerance")
    return {"status": "PASS", "root": str(root.resolve()), "checks": checks,
            "manifest_sha256": sha256(manifest_path), "deep": deep}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True,
                        help="directory containing the persisted E5 manifest.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--deep", action="store_true",
                        help="scan all vector rows and verify the unit-norm contract")
    args = parser.parse_args()
    result = validate(args.root, deep=args.deep)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
