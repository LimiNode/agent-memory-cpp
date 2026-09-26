#!/usr/bin/env python3
"""Source-bound THQ4 -> Qdrant TurboQuant reference control (cosine lane).

This is a small, readable port of the public Qdrant TurboQuant normal mode,
not a claim of native serving compatibility. It pins the upstream revision,
three seeded Hadamard/permutation rounds, Lloyd-Max centroids, per-vector
length rescaling, and packed 1/2-bit payload sizes. TQ+ correction is a
separate follow-up because it requires fitting persisted shift/scale tables.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

D, THQ_BYTES, TOP, QUERY_COUNT = 384, 96, 128, 152
UPSTREAM_REVISION = "6ab21cac18ebb6f4ae29102c7f8f5cc11affd5de"
UPSTREAM_SOURCE = "qdrant/lib/quantization/src/turboquant/{rotation.rs,permutation.rs,lloyd_max.rs,quantization.rs}"
SEEDS = (654605292835415893, 8636605637963351413, 1775280196666917949)
CENTROIDS = {1: np.asarray((-0.7978846, 0.7978846), np.float32), 2: np.asarray((-1.510, -0.4528, 0.4528, 1.510), np.float32)}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok: raise RuntimeError(message)


def load_f32(path: Path, rows: int | None = None) -> np.ndarray:
    require(path.stat().st_size % (D * 4) == 0, f"not FP32x{D}: {path}")
    actual = path.stat().st_size // (D * 4); require(rows is None or actual == rows, f"unexpected row count: {path}")
    return np.asarray(np.memmap(path, mode="r", dtype="<f4", shape=(actual, D)), dtype=np.float32)


def top_ids(scores: np.ndarray, ids: np.ndarray, count: int) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:min(count, len(ids))]]


def ndcg10(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    rel = {int(doc): float(score) for doc, score in zip(qids, grades) if int(doc) >= 0 and float(score) > 0}
    gains = np.asarray([2.0 ** rel.get(int(doc), 0.0) - 1.0 for doc in ids[:10]], dtype=np.float64)
    ideal = np.sort(np.asarray([2.0 ** score - 1.0 for score in rel.values()], dtype=np.float64))[::-1][:10]
    den = np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal)))) if len(ideal) else 0.0
    return float(np.sum(gains / np.log2(np.arange(2, 2 + len(gains))) / den)) if den else 0.0


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    v = np.asarray(codes, dtype=np.uint8); out = np.empty((len(v), D), dtype=np.uint8)
    for b in range(THQ_BYTES):
        x = v[:, b]; out[:, 4 * b:4 * b + 4] = np.stack((x & 3, (x >> 2) & 3, (x >> 4) & 3, (x >> 6) & 3), axis=1)
    return out


def _permutation(dim: int, seed: int) -> np.ndarray:
    a, c, state = 6364136223846793005, 1442695040888963407, seed; values = np.arange(dim, dtype=np.int64)
    for i in range(dim - 1, 0, -1):
        state = (state * a + c) & ((1 << 64) - 1); j = (state >> 32) % (i + 1); values[i], values[j] = values[j], values[i]
    return values


def _chunks(dim: int) -> list[tuple[int, int]]:
    out, offset, left = [], 0, dim
    while left:
        size = 1 << (left.bit_length() - 1); out.append((offset, size)); offset += size; left -= size
    return out


def _wht_normalized(values: np.ndarray) -> None:
    for offset, size in _chunks(values.shape[1]):
        block = values[:, offset:offset + size]; h = 1
        while h < size:
            for j in range(h):
                a = block[:, j::2 * h].copy(); b = block[:, j + h::2 * h].copy(); block[:, j::2 * h] = a + b; block[:, j + h::2 * h] = a - b
            h *= 2
        block /= np.sqrt(float(size))


def rotate(values: np.ndarray) -> np.ndarray:
    out = np.asarray(values, dtype=np.float64).copy(); _wht_normalized(out)
    for seed in SEEDS: out = out[:, _permutation(D, seed)]; _wht_normalized(out)
    return out.astype(np.float32)


def inverse_rotate(values: np.ndarray) -> np.ndarray:
    out = np.asarray(values, dtype=np.float64).copy(); _wht_normalized(out)
    for seed in reversed(SEEDS):
        permutation = _permutation(D, seed); inverse = np.empty(D, dtype=np.int64); inverse[permutation] = np.arange(D); out = out[:, inverse]; _wht_normalized(out)
    return out.astype(np.float32)


def quantize_residual(residual: np.ndarray, bits: int) -> np.ndarray:
    norms = np.linalg.norm(residual.astype(np.float64), axis=1).astype(np.float32); safe = np.where(norms > 1e-12, norms, 1.0)
    rotated = rotate(residual) * (np.sqrt(float(D)) / safe)[:, None]; centroids = CENTROIDS[bits]; boundaries = (centroids[:-1] + centroids[1:]) * 0.5
    levels = np.sum(rotated[:, :, None] > boundaries[None, None, :], axis=2, dtype=np.uint8); decoded = inverse_rotate(centroids[levels]) * (safe / np.sqrt(float(D)))[:, None]; decoded[norms <= 1e-12] = 0.0
    return decoded.astype(np.float32)


def interval_top(query: np.ndarray, ids: np.ndarray, codes: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    levels = unpack_thq(np.asarray(codes[ids])); lut = np.empty((D, 4), dtype=np.float32)
    for d in range(D):
        for level in range(4):
            low = -np.inf if level == 0 else thresholds[d, level - 1]; high = np.inf if level == 3 else thresholds[d, level]; delta = low - query[d] if query[d] < low else query[d] - high if query[d] > high else 0.0; lut[d, level] = delta * delta
    return ids[np.lexsort((ids, np.sum(lut[np.arange(D)[None, :], levels], axis=1)))[:min(TOP, len(ids))]]


def self_test() -> None:
    rng = np.random.default_rng(20260922); x = rng.normal(size=(7, D)).astype(np.float32); require(np.max(np.abs(x - inverse_rotate(rotate(x)))) < 2e-5, "TurboQuant rotation inverse failed")
    for bits in (1, 2): require(np.isfinite(quantize_residual(x, bits)).all(), f"TurboQuant {bits}-bit decode failed")
    print("TurboQuant reference self-test: PASS")


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); names = ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt", "output")
    for name in names: parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path)
    parser.add_argument("--artifact", type=Path, help="persisted TurboQuant decode payload used by the independent audit")
    args = parser.parse_args()
    if args.self_test: self_test(); return
    if any(getattr(args, name.replace("-", "_")) is None for name in names): parser.error("all source and output paths are required")
    docs = load_f32(args.documents, 1_000_000); train = load_f32(args.train_vectors); queries = load_f32(args.queries, QUERY_COUNT); qids = np.asarray(np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20))); grades = np.asarray(np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20))); teacher = np.asarray(np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10)))
    raw = json.loads(args.candidate_raw.read_text(encoding="utf-8")); counts = np.asarray([int(row["candidate_count"]) for row in raw["rows"]], dtype=np.int64); offsets = np.concatenate(([0], np.cumsum(counts))); total = int(offsets[-1]); records = np.memmap(args.candidate_flat, mode="r", dtype=np.uint8, shape=(total, 148)); candidate_ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64); receipt = json.loads(args.candidate_receipt.read_text(encoding="utf-8")); require(receipt.get("execution_status") == "EXECUTED" and receipt.get("raw_sha256") == sha256(args.candidate_raw) and receipt.get("flat_file", {}).get("sha256") == sha256(args.candidate_flat), "candidate provenance differs")
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3); train_levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8); centroids = np.empty((D, 4), np.float32); fallback = np.mean(train, axis=0)
    for d in range(D):
        for level in range(4):
            values = train[train_levels[:, d] == level, d]; centroids[d, level] = np.mean(values) if len(values) else fallback[d]
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES)); selected_rows = [interval_top(queries[qi], candidate_ids[offsets[qi]:offsets[qi + 1]], thq_codes, thresholds) for qi in range(QUERY_COUNT)]; selected_unique = np.unique(np.concatenate(selected_rows)); levels = unpack_thq(np.asarray(thq_codes[selected_unique])); base = centroids[np.arange(D)[None, :], levels]; residual = np.asarray(docs[selected_unique], np.float32) - base; decoded = {bits: quantize_residual(residual, bits) for bits in (1, 2)}; pos = {int(doc): i for i, doc in enumerate(selected_unique)}
    rows = []
    artifact = args.artifact or args.output.with_suffix(".payload.npz")
    selected_flat = np.concatenate(selected_rows).astype(np.int64)
    offsets_out = np.concatenate(([0], np.cumsum([len(row) for row in selected_rows], dtype=np.int64)))
    artifact.parent.mkdir(parents=True, exist_ok=True)
    np.savez(artifact, document_ids=np.asarray(selected_unique, dtype=np.int64), base=np.asarray(base, dtype=np.float32), decoded1=np.asarray(decoded[1], dtype=np.float32), decoded2=np.asarray(decoded[2], dtype=np.float32), row_ids=selected_flat, row_offsets=offsets_out)
    for qi, query in enumerate(queries):
        ids = selected_rows[qi]; indexes = np.asarray([pos[int(doc)] for doc in ids]); selected_base = base[indexes]
        for bits in (1, 2):
            reconstructed = selected_base + decoded[bits][indexes]; scores = (reconstructed @ query) / np.maximum(np.linalg.norm(reconstructed, axis=1) * np.linalg.norm(query), np.finfo(np.float64).tiny); ranked = top_ids(scores, ids, 10); rows.append({"query": qi, "arm": f"turboquant{bits}", "top10_ids": ranked.astype(int).tolist(), "thq4_top128_ids": ids.astype(int).tolist(), "qrels_ndcg10": ndcg10(ranked, qids[qi], grades[qi]), "teacher_overlap": float(np.isin(teacher[qi], ranked).sum() / 10), "side_payload_bytes": bits * D // 8 + 4, "cascade_total_bytes": THQ_BYTES + bits * D // 8 + 4})
    summaries = {arm: {"mean_qrels_ndcg10": float(np.mean([r["qrels_ndcg10"] for r in rows if r["arm"] == arm])), "p05_qrels_ndcg10": float(np.percentile([r["qrels_ndcg10"] for r in rows if r["arm"] == arm], 5)), "worst_qrels_ndcg10": float(np.min([r["qrels_ndcg10"] for r in rows if r["arm"] == arm]))} for arm in ("turboquant1", "turboquant2")}; sources = {name: getattr(args, name.replace("-", "_")) for name in names[:-1]}
    result = {"schema_version": 2, "family": "thq_turboquant_reference_gate_c_v1", "status": "EXECUTED", "source_replay": True, "metric": "cosine", "codec_metric": "dot-compatible residual reconstruction; final ranking cosine", "upstream_revision": UPSTREAM_REVISION, "upstream_source": UPSTREAM_SOURCE, "rotation": "three seeded normalized WHT rounds; exact Qdrant seeds", "bits": [1, 2], "query_count": QUERY_COUNT, "selected_unique_documents": int(len(selected_unique)), "source_hashes": {name: sha256(path) for name, path in sources.items()}, "runner_sha256": sha256(Path(__file__)), "artifact_path": str(artifact), "artifact_sha256": sha256(artifact), "payload_contract": {"side_payload_bytes": {"turboquant1": 52, "turboquant2": 100}, "persisted_fields": ["document_ids", "base", "decoded1", "decoded2", "row_ids", "row_offsets"]}, "summaries": summaries, "rows": rows, "limitations": ["TurboQuant normal mode only; TQ+ shift/scale correction is a separate gate", "candidate-local THQ top128 replay; not native serving latency", "residuals use the Dot-compatible length metadata path; this is not Qdrant DistanceType::Cosine encoding"]}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__": main()
