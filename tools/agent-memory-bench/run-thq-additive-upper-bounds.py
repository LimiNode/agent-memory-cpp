#!/usr/bin/env python3
"""Capacity diagnostics for additive THQ residual codecs.

These are deliberately neutral local references, not reproductions of AVQ,
AAQ, or QINCo.  The score oracle uses the exact document score to choose the
closest retained beam score; it is therefore a leaky per-document score
approximation diagnostic, never a retrieval or production upper bound.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

D = 384
THQ_BYTES = 96
TOP = 128
SEED = 20260920
VARIANTS = ("additive_greedy", "additive_mse_beam", "additive_fp32_score_oracle")
# A 256-way stage stores one 8-bit index, hence one byte.  These are actual
# side-code bytes, not bit counts.  32/48 B arms are explicit rate-matched
# controls for THQ-joint2/joint3-sized side payloads.
STAGES = {4: 4, 6: 6, 8: 8, 32: 32, 48: 48}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def top_ids(scores: np.ndarray, ids: np.ndarray, count: int = 10) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:count]]


def ndcg10(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    rel = {int(doc): float(value) for doc, value in zip(qids, grades) if int(doc) >= 0 and float(value) > 0}
    values = np.asarray([2.0 ** rel.get(int(doc), 0.0) - 1.0 for doc in ids[:10]], dtype=np.float64)
    ideal = np.sort(np.asarray([2.0 ** value - 1.0 for value in rel.values()], dtype=np.float64))[::-1][:10]
    dcg = float(np.sum(values / np.log2(np.arange(2, 2 + len(values)))))
    idcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal))))) if len(ideal) else 0.0
    return dcg / idcg if idcg else 0.0


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    bits = np.unpackbits(np.asarray(codes, dtype=np.uint8), axis=1, bitorder="little")
    return (bits[:, 0::2] + 2 * bits[:, 1::2]).astype(np.uint8)


def load_candidates(flat: Path, raw: Path, receipt: Path) -> tuple[np.ndarray, np.ndarray]:
    metadata = json.loads(raw.read_text(encoding="utf-8"))
    rows = metadata.get("rows")
    if not isinstance(rows, list) or len(rows) != 152:
        raise RuntimeError("candidate raw must contain 152 rows")
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    total = int(offsets[-1])
    if flat.stat().st_size != total * 148:
        raise RuntimeError("candidate flat/raw cardinality mismatch")
    records = np.memmap(flat, mode="r", dtype=np.uint8, shape=(total, 148))
    ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    if np.any(ids < 0) or np.any(ids >= 1_000_000):
        raise RuntimeError("candidate document ID outside 1M corpus")
    for start, stop in zip(offsets[:-1], offsets[1:]):
        if len(np.unique(ids[start:stop])) != int(stop - start):
            raise RuntimeError("candidate row contains duplicate document IDs")
    receipt_data = json.loads(receipt.read_text(encoding="utf-8"))
    if receipt_data.get("execution_status") != "EXECUTED":
        raise RuntimeError("candidate receipt is not EXECUTED")
    if receipt_data.get("raw_sha256") != sha256(raw) or receipt_data.get("flat_file", {}).get("sha256") != sha256(flat):
        raise RuntimeError("candidate receipt/source SHA mismatch")
    return ids, offsets


def kmeans(values: np.ndarray, k: int, iterations: int, seed: int) -> np.ndarray:
    """Chunked deterministic Lloyd k-means used only for the local reference."""
    x = np.asarray(values, dtype=np.float32)
    if x.shape[0] < k:
        raise ValueError("training rows must be at least the codebook size")
    rng = np.random.default_rng(seed)
    centers = x[rng.choice(x.shape[0], k, replace=False)].copy()
    for _ in range(iterations):
        sums = np.zeros_like(centers, dtype=np.float64)
        counts = np.zeros(k, dtype=np.int64)
        for start in range(0, len(x), 4096):
            block = x[start:start + 4096]
            labels = nearest_indices(block, centers)
            np.add.at(sums, labels, block)
            np.add.at(counts, labels, 1)
        nonempty = counts > 0
        centers[nonempty] = (sums[nonempty] / counts[nonempty, None]).astype(np.float32)
        if not np.all(nonempty):
            centers[~nonempty] = x[rng.choice(len(x), int(np.sum(~nonempty)), replace=False)]
    return centers


def fit_additive(residual: np.ndarray, stages: int, iterations: int = 8) -> list[np.ndarray]:
    remaining = np.asarray(residual, dtype=np.float32).copy()
    codebooks = []
    for stage in range(stages):
        centers = kmeans(remaining, 256, iterations, SEED + stage)
        remaining -= centers[nearest_indices(remaining, centers)]
        codebooks.append(centers)
    return codebooks


def fit_additive_faiss(residual: np.ndarray, stages: int, iterations: int = 8,
                       beam_width: int = 1) -> list[np.ndarray]:
    """Fit one additive sequence through Faiss' native residual quantizer.

    This is an implementation acceleration/control, not a claim that Faiss RQ
    reproduces AVQ, AAQ, or QINCo.  The returned stage tables have the same
    prefix semantics as the transparent reference fitter.
    """
    try:
        import faiss
        quantizer = faiss.ResidualQuantizer(residual.shape[1], stages, 8)
    except ImportError as exc:
        raise RuntimeError("--fit-backend faiss requires the optional faiss-cpu package") from exc
    except (AttributeError, TypeError) as exc:
        raise RuntimeError("installed Faiss lacks ResidualQuantizer(d, M, nbits)") from exc
    # Faiss defaults to progressive-dimension training, which repeats the
    # expensive dimensional schedule for every stage.  The matched gate needs
    # one ordinary full-dimensional residual sequence; pin that mode so the
    # acceleration comparison is reproducible.
    quantizer.train_type = faiss.ResidualQuantizer.Train_default
    quantizer.cp.niter = int(iterations)
    quantizer.max_beam_size = int(beam_width)
    quantizer.verbose = False
    quantizer.train(np.ascontiguousarray(residual, dtype=np.float32))
    codebooks = faiss.vector_to_array(quantizer.codebooks).astype(np.float32, copy=False)
    offsets = faiss.vector_to_array(quantizer.codebook_offsets).astype(np.int64, copy=False)
    if len(offsets) != stages + 1 or offsets[-1] * residual.shape[1] != len(codebooks):
        raise RuntimeError("Faiss residual codebook manifest is inconsistent")
    return [codebooks[offsets[i] * residual.shape[1]:offsets[i + 1] * residual.shape[1]]
            .reshape(256, residual.shape[1]).copy() for i in range(stages)]


def faiss_provenance() -> tuple[str, str]:
    try:
        import faiss
    except ImportError as exc:
        raise RuntimeError("--fit-backend faiss requires the optional faiss-cpu package") from exc
    return getattr(faiss, "__version__", "unknown"), "Train_default"


def nearest_indices(values: np.ndarray, centers: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    centers = np.asarray(centers, dtype=np.float32)
    out = np.empty(len(values), dtype=np.int64)
    center_norm = np.sum(centers * centers, axis=1, dtype=np.float32)
    for start in range(0, len(values), 4096):
        block = values[start:start + 4096]
        distances = (np.sum(block * block, axis=1, keepdims=True)
                     + center_norm[None, :] - 2.0 * (block @ centers.T))
        out[start:start + len(block)] = np.argmin(distances, axis=1)
    return out


def greedy_encode(values: np.ndarray, codebooks: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    remaining = np.asarray(values, dtype=np.float32).copy()
    decoded = np.zeros_like(remaining)
    codes = np.empty((len(remaining), len(codebooks)), dtype=np.uint8)
    for stage, centers in enumerate(codebooks):
        chosen_ids = nearest_indices(remaining, centers)
        codes[:, stage] = chosen_ids.astype(np.uint8)
        chosen = centers[chosen_ids]
        decoded += chosen
        remaining -= chosen
    return decoded, codes


def beam_encode(values: np.ndarray, codebooks: list[np.ndarray], width: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Retain a reconstruction beam per row; this is an optimistic encoder control."""
    if width < 1:
        raise ValueError("beam width must be positive")
    x = np.asarray(values, dtype=np.float32)
    recon = np.zeros((len(x), 1, D), dtype=np.float32)
    paths = np.zeros((len(x), 1, 0), dtype=np.uint8)
    for centers in codebooks:
        candidates = recon[:, :, None, :] + centers[None, None, :, :]
        errors = np.sum((x[:, None, None, :] - candidates) ** 2, axis=3)
        beam = min(width, errors.shape[1] * errors.shape[2])
        flat = errors.reshape(len(x), -1)
        keep = np.argpartition(flat, beam - 1, axis=1)[:, :beam]
        order = np.take_along_axis(flat, keep, axis=1)
        row = np.arange(len(x))[:, None]
        prev = keep // centers.shape[0]
        code = keep % centers.shape[0]
        recon = candidates[row, prev, code]
        paths = np.concatenate((paths[np.arange(len(x))[:, None], prev, :],
                                code[..., None].astype(np.uint8)), axis=2)
    return recon, order, paths


