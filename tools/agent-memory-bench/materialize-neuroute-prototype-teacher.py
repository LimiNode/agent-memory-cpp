#!/usr/bin/env python3
"""Materialize a leakage-safe FP32 K8-prototype teacher cache.

The cache contains only deterministic top-k prototype ids.  Query and
prototype vectors remain in the source materialization; this keeps the cache
small and makes accidental replacement of the frozen teacher observable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def top_k(scores: np.ndarray, count: int) -> np.ndarray:
    """Return ids ordered by descending score, then ascending id."""
    candidates = np.argpartition(-scores, count - 1)[:count]
    return candidates[np.lexsort((candidates, -scores[candidates]))]


def materialize(queries: np.ndarray, prototypes: np.ndarray, top_count: int,
                block_size: int) -> np.ndarray:
    require(queries.ndim == prototypes.ndim == 2 and
            queries.shape[1] == prototypes.shape[1],
            "query and prototype dimensions differ")
    require(len(queries) == 8141,
            "prototype teacher requires exactly 8,141 queries")
    require(0 < top_count <= len(prototypes), "teacher top-k is invalid")
    require(block_size > 0, "teacher block size is invalid")
    result = np.empty((len(queries), top_count), dtype=np.int32)
    for first in range(0, len(queries), block_size):
        stop = min(first + block_size, len(queries))
        scores = np.asarray(queries[first:stop], dtype=np.float32) @ \
            np.asarray(prototypes, dtype=np.float32).T
        for row in range(stop - first):
            result[first + row] = top_k(scores[row], top_count)
    return result


def run(input_path: Path, output_path: Path, manifest_path: Path,
        top_count: int, block_size: int) -> None:
    with np.load(input_path, mmap_mode="r", allow_pickle=False) as source:
        require({"queries", "prototype_vectors"}.issubset(source.files),
                "teacher input must contain queries and prototype_vectors")
        queries = np.asarray(source["queries"], dtype=np.float32)
        prototypes = np.asarray(source["prototype_vectors"], dtype=np.float32)
        teacher = materialize(queries, prototypes, top_count, block_size)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Use an explicit stream so the manifest always names the exact requested
    # path (numpy otherwise appends ``.npz`` for suffix-less paths).
    with output_path.open("wb") as stream:
        np.savez(stream, teacher_top_prototypes=teacher)
    manifest = {
        "schema_version": 1,
        "family": "neuroute_prototype_fp32_teacher_cache",
        "source_npz_sha256": sha256(input_path),
        "query_count": int(len(queries)),
        "prototype_count": int(len(prototypes)),
        "dimension": int(prototypes.shape[1]),
        "teacher_top_k": int(top_count),
        "score": "float32_dot",
        "tie_break": "ascending_prototype_id",
        "block_size": int(block_size),
        "output_npz_sha256": sha256(output_path),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")


def self_test() -> None:
    prototypes = np.zeros((32, 4), dtype=np.float32)
    prototypes[:4] = np.eye(4, dtype=np.float32)
    queries = prototypes[:1]
    # The production count is deliberately enforced by materialize(); test the
    # ordering helper independently with a small deterministic fixture.
    first = top_k(queries[0] @ prototypes.T, 4)
    require(np.array_equal(first, np.arange(4, dtype=np.int64)),
            "teacher ordering self-test differs")
    print("NeuRoute prototype teacher materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--top-k", type=int, default=1024)
    parser.add_argument("--block-size", type=int, default=8)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        require(args.input is not None and args.output is not None,
                "--input and --output are required")
        manifest = args.manifest or args.output.with_suffix(".json")
        run(args.input, args.output, manifest, args.top_k, args.block_size)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"materialize-neuroute-prototype-teacher: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
