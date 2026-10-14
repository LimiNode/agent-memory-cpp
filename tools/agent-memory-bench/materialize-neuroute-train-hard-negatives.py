#!/usr/bin/env python3
"""Materialize exact E5 ranks for supervised projection hard negatives."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def exact_top(documents: np.ndarray, queries: np.ndarray, count: int,
              block: int) -> np.ndarray:
    best_scores = np.empty((len(queries), 0), dtype=np.float32)
    best_ids = np.empty((len(queries), 0), dtype=np.int64)
    for start in range(0, len(documents), block):
        stop = min(start + block, len(documents))
        scores = np.asarray(documents[start:stop] @ queries.T,
                            dtype=np.float32).T
        ids = np.arange(start, stop, dtype=np.int64)
        next_scores = np.empty((len(queries), min(count,
                                                   len(best_ids[0]) + len(ids))),
                                dtype=np.float32)
        next_ids = np.empty_like(next_scores, dtype=np.int64)
        for index in range(len(queries)):
            merged_scores = np.concatenate((best_scores[index], scores[index]))
            merged_ids = np.concatenate((best_ids[index], ids))
            keep = min(count, len(merged_ids))
            positions = np.argpartition(-merged_scores, keep - 1)[:keep]
            order = np.lexsort((merged_ids[positions],
                                -merged_scores[positions]))
            chosen = positions[order]
            next_scores[index] = merged_scores[chosen]
            next_ids[index] = merged_ids[chosen]
        best_scores, best_ids = next_scores, next_ids
        print(f"hard-negative teacher {stop}/{len(documents)}", flush=True)
    return best_ids


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=1000)
    parser.add_argument("--block", type=int, default=8192)
    args = parser.parse_args()
    manifest = json.loads(args.cache.read_text(encoding="utf-8"))
    root = args.cache.parent
    train_queries = np.load(root / manifest["outputs"]["train_queries"]["path"],
                             mmap_mode="r", allow_pickle=False)
    source = manifest["source"]
    documents = np.memmap(Path(source["document_vectors"]), mode="r",
                          dtype="<f4", shape=(int(source["document_count"]),
                                                int(source["dimension"])))
    values = exact_top(documents, np.asarray(train_queries, dtype=np.float32),
                       args.top_k, args.block)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.save(args.output, values, allow_pickle=False)
    receipt = {"schema_version": 1,
               "family": "neuroute_train_e5_hard_negative_ids",
               "cache": str(args.cache.resolve()),
               "cache_sha256": sha256(args.cache), "top_k": args.top_k,
               "shape": list(values.shape), "dtype": str(values.dtype),
               "output_sha256": sha256(args.output)}
    args.output.with_suffix(args.output.suffix + ".json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
