#!/usr/bin/env python3
"""Materialize a reproducible packed-INT8 MDBX prototype fixture.

The fixture intentionally uses exact-FP32 top-128 candidates.  This isolates
physical MDBX read amplification and packed cosine scoring; it is not routed
quality evidence and must not be presented as Prototype-IVF or R4 latency.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_harness() -> object:
    path = Path(__file__).with_name("evaluate-fresh-qrels.py")
    spec = importlib.util.spec_from_file_location("fresh_qrels", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load fresh qrels harness")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--candidate-k", type=int, default=128)
    parser.add_argument("--chunk-rows", type=int, default=65536)
    args = parser.parse_args()
    if args.candidate_k <= 0 or args.chunk_rows <= 0:
        raise ValueError("candidate-k and chunk-rows must be positive")
    fresh = load_harness()
    data = fresh.load_root(args.evaluation_root)
    documents = data["documents"]
    document_count, dimension = documents.shape
    output = args.output_root
    output.mkdir(parents=True, exist_ok=True)
    codes_path = output / "codes.i8"
    scales_path = output / "scales.f32"
    thq_path = output / "thq.u8"
    query_path = output / "queries.f32"
    candidates_path = output / "candidates.u32"
    expected_path = output / "expected.u32"
    scales = np.memmap(scales_path, dtype="<f4", mode="w+", shape=(document_count,))
    codes = np.memmap(codes_path, dtype="i1", mode="w+", shape=(document_count, dimension))
    for begin in range(0, document_count, args.chunk_rows):
        end = min(document_count, begin + args.chunk_rows)
        block = np.asarray(documents[begin:end], dtype=np.float32)
        scale = np.max(np.abs(block), axis=1) / 127.0
        scale[scale == 0.0] = 1.0
        scales[begin:end] = scale
        codes[begin:end] = np.rint(block / scale[:, None]).clip(-127, 127).astype(np.int8)
    scales.flush(); codes.flush()
    np.zeros(document_count * 96, dtype=np.uint8).tofile(thq_path)
    np.asarray(data["queries"], dtype="<f4").tofile(query_path)
    candidates = np.empty((len(data["query_ids"]), args.candidate_k), dtype=np.uint32)
    expected = np.empty((len(data["query_ids"]), 10), dtype=np.uint32)
    query_norms = np.linalg.norm(np.asarray(data["queries"], dtype=np.float32), axis=1)
    for position in range(len(data["query_ids"])):
        exact_positions, _ = fresh.exact_top(data, position, args.candidate_k)
        candidates[position] = exact_positions.astype(np.uint32)
        selected = np.asarray(codes[exact_positions], dtype=np.float32)
        score = selected @ np.asarray(data["queries"][position], dtype=np.float32)
        code_norm = np.linalg.norm(selected, axis=1)
        score = score / np.maximum(code_norm * query_norms[position], np.finfo(np.float32).tiny)
        order = np.lexsort((exact_positions, -score))
        expected[position] = exact_positions[order[:10]].astype(np.uint32)
    candidates.tofile(candidates_path); expected.tofile(expected_path)
    receipt = {
        "schema_version": 1,
        "family": "native_mdbx_packed_int8_prototype_fixture_v1",
        "status": "EXECUTED",
        "metric": "reconstructed_cosine_exact",
        "documents": document_count,
        "dimension": dimension,
        "queries": len(data["query_ids"]),
        "candidate_k": args.candidate_k,
        "candidate_source": "exact FP32 top-k; physical-read isolation only",
        "payload": {"code_bytes_per_document": dimension, "scale_bytes_per_document": 4, "thq_bytes_per_document": 96},
        "source": {"materialization_manifest_sha256": data["manifest_sha256"], "document_vectors_sha256": data["document_vectors_sha256"], "query_vectors_sha256": data["query_vectors_sha256"]},
        "outputs": {path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)} for path in (codes_path, scales_path, thq_path, query_path, candidates_path, expected_path)},
        "limitations": ["exact-oracle candidates are not a routed serving mode", "MDBX lifecycle and crash/recovery require the native runner", "quantizer is a benchmark INT8 proxy, not a frozen finalist artifact"],
    }
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output / 'receipt.json'), "documents": document_count, "queries": len(data["query_ids"]), "candidate_k": args.candidate_k}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        raise SystemExit(f"prepare-mdbx-prototype-fixture: {error}")
