#!/usr/bin/env python3
"""Create a deterministic synthetic 8,141-query prototype-metric source.

This is a controlled geometry stress test. It must not be reported as real
multilingual or held-out corpus evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def normalize(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    return values / np.maximum(np.linalg.norm(values, axis=1, keepdims=True),
                               np.float32(1.0e-8))


def materialize(source: Path, output: Path, manifest: Path, seed: int,
                prototype_count: int, query_count: int) -> None:
    with np.load(source, mmap_mode="r", allow_pickle=False) as values:
        if "prototype_vectors" not in values.files:
            raise ValueError("source lacks prototype_vectors")
        prototypes = np.asarray(values["prototype_vectors"], dtype=np.float32)
    if prototypes.ndim != 2 or prototypes.shape[1] != 384:
        raise ValueError("prototype geometry must be N x 384")
    if prototype_count <= 0 or prototype_count > len(prototypes):
        raise ValueError("prototype count is invalid")
    prototypes = np.ascontiguousarray(prototypes[:prototype_count])
    rng = np.random.default_rng(seed)
    left = rng.integers(0, prototype_count, size=query_count, dtype=np.int64)
    right = rng.integers(0, prototype_count, size=query_count, dtype=np.int64)
    queries = (np.float32(0.72) * prototypes[left] +
               np.float32(0.28) * prototypes[right] +
               np.float32(0.025) * rng.normal(size=(query_count, 384)))
    queries = np.ascontiguousarray(normalize(queries), dtype=np.float32)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        np.savez(stream, queries=queries, prototype_vectors=prototypes)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "family": "neuroute_prototype_binary_synthetic_source",
        "source_npz_sha256": sha256(source),
        "output_npz_sha256": sha256(output),
        "seed": int(seed),
        "prototype_count": int(prototype_count),
        "query_count": int(query_count),
        "dimension": 384,
        "query_recipe": "0.72*a + 0.28*b + N(0,0.025), normalized",
        "real_corpus_evidence": False,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--seed", type=int, default=284)
    parser.add_argument("--prototype-count", type=int, default=452700)
    parser.add_argument("--query-count", type=int, default=8141)
    args = parser.parse_args()
    materialize(args.source, args.output, args.manifest or
                args.output.with_suffix(".json"), args.seed,
                args.prototype_count, args.query_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