def self_test() -> None:
    if STAGES != {4: 4, 6: 6, 8: 8, 32: 32, 48: 48}:
        raise RuntimeError("rate-matched additive stage manifest drift")
    rng = np.random.default_rng(SEED)
    values = rng.normal(size=(96, D)).astype(np.float32)
    centers = kmeans(values, 8, 2, SEED)
    decoded, greedy_codes = greedy_encode(values[:5], [centers, centers])
    beam, errors, beam_codes = beam_encode(values[:5], [centers, centers], 4)
    if decoded.shape != (5, D) or beam.shape != (5, 4, D) or errors.shape != (5, 4):
        raise RuntimeError("additive upper-bound self-test shape mismatch")
    if np.any(~np.isfinite(beam)) or np.any(~np.isfinite(errors)):
        raise RuntimeError("additive upper-bound self-test produced non-finite values")
    if greedy_codes.shape != (5, 2) or beam_codes.shape != (5, 4, 2):
        raise RuntimeError("additive code-stream shape mismatch")
    decoded_again = sum((centers[greedy_codes[:, i]] for i, centers in enumerate([centers, centers])),
                        start=np.zeros_like(decoded))
    if not np.allclose(decoded, decoded_again):
        raise RuntimeError("greedy code stream does not decode to reconstruction")
    query = rng.normal(size=D).astype(np.float32)
    exact = np.einsum("kd,d->k", values[:5], query)
    beam_scores = np.einsum("kbd,d->kb", beam, query)
    chosen = np.argmin(np.abs(beam_scores - exact[:, None]), axis=1)
    if chosen.shape != (5,) or not np.isfinite(beam_scores).all():
        raise RuntimeError("score-approximation oracle mismatch")
    print("THQ additive codec self-test: ok")


