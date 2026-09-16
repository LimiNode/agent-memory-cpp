#!/usr/bin/env python3
"""Held-out query retrieval-loss decoder probe for the THQ4 final-rerank stage."""
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


def feature_tensor(levels: np.ndarray) -> torch.Tensor:
    return torch.nn.functional.one_hot(torch.from_numpy(levels.astype(np.int64)), num_classes=4).reshape(len(levels), -1).float()


class Decoder(nn.Module):
    def __init__(self, hidden: int) -> None:
        super().__init__()
        self.layers = nn.Sequential(nn.Linear(D * 4, hidden), nn.ReLU(), nn.Linear(hidden, D))

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return self.layers(features)


def self_test() -> None:
    levels = np.zeros((2, D), dtype=np.uint8)
    levels[0, :3] = [0, 1, 2]
    levels[1, :3] = [3, 2, 1]
    model = Decoder(8)
    result = model(feature_tensor(levels))
    if result.shape != (2, D):
        raise RuntimeError("decoder shape differs")
    print("THQ retrieval distillation stage-local self-test PASS")


def norm_rank(values: np.ndarray, query: np.ndarray, ids: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(values, axis=1).astype(np.float32)
    return h.top_k((values @ query) / np.maximum(norms, np.finfo(np.float32).tiny), ids)


def parse_pairs(path: Path, id_to_index: dict[str, int], query_start: int,
                query_ids: list[str], rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, int]:
    query_order = {value: i for i, value in enumerate(query_ids)}
    positives: dict[int, list[int]] = {}
    negatives: dict[int, list[int]] = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            fields = line.split()
            if len(fields) < 4 or fields[0] not in query_order or query_order[fields[0]] < query_start:
                continue
            index = id_to_index.get(fields[2])
            if index is None:
                continue
            bucket = positives if int(fields[3]) > 0 else negatives
            bucket.setdefault(query_order[fields[0]], []).append(index)
    pos_rows: list[tuple[int, int]] = []
    neg_rows: list[tuple[int, int]] = []
    for qi in sorted(positives):
        if qi not in negatives:
            continue
        pos = positives[qi]
        neg = negatives[qi]
        for positive in pos:
            chosen = rng.choice(neg, size=min(8, len(neg)), replace=False)
            for negative in chosen:
                pos_rows.append((qi, positive)); neg_rows.append((qi, int(negative)))
    return np.asarray(pos_rows, dtype=np.int64), np.asarray(neg_rows, dtype=np.int64), len(positives)


def main() -> None:
    if "--self-test" in __import__("sys").argv[1:]:
        self_test()
        return
    parser = argparse.ArgumentParser()
    for name in ("documents", "queries", "query_ids", "document_ids", "qrels", "thq4_codes", "thq4_thresholds"):
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)
    parser.add_argument("--query-count", type=int, default=8)
    parser.add_argument("--train-query-start", type=int, default=8)
    parser.add_argument("--hidden", type=int, default=128)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    count = args.documents.stat().st_size // (4 * D)
    query_total = args.queries.stat().st_size // (4 * D)
    query_count = min(args.query_count, query_total)
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(count, D))
    queries_all = np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_total, D))
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(count, 96))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    all_query_ids = h.load_ids(args.query_ids)
    query_ids = all_query_ids[:query_count]
    document_ids = h.load_ids(args.document_ids)
    id_to_index = {value: i for i, value in enumerate(document_ids)}
    grades = h.load_qrels(args.qrels, id_to_index, query_ids)

    rng = np.random.default_rng(20260916)
    positive_rows, negative_rows, train_queries = parse_pairs(
        args.qrels, id_to_index, args.train_query_start, all_query_ids, rng)
    if len(positive_rows) == 0:
        raise RuntimeError("no retrieval training pairs were found")
    pair_queries = torch.from_numpy(np.asarray(queries_all[positive_rows[:, 0]], dtype=np.float32))
    levels_pos = h.unpack_thq(np.asarray(thq_codes[positive_rows[:, 1]]))
    levels_neg = h.unpack_thq(np.asarray(thq_codes[negative_rows[:, 1]]))
    source_pos = torch.from_numpy(np.asarray(documents[positive_rows[:, 1]], dtype=np.float32))
    source_neg = torch.from_numpy(np.asarray(documents[negative_rows[:, 1]], dtype=np.float32))
    features_pos = feature_tensor(levels_pos)
    features_neg = feature_tensor(levels_neg)

    torch.manual_seed(20260916)
    model = Decoder(args.hidden)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_history: list[float] = []
    order = np.arange(len(positive_rows))
    for _ in range(args.epochs):
        rng.shuffle(order)
        epoch_loss = 0.0
        for start in range(0, len(order), args.batch_size):
            batch = torch.from_numpy(order[start:start + args.batch_size])
            q = pair_queries[batch]
            pred_pos = model(features_pos[batch])
            pred_neg = model(features_neg[batch])
            pos_score = torch.sum(torch.nn.functional.normalize(pred_pos, dim=1) * q, dim=1)
            neg_score = torch.sum(torch.nn.functional.normalize(pred_neg, dim=1) * q, dim=1)
            margin = torch.relu(0.1 - pos_score + neg_score).mean()
            mse = 0.01 * (torch.mean((pred_pos - source_pos[batch]) ** 2) +
                          torch.mean((pred_neg - source_neg[batch]) ** 2))
            loss = margin + mse
            optimizer.zero_grad(); loss.backward(); optimizer.step()
            epoch_loss += float(loss.detach()) * len(batch)
        loss_history.append(epoch_loss / len(order))

    rows = []
    ids = np.arange(count, dtype=np.int64)
    for qi in range(query_count):
        query = np.asarray(queries_all[qi], dtype=np.float32)
        exact_scores = np.empty(count, dtype=np.float32)
        interval_scores = np.empty(count, dtype=np.float32)
        lut = np.empty((D, 4), dtype=np.float32)
        for coordinate in range(D):
            for level in range(4):
                low = -np.inf if level == 0 else thresholds[coordinate, level - 1]
                high = np.inf if level == 3 else thresholds[coordinate, level]
                delta = low - query[coordinate] if query[coordinate] < low else (
                    query[coordinate] - high if query[coordinate] > high else 0.0)
                lut[coordinate, level] = delta * delta
        for start in range(0, count, 16384):
            stop = min(start + 16384, count)
            exact_scores[start:stop] = np.asarray(documents[start:stop]) @ query
            levels = h.unpack_thq(np.asarray(thq_codes[start:stop]))
            interval_scores[start:stop] = np.sum(lut[np.arange(D)[None, :], levels], axis=1)
        teacher = h.top_k(exact_scores, ids)
        candidate = h.top_k(interval_scores, ids, TOP, ascending=True)
        candidate_exact = h.top_k(exact_scores[candidate], candidate)
        levels = h.unpack_thq(np.asarray(thq_codes[candidate]))
        with torch.no_grad():
            decoded = model(feature_tensor(levels)).numpy()
        # Keep an exact source-vector control beside the learned decoder.  It
        # is an upper bound for the fixed candidate set, not a deployable arm.
        base = np.asarray(documents[candidate], dtype=np.float32)
        for arm, values in (("retrieval-decoder", decoded), ("source-control", base)):
            selected = norm_rank(values, query, candidate)
            rows.append({"query": qi, "query_id": query_ids[qi], "arm": arm,
                         "teacher_overlap": float(np.isin(teacher, selected).sum() / 10.0),
                         "candidate_fp32_overlap": float(np.isin(candidate_exact, selected).sum() / 10.0),
                         "qrels_ndcg10": h.ndcg(selected, grades[qi]), "top10": selected.astype(int).tolist()})
    summaries = {}
    for arm in ("retrieval-decoder", "source-control"):
        values = [row["teacher_overlap"] for row in rows if row["arm"] == arm]
        summaries[arm] = {"teacher_overlap_mean": float(np.mean(values)),
                          "teacher_overlap_min": float(np.min(values)),
                          "qrels_ndcg10_mean": float(np.mean([row["qrels_ndcg10"] for row in rows if row["arm"] == arm]))}
    result = {"schema_version": 1, "family": "thq_retrieval_distill_stage_local_v1",
              "status": "EXECUTED", "evidence_status": "heldout_query_retrieval_loss_probe",
              "documents": count, "query_count": query_count, "training_query_start": args.train_query_start,
              "training_queries_with_pairs": train_queries, "pair_count": int(len(positive_rows)),
              "hidden": args.hidden, "epochs": args.epochs, "seed": 20260916,
              "prefilter": "full_corpus_thq4_interval_squared_top128",
              "documents_sha256": sha256(args.documents), "queries_sha256": sha256(args.queries),
              "query_ids_sha256": sha256(args.query_ids), "document_ids_sha256": sha256(args.document_ids),
              "qrels_sha256": sha256(args.qrels), "thq_sha256": sha256(args.thq4_codes),
              "loss_history": loss_history, "summaries": summaries, "rows": rows,
              "limitations": ["eight held-out queries", "qrels-trained decoder is a research probe",
                              "full-corpus THQ4 top128 is not the canonical R4 stream", "not native/page/MDBX latency"]}
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
