#!/usr/bin/env python3
"""Matched THQ4 residual gate for paper-faithful RSLM and local controls.

The three RSLM arms are alternatives after the same THQ4 top-128 filter.  The
local arms intentionally retain the historical randomized FWHT/Lloyd-Max
control so that a paper-faithful claim cannot be smuggled into old results.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np


D, TOP, QUERY_COUNT = 384, 128, 152
UNIT_NORM_TOLERANCE = 1e-4


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).resolve().parent
faithful = load_module("rslm_faithful_reference", HERE / "rslm-faithful-reference.py")
local_helpers = load_module("rslm_local_helpers", HERE / "run-thq-residual-extended-frontier.py")
packed = load_module("packed_codecs", HERE / "thq-packed-codecs.py")


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    bits = np.unpackbits(np.asarray(codes, dtype=np.uint8), axis=1, bitorder="little")
    return (bits[:, 0::2] + 2 * bits[:, 1::2]).astype(np.uint8)


def fit_centroids(train: np.ndarray, thresholds: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    levels = np.sum(train[:, :, None] > thresholds[None, :, :], axis=2, dtype=np.uint8)
    centroids = np.empty((D, 4), dtype=np.float32)
    fallback = np.mean(train, axis=0, dtype=np.float64).astype(np.float32)
    for coordinate in range(D):
        for level in range(4):
            values = train[levels[:, coordinate] == level, coordinate]
            centroids[coordinate, level] = float(np.mean(values)) if len(values) else fallback[coordinate]
    return centroids, levels


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
        raise RuntimeError("candidate ID out of range")
    for start, stop in zip(offsets[:-1], offsets[1:]):
        if len(np.unique(ids[start:stop])) != int(stop - start):
            raise RuntimeError("candidate row contains duplicate IDs")
    receipt_data = json.loads(receipt.read_text(encoding="utf-8"))
    if receipt_data.get("execution_status") != "EXECUTED":
        raise RuntimeError("candidate receipt is not EXECUTED")
    if receipt_data.get("raw_sha256") != sha256(raw) or receipt_data.get("flat_file", {}).get("sha256") != sha256(flat):
        raise RuntimeError("candidate receipt binding differs")
    return ids, offsets


def interval_top(query: np.ndarray, ids: np.ndarray, codes: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    levels = unpack_thq(np.asarray(codes[ids]))
    lut = np.empty((D, 4), dtype=np.float32)
    for coordinate in range(D):
        for level in range(4):
            low = -np.inf if level == 0 else thresholds[coordinate, level - 1]
            high = np.inf if level == 3 else thresholds[coordinate, level]
            delta = (low - query[coordinate] if query[coordinate] < low else
                     query[coordinate] - high if query[coordinate] > high else 0.0)
            lut[coordinate, level] = delta * delta
    distances = np.sum(lut[np.arange(D)[None, :], levels], axis=1)
    return ids[np.lexsort((ids, distances))[:min(TOP, len(ids))]]


def top10(scores: np.ndarray, ids: np.ndarray) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:10]]


def ndcg10(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    relevance = {int(doc): float(value) for doc, value in zip(qids, grades) if int(doc) >= 0 and float(value) > 0.0}
    gains = np.asarray([2.0 ** relevance.get(int(doc), 0.0) - 1.0 for doc in ids[:10]])
    ideal = np.sort(np.asarray([2.0 ** value - 1.0 for value in relevance.values()]))[::-1][:10]
    discounts = np.log2(np.arange(2, 2 + len(gains)))
    idcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal))))) if len(ideal) else 0.0
    return float(np.sum(gains / discounts) / idcg) if idcg else 0.0


def self_test() -> None:
    result = faithful.self_test()
    assert result["rotation_max_abs_error"] < 2e-4
    assert all(result["codecs"][str(bits)]["packed_bytes"] == {2: 96, 3: 144, 4: 192}[bits] for bits in (2, 3, 4))
    print(json.dumps({"status": "PASS", "reference": "google-research/rslm", "reference_initial_commit": faithful.REFERENCE_INITIAL_COMMIT, "reference_content_commit": faithful.REFERENCE_CONTENT_COMMIT, "reference_snapshot_commit": faithful.REFERENCE_SNAPSHOT_COMMIT, "reference_notebook_blob": faithful.REFERENCE_NOTEBOOK_BLOB, "reference_notebook_sha256": faithful.REFERENCE_NOTEBOOK_SHA256, **result}, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    for name in ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids",
                 "thq4-codes", "thq4-thresholds", "candidate-flat", "candidate-raw", "candidate-receipt", "output"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path)
    parser.add_argument("--rq-audit", type=Path, help="optional prior RQ32/RQ48 audit; informational only")
    parser.add_argument("--local-fit-rows", type=int, default=8192)
    parser.add_argument("--local-iterations", type=int, default=4)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    required = (args.documents, args.train_vectors, args.queries, args.qrel_ids, args.qrel_scores, args.teacher_ids,
                args.thq4_codes, args.thq4_thresholds, args.candidate_flat, args.candidate_raw, args.candidate_receipt, args.output)
    if any(value is None for value in required):
        parser.error("full replay requires all source paths")
    count = args.documents.stat().st_size // (4 * D)
    train_count = args.train_vectors.stat().st_size // (4 * D)
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(count, D))
    train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(train_count, D)), dtype=np.float32)
    query_count = args.queries.stat().st_size // (4 * D)
    queries = np.asarray(np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_count, D)), dtype=np.float32)
    document_norm_max_error = 0.0
    for norm_start in range(0, count, 16384):
        norm_chunk = np.asarray(documents[norm_start:norm_start + 16384], dtype=np.float32)
        document_norm_max_error = max(document_norm_max_error, float(np.max(np.abs(np.linalg.norm(norm_chunk, axis=1) - 1.0))))
    query_norms = np.linalg.norm(queries, axis=1)
    norm_diagnostics = {
        "document_max_abs_error": document_norm_max_error,
        "query_max_abs_error": float(np.max(np.abs(query_norms - 1.0))),
        "tolerance": UNIT_NORM_TOLERANCE,
    }
    if norm_diagnostics["document_max_abs_error"] > UNIT_NORM_TOLERANCE or norm_diagnostics["query_max_abs_error"] > UNIT_NORM_TOLERANCE:
        raise RuntimeError(f"canonical IP/cosine comparison requires unit-normalized inputs: {norm_diagnostics}")
    qrel_ids = np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(len(queries), 20))
    qrel_scores = np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(len(queries), 20))
    teacher_ids = np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(len(queries), 10))
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(count, 96))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    candidate_ids, offsets = load_candidates(args.candidate_flat, args.candidate_raw, args.candidate_receipt)
    centroids, train_levels = fit_centroids(train, thresholds)
    train_base = centroids[np.arange(D)[None, :], train_levels]
    train_residual = train - train_base
    unique_ids = np.unique(candidate_ids)
    id_to_row = {int(doc): i for i, doc in enumerate(unique_ids)}
    artifact_dir = args.output.parent / "thq-rslm-faithful-artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    models: dict[str, dict[str, object]] = {}
    local_fit_rows = min(max(1, int(args.local_fit_rows)), len(train_residual))
    local_fit = train_residual[:local_fit_rows]
    for bits in (2, 3, 4):
        faithful_path = artifact_dir / f"rslm{bits}.faithful.f32"
        faithful_decoded = np.memmap(faithful_path, mode="w+", dtype="<f4", shape=(len(unique_ids), D))
        for start in range(0, len(unique_ids), 8192):
            stop = min(start + 8192, len(unique_ids))
            ids_chunk = unique_ids[start:stop]
            levels_chunk = unpack_thq(np.asarray(thq_codes[ids_chunk]))
            base_chunk = centroids[np.arange(D)[None, :], levels_chunk]
            docs_chunk = np.asarray(documents[ids_chunk], dtype=np.float32)
            symbols, scales = faithful.encode(docs_chunk - base_chunk, bits)
            decoded_residual = faithful.decode(symbols, scales, bits)
            combined = base_chunk + decoded_residual
            original_norm_sq = np.sum(docs_chunk * docs_chunk, axis=1)
            combined_norm_sq = np.sum(combined * combined, axis=1)
            global_scale = np.sqrt(original_norm_sq / np.maximum(combined_norm_sq, np.finfo(np.float32).tiny))
            global_scale_bits = np.asarray([faithful.ue7m9_encode(float(value)) for value in global_scale], dtype=np.uint16)
            global_scale_decoded = np.asarray([faithful.ue7m9_decode(int(value)) for value in global_scale_bits], dtype=np.float32)
            faithful_decoded[start:stop] = combined * global_scale_decoded[:, None]
        faithful_decoded.flush()
        symbol_bytes = int((D * bits + 7) // 8)
        models[f"rslm{bits}-faithful"] = {
            "bits": bits, "decoded": faithful_decoded, "full_vector": True,
            # Official relative mode stores the raw codec's UE7M9 scale and
            # a second UE7M9 scale for the reconstructed full vector.
            "side_bytes": symbol_bytes + 4, "raw_payload_bytes": symbol_bytes + 2,
            "inner_scale_bytes": 2, "outer_scale_bytes": 2,
            "source": "official-relative-mode", "artifact_sha256": sha256(faithful_path)
        }
        signs = np.random.default_rng(20260916).choice(np.asarray([-1.0, 1.0], dtype=np.float32), size=(3, 128))
        centers = local_helpers.lloyd_centers(local_helpers.fwht_blocks(local_fit, signs), bits, iterations=int(args.local_iterations))
        local_path = artifact_dir / f"rslm{bits}.local-residual.f32"
        local_decoded = np.memmap(local_path, mode="w+", dtype="<f4", shape=(len(unique_ids), D))
        for start in range(0, len(unique_ids), 8192):
            stop = min(start + 8192, len(unique_ids))
            ids_chunk = unique_ids[start:stop]
            levels_chunk = unpack_thq(np.asarray(thq_codes[ids_chunk]))
            base_chunk = centroids[np.arange(D)[None, :], levels_chunk]
            residual_chunk = np.asarray(documents[ids_chunk], dtype=np.float32) - base_chunk
            rotated = local_helpers.fwht_blocks(residual_chunk, signs)
            local_symbols = np.argmin(np.abs(rotated[:, :, None] - centers[None, :, :]), axis=2).astype(np.uint8)
            local_decoded[start:stop] = local_helpers.fwht_blocks(centers[np.arange(D)[None, :], local_symbols], signs, inverse=True)
        local_decoded.flush()
        models[f"rslm{bits}-local"] = {"bits": bits, "decoded": local_decoded, "full_vector": False, "side_bytes": int((D * bits + 7) // 8), "raw_payload_bytes": int((D * bits + 7) // 8), "inner_scale_bytes": 0, "outer_scale_bytes": 0, "source": "historical-local-control", "artifact_sha256": sha256(local_path)}
    rows = []
    for qi, query in enumerate(queries):
        candidate = candidate_ids[offsets[qi]:offsets[qi + 1]]
        filtered = interval_top(query, candidate, thq_codes, thresholds)
        positions = np.asarray([id_to_row[int(doc)] for doc in filtered])
        levels = unpack_thq(np.asarray(thq_codes[filtered]))
        base = centroids[np.arange(D)[None, :], levels]
        exact_ip = top10(np.asarray(documents[filtered]) @ query, filtered)
        exact_cosine_scores = np.einsum("kd,d->k", np.asarray(documents[filtered]), query) / np.maximum(np.linalg.norm(np.asarray(documents[filtered]), axis=1) * query_norms[qi], np.finfo(np.float32).tiny)
        exact_cosine = top10(exact_cosine_scores, filtered)
        for name, model in models.items():
            decoded = np.asarray(model["decoded"])[positions]
            values = decoded if bool(model["full_vector"]) else base + decoded
            ip_scores = np.einsum("kd,d->k", values, query)
            cosine_scores = ip_scores / np.maximum(np.linalg.norm(values, axis=1) * query_norms[qi], np.finfo(np.float32).tiny)
            selected_ip = top10(ip_scores, filtered)
            selected_cosine = top10(cosine_scores, filtered)
            rows.append({"query": qi, "arm": name,
                         "ip_top10_ids": selected_ip.astype(int).tolist(),
                         "cosine_top10_ids": selected_cosine.astype(int).tolist(),
                         "ip_candidate_fp32_overlap": float(np.isin(exact_ip, selected_ip).sum() / 10.0),
                         "cosine_candidate_fp32_overlap": float(np.isin(exact_cosine, selected_cosine).sum() / 10.0),
                         "ip_teacher_overlap": float(np.isin(teacher_ids[qi], selected_ip).sum() / 10.0),
                         "cosine_teacher_overlap": float(np.isin(teacher_ids[qi], selected_cosine).sum() / 10.0),
                         "ip_qrels_ndcg10": ndcg10(selected_ip, qrel_ids[qi], qrel_scores[qi]),
                         "cosine_qrels_ndcg10": ndcg10(selected_cosine, qrel_ids[qi], qrel_scores[qi]),
                         "side_payload_bytes": int(model["side_bytes"]),
                         "raw_codec_payload_bytes": int(model["raw_payload_bytes"]),
                         "inner_scale_bytes": int(model["inner_scale_bytes"]),
                         "outer_scale_bytes": int(model["outer_scale_bytes"]),
                         "cascade_total_bytes": 96 + int(model["side_bytes"])})
    summaries = {}
    for name in models:
        subset = [row for row in rows if row["arm"] == name]
        summaries[name] = {metric: float(np.mean([row[metric] for row in subset])) for metric in ("ip_qrels_ndcg10", "cosine_qrels_ndcg10", "ip_teacher_overlap", "cosine_teacher_overlap", "ip_candidate_fp32_overlap", "cosine_candidate_fp32_overlap")}
    result = {"schema_version": 1, "family": "thq_rslm_faithful_gate_v1", "status": "EXECUTED", "reference_source": f"https://github.com/google-research/google-research/tree/{faithful.REFERENCE_CONTENT_COMMIT}/rslm", "reference_initial_commit": faithful.REFERENCE_INITIAL_COMMIT, "reference_content_commit": faithful.REFERENCE_CONTENT_COMMIT, "reference_snapshot_commit": faithful.REFERENCE_SNAPSHOT_COMMIT, "reference_notebook_blob": faithful.REFERENCE_NOTEBOOK_BLOB, "reference_notebook_sha256": faithful.REFERENCE_NOTEBOOK_SHA256, "paper": "arXiv:2608.30384", "documents": count, "training_count": train_count, "query_count": len(queries), "local_fit_rows": local_fit_rows, "local_iterations": int(args.local_iterations), "runner_sha256": sha256(Path(__file__)), "faithful_reference_sha256": sha256(HERE / "rslm-faithful-reference.py"), "local_helper_sha256": sha256(HERE / "run-thq-residual-extended-frontier.py"), "packed_codec_helper_sha256": sha256(HERE / "thq-packed-codecs.py"), "candidate_flat_sha256": sha256(args.candidate_flat), "candidate_raw_sha256": sha256(args.candidate_raw), "candidate_receipt_sha256": sha256(args.candidate_receipt), "documents_sha256": sha256(args.documents), "training_sha256": sha256(args.train_vectors), "queries_sha256": sha256(args.queries), "qrel_ids_sha256": sha256(args.qrel_ids), "qrel_scores_sha256": sha256(args.qrel_scores), "teacher_ids_sha256": sha256(args.teacher_ids), "thq4_codes_sha256": sha256(args.thq4_codes), "thq4_thresholds_sha256": sha256(args.thq4_thresholds), "candidate_unique_documents": int(len(unique_ids)), "norm_diagnostics": norm_diagnostics, "scoring_protocol": {"primary": "ip", "diagnostic": "cosine_adapted", "unit_norm_tolerance": UNIT_NORM_TOLERANCE, "exact_ip_oracle": "documents @ query", "approx_ip": "outer-scaled reconstructed vector @ query", "approx_cosine": "reconstructed vector cosine query; positive outer scale cancels"}, "models": {name: {"bits": int(model["bits"]), "side_payload_bytes": int(model["side_bytes"]), "raw_codec_payload_bytes": int(model["raw_payload_bytes"]), "inner_scale_bytes": int(model["inner_scale_bytes"]), "outer_scale_bytes": int(model["outer_scale_bytes"]), "source": model["source"]} for name, model in models.items()}, "summaries": summaries, "rows": rows, "rq_audit_sha256": sha256(args.rq_audit) if args.rq_audit else None, "limitations": ["RSLM uses the official paper constants, two-pass transform, inner UE7M9 norm scale, and relative-mode outer UE7M9 full-vector norm scale", "direct/raw codec payload and official relative residual payload are reported separately; relative mode adds the second UE7M9 scale", "this is a NumPy correctness oracle, not native latency", "RSLM and local control are matched after the same THQ4 top128 filter", "IP is the paper-faithful primary line; cosine-adapted is a separate product diagnostic", "local control fit is explicitly bounded by local_fit_rows/local_iterations and is not the historical full-fit result", "RQ32/RQ48 audit is accepted only as an external baseline; no claim of matched RQ replay is made by this runner", "held-out domain confirmation remains pending"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
