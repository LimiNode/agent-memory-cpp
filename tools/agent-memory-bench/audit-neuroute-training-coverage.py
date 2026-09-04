#!/usr/bin/env python3
"""Audit which prototype rows can receive gradients in NeuRoute training."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def random_negatives(teacher: np.ndarray, count: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    result = np.empty((len(teacher) // 2, count), dtype=np.int32)
    prototype_count = int(teacher.max()) + 1
    for q in range(len(result)):
        forbidden = set(map(int, teacher[q]))
        values: list[int] = []
        while len(values) < count:
            candidate = int(rng.integers(0, prototype_count))
            if candidate not in forbidden and candidate not in values:
                values.append(candidate)
        result[q] = values
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    with np.load(args.teacher_cache, mmap_mode="r", allow_pickle=False) as data:
        teacher = np.asarray(data["teacher_top_prototypes"], dtype=np.int32)
    prototype_count = int(args.prototype_count)
    train_count = len(teacher) // 2
    positives = np.asarray(args.positive_ranks, dtype=np.int64)
    negatives = np.asarray(args.negative_ranks, dtype=np.int64)
    occurrences: list[np.ndarray] = [teacher[:train_count, positives].reshape(-1),
                                     teacher[:train_count, negatives].reshape(-1)]
    randoms = random_negatives(teacher, args.random_negatives, args.seed ^ 0x5EED)
    occurrences.append(randoms.reshape(-1))
    # A hard-negative round is model-dependent.  If its mined IDs are supplied,
    # count them exactly; otherwise report the auditable first-round coverage.
    if args.hard_negatives is not None:
        with np.load(args.hard_negatives, allow_pickle=False) as data:
            hard = np.asarray(data["hard_negatives"], dtype=np.int32)
        require(hard.shape[0] == train_count, "hard-negative query count differs")
        occurrences.append(hard.reshape(-1))
    touched = np.bincount(np.concatenate(occurrences), minlength=prototype_count)
    updated = touched > 0
    result = {"schema_version": 1, "family": "neuroute_training_coverage_audit",
              "teacher_cache_sha256": sha256(args.teacher_cache),
              "prototype_count": prototype_count, "train_query_count": train_count,
              "positive_ranks": list(map(int, positives)),
              "negative_ranks": list(map(int, negatives)),
              "random_negatives_per_query": args.random_negatives,
              "hard_negative_file": (None if args.hard_negatives is None
                                      else str(args.hard_negatives)),
              "fraction_prototypes_touched": float(np.mean(updated)),
              "fraction_prototypes_untouched": float(np.mean(~updated)),
              "touched_count": int(np.sum(updated)),
              "untouched_count": int(np.sum(~updated)),
              "updates_per_prototype": {"min": int(touched.min()),
                                        "p50": float(np.quantile(touched, .5)),
                                        "p95": float(np.quantile(touched, .95)),
                                        "max": int(touched.max()),
                                        "mean": float(np.mean(touched))},
              "code_entropy": None}
    if args.codes is not None:
        with np.load(args.codes, mmap_mode="r", allow_pickle=False) as codes:
            prototype_codes = np.asarray(codes["prototype_codes"], dtype=np.uint8)
        require(len(prototype_codes) == prototype_count, "codebook size differs")
        width = prototype_codes.shape[1] * 8
        result["code_entropy"] = {
            "all": _code_entropy(prototype_codes, width),
            "touched": _code_entropy(prototype_codes[updated], width),
            "untouched": _code_entropy(prototype_codes[~updated], width)}
    return result


def _code_entropy(values: np.ndarray, width: int) -> float:
    if len(values) == 0:
        return 0.0
    bits = np.unpackbits(values, axis=1, bitorder="little")[:, :width]
    p = np.clip(np.mean(bits, axis=0), 1e-12, 1 - 1e-12)
    return float(np.mean(-(p * np.log2(p) + (1 - p) * np.log2(1 - p))))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--teacher-cache", type=Path, required=True)
    parser.add_argument("--prototype-count", type=int, required=True)
    parser.add_argument("--positive-ranks", nargs="+", type=int,
                        default=[0, 1, 7, 31, 127, 255, 511, 1023])
    parser.add_argument("--negative-ranks", nargs="+", type=int, default=[64, 256])
    parser.add_argument("--random-negatives", type=int, default=8)
    parser.add_argument("--seed", type=int, default=285)
    parser.add_argument("--hard-negatives", type=Path)
    parser.add_argument("--codes", type=Path,
                        help="optional saved prototype codes for touched/untouched entropy")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(run(args), indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
