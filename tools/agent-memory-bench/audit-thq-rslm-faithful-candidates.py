#!/usr/bin/env python3
"""Fail-closed audit for the faithful RSLM candidate-union materialization."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

D = 384
BITS = (3, 4)
PRODUCTION_METRIC = "cosine"
CONTROL_METRIC = "paper-faithful-ip"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def maybe_sha256(path: Path) -> str | None:
    return sha256(path) if path.is_file() else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--materialization", type=Path, required=True)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--train-vectors", type=Path, required=True)
    parser.add_argument("--thq4-codes", type=Path, required=True)
    parser.add_argument("--thq4-thresholds", type=Path, required=True)
    parser.add_argument("--candidate-flat", type=Path, required=True)
    parser.add_argument("--candidate-raw", type=Path, required=True)
    parser.add_argument("--candidate-receipt", type=Path, required=True)
    parser.add_argument("--materializer", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--frontier-helper", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw_path = args.materialization / "materialization.raw.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    if raw.get("family") != "thq_rslm_faithful_candidate_materialization_v1": failures.append("family")
    if raw.get("status") != "EXECUTED": failures.append("status")
    ids_path = args.materialization / "candidate.ids.i4"
    centroids_path = args.materialization / "thq4-centroids.f32"
    if not ids_path.is_file() or not centroids_path.is_file(): failures.append("metadata files")
    ids = np.fromfile(ids_path, dtype="<i4") if ids_path.is_file() else np.empty(0, dtype=np.int32)
    if len(ids) != int(raw.get("candidate_unique_documents", -1)) or np.any(ids[1:] <= ids[:-1]): failures.append("candidate IDs")
    if centroids_path.is_file() and centroids_path.stat().st_size != D * 4 * 4: failures.append("centroid shape")
    expected_sources = {
        "documents_sha256": args.documents, "train_vectors_sha256": args.train_vectors,
        "thq4_codes_sha256": args.thq4_codes, "thq4_thresholds_sha256": args.thq4_thresholds,
        "faithful_reference_sha256": args.reference, "frontier_helper_sha256": args.frontier_helper,
        "candidate_flat_sha256": args.candidate_flat, "candidate_raw_sha256": args.candidate_raw,
        "candidate_receipt_sha256": args.candidate_receipt,
    }
    for field, path in expected_sources.items():
        if not path.is_file() or sha256(path) != raw.get(field): failures.append(f"source binding: {field}")
    for bits in BITS:
        width = (D * bits + 7) // 8
        for suffix, item_size in (("symbols.u8", width), ("inner-scale.u16", 2), ("outer-scale.u16", 2)):
            path = args.materialization / f"rslm{bits}.{suffix}"
            if not path.is_file() or path.stat().st_size != len(ids) * item_size: failures.append(f"shape: rslm{bits}.{suffix}")
            if path.is_file() and sha256(path) != raw.get("models", {}).get(str(bits), {}).get({"symbols.u8": "symbols_sha256", "inner-scale.u16": "inner_scale_sha256", "outer-scale.u16": "outer_scale_sha256"}[suffix]):
                failures.append(f"hash: rslm{bits}.{suffix}")

    # Recompute a deterministic sample through the reference path.  This is a
    # source-bound assignment check, not an independent RSLM implementation.
    if args.materializer.is_file() and sha256(args.materializer) != raw.get("runner_sha256"): failures.append("materializer binding")
    candidate_raw = {}
    candidate_receipt = {}
    if args.candidate_raw.is_file():
        try:
            candidate_raw = json.loads(args.candidate_raw.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            failures.append("candidate raw JSON")
    if args.candidate_receipt.is_file():
        try:
            candidate_receipt = json.loads(args.candidate_receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            failures.append("candidate receipt JSON")
    if candidate_receipt.get("execution_status") != "EXECUTED": failures.append("candidate receipt status")
    if candidate_receipt.get("raw_sha256") != maybe_sha256(args.candidate_raw): failures.append("candidate receipt/raw binding")
    if candidate_receipt.get("flat_file", {}).get("sha256") != maybe_sha256(args.candidate_flat): failures.append("candidate receipt/flat binding")
    counts = np.asarray([int(row["candidate_count"]) for row in candidate_raw.get("rows", [])], dtype=np.int64)
    if len(counts) != 152 or np.any(counts < 5000) or np.any(counts > 5099): failures.append("candidate cardinality")
    if not failures:
        total = int(np.sum(counts))
        if args.candidate_flat.stat().st_size != total * 148: failures.append("candidate flat size")
        records = np.memmap(args.candidate_flat, mode="r", dtype=np.uint8, shape=(total, 148))
        candidate_ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
        replay_ids = np.unique(candidate_ids)
        if not np.array_equal(replay_ids, ids.astype(np.int64)): failures.append("candidate ID replay")
        if sha256(ids_path) != raw.get("candidate_ids_sha256"): failures.append("candidate ID hash binding")

    if not failures:
        faithful = load_module("rslm_faithful_audit", args.reference)
        frontier = load_module("thq_frontier_audit", args.frontier_helper)
        count = args.documents.stat().st_size // (4 * D)
        documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(count, D))
        train_count = args.train_vectors.stat().st_size // (4 * D)
        train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(train_count, D)), dtype=np.float32)
        thq = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(count, 96))
        thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
        centroids = frontier.fit_centroids(train, thresholds)
        stored_centroids = np.fromfile(centroids_path, dtype="<f4").reshape(D, 4)
        if not np.array_equal(centroids, stored_centroids): failures.append("centroid replay")
        sample = np.unique(np.linspace(0, len(ids) - 1, min(16, len(ids)), dtype=np.int64))
        levels = frontier.unpack_thq(np.asarray(thq[ids[sample]]))
        base = centroids[np.arange(D)[None, :], levels]
        docs = np.asarray(documents[ids[sample]], dtype=np.float32)
        for bits in BITS:
            symbols, inner = faithful.encode(docs - base, bits)
            decoded = faithful.decode(symbols, inner, bits)
            combined = base + decoded
            ratio = np.sqrt(np.sum(docs * docs, axis=1) / np.maximum(np.sum(combined * combined, axis=1), np.finfo(np.float32).tiny))
            outer = np.asarray([faithful.ue7m9_encode(float(value)) for value in ratio], dtype="<u2")
            stored_symbols = np.memmap(args.materialization / f"rslm{bits}.symbols.u8", mode="r", dtype=np.uint8, shape=(len(ids), (D * bits + 7) // 8))[sample]
            stored_inner = np.memmap(args.materialization / f"rslm{bits}.inner-scale.u16", mode="r", dtype="<u2", shape=(len(ids),))[sample]
            stored_outer = np.memmap(args.materialization / f"rslm{bits}.outer-scale.u16", mode="r", dtype="<u2", shape=(len(ids),))[sample]
            if not np.array_equal(stored_symbols, symbols) or not np.array_equal(stored_inner, inner) or not np.array_equal(stored_outer, outer):
                failures.append(f"sample replay: RSLM{bits}")
    audit = {"schema_version": 2, "family": "thq_rslm_faithful_candidate_materialization_audit_v1", "status": "PASS" if not failures else "FAIL", "source_binding": not any(item.startswith("source binding:") or item.endswith("binding") for item in failures), "source_replay": False, "candidate_stream_replay": not bool(failures), "sample_replay": not bool(failures), "control_metric": CONTROL_METRIC, "production_serving_metric": PRODUCTION_METRIC, "production_payload_bytes": {"rslm3": 146, "rslm4": 194}, "materialization_raw_sha256": maybe_sha256(raw_path), "materializer_sha256": maybe_sha256(args.materializer), "candidate_flat_sha256": maybe_sha256(args.candidate_flat), "candidate_raw_sha256": maybe_sha256(args.candidate_raw), "candidate_receipt_sha256": maybe_sha256(args.candidate_receipt), "candidate_ids_sha256": maybe_sha256(ids_path), "thq4_centroids_sha256": maybe_sha256(centroids_path), "failures": failures}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(audit, indent=2, sort_keys=True))
    if failures: raise SystemExit(1)


if __name__ == "__main__":
    main()
