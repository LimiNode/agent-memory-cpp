#!/usr/bin/env python3
"""Source-bound bounded TurboQuant+ residual control.

This runner follows Qdrant's public TQMode::Plus algebra (global per-coordinate
shift/scale and the per-vector error-correction scalar) on the canonical THQ
candidate shell.  It is intentionally named a reference *control*: fitting the
shift/scale table is explicit and persisted, while QJL/native SIMD and wire
compatibility are outside this gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from importlib.util import module_from_spec, spec_from_file_location

_normal_path = Path(__file__).with_name("run-thq-turboquant-reference.py")
_spec = spec_from_file_location("tq_normal", _normal_path)
_normal = module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_normal)

D, THQ_BYTES, TOP, QUERY_COUNT = _normal.D, _normal.THQ_BYTES, _normal.TOP, _normal.QUERY_COUNT
UPSTREAM_REVISION = _normal.UPSTREAM_REVISION
UPSTREAM_SOURCE = _normal.UPSTREAM_SOURCE


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def unit_rows(values: np.ndarray) -> np.ndarray:
    rows = np.asarray(values, dtype=np.float32)
    norms = np.linalg.norm(rows.astype(np.float64), axis=1)
    return (rows / np.maximum(norms, np.finfo(np.float64).tiny)[:, None]).astype(np.float32)


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    return _normal.unpack_thq(codes)


def interval_top(query: np.ndarray, ids: np.ndarray, codes: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    return _normal.interval_top(query, ids, codes, thresholds)


def top_ids(scores: np.ndarray, ids: np.ndarray, count: int) -> np.ndarray:
    return _normal.top_ids(scores, ids, count)


def ndcg10(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    return _normal.ndcg10(ids, qids, grades)


def fit_levels(train: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    centroids = np.empty((D, 4), dtype=np.float32)
    fallback = np.mean(train, axis=0)
    for d in range(D):
        for level in range(4):
            values = train[levels[:, d] == level, d]
            centroids[d, level] = np.mean(values) if len(values) else fallback[d]
    return centroids


def fit_error_correction(train_residual: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rotated = _normal.rotate(train_residual)
    lengths = np.linalg.norm(rotated.astype(np.float64), axis=1)
    pre = rotated * (np.sqrt(float(D)) / np.maximum(lengths, 1e-12))[:, None]
    shift = -np.mean(pre, axis=0)
    std = np.std(pre, axis=0)
    scale = 1.0 / np.maximum(std, 1e-4)
    return shift.astype(np.float32), scale.astype(np.float32)


def encode_plus(residual: np.ndarray, shift: np.ndarray, scale: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rotated = _normal.rotate(residual)
    lengths = np.linalg.norm(rotated.astype(np.float64), axis=1).astype(np.float32)
    safe = np.maximum(lengths, 1e-12)
    pre = rotated * (np.sqrt(float(D)) / safe)[:, None]
    ec = np.sum(pre * (-shift[None, :]), axis=1).astype(np.float32)
    plus = (pre + shift[None, :]) * scale[None, :]
    levels = (plus > 0.0).astype(np.uint8)
    return levels, lengths, ec, pre


def decode_plus(levels: np.ndarray, lengths: np.ndarray, shift: np.ndarray, scale: np.ndarray) -> np.ndarray:
    values = ((2.0 * levels.astype(np.float32) - 1.0) / scale[None, :]) - shift[None, :]
    pre = values * (lengths / np.sqrt(float(D)))[:, None]
    return _normal.inverse_rotate(pre)


def main() -> None:
    parser = argparse.ArgumentParser()
    names = ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt", "output")
    for name in names:
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()
    if any(getattr(args, name.replace("-", "_")) is None for name in names):
        parser.error("all source and output paths are required")

    docs = _normal.load_f32(args.documents, 1_000_000)
    train = unit_rows(_normal.load_f32(args.train_vectors))
    queries = unit_rows(_normal.load_f32(args.queries, QUERY_COUNT))
    qids = np.asarray(np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20)))
    grades = np.asarray(np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20)))
    teacher = np.asarray(np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10)))
    raw = json.loads(args.candidate_raw.read_text(encoding="utf-8"))
    counts = np.asarray([int(row["candidate_count"]) for row in raw["rows"]], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts)))
    records = np.memmap(args.candidate_flat, mode="r", dtype=np.uint8, shape=(int(offsets[-1]), 148))
    candidate_ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    receipt = json.loads(args.candidate_receipt.read_text(encoding="utf-8"))
    require(receipt.get("execution_status") == "EXECUTED" and receipt.get("raw_sha256") == sha256(args.candidate_raw) and receipt.get("flat_file", {}).get("sha256") == sha256(args.candidate_flat), "candidate provenance differs")
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    train_centroids = fit_levels(train, thresholds)
    train_levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    train_base = train_centroids[np.arange(D)[None, :], train_levels]
    shift, scale = fit_error_correction(train - train_base)
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    selected_rows = [interval_top(queries[qi], candidate_ids[offsets[qi]:offsets[qi + 1]], thq_codes, thresholds) for qi in range(QUERY_COUNT)]
    selected_unique = np.unique(np.concatenate(selected_rows))
    levels = unpack_thq(np.asarray(thq_codes[selected_unique]))
    base = train_centroids[np.arange(D)[None, :], levels]
    residual = np.asarray(docs[selected_unique], dtype=np.float32) - base
    codes, lengths, ec, _ = encode_plus(residual, shift, scale)
    decoded = decode_plus(codes, lengths, shift, scale)
    pos = {int(doc): i for i, doc in enumerate(selected_unique)}
    row_ids = np.concatenate(selected_rows).astype(np.int64)
    row_offsets = np.concatenate(([0], np.cumsum([len(row) for row in selected_rows], dtype=np.int64)))
    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    np.savez(args.artifact, document_ids=np.asarray(selected_unique, dtype=np.int64), base=np.asarray(base, dtype=np.float32), codes=np.asarray(codes, dtype=np.uint8), lengths=np.asarray(lengths, dtype=np.float32), ec_correction=np.asarray(ec, dtype=np.float32), shift=shift, scale=scale, row_ids=row_ids, row_offsets=row_offsets)
    rows = []
    for qi, query in enumerate(queries):
        ids = selected_rows[qi]
        indexes = np.asarray([pos[int(doc)] for doc in ids])
        direct_vec = base[indexes] + decoded[indexes]
        direct_scores = (direct_vec @ query) / np.maximum(np.linalg.norm(direct_vec, axis=1) * np.linalg.norm(query), np.finfo(np.float64).tiny)
        direct_ranked = top_ids(direct_scores, ids, 10)
        rows.append({"query": qi, "arm": "turboquant_plus1_direct", "top10_ids": direct_ranked.astype(int).tolist(), "thq4_top128_ids": ids.astype(int).tolist(), "qrels_ndcg10": ndcg10(direct_ranked, qids[qi], grades[qi]), "teacher_overlap": float(np.isin(teacher[qi], direct_ranked).sum() / 10), "side_payload_bytes": 56, "cascade_total_bytes": THQ_BYTES + 56})
        rotated_q = _normal.rotate(query[None, :])[0]
        q_plus = (rotated_q / scale).astype(np.float32)
        qm = float(np.dot(rotated_q, -shift))
        raw_residual = (codes[indexes] * 2.0 - 1.0) @ q_plus + qm
        asym_scores = (base[indexes] @ query) + raw_residual * (lengths[indexes] / np.sqrt(float(D)))
        asym_ranked = top_ids(asym_scores, ids, 10)
        rows.append({"query": qi, "arm": "turboquant_plus1_asymmetric", "top10_ids": asym_ranked.astype(int).tolist(), "thq4_top128_ids": ids.astype(int).tolist(), "qrels_ndcg10": ndcg10(asym_ranked, qids[qi], grades[qi]), "teacher_overlap": float(np.isin(teacher[qi], asym_ranked).sum() / 10), "side_payload_bytes": 56, "cascade_total_bytes": THQ_BYTES + 56, "query_ephemeral_bytes": 56})
    summaries = {arm: {"mean_qrels_ndcg10": float(np.mean([r["qrels_ndcg10"] for r in rows if r["arm"] == arm])), "p05_qrels_ndcg10": float(np.percentile([r["qrels_ndcg10"] for r in rows if r["arm"] == arm], 5)), "worst_qrels_ndcg10": float(np.min([r["qrels_ndcg10"] for r in rows if r["arm"] == arm]))} for arm in ("turboquant_plus1_direct", "turboquant_plus1_asymmetric")}
    sources = {name: getattr(args, name.replace("-", "_")) for name in names[:-1]}
    result = {"schema_version": 1, "family": "thq_turboquant_plus_reference_gate_c_v1", "status": "EXECUTED", "source_replay": True, "metric": "cosine", "codec_metric": "Qdrant TQMode::Plus algebra on a residual control", "upstream_revision": UPSTREAM_REVISION, "upstream_source": UPSTREAM_SOURCE, "bits": 1, "query_count": QUERY_COUNT, "selected_unique_documents": int(len(selected_unique)), "source_hashes": {name: sha256(path) for name, path in sources.items()}, "runner_sha256": sha256(Path(__file__)), "artifact_path": str(args.artifact), "artifact_sha256": sha256(args.artifact), "payload_contract": {"side_payload_bytes": 56, "fields": ["48-bit sign code", "float32 residual length", "float32 ec_correction"], "global_metadata_bytes": int(shift.nbytes + scale.nbytes)}, "fit_contract": "shift=-mean(normalized rotated THQ residual), scale=1/std with 1e-4 floor; fit uses 25k train rows only", "summaries": summaries, "rows": rows, "limitations": ["bounded source-pinned algebraic control, not a Qdrant wire-format artifact", "no QJL residual estimator or native SIMD", "candidate-local THQ top128 replay"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
