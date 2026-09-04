#!/usr/bin/env python3
"""Evaluate corpus-specific asymmetric binary codes for a frozen K8 map.

This is an offline research harness. Prototype codes are learned as an index
artifact; only the query encoder is executed at request time. The harness
never claims that exhaustive prototype Hamming scanning is a production
selector.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) +
            "\n").encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_data(input_path: Path, teacher_path: Path) -> tuple[np.ndarray, np.ndarray,
                                                               np.ndarray]:
    with np.load(input_path, mmap_mode="r", allow_pickle=False) as source:
        require({"queries", "prototype_vectors"}.issubset(source.files),
                "input lacks queries/prototype_vectors")
        queries = np.asarray(source["queries"], dtype=np.float32)
        prototypes = np.asarray(source["prototype_vectors"], dtype=np.float32)
    with np.load(teacher_path, mmap_mode="r", allow_pickle=False) as cache:
        require("teacher_top_prototypes" in cache.files,
                "teacher cache lacks teacher_top_prototypes")
        teacher = np.asarray(cache["teacher_top_prototypes"], dtype=np.int32)
    require(queries.shape == (8141, 384), "expected frozen 8,141 x 384 queries")
    require(prototypes.ndim == 2 and prototypes.shape[1] == 384,
            "prototype geometry must be N x 384")
    require(teacher.shape == (len(queries), 1024), "teacher shape differs")
    require(np.all((teacher >= 0) & (teacher < len(prototypes))),
            "teacher prototype id out of range")
    return queries, prototypes, teacher


def packed_popcount(codes: np.ndarray, query: np.ndarray) -> np.ndarray:
    lookup = np.asarray([int(v).bit_count() for v in range(256)], dtype=np.uint8)
    return lookup[np.bitwise_xor(codes, query[None, :])].sum(axis=1, dtype=np.uint16)


def top_indices(distances: np.ndarray, count: int) -> np.ndarray:
    require(0 < count <= len(distances), "top-k count is invalid")
    if count == len(distances):
        selected = np.arange(len(distances), dtype=np.int64)
        return selected[np.lexsort((selected, distances))]
    threshold = np.partition(distances, count - 1)[count - 1]
    lower = np.flatnonzero(distances < threshold)
    boundary = np.flatnonzero(distances == threshold)[:count - len(lower)]
    selected = np.concatenate((lower, boundary))
    return selected[np.lexsort((selected, distances[selected]))]


def code_entropy(codes: np.ndarray, width: int) -> float:
    bits = np.unpackbits(codes, axis=1, bitorder="little")[:, :width]
    p = np.clip(np.mean(bits, axis=0), 1.0e-12, 1.0 - 1.0e-12)
    return float(np.mean(-(p * np.log2(p) + (1.0 - p) * np.log2(1.0 - p))))


def build_pairs(teacher: np.ndarray, train_count: int, positives: list[int],
                negative_ranks: list[int], random_negatives: np.ndarray,
                hard_negatives: np.ndarray | None) -> tuple[np.ndarray, ...]:
    qids: list[int] = []
    pids: list[int] = []
    nids: list[int] = []
    ranks: list[int] = []
    for q in range(train_count):
        forbidden = set(map(int, teacher[q]))
        negatives = [int(v) for v in teacher[q, negative_ranks]]
        negatives.extend(map(int, random_negatives[q]))
        if hard_negatives is not None:
            negatives.extend(map(int, hard_negatives[q]))
        for rank in positives:
            positive = int(teacher[q, rank])
            for negative in negatives:
                if negative == positive:
                    continue
                qids.append(q)
                pids.append(positive)
                nids.append(negative)
                ranks.append(rank)
    return (np.asarray(qids, dtype=np.int64), np.asarray(pids, dtype=np.int64),
            np.asarray(nids, dtype=np.int64), np.asarray(ranks, dtype=np.int32))


def random_negatives(teacher: np.ndarray, prototype_count: int, count: int,
                     seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    result = np.empty((len(teacher) // 2, count), dtype=np.int32)
    for q in range(len(result)):
        forbidden = set(map(int, teacher[q]))
        values: list[int] = []
        while len(values) < count:
            candidate = int(rng.integers(0, prototype_count))
            if candidate not in forbidden and candidate not in values:
                values.append(candidate)
        result[q] = values
    return result


def encode_model(model: Any, values: np.ndarray, batch_size: int = 8192) -> np.ndarray:
    import torch
    outputs: list[np.ndarray] = []
    model.eval()
    with torch.no_grad():
        for first in range(0, len(values), batch_size):
            batch = torch.from_numpy(np.asarray(values[first:first + batch_size],
                                                 dtype=np.float32))
            logits = model(batch).cpu().numpy()
            outputs.append(logits)
    return np.concatenate(outputs, axis=0)


def train_asymmetric(queries: np.ndarray, prototypes: np.ndarray,
                     teacher: np.ndarray, width: int, seed: int,
                     epochs: int, positives: list[int],
                     negative_ranks: list[int], random_count: int,
                     hard_negatives: np.ndarray | None = None,
                     init_codes: np.ndarray | None = None) -> tuple[Any, Any, dict[str, Any]]:
    try:
        import torch
    except ImportError as error:
        raise ValueError("PyTorch is required for asymmetric research") from error
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(8)
    train_count = len(queries) // 2
    randoms = random_negatives(teacher, len(prototypes), random_count, seed ^ 0x5EED)
    qids, pids, nids, ranks = build_pairs(
        teacher, train_count, positives, negative_ranks, randoms, hard_negatives)
    model = torch.nn.Sequential(torch.nn.Linear(queries.shape[1], 96),
                                torch.nn.ReLU(), torch.nn.Linear(96, 64),
                                torch.nn.ReLU(), torch.nn.Linear(64, width))
    code_table = torch.nn.Embedding(len(prototypes), width, sparse=True)
    with torch.no_grad():
        if init_codes is None:
            torch.manual_seed(seed ^ 0xA5A5A5A5)
            code_table.weight.normal_(0.0, 0.35)
        else:
            require(init_codes.shape == (len(prototypes), width),
                    "initial code table shape differs")
            code_table.weight.copy_(torch.from_numpy(init_codes.astype(np.float32)))
    model_optimizer = torch.optim.AdamW(model.parameters(), lr=1.0e-3,
                                        weight_decay=1.0e-4)
    code_optimizer = torch.optim.SparseAdam(code_table.parameters(), lr=3.0e-3)
    generator = torch.Generator().manual_seed(seed ^ 0x9E3779B9)
    started = time.perf_counter()
    batch_size = 4096
    losses: list[float] = []
    for _ in range(epochs):
        order = torch.randperm(len(qids), generator=generator).numpy()
        total = 0.0
        for first in range(0, len(order), batch_size):
            chosen = order[first:first + batch_size]
            q = torch.from_numpy(np.asarray(queries[qids[chosen]], dtype=np.float32))
            p = torch.from_numpy(np.asarray(pids[chosen], dtype=np.int64))
            n = torch.from_numpy(np.asarray(nids[chosen], dtype=np.int64))
            logits = model(q)
            qbits = torch.sigmoid(2.0 * logits)
            pbits = torch.sigmoid(2.0 * code_table(p))
            nbits = torch.sigmoid(2.0 * code_table(n))
            dpos = (qbits + pbits - 2.0 * qbits * pbits).mean(dim=1)
            dneg = (qbits + nbits - 2.0 * qbits * nbits).mean(dim=1)
            rank_weight = torch.exp(-torch.from_numpy(
                ranks[chosen].astype(np.float32)) / 256.0)
            ranking = (rank_weight * torch.relu(0.06 + dpos - dneg)).mean()
            balance = (qbits.mean(dim=0) - 0.5).square().mean()
            centered = qbits - qbits.mean(dim=0, keepdim=True)
            covariance = centered.T @ centered / max(1, qbits.shape[0] - 1)
            covariance = covariance - torch.diag(torch.diag(covariance))
            objective = ranking + 0.02 * balance + 0.01 * covariance.square().mean()
            model_optimizer.zero_grad(set_to_none=True)
            code_optimizer.zero_grad()
            objective.backward()
            model_optimizer.step()
            code_optimizer.step()
            total += float(objective.detach()) * len(chosen)
        losses.append(total / len(order))
    metadata = {"pairs": len(qids), "epochs": epochs,
                "initial_loss": losses[0], "final_loss": losses[-1],
                "train_ms": (time.perf_counter() - started) * 1000.0,
                "positive_ranks": positives, "negative_ranks": negative_ranks,
                "random_negatives_per_query": random_count,
                "hard_negatives": hard_negatives is not None}
    return model, code_table, metadata


def train_asymmetric_listwise(
        queries: np.ndarray, prototypes: np.ndarray, teacher: np.ndarray,
        width: int, seed: int, epochs: int, positives: list[int],
        negative_ranks: list[int], random_count: int,
        hard_negatives: np.ndarray | None = None,
        init_codes: np.ndarray | None = None) -> tuple[Any, Any, dict[str, Any]]:
    """Train a shared query encoder and free prototype codebook listwise.

    The target distribution is rank-weighted over teacher positives rather than
    repeating independent pairwise constraints.  This is an address-utility
    proxy: it gives the codebook a smooth preference for high-utility teacher
    ranks, while remaining runnable on the 8,141-query prototype cache.  It is
    not a document-posting objective; that distinction is retained in the
    experiment note and must be checked by the address replay gate.
    """
    try:
        import torch
    except ImportError as error:
        raise ValueError("PyTorch is required for asymmetric research") from error
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(8)
    train_count = len(queries) // 2
    randoms = random_negatives(teacher, len(prototypes), random_count,
                               seed ^ 0x5EED)
    model = torch.nn.Sequential(torch.nn.Linear(queries.shape[1], 96),
                                torch.nn.ReLU(), torch.nn.Linear(96, 64),
                                torch.nn.ReLU(), torch.nn.Linear(64, width))
    code_table = torch.nn.Embedding(len(prototypes), width, sparse=True)
    with torch.no_grad():
        if init_codes is None:
            torch.manual_seed(seed ^ 0xA5A5A5A5)
            code_table.weight.normal_(0.0, 0.35)
        else:
            require(init_codes.shape == (len(prototypes), width),
                    "initial code table shape differs")
            code_table.weight.copy_(torch.from_numpy(init_codes.astype(np.float32)))
    model_optimizer = torch.optim.AdamW(model.parameters(), lr=1.0e-3,
                                        weight_decay=1.0e-4)
    code_optimizer = torch.optim.SparseAdam(code_table.parameters(), lr=3.0e-3)
    generator = torch.Generator().manual_seed(seed ^ 0x9E3779B9)
    started = time.perf_counter()
    batch_size = 128
    losses: list[float] = []
    positive_ranks = np.asarray(positives, dtype=np.int64)
    negative_ranks_array = np.asarray(negative_ranks, dtype=np.int64)
    rank_weights = np.exp(-positive_ranks.astype(np.float32) / 256.0)
    rank_weights /= rank_weights.sum()
    for _ in range(epochs):
        order = torch.randperm(train_count, generator=generator).numpy()
        total = 0.0
        for first in range(0, len(order), batch_size):
            qids = order[first:first + batch_size]
            q = torch.from_numpy(np.asarray(queries[qids], dtype=np.float32))
            positive_ids = teacher[qids[:, None], positive_ranks]
            negative_ids = teacher[qids[:, None], negative_ranks_array]
            negative_ids = np.concatenate((negative_ids, randoms[qids]), axis=1)
            if hard_negatives is not None:
                negative_ids = np.concatenate((negative_ids, hard_negatives[qids]),
                                              axis=1)
            candidate_ids = np.concatenate((positive_ids, negative_ids), axis=1)
            candidates = torch.from_numpy(candidate_ids.astype(np.int64))
            logits = model(q)
            qbits = torch.sigmoid(2.0 * logits)
            cbits = torch.sigmoid(2.0 * code_table(candidates))
            distances = (qbits[:, None, :] + cbits -
                         2.0 * qbits[:, None, :] * cbits).mean(dim=2)
            scores = -distances / 0.08
            target = torch.zeros_like(scores)
            target[:, :len(positives)] = torch.from_numpy(
                np.broadcast_to(rank_weights, (len(qids), len(positives))).copy())
            # Duplicate IDs can occur between fixed and random negatives.  They
            # are intentionally left in the candidate list: deterministic
            # retrieval uses prototype id tie-breaking, while the loss remains
            # conservative and does not silently drop a sampled item.
            objective = -(target * torch.log_softmax(scores, dim=1)).sum(dim=1).mean()
            balance = (qbits.mean(dim=0) - 0.5).square().mean()
            centered = qbits - qbits.mean(dim=0, keepdim=True)
            covariance = centered.T @ centered / max(1, qbits.shape[0] - 1)
            covariance = covariance - torch.diag(torch.diag(covariance))
            objective = objective + 0.02 * balance + 0.01 * covariance.square().mean()
            model_optimizer.zero_grad(set_to_none=True)
            code_optimizer.zero_grad()
            objective.backward()
            model_optimizer.step()
            code_optimizer.step()
            total += float(objective.detach()) * len(qids)
        losses.append(total / len(order))
    metadata = {"objective": "rank_weighted_listwise",
                "candidate_count": int(len(positives) + len(negative_ranks) +
                                       random_count +
                                       (0 if hard_negatives is None else hard_negatives.shape[1])),
                "epochs": epochs, "initial_loss": losses[0],
                "final_loss": losses[-1],
                "train_ms": (time.perf_counter() - started) * 1000.0,
                "positive_ranks": positives, "negative_ranks": negative_ranks,
                "random_negatives_per_query": random_count,
                "hard_negatives": hard_negatives is not None}
    return model, code_table, metadata


def train_lookup_ceiling(queries: np.ndarray, prototypes: np.ndarray,
                         teacher: np.ndarray, width: int, seed: int,
                         epochs: int, positives: list[int],
                         negative_ranks: list[int], random_count: int) -> tuple[Any, Any, dict[str, Any]]:
    """Train free train-query codes and free prototype codes.

    This is a representational upper bound only: held-out queries have no
    learned lookup code and are intentionally not scored by this mode.
    """
    try:
        import torch
    except ImportError as error:
        raise ValueError("PyTorch is required for asymmetric research") from error
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(8)
    train_count = len(queries) // 2
    randoms = random_negatives(teacher, len(prototypes), random_count, seed ^ 0x5EED)
    qids, pids, nids, ranks = build_pairs(
        teacher, train_count, positives, negative_ranks, randoms, None)
    query_table = torch.nn.Embedding(train_count, width, sparse=True)
    prototype_table = torch.nn.Embedding(len(prototypes), width, sparse=True)
    with torch.no_grad():
        query_table.weight.normal_(0.0, 0.35)
        prototype_table.weight.normal_(0.0, 0.35)
    query_optimizer = torch.optim.SparseAdam(query_table.parameters(), lr=3.0e-3)
    prototype_optimizer = torch.optim.SparseAdam(prototype_table.parameters(), lr=3.0e-3)
    generator = torch.Generator().manual_seed(seed ^ 0x9E3779B9)
    losses: list[float] = []
    started = time.perf_counter()
    for _ in range(epochs):
        order = torch.randperm(len(qids), generator=generator).numpy()
        total = 0.0
        for first in range(0, len(order), 4096):
            chosen = order[first:first + 4096]
            q = torch.from_numpy(qids[chosen])
            p = torch.from_numpy(pids[chosen])
            n = torch.from_numpy(nids[chosen])
            qbits = torch.sigmoid(2.0 * query_table(q))
            pbits = torch.sigmoid(2.0 * prototype_table(p))
            nbits = torch.sigmoid(2.0 * prototype_table(n))
            dpos = (qbits + pbits - 2.0 * qbits * pbits).mean(dim=1)
            dneg = (qbits + nbits - 2.0 * qbits * nbits).mean(dim=1)
            weights = torch.exp(-torch.from_numpy(ranks[chosen].astype(np.float32)) / 256.0)
            objective = (weights * torch.relu(0.06 + dpos - dneg)).mean()
            query_optimizer.zero_grad()
            prototype_optimizer.zero_grad()
            objective.backward()
            query_optimizer.step()
            prototype_optimizer.step()
            total += float(objective.detach()) * len(chosen)
        losses.append(total / len(order))
    metadata = {"pairs": len(qids), "epochs": epochs,
                "initial_loss": losses[0], "final_loss": losses[-1],
                "train_ms": (time.perf_counter() - started) * 1000.0,
                "positive_ranks": positives, "negative_ranks": negative_ranks,
                "random_negatives_per_query": random_count}
    return query_table, prototype_table, metadata


def projection_initialization(prototypes: np.ndarray, width: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    matrix = rng.normal(size=(prototypes.shape[1], width)).astype(np.float32)
    logits = np.asarray(prototypes, dtype=np.float32) @ matrix
    # finite logits avoid an initially saturated sigmoid while preserving the
    # deterministic shared E5-derived sign geometry.
    return np.clip(logits / np.maximum(np.std(logits, axis=0, keepdims=True), 1.0e-5),
                   -2.0, 2.0).astype(np.float32)


def evaluate_lookup(query_codes: np.ndarray, prototype_codes: np.ndarray,
                    teacher: np.ndarray, budgets: list[int]) -> dict[str, Any]:
    return {"configuration": evaluate_codes(
        np.concatenate((query_codes, query_codes), axis=0), prototype_codes,
        teacher[:len(query_codes) * 2], budgets)["configuration"],
            "internal": {"teacher_prototype_recall": {},
                         "note": "not applicable: free query codes have no held-out encoder"}}


def mine_hard_negatives(model: Any, code_table: Any, queries: np.ndarray,
                        teacher: np.ndarray, count: int) -> np.ndarray:
    import torch
    query_logits = encode_model(model, queries[:len(queries) // 2])
    query_codes = np.packbits((query_logits >= 0).astype(np.uint8), axis=1,
                              bitorder="little")
    with torch.no_grad():
        prototype_logits = code_table.weight.detach().cpu().numpy()
    prototype_codes = np.packbits((prototype_logits >= 0).astype(np.uint8), axis=1,
                                  bitorder="little")
    result = np.empty((len(query_codes), count), dtype=np.int32)
    for row, query_code in enumerate(query_codes):
        distances = packed_popcount(prototype_codes, query_code)
        order = top_indices(distances, min(len(distances), 1024 + count + 64))
        forbidden = set(map(int, teacher[row]))
        selected = [int(v) for v in order if int(v) not in forbidden][:count]
        result[row] = selected
    return result


def evaluate_codes(query_codes: np.ndarray, prototype_codes: np.ndarray,
                   teacher: np.ndarray, budgets: list[int]) -> dict[str, Any]:
    train_count = len(query_codes) // 2
    partitions: dict[str, Any] = {}
    for name, begin, end in (("configuration", 0, train_count),
                             ("internal", train_count, len(query_codes))):
        metrics: dict[int, list[float]] = {int(k): [] for k in budgets}
        worst: dict[int, float] = {int(k): 1.0 for k in budgets}
        for row in range(begin, end):
            order = top_indices(packed_popcount(prototype_codes, query_codes[row]),
                                max(budgets))
            target = set(map(int, teacher[row]))
            for budget in budgets:
                value = sum(int(v) in target for v in order[:budget]) / len(target)
                metrics[int(budget)].append(value)
                worst[int(budget)] = min(worst[int(budget)], value)
        partitions[name] = {"teacher_prototype_recall": {
            str(k): {"mean": float(np.mean(v)), "worst": worst[k],
                     "p05": float(np.quantile(v, 0.05))}
            for k, v in metrics.items()}}
    return partitions


def run(args: argparse.Namespace) -> dict[str, Any]:
    queries, prototypes, teacher = load_data(args.input, args.teacher_cache)
    widths = [int(v) for v in args.widths]
    seeds = [int(v) for v in args.seeds]
    train_count = len(queries) // 2
    positives = [int(v) for v in args.positive_ranks]
    negative_ranks = [int(v) for v in args.negative_ranks]
    reports: list[dict[str, Any]] = []
    for seed in seeds:
        for width in widths:
            if not args.ceiling_only:
                trainer = (train_asymmetric_listwise
                           if args.objective == "rank_weighted_listwise"
                           else train_asymmetric)
                model, table, training = trainer(
                    queries, prototypes, teacher, width, seed, args.epochs,
                    positives, negative_ranks, args.random_negatives)
                hard = (mine_hard_negatives(model, table, queries, teacher,
                                            args.hard_negatives)
                        if args.hard_negatives else None)
                if args.hard_negatives:
                    model, table, hard_training = trainer(
                        queries, prototypes, teacher, width, seed ^ 0x1234,
                        args.hard_epochs, positives, negative_ranks,
                        args.random_negatives, hard_negatives=hard,
                        init_codes=table.weight.detach().cpu().numpy())
                    training["hard_round"] = hard_training
                query_logits = encode_model(model, queries)
                import torch
                with torch.no_grad():
                    prototype_logits = table.weight.detach().cpu().numpy()
                query_codes = np.packbits((query_logits >= 0).astype(np.uint8),
                                          axis=1, bitorder="little")
                prototype_codes = np.packbits((prototype_logits >= 0).astype(np.uint8),
                                              axis=1, bitorder="little")
                result = {"seed": seed, "width": width,
                          "method": ("asymmetric_query_encoder_free_prototype_codes" if
                                      args.objective == "pairwise" else
                                      "asymmetric_query_encoder_free_prototype_codes_listwise"),
                          "code_bytes_per_prototype": (width + 7) // 8,
                          "prototype_code_bytes": int(prototype_logits.nbytes),
                          "mean_bit_entropy": code_entropy(prototype_codes, width),
                          "training": training,
                          "partitions": evaluate_codes(query_codes, prototype_codes,
                                                         teacher, args.budgets)}
                reports.append(result)
                if (args.artifact_output is not None and
                        args.artifact_width == width and args.artifact_seed == seed):
                    args.artifact_output.parent.mkdir(parents=True, exist_ok=True)
                    with args.artifact_output.open("wb") as stream:
                        np.savez(stream, query_codes=query_codes,
                                 prototype_codes=prototype_codes)
                    if args.model_output is not None:
                        import torch
                        args.model_output.parent.mkdir(parents=True, exist_ok=True)
                        torch.save(model.state_dict(), args.model_output)
            if args.include_ceiling or args.ceiling_only:
                ceiling_positives = np.linspace(
                    0, int(teacher.shape[1]) - 1,
                    min(args.ceiling_positive_count, teacher.shape[1]),
                    dtype=np.int32).tolist()
                lookup_q, lookup_p, lookup_training = train_lookup_ceiling(
                    queries, prototypes, teacher, width, seed ^ 0xC0DE,
                    args.ceiling_epochs, ceiling_positives, negative_ranks,
                    args.random_negatives)
                import torch
                with torch.no_grad():
                    lookup_q_logits = lookup_q.weight.detach().cpu().numpy()
                    lookup_p_logits = lookup_p.weight.detach().cpu().numpy()
                lookup_q_codes = np.packbits((lookup_q_logits >= 0).astype(np.uint8),
                                             axis=1, bitorder="little")
                lookup_p_codes = np.packbits((lookup_p_logits >= 0).astype(np.uint8),
                                             axis=1, bitorder="little")
                reports.append({"seed": seed, "width": width,
                                "method": "free_query_and_free_prototype_codes_ceiling",
                                "code_bytes_per_prototype": (width + 7) // 8,
                                "prototype_code_bytes": int(lookup_p_logits.nbytes),
                                "mean_bit_entropy": code_entropy(lookup_p_codes, width),
                                "training": lookup_training,
                                "partitions": evaluate_lookup(
                                    lookup_q_codes, lookup_p_codes, teacher,
                                    args.budgets)})
    return {"schema_version": 1,
            "family": "neuroute_asymmetric_prototype_map",
            "input_sha256": sha256(args.input),
            "teacher_cache_sha256": sha256(args.teacher_cache),
            "query_count": len(queries), "prototype_count": len(prototypes),
            "widths": widths, "seeds": seeds, "reports": reports,
            "contract": {"positive_ranks": positives,
                         "objective": args.objective,
                         "negative_ranks": negative_ranks,
                         "random_negatives": args.random_negatives,
                         "hard_negatives": args.hard_negatives,
                         "hard_epochs": args.hard_epochs,
                         "budgets": args.budgets},
            "decision": {"prototype_address_dedup_licensed": False,
                         "full_cascade_licensed": False,
                         "exhaustive_hamming_is_offline_only": True}}


def self_test() -> None:
    values = np.asarray([2, 1, 1, 1, 0], dtype=np.uint16)
    require(np.array_equal(top_indices(values, 3), np.asarray([4, 1, 2])),
            "deterministic boundary tie-break differs")
    print("NeuRoute asymmetric prototype-map self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--teacher-cache", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--widths", nargs="+", type=int, default=[32, 64, 128])
    parser.add_argument("--seeds", nargs="+", type=int, default=[285, 286, 287])
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--hard-epochs", type=int, default=1)
    parser.add_argument("--positive-ranks", nargs="+", type=int,
                        default=[0, 1, 7, 31, 127, 255, 511, 1023])
    parser.add_argument("--negative-ranks", nargs="+", type=int, default=[64, 256])
    parser.add_argument("--random-negatives", type=int, default=8)
    parser.add_argument("--hard-negatives", type=int, default=8)
    parser.add_argument("--objective", choices=["pairwise", "rank_weighted_listwise"],
                        default="pairwise",
                        help="prototype training objective; listwise is a rank-utility proxy")
    parser.add_argument("--include-ceiling", action="store_true",
                        help="also run free-query/free-prototype representational ceiling")
    parser.add_argument("--ceiling-only", action="store_true",
                        help="run only the free-query/free-prototype ceiling")
    parser.add_argument("--ceiling-epochs", type=int, default=8)
    parser.add_argument("--ceiling-positive-count", type=int, default=64)
    parser.add_argument("--artifact-output", type=Path,
                        help="save query/prototype codes for one selected cell")
    parser.add_argument("--artifact-width", type=int)
    parser.add_argument("--artifact-seed", type=int)
    parser.add_argument("--model-output", type=Path,
                        help="save query encoder state for a selected artifact cell")
    parser.add_argument("--budgets", nargs="+", type=int,
                        default=[1024, 2048, 4096, 8192])
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        require(args.input is not None and args.teacher_cache is not None and
                args.output is not None, "input, teacher-cache and output are required")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical(run(args)))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"run-neuroute-asymmetric-prototype-map: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
