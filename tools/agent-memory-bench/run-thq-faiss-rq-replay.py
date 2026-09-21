#!/usr/bin/env python3
"""Full canonical THQ4 + Faiss ResidualQuantizer 32/48-byte replay."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np


D = 384
THQ_BYTES = 96
TOP = 128
QUERY_COUNT = 152
PAYLOADS = (32, 48)
SEED = 20260921


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    bits = np.unpackbits(np.asarray(codes, dtype=np.uint8), axis=1, bitorder="little")
    return (bits[:, 0::2] + 2 * bits[:, 1::2]).astype(np.uint8)


def top_ids(scores: np.ndarray, ids: np.ndarray, count: int = 10) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:count]]


def ndcg10(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    relevance = {int(doc): float(value) for doc, value in zip(qids, grades)
                 if int(doc) >= 0 and float(value) > 0.0}
    gains = np.asarray([2.0 ** relevance.get(int(doc), 0.0) - 1.0 for doc in ids[:10]])
    ideal = np.sort(np.asarray([2.0 ** value - 1.0 for value in relevance.values()]))[::-1][:10]
    dcg = float(np.sum(gains / np.log2(np.arange(2, 2 + len(gains)))))
    idcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal))))) if len(ideal) else 0.0
    return dcg / idcg if idcg else 0.0


def cosine(values: np.ndarray, query: np.ndarray) -> np.ndarray:
    qnorm = max(float(np.linalg.norm(query)), np.finfo(np.float32).tiny)
    norms = np.maximum(np.linalg.norm(values, axis=1) * qnorm, np.finfo(np.float32).tiny)
    return np.einsum("kd,d->k", values, query) / norms


def load_candidates(flat: Path, raw: Path, receipt: Path) -> tuple[np.ndarray, np.ndarray]:
    rows = json.loads(raw.read_text(encoding="utf-8")).get("rows")
    if not isinstance(rows, list) or len(rows) != QUERY_COUNT:
        raise RuntimeError("candidate raw must contain 152 rows")
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    total = int(offsets[-1])
    if flat.stat().st_size != total * 148:
        raise RuntimeError("candidate flat/raw cardinality mismatch")
    records = np.memmap(flat, mode="r", dtype=np.uint8, shape=(total, 148))
    ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    if np.any(ids < 0) or np.any(ids >= 1_000_000):
        raise RuntimeError("candidate document ID outside corpus")
    for start, stop in zip(offsets[:-1], offsets[1:]):
        if len(np.unique(ids[start:stop])) != int(stop - start):
            raise RuntimeError("candidate row contains duplicate document IDs")
    receipt_data = json.loads(receipt.read_text(encoding="utf-8"))
    if receipt_data.get("execution_status") != "EXECUTED":
        raise RuntimeError("candidate receipt is not EXECUTED")
    if receipt_data.get("raw_sha256") != sha256(raw):
        raise RuntimeError("candidate raw receipt binding differs")
    if receipt_data.get("flat_file", {}).get("sha256") != sha256(flat):
        raise RuntimeError("candidate flat receipt binding differs")
    return ids, offsets


def fit_centroids(train: np.ndarray, thresholds: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    centroids = np.empty((D, 4), dtype=np.float32)
    fallback = np.mean(train, axis=0, dtype=np.float64).astype(np.float32)
    for coordinate in range(D):
        for level in range(4):
            values = train[levels[:, coordinate] == level, coordinate]
            centroids[coordinate, level] = float(np.mean(values)) if len(values) else fallback[coordinate]
    return centroids, levels


def interval_top(query: np.ndarray, ids: np.ndarray, codes: np.ndarray,
                 thresholds: np.ndarray) -> np.ndarray:
    levels = unpack_thq(np.asarray(codes[ids]))
    lut = np.empty((D, 4), dtype=np.float32)
    for coordinate in range(D):
        for level in range(4):
            low = -np.inf if level == 0 else thresholds[coordinate, level - 1]
            high = np.inf if level == 3 else thresholds[coordinate, level]
            delta = (low - query[coordinate] if query[coordinate] < low else
                     query[coordinate] - high if query[coordinate] > high else 0.0)
            lut[coordinate, level] = delta * delta
    scores = np.sum(lut[np.arange(D)[None, :], levels], axis=1)
    return ids[np.lexsort((ids, scores))[:min(TOP, len(ids))]]


def fit_faiss(residual: np.ndarray, iterations: int, fit_beam: int,
              clustering_seed: int, threads: int):
    try:
        import faiss
    except ImportError as exc:
        raise RuntimeError("Faiss replay requires faiss-cpu==1.15.0") from exc
    faiss.omp_set_num_threads(int(threads))
    quantizer = faiss.ResidualQuantizer(D, max(PAYLOADS), 8)
    quantizer.train_type = faiss.ResidualQuantizer.Train_default
    quantizer.cp.niter = int(iterations)
    quantizer.cp.seed = int(clustering_seed)
    quantizer.max_beam_size = int(fit_beam)
    quantizer.verbose = False
    started = time.perf_counter()
    quantizer.train(np.ascontiguousarray(residual, dtype=np.float32))
    seconds = time.perf_counter() - started
    codebooks = faiss.vector_to_array(quantizer.codebooks).astype(np.float32, copy=True)
    if codebooks.size != max(PAYLOADS) * 256 * D or not np.isfinite(codebooks).all():
        raise RuntimeError("Faiss returned an invalid shared codebook sequence")
    return (codebooks.reshape(max(PAYLOADS), 256, D), seconds,
            faiss.__version__, faiss.omp_get_max_threads())


def prefix_quantizer(codebooks: np.ndarray, stages: int, encode_beam: int):
    import faiss
    quantizer = faiss.ResidualQuantizer(D, stages, 8)
    faiss.copy_array_to_vector(np.ascontiguousarray(codebooks[:stages]).reshape(-1),
                               quantizer.codebooks)
    quantizer.is_trained = True
    quantizer.max_beam_size = int(encode_beam)
    quantizer.compute_codebook_tables()
    if int(quantizer.code_size) != stages:
        raise RuntimeError("Faiss prefix code size differs from side payload")
    return quantizer


def self_test() -> None:
    rng = np.random.default_rng(SEED)
    values = rng.normal(size=(1024, D)).astype(np.float32)
    codebooks, _seconds, version, threads = fit_faiss(
        values, iterations=1, fit_beam=1, clustering_seed=SEED, threads=1)
    q32 = prefix_quantizer(codebooks, 32, 1)
    codes = q32.compute_codes(values[:8])
    decoded = q32.decode(codes)
    if codes.shape != (8, 32) or decoded.shape != (8, D):
        raise RuntimeError("Faiss prefix encode/decode shape mismatch")
    if not np.isfinite(decoded).all() or not version or threads != 1:
        raise RuntimeError("Faiss prefix encode/decode produced invalid output")
    print("THQ Faiss RQ replay self-test: PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    for name in ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores",
                 "teacher-ids", "thq4-codes", "thq4-thresholds", "candidate-flat",
                 "candidate-raw", "candidate-receipt", "output", "models-output",
                 "codes-output"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path)
    parser.add_argument("--fit-rows", type=int, default=0)
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--fit-beam", type=int, default=1)
    parser.add_argument("--encode-beam", type=int, default=8)
    parser.add_argument("--faiss-seed", type=int, default=SEED)
    parser.add_argument("--faiss-threads", type=int, default=8)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    required = (args.documents, args.train_vectors, args.queries, args.qrel_ids,
                args.qrel_scores, args.teacher_ids, args.thq4_codes,
                args.thq4_thresholds, args.candidate_flat, args.candidate_raw,
                args.candidate_receipt, args.output, args.models_output,
                args.codes_output)
    if any(path is None for path in required):
        parser.error("all source and output paths are required")
    if (args.iterations < 1 or args.fit_beam < 1 or args.encode_beam < 1
            or args.faiss_threads < 1 or args.fit_rows < 0):
        parser.error("iteration and beam values must be positive; fit rows must be non-negative")
    if args.documents.stat().st_size != 1_000_000 * D * 4:
        raise RuntimeError("document source must contain 1M FP32x384 rows")
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    if args.train_vectors.stat().st_size % (D * 4):
        raise RuntimeError("training source is not an FP32x384 matrix")
    train_count = args.train_vectors.stat().st_size // (D * 4)
    train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4",
                                 shape=(train_count, D)), dtype=np.float32)
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4")
    if thresholds.size != D * 3:
        raise RuntimeError("THQ thresholds must contain 384x3 values")
    thresholds = thresholds.reshape(D, 3)
    if args.thq4_codes.stat().st_size != 1_000_000 * THQ_BYTES:
        raise RuntimeError("THQ codes must contain 1M x 96 bytes")
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8,
                          shape=(1_000_000, THQ_BYTES))
    queries = np.asarray(np.memmap(args.queries, mode="r", dtype="<f4",
                                   shape=(QUERY_COUNT, D)), dtype=np.float32)
    qrel_ids = np.asarray(np.memmap(args.qrel_ids, mode="r", dtype="<i8",
                                    shape=(QUERY_COUNT, 20)))
    qrel_scores = np.asarray(np.memmap(args.qrel_scores, mode="r", dtype="<f4",
                                       shape=(QUERY_COUNT, 20)))
    teacher_ids = np.asarray(np.memmap(args.teacher_ids, mode="r", dtype="<i8",
                                       shape=(QUERY_COUNT, 10)))
    candidate_ids, offsets = load_candidates(args.candidate_flat, args.candidate_raw,
                                              args.candidate_receipt)

    centroids, train_levels = fit_centroids(train, thresholds)
    train_base = centroids[np.arange(D)[None, :], train_levels]
    if args.fit_rows:
        if args.fit_rows < 256 or args.fit_rows > train_count:
            raise RuntimeError("fit-rows must be zero or in [256, training_count]")
        fit_indices = np.linspace(0, train_count - 1, args.fit_rows, dtype=np.int64)
    else:
        fit_indices = np.arange(train_count, dtype=np.int64)
    fit_residual = np.ascontiguousarray(train[fit_indices] - train_base[fit_indices],
                                        dtype=np.float32)
    codebooks, fit_seconds, faiss_version, faiss_threads = fit_faiss(
        fit_residual, args.iterations, args.fit_beam, args.faiss_seed,
        args.faiss_threads)
    quantizers = {payload: prefix_quantizer(codebooks, payload, args.encode_beam)
                  for payload in PAYLOADS}

    rows = []
    saved_selected = []
    saved_codes = {payload: [] for payload in PAYLOADS}
    encode_seconds = {payload: 0.0 for payload in PAYLOADS}
    for qi, query in enumerate(queries):
        ids = candidate_ids[offsets[qi]:offsets[qi + 1]]
        selected = interval_top(query, ids, thq_codes, thresholds)
        selected_levels = unpack_thq(np.asarray(thq_codes[selected]))
        base = centroids[np.arange(D)[None, :], selected_levels]
        docs = np.asarray(documents[selected], dtype=np.float32)
        residual = np.ascontiguousarray(docs - base, dtype=np.float32)
        candidate_docs = np.asarray(documents[ids], dtype=np.float32)
        candidate_exact = top_ids(cosine(candidate_docs, query), ids)
        saved_selected.append(selected.astype(np.int64))
        for payload, quantizer in quantizers.items():
            started = time.perf_counter()
            codes = quantizer.compute_codes(residual)
            decoded = quantizer.decode(codes)
            encode_seconds[payload] += time.perf_counter() - started
            if codes.shape != (TOP, payload) or decoded.shape != (TOP, D):
                raise RuntimeError("Faiss prefix artifact shape mismatch")
            reconstructed = base + decoded
            ranked = top_ids(cosine(reconstructed, query), selected)
            saved_codes[payload].append(np.asarray(codes, dtype=np.uint8))
            rows.append({
                "query": qi,
                "arm": f"faiss_rq{payload}",
                "side_payload_bytes": payload,
                "cascade_total_bytes": THQ_BYTES + payload,
                "top10_ids": ranked.astype(int).tolist(),
                "thq4_top128_ids": selected.astype(int).tolist(),
                "candidate_fp32_top10_ids": candidate_exact.astype(int).tolist(),
                "candidate_fp32_overlap": float(np.isin(candidate_exact, ranked).sum() / 10.0),
                "teacher_overlap": float(np.isin(teacher_ids[qi], ranked).sum() / 10.0),
                "qrels_ndcg10": ndcg10(ranked, qrel_ids[qi], qrel_scores[qi]),
            })

    args.models_output.parent.mkdir(parents=True, exist_ok=True)
    args.codes_output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.models_output, centroids=centroids.astype("<f4"),
                        rq_codebooks=codebooks.astype("<f4"))
    np.savez_compressed(
        args.codes_output,
        selected_ids=np.stack(saved_selected).astype("<i8"),
        **{f"codes_{payload}": np.stack(saved_codes[payload]).astype(np.uint8)
           for payload in PAYLOADS},
    )
    summaries = {}
    for payload in PAYLOADS:
        subset = [row for row in rows if row["arm"] == f"faiss_rq{payload}"]
        summaries[f"faiss_rq{payload}"] = {
            "mean_qrels_ndcg10": float(np.mean([row["qrels_ndcg10"] for row in subset])),
            "p05_qrels_ndcg10": float(np.percentile(
                [row["qrels_ndcg10"] for row in subset], 5)),
            "worst_qrels_ndcg10": float(np.min([row["qrels_ndcg10"] for row in subset])),
            "mean_candidate_fp32_overlap": float(np.mean(
                [row["candidate_fp32_overlap"] for row in subset])),
            "mean_teacher_overlap": float(np.mean([row["teacher_overlap"] for row in subset])),
            "side_payload_bytes": payload,
            "cascade_total_bytes": THQ_BYTES + payload,
            "global_codebook_bytes": payload * 256 * D * 4,
            "full_1m_logical_total_bytes":
                1_000_000 * (THQ_BYTES + payload) + payload * 256 * D * 4,
            "encode_decode_seconds_152x128": encode_seconds[payload],
        }
    sources = {
        "documents": args.documents, "train_vectors": args.train_vectors,
        "queries": args.queries, "qrel_ids": args.qrel_ids,
        "qrel_scores": args.qrel_scores, "teacher_ids": args.teacher_ids,
        "thq4_codes": args.thq4_codes, "thq4_thresholds": args.thq4_thresholds,
        "candidate_flat": args.candidate_flat, "candidate_raw": args.candidate_raw,
        "candidate_receipt": args.candidate_receipt,
    }
    result = {
        "schema_version": 1,
        "family": "thq_faiss_rq_replay_v1",
        "status": "EXECUTED",
        "source_replay": True,
        "runner_sha256": sha256(Path(__file__)),
        "query_count": QUERY_COUNT,
        "metric": "cosine",
        "faiss_version": faiss_version,
        "faiss_train_type": "Train_default",
        "faiss_clustering_seed": args.faiss_seed,
        "faiss_threads": faiss_threads,
        "fit_rows": len(fit_indices),
        "fit_strategy": "all_train_rows" if len(fit_indices) == train_count else "uniform_stride",
        "fit_indices_sha256": sha256_bytes(fit_indices.astype("<i8", copy=False).tobytes()),
        "iterations": args.iterations,
        "fit_beam": args.fit_beam,
        "encode_beam": args.encode_beam,
        "shared_fit": True,
        "fit_stage_count": max(PAYLOADS),
        "prefix_payload_bytes": list(PAYLOADS),
        "fit_seconds": fit_seconds,
        "artifact_hashes": {"models": sha256(args.models_output),
                            "codes": sha256(args.codes_output)},
        "source_hashes": {name: sha256(path) for name, path in sources.items()},
        "summaries": summaries,
        "rows": rows,
        "limitations": [
            "Faiss ResidualQuantizer control, not AVQ/AAQ/QINCo",
            "candidate-local side-code replay; 1M storage is logical accounting",
            "no native serving kernel or persistent side-code layout",
            "same repeatedly studied 152-query shell; held-out confirmation pending",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
