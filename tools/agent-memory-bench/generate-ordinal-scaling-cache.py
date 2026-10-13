#!/usr/bin/env python3
"""Materialize exact E5 teacher top-10 labels for additional query vectors."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def exact_top10(documents: np.ndarray, queries: np.ndarray, block: int) -> tuple[np.ndarray, np.ndarray]:
    ids = np.empty((len(queries), 10), dtype=np.int64)
    scores = np.empty((len(queries), 10), dtype=np.float32)
    for start in range(0, len(queries), block):
        part = queries[start:start + block]
        values = documents @ part.T
        candidates = np.argpartition(values, -10, axis=0)[-10:]
        for column in range(values.shape[1]):
            selected = candidates[:, column]
            order = np.argsort(values[selected, column])[::-1]
            ids[start + column] = selected[order]
            scores[start + column] = values[selected[order], column]
    return ids, scores


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--query-count", type=int, default=1000)
    parser.add_argument("--block", type=int, default=16)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    documents = np.memmap(args.documents, dtype="<f4", mode="r",
                          shape=(1_000_000, 384))
    queries = np.memmap(args.queries, dtype="<f4", mode="r",
                        shape=(args.query_count, 384))
    ids, scores = exact_top10(documents, queries, args.block)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.save(args.output.with_name("queries.npy"), np.asarray(queries, dtype=np.float32))
    np.save(args.output.with_name("teacher-ids.npy"), ids)
    np.save(args.output.with_name("teacher-scores.npy"), scores)
    manifest = {"family": "ordinal_scaling_teacher_cache", "query_count": len(queries),
                "dimension": 384, "teacher": "exact_global_e5_inner_product",
                "documents_sha256": sha256(args.documents),
                "queries_sha256": sha256(args.output.with_name("queries.npy")),
                "teacher_ids_sha256": sha256(args.output.with_name("teacher-ids.npy")),
                "teacher_scores_sha256": sha256(args.output.with_name("teacher-scores.npy"))}
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
