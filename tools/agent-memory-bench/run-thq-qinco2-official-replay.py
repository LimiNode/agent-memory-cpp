#!/usr/bin/env python3
"""Source-bound replay for an official QINCo2 checkpoint on the THQ shell.

This runner deliberately keeps the external QINCo2 checkout out of the
product tree.  It materializes only candidate-local codes and records every
input/checkpoint hash; a separate audit re-decodes those persisted codes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

D, THQ_BYTES, QUERY_COUNT = 384, 96, 152


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def unpack(codes: np.ndarray) -> np.ndarray:
    packed = np.asarray(codes, dtype=np.uint8)
    levels = np.empty((len(packed), D), dtype=np.uint8)
    for byte in range(THQ_BYTES):
        value = packed[:, byte]
        levels[:, 4 * byte : 4 * byte + 4] = np.stack(
            (value & 3, (value >> 2) & 3, (value >> 4) & 3, (value >> 6) & 3), axis=1
        )
    return levels


def top_ids(scores: np.ndarray, ids: np.ndarray) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:10]]


def ndcg10(ids: np.ndarray, grades: dict[int, float]) -> float:
    gains = np.asarray([2.0 ** grades.get(int(doc), 0.0) - 1.0 for doc in ids])
    ideal = np.sort(np.asarray([2.0 ** g - 1.0 for g in grades.values()]))[::-1][:10]
    denom = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal)))))
    return float(np.sum(gains / np.log2(np.arange(2, 2 + len(gains))) / denom)) if denom else 0.0


def main() -> None:
    p = argparse.ArgumentParser()
    for name in ("qinco-root", "checkpoint", "documents", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "lsq-models", "lsq-codes", "output", "codes-output"):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    p.add_argument("--batch-size", type=int, default=64)
    a = p.parse_args()
    if a.batch_size < 1:
        p.error("batch size must be positive")
    root = a.qinco_root.resolve()
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    sys.path.insert(0, str(root))
    import torch
    from qinco.model.qinco_base import QINCo

    saved = torch.load(a.checkpoint, map_location="cpu", weights_only=False)
    params = saved.get("parameters", {})
    required = {"M", "K", "L", "de", "dh", "A", "B"}
    if not required.issubset(params) or int(saved.get("data_dim", D)) != D:
        raise RuntimeError("checkpoint does not contain the expected QINCo2 configuration")
    acc = SimpleNamespace(device=torch.device("cpu"), print=lambda *args, **kwargs: None)
    cfg = SimpleNamespace(_accelerator=acc, _D=D, _M_ivf=int(params["M"]), K=int(params["K"]),
        L=int(params["L"]), de=int(params["de"]), dh=int(params["dh"]), A=int(params["A"]),
        B=int(params["B"]), _ivf_book=None, _qinco_jit=False, ivf_in_use=False, task="eval",
        _data_mean=np.zeros(D, np.float32), _data_std=1.0, codebook_noise_init=0.0,
        qinco1_mode=False,
        # Keep the upstream beam search intact, but let the research runner
        # choose a larger inference chunk than the upstream conservative
        # default.  This changes batching only, not code assignment semantics.
        enc_max_bs=max(32768, int(a.batch_size) * int(params["A"]) * int(params["B"])))
    model = QINCo(cfg)
    model.load_state_dict(saved["model"])
    model.eval()

    lsq = np.load(a.lsq_models, allow_pickle=False)
    selected = np.asarray(np.load(a.lsq_codes, allow_pickle=False)["selected_ids"], dtype=np.int64)
    unique_ids = np.unique(selected)
    centroids = np.asarray(lsq["centroids"], dtype=np.float32)
    docs = np.memmap(a.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    queries = np.memmap(a.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D))
    thq = np.memmap(a.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    levels = unpack(np.asarray(thq[unique_ids]))
    base = centroids[np.arange(D)[None, :], levels]
    residual = np.asarray(docs[unique_ids], dtype=np.float32) - base
    codes_parts, decode_parts = [], []
    with torch.inference_mode():
        for start in range(0, len(unique_ids), a.batch_size):
            batch = torch.from_numpy(residual[start : start + a.batch_size])
            codes, decoded = model.encode((batch - model.data_mean) / model.data_std)
            codes_parts.append(codes.cpu().numpy().astype(np.uint16))
            decode_parts.append((decoded * model.data_std + model.data_mean).cpu().numpy().astype(np.float32))
    codes = np.concatenate(codes_parts, axis=1)
    decoded = np.concatenate(decode_parts, axis=0)
    reconstructed = base + decoded
    qrel_ids = np.memmap(a.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20))
    qrel_scores = np.memmap(a.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20))
    teacher_ids = np.memmap(a.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10))
    position = {int(doc): i for i, doc in enumerate(unique_ids)}
    rows = []
    for qi in range(QUERY_COUNT):
        ids = selected[qi]
        indexes = np.asarray([position[int(doc)] for doc in ids], dtype=np.int64)
        values = np.asarray(reconstructed[indexes], dtype=np.float64)
        query = np.asarray(queries[qi], dtype=np.float64)
        scores = (values @ query) / np.maximum(np.linalg.norm(values, axis=1) * np.linalg.norm(query), 1e-30)
        exact = np.asarray(docs[ids], dtype=np.float64)
        exact_scores = (exact @ query) / np.maximum(np.linalg.norm(exact, axis=1) * np.linalg.norm(query), 1e-30)
        ranked = top_ids(scores, ids)
        exact_top = top_ids(exact_scores, ids)
        grades = {int(doc): float(score) for doc, score in zip(qrel_ids[qi], qrel_scores[qi]) if int(doc) >= 0 and float(score) > 0}
        rows.append({"query": qi, "arm": "qinco2_official_16b", "top10_ids": ranked.astype(int).tolist(),
            "candidate_fp32_top10_ids": exact_top.astype(int).tolist(), "candidate_fp32_overlap": float(np.isin(exact_top, ranked).sum() / 10.0),
            "qrels_ndcg10": ndcg10(ranked, grades), "teacher_overlap": float(np.isin(teacher_ids[qi], ranked).sum() / 10.0), "side_payload_bytes": int(params["M"]),
            "cascade_total_bytes": THQ_BYTES + int(params["M"])})
    a.codes_output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(a.codes_output, selected_ids=selected, unique_ids=unique_ids, codes=codes)
    model_bytes = int(sum(value.numel() * value.element_size() for value in saved["model"].values() if hasattr(value, "numel")))
    summary = {"mean_qrels_ndcg10": float(np.mean([r["qrels_ndcg10"] for r in rows])),
               "p05_qrels_ndcg10": float(np.percentile([r["qrels_ndcg10"] for r in rows], 5)),
               "worst_qrels_ndcg10": float(np.min([r["qrels_ndcg10"] for r in rows])),
               "side_payload_bytes": int(params["M"]), "cascade_total_bytes": THQ_BYTES + int(params["M"]),
               "global_model_bytes": model_bytes, "checkpoint_bytes": int(a.checkpoint.stat().st_size)}
    sources = {"documents": a.documents, "queries": a.queries, "qrel-ids": a.qrel_ids, "qrel-scores": a.qrel_scores, "teacher-ids": a.teacher_ids, "thq4-codes": a.thq4_codes,
               "lsq-models": a.lsq_models, "lsq-codes": a.lsq_codes}
    result = {"schema_version": 1, "family": "thq_qinco2_official_replay_v1", "status": "EXECUTED",
        "quality_status": "BOUNDED_UNDERTRAINED_CONTROL", "metric": "cosine", "query_count": QUERY_COUNT,
        "upstream_repository": "https://github.com/facebookresearch/Qinco", "upstream_revision": revision,
        "upstream_license": "CC-BY-NC-4.0", "candidate_count": 128, "unique_documents": int(len(unique_ids)),
        "checkpoint_sha256": sha256(a.checkpoint), "codes_artifact_sha256": sha256(a.codes_output),
        "runner_sha256": sha256(Path(__file__)), "source_hashes": {k: sha256(v) for k, v in sources.items()},
        "config": {k: int(params[k]) for k in required}, "training": {"checkpoint_epoch": int(saved.get("epoch", -1))},
        "summaries": {"qinco2_official_16b": summary}, "rows": rows,
        "limitations": ["official QINCo2 checkpoint trained for a bounded 25k/short schedule, not the 60-epoch production schedule", "candidate-local replay on the historical 152-query fold", "external CC-BY-NC source is not vendored", "no production selection claim"]}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