def self_test_faiss() -> None:
    """Exercise the optional Faiss fitter without requiring corpus artifacts."""
    rng = np.random.default_rng(SEED)
    values = rng.normal(size=(512, D)).astype(np.float32)
    codebooks = fit_additive_faiss(values, stages=4, iterations=1, beam_width=1)
    if len(codebooks) != 4:
        raise RuntimeError("Faiss self-test returned the wrong stage count")
    if any(table.shape != (256, D) for table in codebooks):
        raise RuntimeError("Faiss self-test returned an invalid codebook shape")
    if any(not np.isfinite(table).all() for table in codebooks):
        raise RuntimeError("Faiss self-test returned non-finite codebook values")
    # The production arms are prefixes of one shared fit.  Materialise the
    # same manifest here so a backend/API change cannot silently alter that
    # contract while the ordinary NumPy self-test remains green.
    prefixes = {stages: codebooks[:stages] for stages in range(1, 5)}
    if [len(prefixes[stages]) for stages in range(1, 5)] != [1, 2, 3, 4]:
        raise RuntimeError("Faiss self-test prefix manifest is invalid")
    print("THQ additive Faiss self-test: ok")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--self-test-faiss", action="store_true")
    for name in ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids",
                 "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt", "output"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path)
    parser.add_argument("--beam-width", type=int, default=8)
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--fit-backend", choices=("numpy", "faiss"), default="numpy")
    parser.add_argument("--faiss-beam-width", type=int, default=1)
    parser.add_argument("--fit-rows", type=int, default=0,
                        help="uniformly subsample this many training rows for additive codebooks; 0 uses all rows")
    parser.add_argument("--models-output", type=Path)
    parser.add_argument("--codes-output", type=Path)
    args = parser.parse_args()
    if args.self_test and args.self_test_faiss:
        parser.error("--self-test and --self-test-faiss are mutually exclusive")
    if args.self_test_faiss:
        self_test_faiss()
        return
    if args.self_test:
        self_test()
        return
    required = (args.documents, args.train_vectors, args.queries, args.qrel_ids, args.qrel_scores,
                args.teacher_ids, args.thq4_codes, args.thq4_thresholds, args.candidate_flat,
                args.candidate_raw, args.candidate_receipt, args.output)
    if any(value is None for value in required):
        parser.error("all source paths and --output are required unless --self-test is used")
    if args.beam_width < 1 or args.iterations < 1 or args.fit_rows < 0 or args.faiss_beam_width < 1:
        parser.error("beam width and iterations must be positive; fit rows must be non-negative")
    if args.documents.stat().st_size != 1_000_000 * D * 4:
        raise RuntimeError("upper-bound gate requires the 1M-row FP32 document source")
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    if args.train_vectors.stat().st_size % (D * 4):
        raise RuntimeError("training source is not an exact FP32x384 matrix")
    train_rows = args.train_vectors.stat().st_size // (D * 4)
    if train_rows < 256:
        raise RuntimeError("training source is too small for 256-way additive codebooks")
    train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(train_rows, D)), dtype=np.float32)
    if args.fit_rows:
        if args.fit_rows < 256 or args.fit_rows > train_rows:
            raise RuntimeError("fit-rows must be zero or in [256, train_rows]")
        fit_indices = np.linspace(0, train_rows - 1, args.fit_rows, dtype=np.int64)
        fit_train = train[fit_indices]
    else:
        fit_indices = np.arange(train_rows, dtype=np.int64)
        fit_train = train
    fit_indices_sha256 = sha256_bytes(fit_indices.astype("<i8", copy=False).tobytes())
    if args.queries.stat().st_size != 152 * D * 4:
        raise RuntimeError("queries source must contain exactly 152 x 384 FP32 rows")
    if args.qrel_ids.stat().st_size != 152 * 20 * 8 or args.qrel_scores.stat().st_size != 152 * 20 * 4:
        raise RuntimeError("qrels sources must contain exactly 152 x 20 rows")
    if args.teacher_ids.stat().st_size != 152 * 10 * 8:
        raise RuntimeError("teacher source must contain exactly 152 x 10 IDs")
    queries = np.asarray(np.memmap(args.queries, mode="r", dtype="<f4", shape=(152, D)), dtype=np.float32)
    qrel_ids = np.asarray(np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(152, 20)))
    qrel_scores = np.asarray(np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(152, 20)))
    teacher_ids = np.asarray(np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(152, 10)))
    if args.thq4_codes.stat().st_size != 1_000_000 * THQ_BYTES:
        raise RuntimeError("THQ4 source must contain exactly 1M x 96 bytes")
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4")
    if thresholds.size != D * 3:
        raise RuntimeError("THQ thresholds must contain 384x3 values")
    thresholds = thresholds.reshape(D, 3)
    candidate_ids, offsets = load_candidates(args.candidate_flat, args.candidate_raw, args.candidate_receipt)
    # Fit THQ centroids and all additive models on documents only.
    train_levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    centroids = np.zeros((D, 4), dtype=np.float32)
    for coordinate in range(D):
        for level in range(4):
            values = train[train_levels[:, coordinate] == level, coordinate]
            centroids[coordinate, level] = float(np.mean(values)) if len(values) else float(np.mean(train[:, coordinate]))
    train_base = centroids[np.arange(D)[None, :], train_levels]
    fit_levels = np.sum(fit_train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    fit_base = centroids[np.arange(D)[None, :], fit_levels]
    residual_train = fit_train - fit_base
    if args.fit_backend == "faiss":
        shared_codebooks = fit_additive_faiss(residual_train, max(STAGES.values()),
                                              args.iterations, args.faiss_beam_width)
    else:
        shared_codebooks = fit_additive(residual_train, max(STAGES.values()), args.iterations)
    # Every rate arm is a prefix of the same deterministic 48-stage fit. This
    # avoids repeating identical work and makes the prefix relationship
    # explicit in the persisted model manifest.
    models = {payload: shared_codebooks[:stages] for payload, stages in STAGES.items()}
    code_artifacts = {payload: {} for payload in STAGES}
    rows = []
    for qi, query in enumerate(queries):
        ids = candidate_ids[offsets[qi]:offsets[qi + 1]]
        levels = unpack_thq(np.asarray(thq_codes[ids]))
        base = centroids[np.arange(D)[None, :], levels]
        docs = np.asarray(documents[ids], dtype=np.float32)
        # Canonical stage boundary: THQ interval-squared top-128.
        lut = np.empty((D, 4), dtype=np.float32)
        for d in range(D):
            for level in range(4):
                low = -np.inf if level == 0 else thresholds[d, level - 1]
                high = np.inf if level == 3 else thresholds[d, level]
                delta = low - query[d] if query[d] < low else (query[d] - high if query[d] > high else 0.0)
                lut[d, level] = delta * delta
        interval = np.sum(lut[np.arange(D)[None, :], levels], axis=1)
        selected = ids[np.lexsort((ids, interval))[:min(TOP, len(ids))]]
        selected_pos = np.asarray([int(np.flatnonzero(ids == doc)[0]) for doc in selected])
        selected_base = base[selected_pos]
        selected_docs = docs[selected_pos]
        teacher = teacher_ids[qi]
        for payload, codebooks in models.items():
            residual = selected_docs - selected_base
            greedy, greedy_codes = greedy_encode(residual, codebooks)
            beam, _errors, beam_codes = beam_encode(residual, codebooks, args.beam_width)
            recon_greedy = selected_base + greedy
            # The MSE beam is the optimistic document-side encoder.
            mse_indices = np.argmin(np.sum((residual[:, None, :] - beam) ** 2, axis=2), axis=1)
            recon_beam = beam[np.arange(len(selected)), mse_indices] + selected_base
            # Leaky score-approximation oracle: choose the beam member whose
            # cosine score is closest to the exact FP32 document score.
            beam_all = beam + selected_base[:, None, :]
            qnorm = max(float(np.linalg.norm(query)), np.finfo(np.float32).tiny)
            beam_scores = np.einsum("kbd,d->kb", beam_all, query) / np.maximum(np.linalg.norm(beam_all, axis=2) * qnorm, np.finfo(np.float32).tiny)
            exact_scores = np.einsum("kd,d->k", selected_docs, query) / np.maximum(np.linalg.norm(selected_docs, axis=1) * qnorm, np.finfo(np.float32).tiny)
            oracle = beam_all[np.arange(len(selected)), np.argmin(np.abs(beam_scores - exact_scores[:, None]), axis=1)]
            code_artifacts[payload][qi] = {"selected_ids": selected.astype(np.int64),
                                           "greedy_codes": greedy_codes,
                                           "beam_codes": beam_codes,
                                           "mse_codes": beam_codes[np.arange(len(selected)), mse_indices]}
            for variant, values, leaky in (("additive_greedy", recon_greedy, False),
                                           ("additive_mse_beam", recon_beam, False),
                                           ("additive_fp32_score_oracle", oracle, True)):
                scores = np.einsum("kd,d->k", values, query) / np.maximum(np.linalg.norm(values, axis=1) * qnorm, np.finfo(np.float32).tiny)
                ranked = top_ids(scores, selected)
                serving_bytes = payload if variant != "additive_fp32_score_oracle" else None
                global_codebook_bytes = len(codebooks) * 256 * D * 4
                rows.append({"query": qi, "payload_bytes": payload, "variant": variant, "query_leaking": leaky,
                             "selected_ids": selected.astype(int).tolist(),
                             "serving_payload_bytes": serving_bytes,
                             "retained_beam_code_bytes": payload * args.beam_width if leaky else payload,
                             "global_codebook_bytes": int(global_codebook_bytes),
                             "complete_1m_bytes": None if serving_bytes is None else int(global_codebook_bytes + 1_000_000 * (THQ_BYTES + serving_bytes)),
                             "teacher_overlap": float(np.isin(teacher, ranked).sum() / 10.0),
                             "qrels_ndcg10": ndcg10(ranked, qrel_ids[qi], qrel_scores[qi]),
                             "top10_ids": ranked.astype(int).tolist()})
    summaries = {}
    for payload in STAGES:
        summaries[str(payload)] = {}
        for variant in VARIANTS:
            subset = [row for row in rows if row["payload_bytes"] == payload and row["variant"] == variant]
            quality = [float(row["qrels_ndcg10"]) for row in subset]
            summaries[str(payload)][variant] = {"mean_qrels_ndcg10": float(np.mean(quality)),
                                                 "p05_qrels_ndcg10": float(np.percentile(quality, 5)),
                                                 "worst_qrels_ndcg10": float(np.min(quality)),
                                                 "mean_teacher_overlap": float(np.mean([row["teacher_overlap"] for row in subset])),
                                                 "query_leaking": bool(subset[0]["query_leaking"]),
                                                 "serving_payload_bytes": subset[0]["serving_payload_bytes"],
                                                 "retained_beam_code_bytes": subset[0]["retained_beam_code_bytes"],
                                                 "global_codebook_bytes": subset[0]["global_codebook_bytes"],
                                                 "complete_1m_bytes": subset[0]["complete_1m_bytes"]}
    model_path = args.models_output or args.output.with_suffix(".models.npz")
    codes_path = args.codes_output or args.output.with_suffix(".codes.npz")
    model_path.parent.mkdir(parents=True, exist_ok=True)
    codes_path.parent.mkdir(parents=True, exist_ok=True)
    model_arrays = {f"payload_{payload}_stage_{stage}": centers.astype("<f4")
                    for payload, codebooks in models.items() for stage, centers in enumerate(codebooks)}
    np.savez_compressed(model_path, **model_arrays)
    code_arrays = {}
    for payload, per_query in code_artifacts.items():
        code_arrays[f"payload_{payload}_selected_ids"] = np.stack([per_query[q]["selected_ids"] for q in range(152)])
        code_arrays[f"payload_{payload}_greedy_codes"] = np.stack([per_query[q]["greedy_codes"] for q in range(152)])
        code_arrays[f"payload_{payload}_beam_codes"] = np.stack([per_query[q]["beam_codes"] for q in range(152)])
        code_arrays[f"payload_{payload}_mse_codes"] = np.stack([per_query[q]["mse_codes"] for q in range(152)])
    np.savez_compressed(codes_path, **code_arrays)
    result = {"schema_version": 2, "family": "thq_additive_upper_bounds_v2", "status": "EXECUTED",
              "source_replay": True, "metric": "cosine", "seed": SEED, "query_count": 152,
              "runner_sha256": sha256(Path(__file__)),
              "prefilter": "frozen R4 candidate stream -> canonical THQ4 interval-squared top128",
              "beam_width": args.beam_width, "iterations": args.iterations,
              "fit_rows": int(len(fit_train)),
              "fit_strategy": "all_train_rows" if not args.fit_rows else "uniform_stride",
              "fit_backend": args.fit_backend,
              "faiss_version": faiss_provenance()[0] if args.fit_backend == "faiss" else None,
              "faiss_train_type": faiss_provenance()[1] if args.fit_backend == "faiss" else None,
              "faiss_beam_width": args.faiss_beam_width if args.fit_backend == "faiss" else None,
              "fit_indices_sha256": fit_indices_sha256,
              "shared_fit": True,
              "fit_stage_count": max(STAGES.values()),
              "stages_by_payload_bytes": STAGES,
              "side_code_bytes": sorted(STAGES), "total_bytes_by_side_code": {str(p): THQ_BYTES + p for p in STAGES},
              "global_codebook_bytes_by_side_code": {str(payload): int(stages * 256 * D * 4) for payload, stages in STAGES.items()},
              "storage_semantics": {"greedy_and_mse_beam": "THQ4 plus one selected path",
                                    "score_oracle": "retained beam paths plus exact FP32 document score; no serving payload"},
              "artifact_hashes": {"models": sha256(model_path), "codes": sha256(codes_path)},
              "source_hashes": {name: sha256(path) for name, path in {
                  "documents": args.documents, "train_vectors": args.train_vectors, "queries": args.queries,
                  "qrel_ids": args.qrel_ids, "qrel_scores": args.qrel_scores, "teacher_ids": args.teacher_ids,
                  "thq4_codes": args.thq4_codes, "thq4_thresholds": args.thq4_thresholds,
                  "candidate_flat": args.candidate_flat, "candidate_raw": args.candidate_raw,
                  "candidate_receipt": args.candidate_receipt}.items()},
              "limitations": ["local additive reference, not faithful AVQ/AAQ/QINCo", "score oracle is query-leaking and not a retrieval upper bound",
                              "NumPy quality only", "no native latency or persistent layout"],
              "model_hashes": {str(payload): [hashlib.sha256(c.astype("<f4").tobytes()).hexdigest() for c in codebooks]
                              for payload, codebooks in models.items()},
              "summaries": summaries, "rows": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
