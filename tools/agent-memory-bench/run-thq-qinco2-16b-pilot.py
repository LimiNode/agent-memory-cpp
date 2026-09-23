#!/usr/bin/env python3
"""Bounded 16-byte QINCo2 residual pilot on the canonical THQ shell.

The official external QINCo2 model is initialized from the first 16 stages of
the train-fitted LSQ32 codebooks and then receives a deliberately bounded CPU
fit.  The result exercises real K=256 implicit codebooks, candidate
preselection and beam search, but is not a converged production training run.
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

D, THQ_BYTES, QUERY_COUNT, TOP, STAGES, K = 384, 96, 152, 128, 16, 256


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
        levels[:, 4 * byte : 4 * byte + 4] = np.stack((value & 3, (value >> 2) & 3, (value >> 4) & 3, (value >> 6) & 3), axis=1)
    return levels


def top_ids(scores: np.ndarray, ids: np.ndarray) -> np.ndarray:
    return ids[np.lexsort((ids, -np.asarray(scores, dtype=np.float64)))[:10]]


def cosine(values: np.ndarray, query: np.ndarray) -> np.ndarray:
    x, q = np.asarray(values, dtype=np.float64), np.asarray(query, dtype=np.float64)
    return (x @ q) / np.maximum(np.linalg.norm(x, axis=1) * np.linalg.norm(q), 1e-30)


def ndcg10(ids: np.ndarray, qids: np.ndarray, grades: np.ndarray) -> float:
    rel = {int(d): float(g) for d, g in zip(qids, grades) if int(d) >= 0 and float(g) > 0}
    gains = np.asarray([2.0 ** rel.get(int(d), 0.0) - 1.0 for d in ids])
    ideal = np.sort(np.asarray([2.0 ** grade - 1.0 for grade in rel.values()]))[::-1][:10]
    dcg = float(np.sum(gains / np.log2(np.arange(2, 2 + len(gains)))))
    idcg = float(np.sum(ideal / np.log2(np.arange(2, 2 + len(ideal))))) if len(ideal) else 0.0
    return dcg / idcg if idcg else 0.0


def build_config(torch, data_mean: np.ndarray, data_std: float, train: bool) -> SimpleNamespace:
    accelerator = SimpleNamespace(device=torch.device("cpu"), print=lambda *args, **kwargs: None)
    return SimpleNamespace(
        _accelerator=accelerator, _D=D, _M_ivf=STAGES, K=K, L=1, de=32, dh=64,
        A=8, B=4, _ivf_book=None, _qinco_jit=False, ivf_in_use=False,
        task="train" if train else "eval", _data_mean=data_mean, _data_std=data_std,
        codebook_noise_init=0.0, qinco1_mode=False, enc_max_bs=32768,
    )


def main() -> None:
    p = argparse.ArgumentParser()
    for name in ("qinco-root", "documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "lsq-models", "lsq-codes", "output", "model-artifact", "codes-artifact"):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    p.add_argument("--train-rows", type=int, default=1024)
    p.add_argument("--epochs", type=int, default=2)
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--learning-rate", type=float, default=8e-4)
    p.add_argument("--seed", type=int, default=20260923)
    a = p.parse_args()
    if a.train_rows < K or a.epochs < 1 or a.batch_size < 1:
        p.error("invalid bounded QINCo2 configuration")

    root = a.qinco_root.resolve()
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    sys.path.insert(0, str(root))
    import torch
    from qinco.model.qinco_base import QINCo, initialize_qinco_codebooks

    np.random.seed(a.seed)
    torch.manual_seed(a.seed)
    docs = np.memmap(a.documents, mode="r", dtype="<f4", shape=(1_000_000, D))
    train = np.memmap(a.train_vectors, mode="r", dtype="<f4", shape=(25_000, D))
    queries = np.memmap(a.queries, mode="r", dtype="<f4", shape=(QUERY_COUNT, D))
    qids = np.memmap(a.qrel_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 20))
    grades = np.memmap(a.qrel_scores, mode="r", dtype="<f4", shape=(QUERY_COUNT, 20))
    teacher = np.memmap(a.teacher_ids, mode="r", dtype="<i8", shape=(QUERY_COUNT, 10))
    thq = np.memmap(a.thq4_codes, mode="r", dtype=np.uint8, shape=(1_000_000, THQ_BYTES))
    thresholds = np.fromfile(a.thq4_thresholds, dtype="<f4").reshape(3, D)
    lsq_models = np.load(a.lsq_models, allow_pickle=False)
    centroids = np.asarray(lsq_models["centroids"], dtype=np.float32)
    books = np.asarray(lsq_models["lsq32_codebooks"], dtype=np.float32)
    offsets = np.asarray(lsq_models["lsq32_offsets"], dtype=np.int64)
    selected = np.asarray(np.load(a.lsq_codes, allow_pickle=False)["selected_ids"], dtype=np.int64)
    unique_ids = np.unique(selected)

    train_values = np.asarray(train[: a.train_rows], dtype=np.float32)
    train_levels = np.sum(train_values[:, None, :] > thresholds[None, :, :], axis=1)
    train_base = centroids[np.arange(D)[None, :], train_levels]
    residual_train = np.asarray(train_values - train_base, dtype=np.float32)
    data_mean = residual_train.mean(axis=0, dtype=np.float64).astype(np.float32)
    data_std = float(np.std(residual_train.astype(np.float64)))
    cfg = build_config(torch, data_mean, data_std, train=True)
    model = QINCo(cfg)
    rq_centroids = torch.from_numpy(np.stack([books[offsets[stage] : offsets[stage + 1]] for stage in range(STAGES)]))
    initialize_qinco_codebooks(cfg, model, rq_centroids)
    optimizer = torch.optim.AdamW(model.parameters(), lr=a.learning_rate, weight_decay=0.1)
    permutation_rng = np.random.default_rng(a.seed)
    losses = []
    model.train()
    for _ in range(a.epochs):
        for start in range(0, a.train_rows, a.batch_size):
            if start == 0:
                order = permutation_rng.permutation(a.train_rows)
            batch = torch.from_numpy(residual_train[order[start : start + a.batch_size]])
            _, _, loss_dict = model(batch, step="train")
            loss = torch.stack(list(loss_dict.values())).sum()
            loss.backward()
            torch.nn.utils.clip_grad_value_(model.parameters(), 0.1)
            optimizer.step()
            optimizer.zero_grad()
            losses.append(float(loss.detach()))

    unique_values = np.asarray(docs[unique_ids], dtype=np.float32)
    unique_levels = unpack(np.asarray(thq[unique_ids]))
    unique_base = centroids[np.arange(D)[None, :], unique_levels]
    residual_unique = np.asarray(unique_values - unique_base, dtype=np.float32)
    model.eval()
    code_parts, decode_parts = [], []
    with torch.inference_mode():
        for start in range(0, len(unique_ids), 128):
            batch = torch.from_numpy(residual_unique[start : start + 128])
            codes, decoded = model.encode((batch - model.data_mean) / model.data_std)
            decoded = decoded * model.data_std + model.data_mean
            code_parts.append(codes.cpu().numpy().T.astype(np.uint8))
            decode_parts.append(decoded.cpu().numpy().astype(np.float32))
    unique_codes = np.concatenate(code_parts)
    decoded_residual = np.concatenate(decode_parts)
    reconstructed = unique_base + decoded_residual
    final_norms = np.linalg.norm(reconstructed, axis=1).astype(np.float32)
    position = {int(doc): index for index, doc in enumerate(unique_ids)}
    rows = []
    for qi, query in enumerate(np.asarray(queries, dtype=np.float32)):
        ids = selected[qi]
        indexes = np.asarray([position[int(doc)] for doc in ids], dtype=np.int64)
        rank = top_ids(cosine(reconstructed[indexes], query), ids)
        rows.append({"query": qi, "arm": "qinco2_16b_bounded", "top10_ids": rank.astype(int).tolist(), "thq4_top128_ids": ids.astype(int).tolist(), "qrels_ndcg10": ndcg10(rank, qids[qi], grades[qi]), "teacher_overlap": float(np.isin(teacher[qi], rank).sum() / 10.0), "side_payload_bytes": 20, "cascade_total_bytes": 116})

    a.model_artifact.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": model.state_dict(), "data_mean": torch.from_numpy(data_mean), "data_std": data_std, "config": {"D": D, "M": STAGES, "K": K, "L": 1, "de": 32, "dh": 64, "A": 8, "B": 4}}, a.model_artifact)
    np.savez_compressed(a.codes_artifact, selected_ids=selected, unique_ids=unique_ids, codes=unique_codes, centroids=centroids, final_norms=final_norms)
    summary = {"mean_qrels_ndcg10": float(np.mean([row["qrels_ndcg10"] for row in rows])), "p05_qrels_ndcg10": float(np.percentile([row["qrels_ndcg10"] for row in rows], 5)), "worst_qrels_ndcg10": float(np.min([row["qrels_ndcg10"] for row in rows])), "mean_teacher_overlap": float(np.mean([row["teacher_overlap"] for row in rows])), "side_payload_bytes": 20, "cascade_total_bytes": 116, "global_model_bytes": int(sum(value.numel() * value.element_size() for value in model.state_dict().values()))}
    sources = {name: getattr(a, name.replace("-", "_")) for name in ("documents", "train-vectors", "queries", "qrel-ids", "qrel-scores", "teacher-ids", "thq4-codes", "thq4-thresholds", "lsq-models", "lsq-codes")}
    result = {"schema_version": 1, "family": "thq_qinco2_16b_bounded_pilot_v1", "status": "EXECUTED", "source_replay": True, "quality_status": "BOUNDED_UNDERTRAINED_PILOT", "metric": "cosine", "upstream_repository": "https://github.com/facebookresearch/Qinco", "upstream_revision": revision, "upstream_license": "CC-BY-NC", "payload_contract": {"final_norm_included": True, "side_payload_bytes": 20, "fields": ["16-byte QINCo2 code", "FP32 final norm"]}, "candidate_stream_hash": "d76cabd553bbd1453908a9cd28fe3578895cf2cd3876026a5b1fd5813839bc79", "config": {"stages": STAGES, "codebook_size": K, "code_bytes": 16, "final_norm_bytes": 4, "train_rows": a.train_rows, "epochs": a.epochs, "batch_size": a.batch_size, "learning_rate": a.learning_rate, "seed": a.seed, "hidden_dim": 64, "embedding_dim": 32, "substep_candidates": 8, "beam": 4, "initialization": "first 16 train-fitted LSQ32 codebooks"}, "training": {"optimizer_steps": len(losses), "initial_total_loss": losses[0], "final_total_loss": losses[-1]}, "source_hashes": {name: sha256(path) for name, path in sources.items()}, "runner_sha256": sha256(Path(__file__)), "model_artifact_sha256": sha256(a.model_artifact), "codes_artifact_sha256": sha256(a.codes_artifact), "summaries": {"qinco2_16b_bounded": summary}, "rows": rows, "limitations": ["bounded CPU fit on the first canonical training rows, not a converged QINCo2 training schedule", "LSQ32 initialization gives a stronger start but is not the official RQ initialization pipeline", "candidate-local quality only; no native timing", "FP32 final norm charged in the 20-byte side payload", "external CC-BY-NC source is not vendored", "this pilot cannot support a family-level negative conclusion if undertraining remains material"]}
    a.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
