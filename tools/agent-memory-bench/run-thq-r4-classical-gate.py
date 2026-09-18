#!/usr/bin/env python3
"""Reference classical residual-codec gate on the frozen 152-query R4 shell.

The runner deliberately separates candidate membership from document-side
reconstruction.  It evaluates a candidate-local FP32 ceiling, THQ4 centroid,
hierarchical conditional residuals, PCA/PQ/OPQ and a bounded FWHT/Lloyd-Max
control.  Timings are Python reference timings only; no native-kernel claim is
made here.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

D = 384
THQ_BYTES = 96
TOP = 128


def load_helpers():
    path = Path(__file__).with_name("run-thq-residual-extended-frontier.py")
    spec = importlib.util.spec_from_file_location("thq_residual_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load residual helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


h = load_helpers()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def digest_array(value: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(value, dtype="<f4").tobytes()).hexdigest()


def top_ids(scores: np.ndarray, ids: np.ndarray, limit: int,
            ascending: bool = False) -> np.ndarray:
    order = np.lexsort((ids, scores if ascending else -scores))
    return ids[order[:limit]]


def ndcg(ids: np.ndarray, qrel_ids: np.ndarray, qrel_scores: np.ndarray) -> float:
    grades = {int(doc): float(score) for doc, score in zip(qrel_ids, qrel_scores)
              if int(doc) >= 0 and float(score) > 0.0}
    gains = np.asarray([2.0 ** grades.get(int(doc), 0.0) - 1.0 for doc in ids[:10]])
    discounts = np.log2(np.arange(2, 2 + len(gains), dtype=np.float64))
    ideal = np.sort(np.asarray([2.0 ** score - 1.0 for score in grades.values()]))[::-1][:10]
    ideal_dcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal), dtype=np.float64))))
    return float(np.sum(gains / discounts) / ideal_dcg) if ideal_dcg else 0.0


def fit_refined_hierarchical(train_residual: np.ndarray, levels: np.ndarray,
                              bits: int, iterations: int = 5) -> np.ndarray:
    """Conditional quantile initialization followed by Lloyd mean updates."""
    levels_count = 1 << bits
    codebook = np.empty((D, 4, levels_count), dtype=np.float32)
    for coordinate in range(D):
        for coarse in range(4):
            values = train_residual[levels[:, coordinate] == coarse, coordinate]
            if len(values) == 0:
                values = train_residual[:, coordinate]
            fractions = (np.arange(levels_count, dtype=np.float64) + 0.5) / levels_count
            codebook[coordinate, coarse] = np.quantile(values, fractions, method="linear")
    for _ in range(iterations):
        for coordinate in range(D):
            for coarse in range(4):
                mask = levels[:, coordinate] == coarse
                if not np.any(mask):
                    continue
                values = train_residual[mask, coordinate]
                symbols = np.argmin(np.abs(values[:, None] - codebook[coordinate, coarse][None, :]), axis=1)
                for symbol in range(levels_count):
                    selected = values[symbols == symbol]
                    if len(selected):
                        codebook[coordinate, coarse, symbol] = np.mean(selected, dtype=np.float64)
    return codebook


def decode_hierarchical(residual: np.ndarray, levels: np.ndarray,
                        codebook: np.ndarray) -> np.ndarray:
    decoded = np.empty_like(residual, dtype=np.float32)
    for coordinate in range(D):
        coarse = levels[:, coordinate]
        symbols = np.argmin(np.abs(residual[:, coordinate, None] -
                                   codebook[coordinate, coarse]), axis=1)
        decoded[:, coordinate] = codebook[coordinate, coarse, symbols]
    return decoded


def payload_bytes(kind: str) -> int:
    if kind == "thq4-centroid":
        return 96
    if kind == "thq4-fp32":
        return 96 + 1536
    if kind == "direct-int8":
        return 388
    if kind == "pca32x8":
        return 96 + 32
    if kind == "pq32x8":
        return 96 + 32
    if kind == "opq32x4":
        return 96 + 16
    if kind.startswith("hierarchical"):
        bits = int(kind.rsplit("-", 1)[1].replace("bit", ""))
        return 96 + (D * bits) // 8
    if kind.startswith("rslm"):
        bits = int(kind[4:])
        return 96 + 48 * bits
    raise ValueError(kind)


def score_reconstruction(values: np.ndarray, query: np.ndarray) -> np.ndarray:
    scores = values @ query
    norms = np.linalg.norm(values, axis=1)
    return scores / np.maximum(norms, np.finfo(np.float32).tiny)


def load_candidate_ids(flat: Path, raw: Path) -> tuple[np.ndarray, np.ndarray]:
    receipt = json.loads(raw.read_text(encoding="utf-8"))
    rows = receipt["rows"]
    counts = np.asarray([int(row["candidate_count"]) for row in rows], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts)))
    expected = int(offsets[-1])
    if flat.stat().st_size != expected * 148:
        raise RuntimeError("candidate flat size differs from raw row counts")
    records = np.memmap(flat, mode="r", dtype=np.uint8, shape=(expected, 148))
    ids = np.asarray(records[:, :4]).copy().view("<i4").reshape(-1).astype(np.int64)
    if np.any(ids < 0) or np.any(ids >= 1_000_000):
        raise RuntimeError("candidate ID out of range")
    for query in range(len(rows)):
        row = ids[offsets[query]:offsets[query + 1]]
        if len(np.unique(row)) != len(row):
            raise RuntimeError(f"candidate IDs duplicated for query {query}")
    return ids, offsets


def validate_candidate_receipt(receipt_path: Path, raw: Path, flat: Path) -> dict:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    raw_sha = sha(raw)
    flat_sha = sha(flat)
    if receipt.get("family") != "semantic_r4_fused_candidate_materialization_v1":
        raise RuntimeError("candidate receipt family differs")
    if receipt.get("execution_status") != "EXECUTED":
        raise RuntimeError("candidate receipt is not executed")
    if receipt.get("raw_sha256") != raw_sha:
        raise RuntimeError("candidate receipt/raw binding differs")
    flat_entry = receipt.get("flat_file", {})
    if flat_entry.get("sha256") != flat_sha or int(flat_entry.get("bytes", -1)) != flat.stat().st_size:
        raise RuntimeError("candidate receipt/flat binding differs")
    for field in ("runner_sha256", "thq_manifest_sha256", "layout_manifest_sha256", "native_receipt_sha256"):
        if not receipt.get(field):
            raise RuntimeError(f"candidate receipt missing {field}")
    return {
        "receipt_sha256": sha(receipt_path),
        "raw_sha256": raw_sha,
        "flat_sha256": flat_sha,
        "runner_sha256": receipt["runner_sha256"],
        "thq_manifest_sha256": receipt["thq_manifest_sha256"],
        "layout_manifest_sha256": receipt["layout_manifest_sha256"],
        "native_receipt_sha256": receipt["native_receipt_sha256"],
    }


def main() -> None:
    if "--self-test" in sys.argv[1:]:
        probe = np.asarray([2.0, 1.0], dtype=np.float32)
        if top_ids(probe, np.asarray([0, 1]), 1).tolist() != [0]:
            raise RuntimeError("top-k tie/order policy differs")
        print("run-thq-r4-classical-gate self-test PASS")
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--train-vectors", type=Path, required=True)
    parser.add_argument("--thq4-codes", type=Path, required=True)
    parser.add_argument("--thq4-thresholds", type=Path, required=True)
    parser.add_argument("--int8-codes", type=Path, required=True)
    parser.add_argument("--int8-scales", type=Path, required=True)
    parser.add_argument("--candidate-flat", type=Path, required=True)
    parser.add_argument("--candidate-raw", type=Path, required=True)
    parser.add_argument("--candidate-receipt", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--qrel-ids", type=Path, required=True)
    parser.add_argument("--qrel-scores", type=Path, required=True)
    parser.add_argument("--teacher-ids", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    document_count = args.documents.stat().st_size // (4 * D)
    train_count = args.train_vectors.stat().st_size // (4 * D)
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(document_count, D))
    training = np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(train_count, D))
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8,
                          shape=(document_count, THQ_BYTES))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    int8_codes = np.memmap(args.int8_codes, mode="r", dtype=np.int8,
                           shape=(document_count, D))
    int8_scales = np.memmap(args.int8_scales, mode="r", dtype="<f4",
                            shape=(document_count,))
    query_count = args.queries.stat().st_size // (4 * D)
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_count, D))
    qrel_ids = np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(query_count, 20))
    qrel_scores = np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(query_count, 20))
    teacher_ids = np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(query_count, 10))
    candidate_ids, offsets = load_candidate_ids(args.candidate_flat, args.candidate_raw)
    candidate_provenance = validate_candidate_receipt(args.candidate_receipt, args.candidate_raw,
                                                      args.candidate_flat)
    if len(offsets) - 1 != query_count:
        raise RuntimeError("candidate/query count differs")

    train_values = np.asarray(training, dtype=np.float32)
    centroids = h.h.fit_centroids(train_values, thresholds)
    train_levels = h.h.unpack_thq(h.h.pack_thq(train_values, thresholds))
    train_base = centroids[np.arange(D)[None, :], train_levels]
    train_residual = train_values - train_base
    basis, _ = h.h.fit_pca(train_residual, 32)

    models: dict[str, dict] = {}
    models["thq4-centroid"] = {"kind": "centroid", "model_sha256": digest_array(centroids)}
    pca_scales = np.maximum(np.max(np.abs(train_residual @ basis), axis=0) / 127.0, 1e-8)
    models["pca32x8"] = {"kind": "pca", "basis": basis, "scales": pca_scales,
                          "model_sha256": digest_array(basis),
                          "scale_sha256": digest_array(pca_scales)}

    levels_models = {}
    for bits in (1, 2, 3):
        codebook = fit_refined_hierarchical(train_residual, train_levels, bits)
        name = f"hierarchical-{bits}bit"
        levels_models[name] = codebook
        models[name] = {"kind": "hierarchical", "bits": bits,
                        "model_sha256": digest_array(codebook)}

    signs = np.random.default_rng(20260916).choice(
        np.asarray([-1.0, 1.0], dtype=np.float32), size=(3, 128))
    rotated_train = h.fwht_blocks(train_residual, signs)
    for bits in (2, 3, 4):
        centers = h.lloyd_centers(rotated_train, bits)
        name = f"rslm{bits}"
        models[name] = {"kind": "rslm", "bits": bits, "centers": centers,
                        "model_sha256": digest_array(centers),
                        "rotation_sha256": digest_array(signs)}

    import faiss
    pq = faiss.ProductQuantizer(D, 32, 8)
    pq.cp.niter = 12
    pq.cp.seed = 20260916
    pq.cp.verbose = False
    pq.train(np.ascontiguousarray(train_residual))
    pq_centers = faiss.vector_to_array(pq.centroids).reshape(32, 256, D // 32).astype(np.float32)
    models["pq32x8"] = {"kind": "pq", "pq": pq, "centers": pq_centers,
                         "model_sha256": digest_array(pq_centers)}

    opq = faiss.OPQMatrix(D, 32)
    opq_pq = faiss.ProductQuantizer(D, 32, 4)
    opq_pq.cp.niter = 12
    opq_pq.cp.seed = 20260916
    opq_pq.cp.verbose = False
    opq.pq = opq_pq
    opq.niter = 8
    opq.niter_pq = 4
    opq.niter_pq_0 = 4
    opq.verbose = False
    opq.train(np.ascontiguousarray(train_residual))
    rotation = faiss.vector_to_array(opq.A).reshape(D, D).astype(np.float32)
    opq_centers = faiss.vector_to_array(opq_pq.centroids).reshape(32, 16, D // 32).astype(np.float32)
    models["opq32x4"] = {"kind": "opq", "pq": opq_pq, "centers": opq_centers,
                          "rotation": rotation, "model_sha256": digest_array(opq_centers),
                          "rotation_sha256": digest_array(rotation)}

    rows = []
    for qi in range(query_count):
        ids = candidate_ids[offsets[qi]:offsets[qi + 1]]
        docs = np.asarray(documents[ids], dtype=np.float32)
        query = np.asarray(queries[qi], dtype=np.float32)
        exact_scores = docs @ query
        exact_top = top_ids(exact_scores, ids, 10)
        thq_levels = h.h.unpack_thq(np.asarray(thq_codes[ids]))
        base = centroids[np.arange(D)[None, :], thq_levels]
        interval_lut = np.empty((D, 4), dtype=np.float32)
        for coordinate in range(D):
            for level in range(4):
                low = -np.inf if level == 0 else thresholds[coordinate, level - 1]
                high = np.inf if level == 3 else thresholds[coordinate, level]
                delta = low - query[coordinate] if query[coordinate] < low else (
                    query[coordinate] - high if query[coordinate] > high else 0.0)
                interval_lut[coordinate, level] = delta * delta
        interval = np.sum(interval_lut[np.arange(D)[None, :], thq_levels], axis=1)
        thq_top = top_ids(interval, ids, min(TOP, len(ids)), ascending=True)
        reconstructions: dict[str, np.ndarray] = {"candidate-fp32": docs,
                                                    "thq4-fp32": docs,
                                                    "thq4-centroid": base,
                                                    "direct-int8": np.asarray(int8_codes[ids], dtype=np.float32) *
                                                    np.asarray(int8_scales[ids], dtype=np.float32)[:, None]}
        residual = docs - base
        projected = residual @ basis
        pca_codes = np.clip(np.rint(projected / pca_scales), -127, 127).astype(np.int8)
        reconstructions["pca32x8"] = base + (pca_codes.astype(np.float32) * pca_scales) @ basis.T
        for name, codebook in levels_models.items():
            reconstructions[name] = base + decode_hierarchical(residual, thq_levels, codebook)
        rotated = h.fwht_blocks(residual, signs)
        for name in ("rslm2", "rslm3", "rslm4"):
            decoded = h.quantize_decode(rotated, models[name]["centers"])
            reconstructions[name] = base + h.fwht_blocks(decoded, signs, inverse=True)
        pq_codes = h.unpack_pq_codes(np.asarray(models["pq32x8"]["pq"].compute_codes(
            np.ascontiguousarray(residual)), dtype=np.uint8), 32, 8)
        reconstructions["pq32x8"] = base + h.reconstruct_pq(pq_codes, pq_centers)
        opq_transformed = np.ascontiguousarray(residual @ rotation.T)
        opq_codes = h.unpack_pq_codes(np.asarray(opq_pq.compute_codes(opq_transformed), dtype=np.uint8), 32, 4)
        reconstructions["opq32x4"] = base + h.reconstruct_pq(opq_codes, opq_centers) @ rotation

        for name, values in reconstructions.items():
            target_ids = ids if name in ("candidate-fp32", "direct-int8") else thq_top
            positions = np.arange(len(ids)) if name in ("candidate-fp32", "direct-int8") else np.asarray(
                [int(np.flatnonzero(ids == doc)[0]) for doc in thq_top])
            selected = top_ids(score_reconstruction(values[positions], query), target_ids, 10)
            row = {"query": qi, "arm": name, "top10_ids": selected.astype(int).tolist(),
                   "qrels_ndcg10": ndcg(selected, qrel_ids[qi], qrel_scores[qi]),
                   "teacher_overlap": float(np.isin(teacher_ids[qi], selected).sum() / 10.0),
                   "candidate_fp32_overlap": float(np.isin(exact_top, selected).sum() / 10.0),
                   "thq4_top128_teacher_overlap": float(np.isin(teacher_ids[qi], thq_top).sum() / 10.0),
                   "candidate_count": int(len(ids)), "thq4_top128_count": int(len(thq_top)),
                   "logical_payload_bytes_per_document": payload_bytes(name) if name != "candidate-fp32" else 1536,
                   "timing_scope": "python_reference_candidate_reconstruction_and_selection"}
            rows.append(row)

    summaries = {}
    for arm in sorted({row["arm"] for row in rows}):
        arm_rows = [row for row in rows if row["arm"] == arm]
        def stats(key: str) -> dict[str, float]:
            values = np.asarray([row[key] for row in arm_rows], dtype=np.float64)
            return {"mean": float(np.mean(values)), "p05": float(np.quantile(values, 0.05)),
                    "min": float(np.min(values))}
        summaries[arm] = {"qrels_ndcg10": stats("qrels_ndcg10"),
                          "teacher_overlap": stats("teacher_overlap"),
                          "candidate_fp32_overlap": stats("candidate_fp32_overlap")}

    by_query_arm = {(row["query"], row["arm"]): row for row in rows}
    baseline_names = ("candidate-fp32", "direct-int8")
    for arm in summaries:
        summaries[arm]["qrels_ndcg10_paired"] = {}
        for baseline in baseline_names:
            if arm == baseline:
                continue
            deltas = np.asarray([
                by_query_arm[(query, arm)]["qrels_ndcg10"] -
                by_query_arm[(query, baseline)]["qrels_ndcg10"]
                for query in range(query_count)
            ], dtype=np.float64)
            rng = np.random.default_rng(20260918)
            samples = deltas[rng.integers(0, len(deltas), size=(2000, len(deltas)))].mean(axis=1)
            summaries[arm]["qrels_ndcg10_paired"][baseline] = {
                "mean_delta": float(np.mean(deltas)),
                "p05_delta": float(np.quantile(deltas, 0.05)),
                "min_delta": float(np.min(deltas)),
                "worst_query_loss": float(np.min(deltas)),
                "bootstrap_ci95": [float(np.quantile(samples, 0.025)),
                                    float(np.quantile(samples, 0.975))],
            }

    result = {
        "schema_version": 1,
        "family": "thq_r4_codec_function_gate_v1",
        "status": "EXECUTED",
        "runner_sha256": sha(Path(__file__)),
        "evidence_status": "152_query_frozen_r4_codec_function_oracle_numpy_reference",
        "documents": document_count,
        "training_count": train_count,
        "query_count": query_count,
        "candidate_flat_sha256": sha(args.candidate_flat),
        "candidate_raw_sha256": sha(args.candidate_raw),
        "candidate_receipt_sha256": candidate_provenance["receipt_sha256"],
        "candidate_provenance": candidate_provenance,
        "documents_sha256": sha(args.documents),
        "training_sha256": sha(args.train_vectors),
        "thq4_codes_sha256": sha(args.thq4_codes),
        "thq4_thresholds_sha256": sha(args.thq4_thresholds),
        "int8_codes_sha256": sha(args.int8_codes),
        "int8_scales_sha256": sha(args.int8_scales),
        "queries_sha256": sha(args.queries),
        "qrel_ids_sha256": sha(args.qrel_ids),
        "qrel_scores_sha256": sha(args.qrel_scores),
        "teacher_ids_sha256": sha(args.teacher_ids),
        "model_hashes": {name: {key: value for key, value in model.items()
                                if key.endswith("sha256")}
                         for name, model in models.items()},
        "summaries": summaries,
        "rows": rows,
        "limitations": [
            "candidate-local replay; no routing membership claim",
            "Python/NumPy/Faiss reference timing, not native SIMD or OS-page latency",
            "hierarchical and RSLM arms are bounded controls, not claims of reproducing every paper detail",
            "candidate-FP32 is a quality ceiling for this frozen candidate shell",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
