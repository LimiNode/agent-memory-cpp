#!/usr/bin/env python3
"""Fresh 305-query quality decomposition for the packed finalist codecs.

This is an independent NumPy research evaluator.  It deliberately measures
quality only: route candidates, THQ top-128 and each persisted packed scorer
are replayed against the untouched fresh qrels.  It is not a serving runner.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import struct
from pathlib import Path
from typing import Any, Callable

import numpy as np

D, N, Q, THQ_BYTES, CANDIDATES, TOP = 384, 1_000_000, 305, 96, 5000, 128


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unpack_levels(codes: np.ndarray) -> np.ndarray:
    out = np.empty((len(codes), D), dtype=np.uint8)
    for b in range(THQ_BYTES):
        value = codes[:, b]
        out[:, 4 * b:4 * b + 4] = np.stack(
            (value & 3, (value >> 2) & 3, (value >> 4) & 3, (value >> 6) & 3), axis=1
        )
    return out


def thq_lut(query: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    result = np.empty((D, 4), dtype=np.float64)
    for d, value in enumerate(query.astype(np.float64)):
        c0, c1, c2 = thresholds[d]
        result[d] = (max(value - c0, 0.0) ** 2,
                     0.0 if c0 <= value <= c1 else min((value - c0) ** 2, (value - c1) ** 2),
                     0.0 if c1 <= value <= c2 else min((value - c1) ** 2, (value - c2) ** 2),
                     max(c2 - value, 0.0) ** 2)
    return result


def thq_scores(codes: np.ndarray, query: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    lut = thq_lut(query, thresholds)
    levels = unpack_levels(codes)
    return lut[np.arange(D)[None, :], levels].sum(axis=1)


def ordered_top(ids: np.ndarray, scores: np.ndarray, k: int, *, ascending: bool = False) -> np.ndarray:
    if not np.all(np.isfinite(scores)):
        raise ValueError("non-finite score encountered")
    take = min(k, len(ids))
    if take == len(ids):
        selected = np.arange(len(ids))
    else:
        selected = np.argpartition(scores if ascending else -scores, take - 1)[:take]
    order = np.lexsort((ids[selected], scores[selected] if ascending else -scores[selected]))
    return ids[selected][order][:k]


def parse_lsq(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if data[:8] != b"AMLSQ01\0":
        raise ValueError("unexpected LSQ payload header")
    stages, dim, count = struct.unpack_from("<III", data, 8)
    if dim != D or count != N:
        raise ValueError("unexpected LSQ payload dimensions")
    off = 20
    ids = np.frombuffer(data, dtype="<i4", count=count, offset=off); off += ids.nbytes
    if ids[0] != 0 or ids[-1] != count - 1:
        raise ValueError("LSQ payload IDs are not the canonical dense corpus order")
    codes = np.frombuffer(data, dtype=np.uint8, count=count * stages, offset=off).reshape(count, stages); off += codes.nbytes
    books = np.frombuffer(data, dtype="<f4", count=stages * 256 * D, offset=off).reshape(stages, 256, D); off += books.nbytes
    cent = np.frombuffer(data, dtype="<f4", count=4 * D, offset=off).reshape(D, 4); off += cent.nbytes
    norms = np.frombuffer(data, dtype="<f4", count=count, offset=off)
    return {"codes": codes, "books": books, "cent": cent, "norms": norms}


def parse_plsq(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if data[:8] != b"AMPLSQF1":
        raise ValueError("unexpected PLSQ payload header")
    count, splits, sub, code_bytes = struct.unpack_from("<4I", data, 8)
    if (count, splits, sub, code_bytes) != (N, 8, 6, 48):
        raise ValueError("unexpected PLSQ payload dimensions")
    off = 24
    codes = np.frombuffer(data, dtype=np.uint8, count=N * code_bytes, offset=off).reshape(N, code_bytes); off += codes.nbytes
    norms = np.frombuffer(data, dtype="<f4", count=N, offset=off); off += norms.nbytes
    cent = np.frombuffer(data, dtype="<f4", count=D * 4, offset=off).reshape(D, 4); off += cent.nbytes
    books = np.frombuffer(data, dtype="<f4", count=splits * sub * 256 * (D // splits), offset=off).reshape(splits, sub, 256, D // splits)
    return {"codes": codes, "norms": norms, "cent": cent, "books": books}


def parse_tq(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    magic = data[:8]
    dim, count, width, flags = struct.unpack_from("<4I", data, 8)
    if (dim, count) != (D, N):
        raise ValueError("unexpected TQ payload dimensions")
    if magic == b"AMTQF01\0":
        off = 24
        cent = np.frombuffer(data, dtype="<f4", count=D * 4, offset=off).reshape(D, 4); off += cent.nbytes
        rows = np.frombuffer(data, dtype=np.uint8, count=N * 52, offset=off).reshape(N, 52)
        return {"signs": rows[:, :48], "scales": rows[:, 48:].view("<f4").reshape(N), "cent": cent, "pq": None, "final_norms": None, "books": None}
    if magic != b"AMTQP01\0" or width != 8:
        raise ValueError("unexpected TQ payload header")
    off = 24
    ids = np.frombuffer(data, dtype="<i4", count=N, offset=off); off += ids.nbytes
    signs = np.frombuffer(data, dtype=np.uint8, count=N * 48, offset=off).reshape(N, 48); off += signs.nbytes
    scales = np.frombuffer(data, dtype="<f4", count=N, offset=off); off += scales.nbytes
    pq = np.frombuffer(data, dtype=np.uint8, count=N * 8, offset=off).reshape(N, 8); off += pq.nbytes
    if flags & 1:
        off += N * 4
    final_norms = np.frombuffer(data, dtype="<f4", count=N, offset=off); off += final_norms.nbytes
    cent = np.frombuffer(data, dtype="<f4", count=D * 4, offset=off).reshape(D, 4); off += cent.nbytes
    books = np.frombuffer(data, dtype="<f4", count=8 * 256 * 48, offset=off).reshape(8, 256, 48)
    return {"signs": signs, "scales": scales, "cent": cent, "pq": pq, "final_norms": final_norms, "books": books}


def make_scorer(name: str, path: Path, thq: np.memmap, thresholds: np.ndarray, aux: dict[str, Path]) -> Callable[[np.ndarray, np.ndarray], np.ndarray]:
    qnorm_cache: dict[int, float] = {}
    if name.startswith("lsq"):
        p = parse_lsq(path)
        def score(ids: np.ndarray, q: np.ndarray) -> np.ndarray:
            rows = p["codes"][ids]; base = p["cent"]
            level = unpack_levels(np.asarray(thq[ids]))
            base_score = np.sum(base[np.arange(D)[None, :], level] * q[None, :], axis=1, dtype=np.float64)
            stage_lut = np.einsum("swd,d->sw", p["books"], q.astype(np.float32), optimize=True)
            code_score = stage_lut[np.arange(p["codes"].shape[1])[None, :], rows].sum(axis=1, dtype=np.float64)
            qn = float(np.linalg.norm(q.astype(np.float64)))
            return (base_score + code_score) / np.maximum(p["norms"][ids].astype(np.float64) * qn, 1e-30)
        return score
    if name == "plsq8x6x8":
        p = parse_plsq(path)
        def score(ids: np.ndarray, q: np.ndarray) -> np.ndarray:
            rows = p["codes"][ids]; level = unpack_levels(np.asarray(thq[ids]))
            value = np.sum(p["cent"][np.arange(D)[None, :], level] * q[None, :], axis=1, dtype=np.float64)
            for split in range(8):
                lut = np.einsum("cwd,d->cw", p["books"][split], q[split * 48:(split + 1) * 48], optimize=True)
                value += lut[np.arange(6)[None, :], rows[:, split * 6:(split + 1) * 6]].sum(axis=1, dtype=np.float64)
            qn = float(np.linalg.norm(q.astype(np.float64)))
            return value / np.maximum(p["norms"][ids].astype(np.float64) * qn, 1e-30)
        return score
    if name in ("tq1", "tq1-pq8"):
        p = parse_tq(path)
        tqref = load_module("tq_reference", Path(__file__).with_name("run-thq-turboquant-reference.py"))
        def score(ids: np.ndarray, q: np.ndarray) -> np.ndarray:
            level = unpack_levels(np.asarray(thq[ids])); value = np.sum(p["cent"][np.arange(D)[None, :], level] * q[None, :], axis=1, dtype=np.float64)
            rot = tqref.rotate(q[None, :].astype(np.float64))[0]
            sign_lut = np.empty((48, 256), dtype=np.float64); c = 0.7978846
            for b in range(48):
                sign_lut[b] = np.asarray([np.sum(np.where((v >> np.arange(8)) & 1, 1., -1.) * c * rot[b * 8:b * 8 + 8]) for v in range(256)])
            value += sign_lut[np.arange(48)[None, :], p["signs"][ids]].sum(axis=1) * p["scales"][ids]
            if name == "tq1-pq8":
                pq_lut = np.einsum("swd,sd->sw", p["books"], q.reshape(8, 48), optimize=True)
                value += pq_lut[np.arange(8)[None, :], p["pq"][ids]].sum(axis=1)
                norms = p["final_norms"][ids]
            else:
                norms = aux["tq1_norms"] if isinstance(aux.get("tq1_norms"), np.ndarray) else np.fromfile(aux["tq1_norms"], dtype="<f4")[ids]
            return value / np.maximum(np.asarray(norms, dtype=np.float64) * np.linalg.norm(q.astype(np.float64)), 1e-30)
        return score
    if name == "int8":
        codes = np.memmap(path, dtype=np.int8, mode="r", shape=(N, D)); inv = np.memmap(aux["int8_inv_norm"], dtype="<f4", mode="r", shape=(N,))
        def score(ids: np.ndarray, q: np.ndarray) -> np.ndarray:
            return (codes[ids].astype(np.float32) @ q.astype(np.float32)).astype(np.float64) * inv[ids].astype(np.float64) * (1.0 / max(float(np.linalg.norm(q.astype(np.float64))), 1e-30))
        return score
    if name == "rslm1":
        ref = load_module("rslm_reference", Path(__file__).with_name("rslm-faithful-reference.py"))
        symbols = np.memmap(aux["rslm_symbols"], dtype=np.uint8, mode="r", shape=(N, 48)); inner = np.memmap(aux["rslm_inner"], dtype="<u2", mode="r", shape=(N,)); norms = np.memmap(aux["rslm_norms"], dtype="<f4", mode="r", shape=(N,)); cent = np.fromfile(aux["rslm_centroids"], dtype="<f4").reshape(D, 4)
        def score(ids: np.ndarray, q: np.ndarray) -> np.ndarray:
            level = unpack_levels(np.asarray(thq[ids])); base = cent[np.arange(D)[None, :], level]
            decoded = ref.decode_joint1(np.asarray(symbols[ids]), np.asarray(inner[ids])).astype(np.float64) + base.astype(np.float64)
            return (decoded @ q.astype(np.float64)) / np.maximum(norms[ids].astype(np.float64) * np.linalg.norm(q.astype(np.float64)), 1e-30)
        return score
    raise ValueError(f"unsupported codec {name}")


def metrics(top_ids: list[str], qrels: dict[str, int]) -> dict[str, float]:
    grades = [qrels.get(x, 0) for x in top_ids[:10]]
    ideal = sorted(qrels.values(), reverse=True)[:10]
    def dcg(values: list[int]) -> float:
        return sum((2.0 ** v - 1.0) / np.log2(i + 2.0) for i, v in enumerate(values))
    denom = dcg(ideal); ndcg = 0.0 if denom == 0.0 else dcg(grades) / denom
    rr = next((1.0 / (i + 1) for i, v in enumerate(grades) if v > 0), 0.0)
    return {"ndcg_at_10": float(ndcg), "mrr": float(rr)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--thq", type=Path, required=True)
    ap.add_argument("--thresholds", type=Path, required=True)
    ap.add_argument("--prototype", type=Path, required=True)
    ap.add_argument("--r4", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--exact-result", type=Path)
    ap.add_argument("--int8", type=Path, required=True); ap.add_argument("--int8-inv-norm", type=Path, required=True)
    ap.add_argument("--lsq32", type=Path, required=True); ap.add_argument("--lsq48", type=Path, required=True)
    ap.add_argument("--tq1", type=Path, required=True); ap.add_argument("--tq1-norms", type=Path, required=True); ap.add_argument("--tq1-pq8", type=Path, required=True)
    ap.add_argument("--plsq", type=Path, required=True); ap.add_argument("--rslm-symbols", type=Path, required=True); ap.add_argument("--rslm-inner", type=Path, required=True); ap.add_argument("--rslm-norms", type=Path, required=True); ap.add_argument("--rslm-centroids", type=Path, required=True)
    args = ap.parse_args()
    root = args.source_root; payload = root / "payload"
    docs = np.memmap(payload / "evaluation-document-vectors.f32", dtype="<f4", mode="r", shape=(N, D)); queries = np.memmap(payload / "evaluation-query-vectors.f32", dtype="<f4", mode="r", shape=(Q, D))
    doc_ids = [json.loads(x)["id"] for x in (payload / "evaluation-document-ids.jsonl").read_text().splitlines()]; query_ids = [json.loads(x)["id"] for x in (payload / "evaluation-query-ids.jsonl").read_text().splitlines()]
    doc_pos = {value: index for index, value in enumerate(doc_ids)}
    qrels = {qid: {} for qid in query_ids}
    for line in (payload / "evaluation-qrels.tsv").read_text().splitlines():
        qid, _, did, grade = line.split(); qrels[qid][did] = int(grade)
    thresholds = np.fromfile(args.thresholds, dtype="<f4").reshape(D, 3); thq = np.memmap(args.thq, dtype=np.uint8, mode="r", shape=(N, THQ_BYTES))
    streams = {"prototype_ivf": np.fromfile(args.prototype, dtype="<i4").reshape(Q, CANDIDATES), "modern_r4": np.fromfile(args.r4, dtype="<i4").reshape(Q, CANDIDATES)}
    codecs = {"int8": (args.int8, {"int8_inv_norm": args.int8_inv_norm}), "lsq32": (args.lsq32, {}), "lsq48": (args.lsq48, {}), "tq1": (args.tq1, {"tq1_norms": args.tq1_norms}), "tq1-pq8": (args.tq1_pq8, {}), "plsq8x6x8": (args.plsq, {}), "rslm1": (args.rslm_symbols, {"rslm_symbols": args.rslm_symbols, "rslm_inner": args.rslm_inner, "rslm_norms": args.rslm_norms, "rslm_centroids": args.rslm_centroids})}
    scorers = {name: make_scorer(name, path, thq, thresholds, aux) for name, (path, aux) in codecs.items()}
    out: dict[str, Any] = {"schema_version": 1, "family": "fresh_packed_quality_decomposition_v1", "status": "EXECUTED", "query_count": Q, "candidate_count": CANDIDATES, "stages": {}, "source": {"documents_sha256": sha256(payload / "evaluation-document-vectors.f32"), "queries_sha256": sha256(payload / "evaluation-query-vectors.f32"), "qrels_sha256": sha256(payload / "evaluation-qrels.tsv"), "thq_sha256": sha256(args.thq), "thresholds_sha256": sha256(args.thresholds)}}
    out["source"]["candidate_streams"] = {name: sha256(path) for name, path in (("prototype_ivf", args.prototype), ("modern_r4", args.r4))}
    exact_top10 = []
    exact_rows: list[dict[str, float]] = []
    if args.exact_result is not None:
        baseline = json.loads(args.exact_result.read_text(encoding="utf-8"))
        rows = baseline.get("per_query", [])
        if len(rows) != Q:
            raise ValueError("exact result does not cover 305 queries")
        exact_top10 = [np.asarray([doc_pos[str(value)] for value in row["top10_ids"]], dtype=np.int32) for row in rows]
        exact_rows = [{"ndcg_at_10": float(row["ndcg_at_10"]), "mrr": float(row["mrr"])} for row in rows]
    else:
        for qi, q in enumerate(queries):
            scores = np.asarray(docs @ q, dtype=np.float32); ids = ordered_top(np.arange(N, dtype=np.int32), scores, 10); exact_top10.append(ids)
    def summarize(rows: list[dict[str, float]]) -> dict[str, Any]:
        nd = [r["ndcg_at_10"] for r in rows]; mr = [r["mrr"] for r in rows]
        return {"mean_ndcg_at_10": float(np.mean(nd)), "mean_mrr": float(np.mean(mr)), "p05_ndcg_at_10": float(np.sort(nd)[max(0, int(np.ceil(.05 * len(nd))) - 1)]), "p05_mrr": float(np.sort(mr)[max(0, int(np.ceil(.05 * len(mr))) - 1)]), "worst_ndcg_at_10": float(np.min(nd)), "worst_mrr": float(np.min(mr)), "per_query": rows}
    if not exact_rows:
        exact_rows = [metrics([doc_ids[i] for i in ids], qrels[query_ids[qi]]) for qi, ids in enumerate(exact_top10)]
    out["exact_oracle"] = summarize(exact_rows)
    for mode, stream in streams.items():
        route_rows: list[dict[str, float]] = []; thq_rows: list[dict[str, float]] = []; codec_rows = {name: [] for name in codecs}
        for qi, q in enumerate(queries):
            cand = stream[qi]; exact_scores = np.asarray(docs[cand] @ q, dtype=np.float32); route_ids = ordered_top(cand, exact_scores, 10); route_rows.append(metrics([doc_ids[i] for i in route_ids], qrels[query_ids[qi]]))
            thq_scores_row = thq_scores(np.asarray(thq[cand]), q, thresholds); top = ordered_top(cand, thq_scores_row, TOP, ascending=True); thq_exact = np.asarray(docs[top] @ q, dtype=np.float32); thq_ids = ordered_top(top, thq_exact, 10); thq_rows.append(metrics([doc_ids[i] for i in thq_ids], qrels[query_ids[qi]]))
            for name, scorer in scorers.items():
                packed = ordered_top(top, scorer(top, q), 10); codec_rows[name].append(metrics([doc_ids[i] for i in packed], qrels[query_ids[qi]]))
        out["stages"][mode] = {"route_exact_fp32": summarize(route_rows), "thq_top128_exact_fp32": summarize(thq_rows), "packed": {name: summarize(rows) for name, rows in codec_rows.items()}}
    out["payloads"] = {name: {"path": str(path), "sha256": sha256(path)} for name, (path, _) in codecs.items()}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8"); print(json.dumps({"output": str(args.output), "query_count": Q, "codecs": list(codecs)}, sort_keys=True))


if __name__ == "__main__":
    main()
