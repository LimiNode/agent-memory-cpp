#!/usr/bin/env python3
"""Bind the frozen real 8,141-query pool to one K8 prototype geometry."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np


GERMAN_ORDER_PREFIX = b"neuroute-v3-de-v1\0"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) +
            "\n").encode("utf-8")


def verified_vectors(root: Path, id_key: str, vector_key: str,
                     expected_count: int) -> tuple[list[str], np.ndarray, str]:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    outputs = manifest["outputs"]
    id_path = root / outputs[id_key]["path"]
    vector_path = root / outputs[vector_key]["path"]
    require(sha256(id_path) == outputs[id_key]["sha256"] and
            sha256(vector_path) == outputs[vector_key]["sha256"],
            f"query payload differs: {root}")
    ids = [str(json.loads(line)["id"]) for line in
           id_path.read_text(encoding="utf-8").splitlines()]
    vectors = np.fromfile(vector_path, dtype="<f4").reshape(-1, 384)
    require(len(ids) == len(vectors) == expected_count,
            f"query count differs: {root}")
    return ids, vectors, sha256(manifest_path)


def materialize(prototype_source: Path, prototype_manifest: Path,
                german_root: Path, multilingual_root: Path,
                output: Path, manifest_path: Path) -> None:
    german_ids, german_vectors, german_manifest_sha = verified_vectors(
        german_root, "evaluation_query_ids", "evaluation_query_vectors", 305)
    external_ids, external_vectors, external_manifest_sha = verified_vectors(
        multilingual_root, "query_ids", "query_vectors", 7988)
    ordered = sorted(german_ids, key=lambda value: (
        hashlib.sha256(GERMAN_ORDER_PREFIX + value.encode("utf-8")).digest(),
        value))[:153]
    positions = {value: index for index, value in enumerate(german_ids)}
    selected_german = np.asarray(
        german_vectors[[positions[value] for value in ordered]], dtype=np.float32)
    queries = np.ascontiguousarray(
        np.concatenate((selected_german, external_vectors), axis=0),
        dtype=np.float32)
    require(queries.shape == (8141, 384), "real query pool shape differs")

    prototype_binding = json.loads(prototype_manifest.read_text(encoding="utf-8"))
    require(prototype_binding.get("family") ==
            "neuroute_semantic_anchor_relocation_materialization",
            "prototype source manifest family differs")
    with np.load(prototype_source, mmap_mode="r", allow_pickle=False) as source:
        require("prototype_vectors" in source.files,
                "prototype source lacks prototype_vectors")
        prototypes = np.asarray(source["prototype_vectors"], dtype=np.float32)
        require(prototypes.ndim == 2 and prototypes.shape[1] == 384,
                "prototype geometry differs")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        with temporary.open("wb") as stream:
            np.savez(stream, queries=queries, prototype_vectors=prototypes)
        temporary.replace(output)

    german_id_sha = hashlib.sha256(
        "\n".join(ordered).encode("utf-8")).hexdigest()
    external_id_sha = hashlib.sha256(
        "\n".join(external_ids).encode("utf-8")).hexdigest()
    manifest = {
        "schema_version": 1,
        "family": "neuroute_prototype_binary_real_source",
        "real_corpus_evidence": True,
        "query_count": int(len(queries)),
        "prototype_count": int(len(prototypes)),
        "dimension": 384,
        "query_pool": {
            "german_count": 153,
            "german_selection": "sha256(neuroute-v3-de-v1\\0 + id), first 153",
            "german_ids_sha256": german_id_sha,
            "german_e5_manifest_sha256": german_manifest_sha,
            "multilingual_count": 7988,
            "multilingual_ids_sha256": external_id_sha,
            "multilingual_manifest_sha256": external_manifest_sha,
        },
        "prototype_source_npz_sha256": sha256(prototype_source),
        "prototype_source_manifest_sha256": sha256(prototype_manifest),
        "prototype_seed": int(prototype_binding["seed"]),
        "output_npz_sha256": sha256(output),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(canonical(manifest))


def self_test() -> None:
    ids = ["de:2", "de:1", "de:3"]
    first = sorted(ids, key=lambda value: (
        hashlib.sha256(GERMAN_ORDER_PREFIX + value.encode("utf-8")).digest(),
        value))
    require(sorted(first) == sorted(ids) and len(set(first)) == len(ids),
            "German query ordering differs")
    print("NeuRoute prototype real-source materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prototype-source", type=Path)
    parser.add_argument("--prototype-manifest", type=Path)
    parser.add_argument("--german-e5-root", type=Path)
    parser.add_argument("--multilingual-query-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        require(all(value is not None for value in (
            args.prototype_source, args.prototype_manifest,
            args.german_e5_root, args.multilingual_query_root, args.output)),
            "all source roots and --output are required")
        materialize(args.prototype_source, args.prototype_manifest,
                    args.german_e5_root, args.multilingual_query_root,
                    args.output, args.manifest or args.output.with_suffix(".json"))
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"materialize-neuroute-prototype-real-source: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
