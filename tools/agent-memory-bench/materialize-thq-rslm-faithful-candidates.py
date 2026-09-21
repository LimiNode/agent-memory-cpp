#!/usr/bin/env python3
"""Materialize official relative RSLM3/4 records for the frozen R4 union.

The output is a candidate-union payload, not a direct 1M document table.  Each
row is keyed by ``candidate.ids.i4`` and contains official packed symbols,
the inner UE7M9 residual scale, and the outer UE7M9 full-vector scale.  The
script deliberately keeps fitting and source provenance explicit so a later
native scorer cannot silently substitute the local FWHT/Lloyd-Max control.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

D = 384
DOCUMENTS = 1_000_000
BITS = (3, 4)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).resolve().parent
faithful = load_module("rslm_faithful_materializer", HERE / "rslm-faithful-reference.py")
frontier = load_module("thq_frontier_materializer", HERE / "run-thq4-residual-frontier.py")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_candidates(flat: Path, raw: Path) -> np.ndarray:
    rows = json.loads(raw.read_text(encoding="utf-8")).get("rows", [])
    if len(rows) != 152:
        raise RuntimeError("candidate raw must contain 152 rows")
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    if np.any(counts < 5000) or np.any(counts > 5099):
        raise RuntimeError("candidate count outside frozen 5000..5099 contract")
    total = int(np.sum(counts))
    if flat.stat().st_size != total * 148:
        raise RuntimeError("candidate flat size differs from raw row counts")
    records = np.memmap(flat, mode="r", dtype=np.uint8, shape=(total, 148))
    ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    if np.any(ids < 0) or np.any(ids >= DOCUMENTS):
        raise RuntimeError("candidate ID out of range")
    return np.unique(ids)


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("documents", "train-vectors", "thq4-codes", "thq4-thresholds",
                 "candidate-flat", "candidate-raw", "candidate-receipt", "output"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    parser.add_argument("--chunk-size", type=int, default=4096)
    args = parser.parse_args()
    if args.chunk_size <= 0:
        raise SystemExit("chunk size must be positive")
    count = args.documents.stat().st_size // (4 * D)
    train_count = args.train_vectors.stat().st_size // (4 * D)
    if count != DOCUMENTS:
        raise SystemExit("documents are not DE-1M")
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(count, D))
    train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(train_count, D)), dtype=np.float32)
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(count, 96))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    unique_ids = load_candidates(args.candidate_flat, args.candidate_raw)
    receipt = json.loads(args.candidate_receipt.read_text(encoding="utf-8"))
    if receipt.get("raw_sha256") != sha256(args.candidate_raw) or receipt.get("flat_file", {}).get("sha256") != sha256(args.candidate_flat):
        raise RuntimeError("candidate receipt binding differs")

    centroids = frontier.fit_centroids(train, thresholds)
    args.output.mkdir(parents=True, exist_ok=True)
    ids_path = args.output / "candidate.ids.i4"
    np.asarray(unique_ids, dtype="<i4").tofile(ids_path)
    centroids_path = args.output / "thq4-centroids.f32"
    np.asarray(centroids, dtype="<f4").tofile(centroids_path)
    models = {}
    for bits in BITS:
        symbol_path = args.output / f"rslm{bits}.symbols.u8"
        inner_path = args.output / f"rslm{bits}.inner-scale.u16"
        outer_path = args.output / f"rslm{bits}.outer-scale.u16"
        symbols = np.memmap(symbol_path, mode="w+", dtype=np.uint8,
                            shape=(len(unique_ids), (D * bits + 7) // 8))
        inner = np.memmap(inner_path, mode="w+", dtype="<u2", shape=(len(unique_ids),))
        outer = np.memmap(outer_path, mode="w+", dtype="<u2", shape=(len(unique_ids),))
        for start in range(0, len(unique_ids), args.chunk_size):
            stop = min(start + args.chunk_size, len(unique_ids))
            ids = unique_ids[start:stop]
            levels = frontier.unpack_thq(np.asarray(thq_codes[ids]))
            base = centroids[np.arange(D)[None, :], levels]
            docs = np.asarray(documents[ids], dtype=np.float32)
            packed, scales = faithful.encode(docs - base, bits)
            decoded = faithful.decode(packed, scales, bits)
            combined = base + decoded
            norm_ratio = np.sqrt(np.sum(docs * docs, axis=1) /
                                 np.maximum(np.sum(combined * combined, axis=1), np.finfo(np.float32).tiny))
            outer_bits = np.asarray([faithful.ue7m9_encode(float(value)) for value in norm_ratio], dtype="<u2")
            symbols[start:stop] = packed
            inner[start:stop] = scales
            outer[start:stop] = outer_bits
        for array in (symbols, inner, outer):
            array.flush()
        models[str(bits)] = {
            "symbol_bytes_per_document": int((D * bits + 7) // 8),
            "side_payload_bytes": int((D * bits + 7) // 8 + 4),
            "symbols_sha256": sha256(symbol_path),
            "inner_scale_sha256": sha256(inner_path),
            "outer_scale_sha256": sha256(outer_path),
        }
    raw = {
        "schema_version": 1,
        "family": "thq_rslm_faithful_candidate_materialization_v1",
        "status": "EXECUTED",
        "documents": count,
        "candidate_unique_documents": int(len(unique_ids)),
        "chunk_size": int(args.chunk_size),
        "reference_initial_commit": faithful.REFERENCE_INITIAL_COMMIT,
        "reference_content_commit": faithful.REFERENCE_CONTENT_COMMIT,
        "reference_snapshot_commit": faithful.REFERENCE_SNAPSHOT_COMMIT,
        "reference_notebook_blob": faithful.REFERENCE_NOTEBOOK_BLOB,
        "reference_notebook_sha256": faithful.REFERENCE_NOTEBOOK_SHA256,
        "runner_sha256": sha256(Path(__file__)),
        "faithful_reference_sha256": sha256(HERE / "rslm-faithful-reference.py"),
        "frontier_helper_sha256": sha256(HERE / "run-thq4-residual-frontier.py"),
        "candidate_flat_sha256": sha256(args.candidate_flat),
        "candidate_raw_sha256": sha256(args.candidate_raw),
        "candidate_receipt_sha256": sha256(args.candidate_receipt),
        "documents_sha256": sha256(args.documents),
        "train_vectors_sha256": sha256(args.train_vectors),
        "thq4_codes_sha256": sha256(args.thq4_codes),
        "thq4_thresholds_sha256": sha256(args.thq4_thresholds),
        "candidate_ids_sha256": sha256(ids_path),
        "thq4_centroids_sha256": sha256(centroids_path),
        "models": models,
        "limitations": [
            "candidate-union materialization, not a full 1M direct table",
            "portable native scorer and top-10 parity are a separate gate",
            "this artifact does not claim serving latency",
        ],
    }
    raw_path = args.output / "materialization.raw.json"
    raw_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt_out = dict(raw)
    receipt_out["raw_sha256"] = sha256(raw_path)
    receipt_out["execution_status"] = raw["status"]
    (args.output / "materialization.receipt.json").write_text(
        json.dumps(receipt_out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {raw_path}")


if __name__ == "__main__":
    main()
