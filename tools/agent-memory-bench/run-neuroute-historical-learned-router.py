#!/usr/bin/env python3
"""Reproduce the supervised direct semantic-address router from PR #176.

This runner is intentionally separate from the PCA ordinal control in #299.
It trains a fixed-final-epoch 384->128->16 query MLP against exact E5 top-10
document-address targets, then uses confidence/logit-margin probing and
document replication.  It is a routing-ceiling replay; no downstream codec is
used to select the model.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def top(scores: np.ndarray, count: int) -> np.ndarray:
    count = min(int(count), len(scores))
    ids = np.argpartition(-scores, count - 1)[:count]
    return ids[np.lexsort((ids, -scores[ids]))]


def gelu(value: np.ndarray) -> np.ndarray:
    return 0.5 * value * (1.0 + np.tanh(math.sqrt(2.0 / math.pi) *
                                      (value + 0.044715 * value ** 3)))


def train_mlp(queries: np.ndarray, targets: np.ndarray, seed: int,
              epochs: int = 160) -> dict[str, np.ndarray]:
    try:
        import torch
    except ImportError as error:
        raise ValueError("PyTorch is required for historical router replay") from error
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(min(8, torch.get_num_threads()))
    mean = queries.mean(axis=0, dtype=np.float64).astype(np.float32)
    scale = queries.std(axis=0, dtype=np.float64).astype(np.float32)
    scale[scale < 1.0e-6] = 1.0
    features = torch.from_numpy(((queries - mean) / scale).astype(np.float32))
    labels = torch.from_numpy(targets.astype(np.float32))
    model = torch.nn.Sequential(torch.nn.Linear(384, 128),
                                torch.nn.GELU(approximate="tanh"),
                                torch.nn.Linear(128, 16))
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.003,
                                  weight_decay=0.0001)
    generator = torch.Generator().manual_seed(seed + 1)
    for _ in range(epochs):
        order = torch.randperm(len(features), generator=generator)
        for start in range(0, len(features), 64):
            selected = order[start:start + 64]
            optimizer.zero_grad(set_to_none=True)
            loss = torch.nn.functional.binary_cross_entropy_with_logits(
                model(features[selected]), labels[selected])
            loss.backward()
            optimizer.step()
    first, second = model[0], model[2]
    return {"query_mean": mean, "query_scale": scale,
            "weight1": first.weight.detach().numpy().astype(np.float32),
            "bias1": first.bias.detach().numpy().astype(np.float32),
            "weight2": second.weight.detach().numpy().astype(np.float32),
            "bias2": second.bias.detach().numpy().astype(np.float32)}


def infer(queries: np.ndarray, artifact: dict[str, np.ndarray]) -> np.ndarray:
    normalized = (queries - artifact["query_mean"]) / artifact["query_scale"]
    hidden = gelu(normalized @ artifact["weight1"].T + artifact["bias1"])
    return (hidden @ artifact["weight2"].T + artifact["bias2"]).astype(np.float32)


def code_values(logits: np.ndarray, width: int) -> np.ndarray:
    powers = np.left_shift(np.uint32(1), np.arange(width, dtype=np.uint32))
    return ((logits[:, :width] >= 0.0).astype(np.uint32) * powers).sum(axis=1,
                                                                        dtype=np.uint32)


def confidence_addresses(logits: np.ndarray, width: int, count: int) -> list[int]:
    base = int(code_values(logits[None, :], width)[0])
    margins = np.abs(logits[:width])
    masks: list[tuple[float, int]] = [(0.0, 0)]
    for flip_count in range(1, min(3, width) + 1):
        for bits in itertools.combinations(range(width), flip_count):
            mask = sum(1 << bit for bit in bits)
            masks.append((float(sum(float(margins[bit]) for bit in bits)), mask))
    masks.sort(key=lambda value: (value[0], value[1]))
    return [base ^ mask for _, mask in masks[:count]]


def document_addresses(logits: np.ndarray, width: int, replication: int) -> list[int]:
    base = int(code_values(logits[None, :], width)[0])
    result = [base]
    for bit in np.argsort(np.abs(logits[:width]), kind="stable")[:replication - 1]:
        result.append(base ^ (1 << int(bit)))
    return result


def build_index(document_logits: np.ndarray, replication: int) -> dict[int, np.ndarray]:
    lists: dict[int, list[int]] = {}
    for position, row in enumerate(document_logits):
        for address in document_addresses(row, 12, replication):
            lists.setdefault(address, []).append(position)
    return {address: np.asarray(values, dtype=np.int32)
            for address, values in lists.items()}


def self_test() -> None:
    logits = np.asarray([2.0, -0.1, 3.0] + [1.0] * 13,
                        dtype=np.float32)
    require(code_values(logits[None, :], 3).tolist() == [5],
            "historical router code primitive differs")
    addresses = confidence_addresses(logits, 3, 4)
    require(addresses[0] == 5 and len(addresses) == len(set(addresses)) == 4,
            "historical router confidence probing differs")
    document = document_addresses(logits, 3, 3)
    require(document[0] == 5 and len(document) == 3,
            "historical router document replication differs")
    print("historical learned router self-test passed")


def qrels_ndcg(selected: np.ndarray, ids: np.ndarray, scores: np.ndarray) -> float:
    valid = ids >= 0
    relevance = {int(doc): float(score) for doc, score in zip(ids[valid], scores[valid])}
    gains = np.asarray([2.0 ** relevance.get(int(doc), 0.0) - 1.0
                        for doc in selected[:10]], dtype=np.float64)
    ideal = np.sort(scores[valid].astype(np.float64))[::-1][:10]
    ideal_gains = 2.0 ** ideal - 1.0
    discount = 1.0 / np.log2(np.arange(2, len(gains) + 2))
    ideal_discount = 1.0 / np.log2(np.arange(2, len(ideal_gains) + 2))
    denominator = float(np.sum(ideal_gains * ideal_discount))
    return float(np.sum(gains * discount) / denominator) if denominator else 0.0


def load_cache(path: Path) -> dict[str, np.ndarray]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent
    result = {name: np.load(root / row["path"], mmap_mode="r",
                            allow_pickle=False)
              for name, row in manifest["outputs"].items()}
    result["documents"] = np.memmap(Path(manifest["source"]["document_vectors"]),
        mode="r", dtype="<f4", shape=(int(manifest["source"]["document_count"]),
                                       int(manifest["source"]["dimension"])))
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    data = load_cache(args.cache)
    documents = np.asarray(data["documents"], dtype=np.float32)
    # Historical #176 document head: deterministic strided document sample
    # (the source used documents[::4]), SVD PCA, then per-axis medians.  The
    # cache's random train-vector sample is retained for provenance only; it
    # must not silently redefine the historical learned-router substrate.
    document_sample = documents[::4]
    mean = document_sample.mean(axis=0, dtype=np.float64).astype(np.float32)
    centered = np.asarray(document_sample - mean, dtype=np.float64)
    _, _, right = np.linalg.svd(centered, full_matrices=False)
    projection = right[:16].T.astype(np.float32)
    document_logits = (documents - mean) @ projection
    threshold = np.median((document_sample - mean) @ projection,
                          axis=0).astype(np.float32)
    document_logits = (document_logits - threshold).astype(np.float32)
    train_teacher = np.asarray(data["train_teacher_ids"], dtype=np.int64)
    target_probabilities = (document_logits[train_teacher] >= 0.0).mean(axis=1)
    model = train_mlp(np.asarray(data["train_queries"], dtype=np.float32),
                      target_probabilities, args.seed, args.epochs)
    query_logits = infer(np.asarray(data["eval_queries"], dtype=np.float32), model)
    rows: list[dict[str, Any]] = []
    partitions = np.asarray(data["eval_partition"])
    for replication in args.replication:
        postings = build_index(document_logits, replication)
        posting_entries = sum(len(values) for values in postings.values())
        for query_index, query in enumerate(np.asarray(data["eval_queries"], dtype=np.float32)):
            for probes in args.probes:
                started = time.perf_counter()
                addresses = confidence_addresses(query_logits[query_index], 12, probes)
                parts = [postings[address] for address in addresses if address in postings]
                raw = sum(len(part) for part in parts)
                candidates = np.unique(np.concatenate(parts)) if parts else np.empty(0, dtype=np.int32)
                scores = documents[candidates] @ query if len(candidates) else np.empty(0, dtype=np.float32)
                order = top(scores, args.document_budget) if len(scores) else np.empty(0, dtype=np.int64)
                selected = candidates[order]
                target = np.asarray(data["eval_teacher_ids"][query_index], dtype=np.int64)
                overlap = float(len(set(map(int, selected[:10])) & set(map(int, target)))) / 10.0
                qrel = qrels_ndcg(selected, np.asarray(data["eval_qrel_ids"][query_index]), np.asarray(data["eval_qrel_scores"][query_index]))
                rows.append({"router": "historical_learned12", "replication": replication,
                    "partition": str(partitions[query_index]), "query": query_index,
                    "probes": probes, "document_budget": args.document_budget,
                    "overlap": overlap, "qrels_ndcg": qrel,
                    "raw_postings": raw, "unique_candidates": int(len(candidates)),
                    "route_ms": (time.perf_counter() - started) * 1000.0,
                    "payload_bytes": 2, "model_bytes": int(sum(value.nbytes for value in model.values())),
                    "posting_entries": posting_entries})
    summaries = []
    for key in sorted({(r["replication"], r["partition"], r["probes"]) for r in rows}):
        current = [r for r in rows if (r["replication"], r["partition"], r["probes"]) == key]
        summaries.append({"router": "historical_learned12", "replication": key[0],
            "partition": key[1], "probes": key[2], "query_count": len(current),
            "mean_overlap": float(np.mean([r["overlap"] for r in current])),
            "p05_overlap": float(np.quantile([r["overlap"] for r in current], .05)),
            "worst_overlap": float(np.min([r["overlap"] for r in current])),
            "mean_qrels_ndcg": float(np.mean([r["qrels_ndcg"] for r in current])),
            "worst_qrels_ndcg": float(np.min([r["qrels_ndcg"] for r in current])),
            "mean_raw_postings": float(np.mean([r["raw_postings"] for r in current])),
            "mean_unique_candidates": float(np.mean([r["unique_candidates"] for r in current])),
            "p95_route_ms": float(np.quantile([r["route_ms"] for r in current], .95)),
            "payload_bytes": 2, "model_bytes": current[0]["model_bytes"],
            "posting_entries": current[0]["posting_entries"]})
    return {"schema_version": 1, "family": "neuroute_historical_learned_router_replay",
            "source_pr": {"training": 176, "scale_transfer": 199},
            "document_head": {"kind": "pca_svd_strided_documents", "stride": 4,
                              "threshold": "median"},
            "training": {"architecture": "mlp_384_128_16", "epochs": args.epochs,
                         "target": "mean_document_address_bit_probability_exact_e5_top10",
                         "seed": args.seed, "labels_used": True},
            "rows": rows, "summaries": summaries,
            "limitations": ["original PR-176 frozen checkpoint and serving fixture are unavailable; this is deterministic retraining", "routing ceiling only; no downstream codec" ]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--probes", default="32,64,128,256")
    parser.add_argument("--replication", default="1,2,3,4")
    parser.add_argument("--document-budget", type=int, default=1024)
    parser.add_argument("--epochs", type=int, default=160)
    parser.add_argument("--seed", type=int, default=20260825)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.cache is None or args.output is None:
        parser.error("--cache and --output are required unless --self-test is used")
    args.probes = [int(x) for x in args.probes.split(",")]
    args.replication = [int(x) for x in args.replication.split(",")]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(run(args), indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
