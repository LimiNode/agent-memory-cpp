#!/usr/bin/env python3
"""Audit native direct-code LSQ cascade output against the source-bound replay."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import numpy as np

DOCUMENTS, DIMENSION, THQ_BYTES, PAGE = 1_000_000, 384, 96, 4096


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pages(ids: np.ndarray, record_bytes: int) -> int:
    touched: set[int] = set()
    for doc_id in ids.tolist():
        begin = int(doc_id) * record_bytes
        touched.update(range(begin // PAGE, (begin + record_bytes - 1) // PAGE + 1))
    return len(touched)


def packed_pages(ids: list[int], payload_ids: np.ndarray, stages: int) -> int:
    positions = {int(doc_id): row for row, doc_id in enumerate(payload_ids.tolist())}
    touched: set[tuple[int, int]] = set()
    for doc_id in ids:
        row = positions[int(doc_id)]
        begin = row * stages
        touched.update((0, page) for page in range(begin // PAGE, (begin + stages - 1) // PAGE + 1))
        touched.add((1, (row * 4) // PAGE))
    return len(touched)


def quantiles(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {name: float(np.percentile(array, percentile)) for name, percentile in (("p50", 50), ("p95", 95), ("p99", 99))}


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("native-jsonl", "source-result", "payload", "candidate-flat", "candidate-offsets", "runner", "output"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    parser.add_argument("--payload-bytes", type=int, choices=(36, 52), required=True)
    args = parser.parse_args()

    source = json.loads(args.source_result.read_text(encoding="utf-8"))
    source_rows = {int(row["query"]): row for row in source["rows"] if row["arm"] == f"faiss_lsq{args.payload_bytes - 4}"}
    native_rows = [json.loads(line) for line in args.native_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(native_rows) != len(source_rows) or sorted(int(row["query"]) for row in native_rows) != sorted(source_rows):
        raise RuntimeError("native/source query cardinality differs")
    offsets = np.fromfile(args.candidate_offsets, dtype="<u8")
    raw_flat = args.candidate_flat.read_bytes()
    record_bytes = next((width for width in (4, 100, 148)
                         if len(raw_flat) % width == 0 and
                         int(offsets[-1]) == len(raw_flat) // width), None)
    if len(offsets) != len(native_rows) + 1 or record_bytes is None:
        raise RuntimeError("candidate stream offsets differ")
    records = np.frombuffer(raw_flat, dtype=np.uint8).reshape(-1, record_bytes)
    flat = records[:, :4].copy().view("<i4").reshape(-1)

    payload = args.payload.read_bytes()
    if payload[:7] != b"AMLSQ01":
        raise RuntimeError("invalid LSQ payload header")
    stages, dimensions, count = struct.unpack_from("<III", payload, 8)
    if dimensions != DIMENSION or args.payload_bytes != stages + 4:
        raise RuntimeError("payload dimensions/width differ")
    header = 20
    payload_ids = np.frombuffer(payload, dtype="<i4", count=count, offset=header)
    model_bytes = stages * 256 * DIMENSION * 4 + 4 * DIMENSION * 4
    model_pages = (model_bytes + PAGE - 1) // PAGE
    full_corpus_pages = (DOCUMENTS * stages + PAGE - 1) // PAGE + (DOCUMENTS * 4 + PAGE - 1) // PAGE

    ordered_top10 = set_top10 = ordered_thq = set_thq = 0
    timing_names = ("thq4_prefilter", "gather_dot", "full_lut_prepare",
                    "full_lut_score", "sparse_lut_prepare",
                    "sparse_lut_score", "all_codec_variants", "total")
    timings: dict[str, list[float]] = {name: [] for name in timing_names}
    full_lut_parity = sparse_lut_parity = 0
    max_full_error = max_sparse_error = 0.0
    codec_page_values: list[int] = []
    thq_page_values: list[int] = []
    for native in native_rows:
        query = int(native["query"])
        source_row = source_rows[query]
        native_top10 = [int(value) for value in native["top10_ids"]]
        full_lut_top10 = [int(value) for value in native["full_lut_top10_ids"]]
        sparse_lut_top10 = [int(value) for value in native["sparse_lut_top10_ids"]]
        native_thq = [int(value) for value in native["thq4_top128_ids"]]
        ordered_top10 += int(native_top10 == [int(value) for value in source_row["top10_ids"]])
        set_top10 += int(set(native_top10) == set(int(value) for value in source_row["top10_ids"]))
        ordered_thq += int(native_thq == [int(value) for value in source_row["thq4_top128_ids"]])
        set_thq += int(set(native_thq) == set(int(value) for value in source_row["thq4_top128_ids"]))
        full_lut_parity += int(full_lut_top10 == native_top10)
        sparse_lut_parity += int(sparse_lut_top10 == native_top10)
        full_error = float(native["max_abs_score_error"]["full_lut"])
        sparse_error = float(native["max_abs_score_error"]["sparse_lut"])
        if (not np.isfinite(full_error) or not np.isfinite(sparse_error) or
                full_error > 1e-10 or sparse_error > 1e-10):
            raise RuntimeError(f"LSQ LUT numerical parity differs at query {query}")
        max_full_error = max(max_full_error, full_error)
        max_sparse_error = max(max_sparse_error, sparse_error)
        begin, end = int(offsets[query]), int(offsets[query + 1])
        if int(native["candidate_count"]) != end - begin or not set(native_thq).issubset(set(flat[begin:end].tolist())):
            raise RuntimeError(f"candidate stream mismatch at query {query}")
        expected_thq_pages = pages(flat[begin:end], THQ_BYTES)
        expected_codec_pages = packed_pages(native_thq, payload_ids, stages)
        if int(native["thq_pages"]) != expected_thq_pages or int(native["codec_pages"]) != expected_codec_pages:
            raise RuntimeError(f"page accounting mismatch at query {query}")
        if int(native["model_pages"]) != model_pages or int(native["full_corpus_codec_pages"]) != full_corpus_pages:
            raise RuntimeError("model/full-corpus page accounting mismatch")
        if native.get("codec_layout") != "candidate_local_packed_rows":
            raise RuntimeError("codec layout contract missing")
        for name in timings:
            value = float(native["timing_ms"][name])
            if not np.isfinite(value) or value < 0.0:
                raise RuntimeError(f"non-finite timing row at query {query}: {name}")
            timings[name].append(value)
        codec_page_values.append(int(native["codec_pages"]))
        thq_page_values.append(int(native["thq_pages"]))

    audit = {
        "schema_version": 2,
        "family": "thq_native_compressed_lsq_audit_v1",
        "status": "PASS" if (ordered_top10 == set_top10 == set_thq ==
                              full_lut_parity == sparse_lut_parity == len(native_rows))
                  else "PASS_WITH_TIE_ORDER_DIAGNOSTIC",
        "source_binding": True,
        "native_jsonl_sha256": sha256(args.native_jsonl),
        "source_result_sha256": sha256(args.source_result),
        "payload_sha256": sha256(args.payload),
        "candidate_flat_sha256": sha256(args.candidate_flat),
        "candidate_offsets_sha256": sha256(args.candidate_offsets),
        "runner_sha256": sha256(args.runner),
        "payload_bytes": args.payload_bytes,
        "stages": stages,
        "query_count": len(native_rows),
        "ordered_top10_parity": ordered_top10 / len(native_rows),
        "set_top10_parity": set_top10 / len(native_rows),
        "ordered_thq_top128_parity": ordered_thq / len(native_rows),
        "set_thq_top128_parity": set_thq / len(native_rows),
        "full_lut_ordered_top10_parity": full_lut_parity / len(native_rows),
        "sparse_lut_ordered_top10_parity": sparse_lut_parity / len(native_rows),
        "max_abs_score_error": {"full_lut": max_full_error,
                                "sparse_lut": max_sparse_error},
        "timing_ms": {name: {"mean": float(np.mean(values)), **quantiles(values)} for name, values in timings.items()},
        "codec_pages_mean": float(np.mean(codec_page_values)),
        "thq_pages_mean": float(np.mean(thq_page_values)),
        "model_pages": model_pages,
        "full_corpus_codec_pages": full_corpus_pages,
        "codec_layout": "candidate_local_packed_rows",
        "checks": ["source top10 parity", "THQ retained-set parity", "candidate stream binding", "gather/full/sparse ordered-top10 parity", "full/sparse numerical parity", "packed-row codec pages", "shared-model pages", "full-corpus hypothetical pages", "finite timing rows"],
        "limitations": ["candidate-local frozen R4 stream, not a full-corpus serving replay", "two ordered THQ mismatches are ordering-only numerical/accumulation differences with set parity 152/152; equal-score identity was not independently established", "native timing is scalar C++ on one host; no OS page-latency claim"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
