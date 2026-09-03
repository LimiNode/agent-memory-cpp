#!/usr/bin/env python3
"""Evaluate a deterministic document/prototype supervised binary ceiling."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy


THIS = Path(__file__).resolve().parent
POPCOUNT = numpy.asarray([int(value).bit_count() for value in range(256)],
                         dtype=numpy.uint8)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and
            value.get("family") == "neuroute_joint_binary_ceiling" and
            value.get("widths") == [256, 384, 512],
            "joint-binary contract differs")
    return value


def sample_pairs(prototypes: numpy.ndarray, documents: numpy.ndarray,
                 offsets: numpy.ndarray, postings: numpy.ndarray,
                 limit: int) -> tuple[numpy.ndarray, numpy.ndarray,
                                       numpy.ndarray, numpy.ndarray]:
    """Select deterministic positive prototype/document pairs."""
    pair_prototypes: list[int] = []
    pair_documents: list[int] = []
    for prototype in range(len(prototypes)):
        ids = postings[int(offsets[prototype]):int(offsets[prototype + 1])]
        for document in ids[:4]:
            pair_prototypes.append(prototype)
            pair_documents.append(int(document))
            if len(pair_prototypes) == limit:
                p = numpy.asarray(pair_prototypes, dtype=numpy.int64)
                d = numpy.asarray(pair_documents, dtype=numpy.int64)
                return prototypes[p], documents[d], p, d
    p = numpy.asarray(pair_prototypes, dtype=numpy.int64)
    d = numpy.asarray(pair_documents, dtype=numpy.int64)
    return prototypes[p], documents[d], p, d


def train_projection(pair_prototypes: numpy.ndarray, pair_documents: numpy.ndarray,
                     width: int, seed: int) -> tuple[numpy.ndarray, numpy.ndarray]:
    """Learn low-variance directions of positive prototype/document pairs."""
    differences = pair_prototypes.astype(numpy.float64) - pair_documents.astype(numpy.float64)
    covariance = (differences.T @ differences) / max(len(differences), 1)
    _, vectors = numpy.linalg.eigh(covariance)
    directions = vectors[:, :min(width, vectors.shape[1])].T.astype(numpy.float32)
    if width > directions.shape[0]:
        rng = numpy.random.default_rng(seed)
        extra = rng.normal(size=(width - directions.shape[0], directions.shape[1]))
        extra /= numpy.linalg.norm(extra, axis=1, keepdims=True)
        directions = numpy.vstack((directions, extra.astype(numpy.float32)))
    thresholds = numpy.median(pair_documents @ directions.T, axis=0).astype(numpy.float32)
    return directions, thresholds


def encode(values: numpy.ndarray, directions: numpy.ndarray,
           thresholds: numpy.ndarray) -> numpy.ndarray:
    result = numpy.empty((len(values), (len(directions) + 7) // 8), dtype=numpy.uint8)
    for start in range(0, len(values), 32768):
        stop = min(start + 32768, len(values))
        projections = numpy.asarray(values[start:stop] @ directions.T, dtype=numpy.float32)
        result[start:stop] = numpy.packbits(projections >= thresholds[None, :],
                                            axis=1, bitorder="little")
    return result


def hamming(left: numpy.ndarray, right: numpy.ndarray) -> numpy.ndarray:
    return POPCOUNT[numpy.bitwise_xor(left, right)].sum(axis=-1,
                                                        dtype=numpy.uint16)


def positive_stats(pair_prototypes: numpy.ndarray, pair_documents: numpy.ndarray,
                   prototype_codes: numpy.ndarray, document_codes: numpy.ndarray) -> dict[str, float]:
    distances = hamming(prototype_codes, document_codes)
    return {"mean": float(numpy.mean(distances)),
            "p50": float(numpy.quantile(distances, .50)),
            "p95": float(numpy.quantile(distances, .95)),
            "p99": float(numpy.quantile(distances, .99))}


def target_stats(documents: numpy.ndarray, document_codes: numpy.ndarray,
                 prototypes: numpy.ndarray, prototype_codes: numpy.ndarray,
                 queries: numpy.ndarray, targets: numpy.ndarray) -> dict[str, Any]:
    rows = []
    for query_index, query in enumerate(queries):
        target_vectors = documents[targets[query_index]]
        scores = numpy.asarray(target_vectors @ prototypes.T, dtype=numpy.float32).max(axis=0)
        selected = numpy.lexsort((numpy.arange(len(scores)), -scores))[:8]
        distances = hamming(document_codes[targets[query_index], None, :],
                             prototype_codes[selected][None, :, :]).min(axis=1)
        rows.append(distances)
    values = numpy.concatenate(rows) if rows else numpy.empty(0, dtype=numpy.uint16)
    return {"r50": float(numpy.quantile(values, .50)),
            "r90": float(numpy.quantile(values, .90)),
            "r95": float(numpy.quantile(values, .95)),
            "r99": float(numpy.quantile(values, .99))}


def evaluate(npz: Any, contract: dict[str, Any]) -> dict[str, Any]:
    files = set(npz.files) if hasattr(npz, "files") else set(npz)
    required = {"documents", "queries", "target_documents", "prototype_vectors",
                "prototype_offsets", "prototype_documents", "document_codes",
                "prototype_codes"}
    require(required.issubset(files), "joint-binary input arrays are missing")
    documents = numpy.asarray(npz["documents"], dtype=numpy.float32)
    queries = numpy.asarray(npz["queries"], dtype=numpy.float32)
    targets = numpy.asarray(npz["target_documents"], dtype=numpy.int64)
    prototypes = numpy.asarray(npz["prototype_vectors"], dtype=numpy.float32)
    offsets = numpy.asarray(npz["prototype_offsets"], dtype=numpy.int64)
    postings = numpy.asarray(npz["prototype_documents"], dtype=numpy.int64)
    frozen_documents = numpy.asarray(npz["document_codes"], dtype=numpy.uint8)
    frozen_prototypes = numpy.asarray(npz["prototype_codes"], dtype=numpy.uint8)
    require(len(prototypes) == len(offsets) - 1 and targets.shape[0] == len(queries),
            "joint-binary shape differs")
    pair_prototypes, pair_documents, pair_ids, pair_document_ids = sample_pairs(
        prototypes, documents, offsets, postings, int(contract["pair_sample"]))
    result: dict[str, Any] = {"schema_version": 1,
                              "family": contract["family"],
                              "prototype_count": len(prototypes),
                              "document_count": len(documents),
                              "pair_sample": len(pair_prototypes), "widths": {}}
    for width in contract["widths"]:
        width = int(width)
        directions, thresholds = train_projection(pair_prototypes, pair_documents,
                                                  width, int(contract["seed"]) ^ width)
        pair_p = encode(pair_prototypes, directions, thresholds)
        pair_d = encode(pair_documents, directions, thresholds)
        all_p = encode(prototypes, directions, thresholds)
        all_d = encode(documents, directions, thresholds)
        result["widths"][str(width)] = {
            "method": "positive_pair_low_variance_projection",
            "positive_pair_hamming": positive_stats(pair_prototypes, pair_documents, pair_p, pair_d),
            "oracle_target_radius": target_stats(documents, all_d, prototypes, all_p, queries, targets),
            "query_partitions": {
                "configuration": target_stats(documents, all_d, prototypes, all_p,
                                                queries[:len(queries) // 2], targets[:len(queries) // 2]),
                "internal": target_stats(documents, all_d, prototypes, all_p,
                                          queries[len(queries) // 2:], targets[len(queries) // 2:])},
            "code_bytes_per_document": (width + 7) // 8,
            "mean_bit_entropy": float(numpy.mean(
                -(numpy.mean(numpy.unpackbits(all_d, axis=1, bitorder="little"), axis=0)
                  * numpy.log2(numpy.maximum(numpy.mean(
                      numpy.unpackbits(all_d, axis=1, bitorder="little"), axis=0), 1e-12))
                  + (1.0 - numpy.mean(numpy.unpackbits(all_d, axis=1, bitorder="little"), axis=0))
                  * numpy.log2(numpy.maximum(1.0 - numpy.mean(
                      numpy.unpackbits(all_d, axis=1, bitorder="little"), axis=0), 1e-12)))))
        }
        if width == 256:
            result["widths"][str(width)]["itq_frozen_positive_pair_hamming"] = positive_stats(
                pair_prototypes, pair_documents,
                frozen_prototypes[pair_ids], frozen_documents[pair_document_ids])
    result["decision"] = {"native_mih_licensed": False,
                           "production_selection_licensed": False,
                           "reason": "geometry ceiling requires held-out full cascade and native probe validation"}
    return result


def synthetic() -> dict[str, numpy.ndarray]:
    rng = numpy.random.default_rng(279)
    documents = rng.normal(size=(32, 256)).astype(numpy.float32)
    prototypes = documents[::4]
    queries = documents[:4]
    codes = numpy.packbits(documents >= 0, axis=1, bitorder="little")
    offsets = numpy.arange(0, 33, 4, dtype=numpy.int64)
    postings = numpy.arange(32, dtype=numpy.int64)
    return {"documents": documents, "queries": queries,
            "target_documents": numpy.arange(4, dtype=numpy.int64)[:, None],
            "prototype_vectors": prototypes, "prototype_offsets": offsets,
            "prototype_documents": postings, "document_codes": codes,
            "prototype_codes": codes[::4]}


def self_test(path: Path) -> int:
    try:
        value = evaluate(synthetic(), load_contract(path))
        require(set(value["widths"]) == {"256", "384", "512"},
                "joint-binary widths missing")
        require(value["decision"]["native_mih_licensed"] is False,
                "joint-binary production gate opened")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"joint-binary runner self-test failed: {error}", file=sys.stderr)
        return 1
    print("Joint-binary runner self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "self-test"))
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-joint-binary.example.json")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "self-test":
            return self_test(args.contract)
        require(args.input is not None and args.output is not None,
                "joint-binary run requires --input and --output")
        result = evaluate(numpy.load(args.input, allow_pickle=False),
                          load_contract(args.contract))
        result["input_sha256"] = sha256(args.input)
        result["contract_sha256"] = sha256(args.contract)
        result["runner_sha256"] = sha256(Path(__file__))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"run-neuroute-joint-binary-ceiling: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
