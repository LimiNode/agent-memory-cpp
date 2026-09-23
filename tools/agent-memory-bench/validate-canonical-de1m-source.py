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
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate(root: Path) -> dict[str, object]:
    manifest_path = root / "manifest.json"
    require(manifest_path.is_file(), f"missing manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
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
        actual_bytes = path.stat().st_size
        if expected_bytes is not None:
            require(actual_bytes == expected_bytes, f"{key}: expected {expected_bytes} bytes, got {actual_bytes}")
        require(actual_bytes == int(entry["bytes"]) if "bytes" in entry else True,
                f"{key}: manifest byte count differs")
        actual_sha = sha256(path)
        require(actual_sha == entry["sha256"], f"{key}: SHA-256 differs from manifest")
        checks[key] = {"path": str(path.resolve()), "bytes": actual_bytes, "sha256": actual_sha}
    require(manifest.get("embedding", {}).get("normalized") is True,
            "canonical E5 source must be L2-normalized")
    require(outputs["evaluation_document_vectors"]["count"] == 1_000_000,
            "canonical document count differs")
    require(outputs["evaluation_query_vectors"]["count"] == 305,
            "canonical query count differs")
    require(outputs["train_vectors"]["count"] == 25_000,
            "canonical train count differs")
    return {"status": "PASS", "root": str(root.resolve()), "checks": checks,
            "manifest_sha256": sha256(manifest_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True,
                        help="directory containing the persisted E5 manifest.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate(args.root)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
