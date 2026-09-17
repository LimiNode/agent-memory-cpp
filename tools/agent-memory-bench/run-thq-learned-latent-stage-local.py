#!/usr/bin/env python3
"""Bounded conditional learned-latent residual screen after a THQ4 prefilter."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import torch
from torch import nn

D = 384
TOP = 128
LATENT_BYTES = (8, 16, 32)


def load_helpers():
    path = Path(__file__).with_name("run-thq4-residual-frontier.py")
    spec = importlib.util.spec_from_file_location("thq4_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load THQ4 helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


h = load_helpers()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def features(levels: np.ndarray) -> torch.Tensor:
    return torch.nn.functional.one_hot(
        torch.from_numpy(levels.astype(np.int64)), num_classes=4).reshape(len(levels), -1).float()


class ConditionalAutoencoder(nn.Module):
    def __init__(self, latent: int, hidden: int) -> None:
        super().__init__()
        self.encoder = nn.Sequential(nn.Linear(D + D * 4, hidden), nn.ReLU(), nn.Linear(hidden, latent))
        self.decoder = nn.Sequential(nn.Linear(D * 4 + latent, hidden), nn.ReLU(), nn.Linear(hidden, D))

    def encode(self, residual: torch.Tensor, code: torch.Tensor) -> torch.Tensor:
        return self.encoder(torch.cat((residual, code), dim=1))

    def decode(self, latent: torch.Tensor, code: torch.Tensor) -> torch.Tensor:
        return self.decoder(torch.cat((code, latent), dim=1))


def self_test() -> None:
    model = ConditionalAutoencoder(32, 16)
    residual = torch.zeros((2, D))
    code = torch.zeros((2, D * 4))
    if model.decode(model.encode(residual, code), code).shape != (2, D):
        raise RuntimeError("conditional autoencoder shape differs")
    print("THQ learned latent stage-local self-test PASS")


def quantize_latent(values: np.ndarray, low: np.ndarray, high: np.ndarray) -> np.ndarray:
    scale = np.maximum(high - low, 1e-8)
    return np.clip(np.rint((values - low) * 255.0 / scale), 0, 255).astype(np.uint8)


def dequantize_latent(codes: np.ndarray, low: np.ndarray, high: np.ndarray) -> np.ndarray:
    scale = np.maximum(high - low, 1e-8)
    return (low + codes.astype(np.float32) * scale / 255.0).astype(np.float32)


def main() -> None:
    if "--self-test" in __import__("sys").argv[1:]:
        self_test()
        return
    parser = argparse.ArgumentParser()
    for name in ("documents", "train_vectors", "queries", "query_ids", "document_ids", "qrels",
                 "thq4_codes", "thq4_thresholds"):
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)
    parser.add_argument("--query-count", type=int, default=8)
    parser.add_argument("--hidden", type=int, default=256)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    count = args.documents.stat().st_size // (4 * D)
    train_count = args.train_vectors.stat().st_size // (4 * D)
    query_total = args.queries.stat().st_size // (4 * D)
    query_count = min(args.query_count, query_total)
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(count, D))
    training = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(train_count, D)), dtype=np.float32)
    queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_total, D))[:query_count]
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(count, 96))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    query_ids = h.load_ids(args.query_ids)[:query_count]
    document_ids = h.load_ids(args.document_ids)
    grades = h.load_qrels(args.qrels, {value: i for i, value in enumerate(document_ids)}, query_ids)

    train_levels = h.unpack_thq(h.pack_thq(training, thresholds))
    centroids = h.fit_centroids(training, thresholds)
    train_base = h.reconstruct(h.pack_thq(training, thresholds), centroids)
    train_residual = training - train_base
    code_train = features(train_levels)
    residual_tensor = torch.from_numpy(train_residual)
    code_tensor = code_train

    torch.manual_seed(20260917)
    model = ConditionalAutoencoder(max(LATENT_BYTES), args.hidden)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    order = np.arange(train_count)
    loss_history: list[float] = []
    rng = np.random.default_rng(20260917)
    for _ in range(args.epochs):
        rng.shuffle(order)
        epoch_loss = 0.0
        for start in range(0, train_count, args.batch_size):
            batch = torch.from_numpy(order[start:start + args.batch_size])
            residual = residual_tensor[batch]
            code = code_tensor[batch]
            latent = model.encode(residual, code)
            # Prefix dropout trains one shared model for all advertised rates.
            width = int(rng.choice(LATENT_BYTES))
            masked = latent.clone()
            masked[:, width:] = 0.0
            prediction = model.decode(masked, code)
            loss = torch.mean((prediction - residual) ** 2)
            optimizer.zero_grad(); loss.backward(); optimizer.step()
            epoch_loss += float(loss.detach()) * len(batch)
        loss_history.append(epoch_loss / train_count)

    with torch.no_grad():
        train_latent = model.encode(residual_tensor, code_tensor).numpy().astype(np.float32)
    low = train_latent.min(axis=0)
    high = train_latent.max(axis=0)
    train_codes = quantize_latent(train_latent, low, high)

    rows = []
    ids = np.arange(count, dtype=np.int64)
    for qi, query_value in enumerate(queries):
        query = np.asarray(query_value, dtype=np.float32)
        exact_scores = np.empty(count, dtype=np.float32)
        interval_scores = np.empty(count, dtype=np.float32)
        lut = np.empty((D, 4), dtype=np.float32)
        for coordinate in range(D):
            for level in range(4):
                low_bound = -np.inf if level == 0 else thresholds[coordinate, level - 1]
                high_bound = np.inf if level == 3 else thresholds[coordinate, level]
                delta = low_bound - query[coordinate] if query[coordinate] < low_bound else (
                    query[coordinate] - high_bound if query[coordinate] > high_bound else 0.0)
                lut[coordinate, level] = delta * delta
        for start in range(0, count, 16384):
            stop = min(start + 16384, count)
            exact_scores[start:stop] = np.asarray(documents[start:stop]) @ query
            levels = h.unpack_thq(np.asarray(thq_codes[start:stop]))
            interval_scores[start:stop] = np.sum(lut[np.arange(D)[None, :], levels], axis=1)
        teacher = h.top_k(exact_scores, ids)
        candidate = h.top_k(interval_scores, ids, TOP, ascending=True)
        candidate_exact = h.top_k(exact_scores[candidate], candidate)
        candidate_levels = h.unpack_thq(np.asarray(thq_codes[candidate]))
        candidate_base = centroids[np.arange(D)[None, :], candidate_levels]
        candidate_residual = np.asarray(documents[candidate], dtype=np.float32) - candidate_base
        code_candidate = features(candidate_levels)
        with torch.no_grad():
            latent = model.encode(torch.from_numpy(candidate_residual), code_candidate).numpy().astype(np.float32)
        for width in LATENT_BYTES:
            quantized = quantize_latent(latent[:, :width], low[:width], high[:width])
            decoded_input = np.zeros((TOP, max(LATENT_BYTES)), dtype=np.float32)
            decoded_input[:, :width] = dequantize_latent(quantized, low[:width], high[:width])
            with torch.no_grad():
                decoded_residual = model.decode(torch.from_numpy(decoded_input), code_candidate).numpy()
            values = candidate_base + decoded_residual
            norms = np.linalg.norm(values, axis=1)
            selected = h.top_k((values @ query) / np.maximum(norms, np.finfo(np.float32).tiny), candidate)
            rows.append({"query": qi, "query_id": query_ids[qi], "arm": f"learned-latent-{width}B",
                         "bytes": 96 + width, "teacher_overlap": float(np.isin(teacher, selected).sum() / 10.0),
                         "candidate_fp32_overlap": float(np.isin(candidate_exact, selected).sum() / 10.0),
                         "qrels_ndcg10": h.ndcg(selected, grades[qi]), "top10": selected.astype(int).tolist()})
    summaries = {}
    for width in LATENT_BYTES:
        values = [row for row in rows if row["arm"] == f"learned-latent-{width}B"]
        summaries[f"learned-latent-{width}B"] = {
            "teacher_overlap_mean": float(np.mean([row["teacher_overlap"] for row in values])),
            "teacher_overlap_min": float(np.min([row["teacher_overlap"] for row in values])),
            "qrels_ndcg10_mean": float(np.mean([row["qrels_ndcg10"] for row in values])),
        }
    result = {"schema_version": 1, "family": "thq_learned_latent_stage_local_v1",
              "status": "EXECUTED", "evidence_status": "eight_query_numpy_stage_local_screen",
              "documents": count, "training_count": train_count, "query_count": query_count,
              "hidden": args.hidden, "epochs": args.epochs, "seed": 20260917,
              "prefilter": "full_corpus_thq4_interval_squared_top128", "latent_bytes": list(LATENT_BYTES),
              "loss_history": loss_history, "training_loss_final": loss_history[-1],
              "latent_low_sha256": hashlib.sha256(low.astype("<f4").tobytes()).hexdigest(),
              "latent_high_sha256": hashlib.sha256(high.astype("<f4").tobytes()).hexdigest(),
              "documents_sha256": sha256(args.documents), "training_sha256": sha256(args.train_vectors),
              "queries_sha256": sha256(args.queries), "query_ids_sha256": sha256(args.query_ids),
              "document_ids_sha256": sha256(args.document_ids), "qrels_sha256": sha256(args.qrels),
              "thq_sha256": sha256(args.thq4_codes), "summaries": summaries, "rows": rows,
              "limitations": ["diagnostic eight-query screen", "single conditional autoencoder with prefix dropout",
                              "not QINCo/AQ reproduction", "not canonical 152-query payload", "not native/page/MDBX latency"]}
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
