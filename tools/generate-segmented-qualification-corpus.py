#!/usr/bin/env python3
"""Generate deterministic UTF-8 corpus rows for seg-v2 qualification runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import tempfile
from pathlib import Path


WORKLOADS = (
    "unique+anchor",
    "shared-hot",
    "isolated-sparse-replacement",
    "zipf-like",
    "mixed-utf8",
    "long-document",
    "small-insert",
    "small-delete",
    "inactive-history",
    "no-op",
)


def tokens_for(
    workload: str,
    index: int,
    rng: random.Random,
    vocabulary_size: int,
    long_document_tokens: int,
    overlap: int,
) -> list[str]:
    rare = f"rare-{index:06d}"
    if workload == "unique+anchor":
        return [rare, "anchor", f"technical-{index % vocabulary_size:05d}"]
    if workload == "shared-hot":
        return ["shared-hot", "shared-hot", "anchor", "transaction"]
    if workload == "isolated-sparse-replacement":
        return [f"sp-{index:06d}", f"sl-{index:06d}", "локальный"]
    if workload == "zipf-like":
        rank = (index * 37 + 11) % 100
        bucket = 1 if rank < 50 else 2 if rank < 75 else 3 if rank < 88 else 4 if rank < 95 else 5
        values = [f"zipf-{bucket}", "anchor"]
        if index % 3 == 0:
            values.append("shared-hot")
        values.extend(f"vocab-{rng.randrange(vocabulary_size):05d}" for _ in range(2))
        return values
    if workload == "mixed-utf8":
        return ["индекс", "данные", "транзакция", "std::vector", "UTF-8", rare]
    if workload == "long-document":
        return [f"long-{(index * 17 + offset) % vocabulary_size:05d}"
                for offset in range(long_document_tokens)] + [rare, "anchor"]
    if workload == "inactive-history":
        return [rare, "history-anchor", f"overlap-{index % overlap:04d}"]
    if workload == "small-insert":
        return [rare, "insert-anchor", "std::span"]
    if workload == "small-delete":
        return [rare, "delete-anchor", "std::map"]
    return ["anchor", "no-op", f"stable-{index % vocabulary_size:05d}"]


def generate(
    units: int,
    seed: int,
    vocabulary_size: int | None = None,
    long_document_tokens: int = 256,
    overlap: int = 32,
    inactive_token_history: int = 64,
) -> tuple[bytes, dict[str, object]]:
    if units <= 0:
        raise ValueError("units must be positive")
    if long_document_tokens <= 0 or overlap <= 0 or inactive_token_history < 0:
        raise ValueError("distribution parameters are out of range")
    vocabulary_size = vocabulary_size or max(32, units // 2)
    if vocabulary_size <= 0:
        raise ValueError("vocabulary size must be positive")
    rng = random.Random(seed)
    lines = ["# workload\tunit_index\ttokens separated by U+001F\n"]
    rows = 0
    for workload in WORKLOADS:
        for index in range(units + 1):
            token_bytes = "\x1f".join(tokens_for(
                workload, index, rng, vocabulary_size, long_document_tokens, overlap))
            lines.append(f"{workload}\t{index}\t{token_bytes}\n")
            rows += 1
    payload = "".join(lines).encode("utf-8")
    manifest = {
        "schema_version": 1,
        "family": "segmented_lexical_performance_qualification_v1",
        "seed": seed,
        "units": units,
        "extra_insert_row": units,
        "workloads": list(WORKLOADS),
        "rows": rows,
        "vocabulary_size": vocabulary_size,
        "long_document_tokens": long_document_tokens,
        "overlap": overlap,
        "inactive_token_history": inactive_token_history,
        "distributions": {
            "unique": "one rare token plus anchor and bounded vocabulary token",
            "shared_hot": "shared-hot repeated in every unit",
            "zipf_like": "deterministic five-bucket skew with shared-hot tail",
            "mixed_utf8": "English technical identifiers and Russian UTF-8 terms",
            "isolated": "two rare partitions without a shared anchor",
            "document_lengths": "short three-token, medium mixed/Zipf, and configurable long documents",
            "inactive_history": "runner performs the declared replacement count before measurement",
        },
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    return payload, manifest


def self_test() -> int:
    payload_a, manifest_a = generate(8, 12648430)
    payload_b, manifest_b = generate(8, 12648430)
    if payload_a != payload_b or manifest_a != manifest_b:
        raise SystemExit("generator is not deterministic")
    altered, _ = generate(8, 12648431)
    if altered == payload_a:
        raise SystemExit("seed mutation did not change corpus")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--units", type=int, default=128)
    parser.add_argument("--seed", type=int, default=12648430)
    parser.add_argument("--vocabulary-size", type=int)
    parser.add_argument("--long-document-tokens", type=int, default=256)
    parser.add_argument("--overlap", type=int, default=32)
    parser.add_argument("--inactive-token-history", type=int, default=64)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.output is None or args.manifest is None:
        parser.error("--output and --manifest are required")
    payload, manifest = generate(
        args.units, args.seed, args.vocabulary_size, args.long_document_tokens,
        args.overlap, args.inactive_token_history)
    args.output.write_bytes(payload)
    args.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
