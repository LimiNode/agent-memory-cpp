#!/usr/bin/env python3
"""Validate existing DE-1M payloads and write one canonical frozen manifest.

Payloads stay outside Git. This command performs no implicit downloads or
copies; every input is explicit and hash-bound before the manifest is written.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def entry(path: Path, expected_bytes: int, label: str) -> dict:
    if not path.is_file(): raise FileNotFoundError(f"{label}: {path}")
    actual = path.stat().st_size
    if actual != expected_bytes: raise ValueError(f"{label}: expected {expected_bytes} bytes, got {actual}")
    return {"path": str(path.resolve()), "sha256": sha256(path), "bytes": actual}

def main() -> int:
    p = argparse.ArgumentParser(); p.add_argument("--documents", type=Path, required=True); p.add_argument("--queries", type=Path, required=True); p.add_argument("--teacher-ids", type=Path, required=True); p.add_argument("--thresholds", type=Path, required=True); p.add_argument("--document-codes", type=Path, required=True); p.add_argument("--query-codes", type=Path, required=True); p.add_argument("--output", type=Path, required=True); p.add_argument("--documents-count", type=int, default=1_000_000); p.add_argument("--queries-count", type=int, default=152); p.add_argument("--dimension", type=int, default=384); a = p.parse_args()
    n, q, d = a.documents_count, a.queries_count, a.dimension
    if min(n, q, d) <= 0: raise ValueError("counts and dimension must be positive")
    manifest = {"schema_version": 1, "family": "frozen_de1m_thq_materialization_v1", "documents": n, "queries": q, "dimension": d, "references": {"document_vectors": entry(a.documents, n*d*4, "documents"), "queries": entry(a.queries, q*d*4, "queries"), "teacher_ids": entry(a.teacher_ids, q*10*8, "teacher_ids")}, "outputs": {"thq4_thresholds": entry(a.thresholds, d*3*4, "thresholds"), "thq4_document_codes": entry(a.document_codes, n*144, "document_codes"), "thq4_query_codes": entry(a.query_codes, q*144, "query_codes")}, "protocol": {"embedding": "normalized E5", "teacher": "exact cosine top-10", "thermometer_levels": 4, "thermometer_bytes_per_document": 144, "packed_ordinal_bytes_per_document": 96, "production_activation": False}}
    a.output.parent.mkdir(parents=True, exist_ok=True); a.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"); print(json.dumps({"family": manifest["family"], "documents": n, "queries": q, "manifest": str(a.output.resolve())}, indent=2)); return 0

if __name__ == "__main__": raise SystemExit(main())
