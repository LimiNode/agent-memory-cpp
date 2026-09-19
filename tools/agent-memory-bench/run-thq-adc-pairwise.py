#!/usr/bin/env python3
"""Evaluate a true teacher-rank pairwise objective for the ADC48 scorer."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import torch

D = 384
TOP = 128


def load_crossfit():
    path = Path(__file__).with_name("run-thq-adc-crossfit.py")
    spec = importlib.util.spec_from_file_location("thq_adc_crossfit", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load cross-fit helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


crossfit = load_crossfit()
gate = crossfit.gate
score = gate.score
h = score.h.h


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def array_sha(values: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(values, dtype="<f4").tobytes()).hexdigest()


def shell_top32(documents, thq_codes, thresholds, centroids, ids, query):
    docs = np.asarray(documents[ids], dtype=np.float32)
    levels = h.unpack_thq(np.asarray(thq_codes[ids]))
    base = centroids[np.arange(D)[None, :], levels]
    interval_lut = np.empty((D, 4), dtype=np.float32)
    for coordinate in range(D):
        for level in range(4):
            low = -np.inf if level == 0 else thresholds[coordinate, level - 1]
            high = np.inf if level == 3 else thresholds[coordinate, level]
            delta = low - query[coordinate] if query[coordinate] < low else (
                query[coordinate] - high if query[coordinate] > high else 0.0)
            interval_lut[coordinate, level] = delta * delta
    interval = np.sum(interval_lut[np.arange(D)[None, :], levels], axis=1)
    thq_ids = ids[np.lexsort((ids, interval))[:min(TOP, len(ids))]]
    thq_pos = np.asarray([int(np.flatnonzero(ids == doc)[0]) for doc in thq_ids])
    exact = docs[thq_pos] @ query
    order = np.lexsort((thq_ids, -exact))[:32]
    selected_pos = thq_pos[order]
    return (np.asarray(docs[selected_pos], dtype=np.float32),
            np.asarray(base[selected_pos], dtype=np.float32),
            np.asarray(thq_ids[order], dtype=np.int64),
            np.asarray(exact[order], dtype=np.float32),
            thq_ids)


def _score_torch(residual_t, base_t, query_t, centers, transform_t, inverse_t):
    """Decode a [groups, 32, blocks, width] batch without materializing codes."""
    groups = residual_t.shape[0]
    transformed = torch.einsum("gnbd,bdk->gnbk", residual_t, transform_t)
    distances = ((transformed[:, :, :, None, :] - centers[None, None, :, :, :]) ** 2).sum(dim=4)
    symbols = torch.argmin(distances, dim=3)
    expanded = centers[None, None].expand(groups, residual_t.shape[1], -1, -1, -1)
    chosen = torch.take_along_dim(expanded, symbols[..., None, None].expand(-1, -1, -1, 1, residual_t.shape[-1]), dim=3).squeeze(3)
    decoded = torch.einsum("gnbi,bik->gnbk", chosen, inverse_t).reshape(groups, residual_t.shape[1], D)
    values = base_t + decoded
    scores = (values * query_t).sum(dim=2) / torch.linalg.vector_norm(values, dim=2).clamp_min(1e-8)
    return scores, decoded, symbols


def _model_stats(centers, groups, transforms):
    residual = np.concatenate([item[0] for item in groups], axis=0)
    with torch.no_grad():
        _, _, symbols = _score_torch(
            torch.from_numpy(residual.reshape(-1, 32, 128, 3)),
            torch.zeros((len(groups), 32, D), dtype=torch.float32),
            torch.zeros((len(groups), 32, D), dtype=torch.float32), centers,
            torch.from_numpy(np.asarray(transforms, dtype=np.float32)),
            torch.from_numpy(np.asarray([np.linalg.inv(t) for t in transforms], dtype=np.float32)))
    counts = np.bincount(symbols.numpy().reshape(-1), minlength=128 * 8)
    probabilities = counts[counts > 0] / max(1, counts.sum())
    entropy = float(-(probabilities * np.log2(probabilities)).sum()) if len(probabilities) else 0.0
    return {"unused_centers": int(np.sum(counts == 0)),
            "assignment_entropy_bits": entropy,
            "center_l2": float(torch.linalg.vector_norm(centers).detach())}


def train_pairwise(groups, transforms, initial_codebooks, *, epochs=4, learning_rate=0.003,
                   reconstruction_fraction=0.10, seed=0, batch_groups=8):
    width = 3
    blocks = 128
    levels = 8
    centers_t = np.asarray([initial_codebooks[b] @ transforms[b].T for b in range(blocks)], dtype=np.float32)
    initial_model_sha = array_sha(centers_t)
    inverse = np.asarray([np.linalg.inv(transforms[b]) for b in range(blocks)], dtype=np.float32)
    centers = torch.nn.Parameter(torch.from_numpy(centers_t))
    transform_t = torch.from_numpy(np.asarray(transforms, dtype=np.float32))
    inverse_t = torch.from_numpy(inverse)
    optimizer = torch.optim.Adam([centers], lr=learning_rate)

    initial_pair, initial_reconstruction = [], []
    with torch.no_grad():
        for residual, base, query in groups:
            residual_t = torch.from_numpy(residual.reshape(1, 32, blocks, width))
            base_t = torch.from_numpy(base.reshape(1, 32, D))
            query_t = torch.from_numpy(np.repeat(query[None, None, :], 32, axis=1).astype(np.float32))
            scores, decoded, _ = _score_torch(residual_t, base_t, query_t, centers, transform_t, inverse_t)
            pair = torch.nn.functional.softplus(-(scores[:, :10, None] - scores[:, None, 10:]) / 0.02).mean()
            initial_pair.append(float(pair))
            initial_reconstruction.append(float(((decoded - residual_t.reshape(1, 32, D)) ** 2).mean()))
    initial_pair_mean = max(float(np.mean(initial_pair)), 1e-8)
    initial_reconstruction_mean = max(float(np.mean(initial_reconstruction)), 1e-12)
    reconstruction_weight = reconstruction_fraction * initial_pair_mean / initial_reconstruction_mean

    rng = np.random.default_rng(seed)
    history = []
    for epoch in range(epochs):
        losses = []
        order = rng.permutation(len(groups))
        for start in range(0, len(groups), batch_groups):
            selected = order[start:start + batch_groups]
            residual = np.stack([groups[i][0] for i in selected], axis=0)
            base = np.stack([groups[i][1] for i in selected], axis=0)
            query = np.stack([groups[i][2] for i in selected], axis=0)
            residual_t = torch.from_numpy(residual.reshape(len(selected), 32, blocks, width))
            base_t = torch.from_numpy(base)
            query_t = torch.from_numpy(np.repeat(query[:, None, :], 32, axis=1).astype(np.float32))
            scores, decoded, _ = _score_torch(residual_t, base_t, query_t, centers, transform_t, inverse_t)
            pair = torch.nn.functional.softplus(-(scores[:, :10, None] - scores[:, None, 10:]) / 0.02).mean()
            reconstruction = ((decoded - residual_t.reshape(len(selected), 32, D)) ** 2).mean()
            loss = pair + reconstruction_weight * reconstruction
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            losses.append({"loss": float(loss.detach()), "pair_loss": float(pair.detach()),
                           "reconstruction_loss": float(reconstruction.detach()),
                           "weighted_reconstruction_loss": float((reconstruction_weight * reconstruction).detach())})
        history.append({"epoch": epoch, **{key: float(np.mean([row[key] for row in losses])) for key in losses[0]},
                        "reconstruction_weight": reconstruction_weight})
    return centers.detach().numpy(), history, {
        "seed": seed, "initial_pair_loss": initial_pair_mean,
        "initial_reconstruction_loss": initial_reconstruction_mean,
        "reconstruction_fraction": reconstruction_fraction,
        "reconstruction_weight": reconstruction_weight,
        "batch_groups": batch_groups, "initial_model_sha256": initial_model_sha,
        "final_model_sha256": array_sha(centers.detach().numpy()),
        "final_model": _model_stats(centers, groups, transforms),
    }


def score_pairwise(residual, base, query, centers_t, transforms):
    blocks, levels, width = centers_t.shape
    inverse = np.asarray([np.linalg.inv(transforms[b]) for b in range(blocks)], dtype=np.float32)
    residual_t = np.einsum("nbd,bkd->nbk", residual.reshape(len(residual), blocks, width),
                           np.asarray(transforms, dtype=np.float32))
    symbols = np.argmin(((residual_t[:, :, None, :] - centers_t[None, :, :, :]) ** 2).sum(axis=3), axis=2)
    chosen_t = np.take_along_axis(centers_t[None], symbols[:, :, None, None], axis=2)[:, :, 0, :]
    decoded = np.einsum("nbd,bkd->nbk", chosen_t, inverse).reshape(len(residual), D)
    values = base + decoded
    return (values @ query) / np.maximum(np.linalg.norm(values, axis=1), np.finfo(np.float32).tiny)


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("documents", "train-vectors", "thq4-codes", "thq4-thresholds",
                 "candidate-flat", "candidate-raw", "candidate-receipt", "queries",
                 "qrel-ids", "qrel-scores", "teacher-ids", "output"):
        parser.add_argument(f"--{name}", dest=name.replace("-", "_"), type=Path, required=True)
    args = parser.parse_args()
    document_count = args.documents.stat().st_size // (4 * D)
    train_count = args.train_vectors.stat().st_size // (4 * D)
    query_count = args.queries.stat().st_size // (4 * D)
    documents = np.memmap(args.documents, mode="r", dtype="<f4", shape=(document_count, D))
    train = np.asarray(np.memmap(args.train_vectors, mode="r", dtype="<f4", shape=(train_count, D)), dtype=np.float32)
    thq_codes = np.memmap(args.thq4_codes, mode="r", dtype=np.uint8, shape=(document_count, 96))
    thresholds = np.fromfile(args.thq4_thresholds, dtype="<f4").reshape(D, 3)
    queries = np.asarray(np.memmap(args.queries, mode="r", dtype="<f4", shape=(query_count, D)), dtype=np.float32)
    qrel_ids = np.memmap(args.qrel_ids, mode="r", dtype="<i8", shape=(query_count, 20))
    qrel_scores = np.memmap(args.qrel_scores, mode="r", dtype="<f4", shape=(query_count, 20))
    teacher_ids = np.memmap(args.teacher_ids, mode="r", dtype="<i8", shape=(query_count, 10))
    candidate_ids, offsets = score.load_candidates(args.candidate_flat, args.candidate_raw)
    score.validate_candidate_receipt(args.candidate_receipt, args.candidate_raw, args.candidate_flat)
    centroids = h.fit_centroids(train, thresholds)
    train_levels = h.unpack_thq(h.pack_thq(train, thresholds))
    train_base = centroids[np.arange(D)[None, :], train_levels]
    train_residual = train - train_base
    shuffled = np.random.default_rng(20260919).permutation(query_count)
    folds = np.array_split(shuffled, 4)
    rows = []
    histories = {}
    diagnostics = {}
    parity = {}
    pairwise_seeds = (11, 22, 33)
    for fold, test_ids in enumerate(folds):
        fit_ids = np.setdiff1d(np.arange(query_count), test_ids)
        covariance = queries[fit_ids].astype(np.float64).T @ queries[fit_ids].astype(np.float64)
        initial, transforms, _ = gate.fit_codebooks(train_residual, covariance, 128, 3, 8192, 5, 1,
                                                     20260919 + fold * 100003)
        groups = []
        for qi in fit_ids:
            ids = candidate_ids[offsets[qi]:offsets[qi + 1]]
            docs, base, _, _, _ = shell_top32(documents, thq_codes, thresholds, centroids, ids, queries[qi])
            groups.append((docs - base, base, queries[qi]))
        print(f"fold {fold}: pairwise training on {len(groups)} THQ-local groups", flush=True)
        initial_centers_t = np.asarray([initial[b] @ transforms[b].T for b in range(128)], dtype=np.float32)
        initial_direct, initial_pairwise = [], []
        parity_matches = 0
        for residual, base, query in groups[:min(4, len(groups))]:
            symbols = gate.gate.encode(residual, initial, transforms)
            direct = gate.gate.direct_adc_scores(base, initial, symbols, query)
            pairwise = score_pairwise(residual, base, query, initial_centers_t, transforms)
            initial_direct.extend(direct.tolist())
            initial_pairwise.extend(pairwise.tolist())
            if np.array_equal(gate.gate.top_ids(direct, np.arange(len(direct))),
                              gate.gate.top_ids(pairwise, np.arange(len(pairwise)))):
                parity_matches += 1
        max_abs_error = float(np.max(np.abs(np.asarray(initial_direct) - np.asarray(initial_pairwise))))
        parity[str(fold)] = {"max_abs_error": max_abs_error,
                             "top10_parity_groups": parity_matches,
                             "top10_parity_group_count": min(4, len(groups))}
        if max_abs_error > 1e-5:
            raise RuntimeError(f"pre-training pairwise/direct ADC parity failed in fold {fold}: {max_abs_error}")
        for pairwise_seed in pairwise_seeds:
            centers_t, history, train_diag = train_pairwise(
                groups, transforms, initial, epochs=4, learning_rate=0.003,
                reconstruction_fraction=0.10, seed=pairwise_seed)
            key = f"fold{fold}-seed{pairwise_seed}"
            histories[key] = history
            diagnostics[key] = train_diag
            for qi in test_ids:
                ids = candidate_ids[offsets[qi]:offsets[qi + 1]]
                _, _, _, _, thq_top = shell_top32(documents, thq_codes, thresholds, centroids, ids, queries[qi])
                thq_pos = np.asarray([int(np.flatnonzero(ids == doc)[0]) for doc in thq_top])
                all_docs = np.asarray(documents[ids], dtype=np.float32)
                levels = h.unpack_thq(np.asarray(thq_codes[ids]))
                all_base = centroids[np.arange(D)[None, :], levels]
                residual_all = all_docs - all_base
                top_scores = score_pairwise(residual_all[thq_pos], all_base[thq_pos], queries[qi], centers_t, transforms)
                selected = gate.gate.top_ids(top_scores, thq_top)
                exact = all_docs[thq_pos] @ queries[qi]
                exact_top = gate.gate.top_ids(exact, thq_top)
                rows.append({"fold": fold, "seed": pairwise_seed, "query": int(qi),
                             "top10_ids": selected.astype(int).tolist(),
                             "candidate_fp32_top10_ids": exact_top.astype(int).tolist(),
                             "thq4_top128_ids": thq_top.astype(int).tolist(),
                             "qrels_ndcg10": gate.gate.ndcg(selected, qrel_ids[qi], qrel_scores[qi]),
                             "teacher_overlap": float(np.isin(teacher_ids[qi], selected).sum() / 10.0),
                             "candidate_fp32_overlap": float(np.isin(exact_top, selected).sum() / 10.0),
                             "payload_bytes": 144, "thq4_top128_count": int(len(thq_top))})
    summaries = {}
    for pairwise_seed in pairwise_seeds:
        seed_rows = [row for row in rows if row["seed"] == pairwise_seed]
        summaries[str(pairwise_seed)] = {metric: float(np.mean([row[metric] for row in seed_rows]))
                                         for metric in ("qrels_ndcg10", "teacher_overlap", "candidate_fp32_overlap")}
    summary = {metric: float(np.mean([row[metric] for row in rows]))
               for metric in ("qrels_ndcg10", "teacher_overlap", "candidate_fp32_overlap")}
    result = {"schema_version": 1, "family": "thq_adc_pairwise_crossfit_v1", "status": "EXECUTED",
              "runner_sha256": sha(Path(__file__)), "documents": document_count, "training_count": train_count,
              "query_count": query_count, "fold_count": 4, "fold_sizes": [len(fold) for fold in folds],
              "fold_seed": 20260919, "fold_queries": [fold.astype(int).tolist() for fold in folds],
              "candidate_flat_sha256": sha(args.candidate_flat),
              "candidate_raw_sha256": sha(args.candidate_raw), "candidate_receipt_sha256": sha(args.candidate_receipt),
              "documents_sha256": sha(args.documents), "training_sha256": sha(args.train_vectors),
              "thq4_codes_sha256": sha(args.thq4_codes), "thq4_thresholds_sha256": sha(args.thq4_thresholds),
              "queries_sha256": sha(args.queries), "qrel_ids_sha256": sha(args.qrel_ids),
              "qrel_scores_sha256": sha(args.qrel_scores), "teacher_ids_sha256": sha(args.teacher_ids),
              "training_protocol": "THQ-local-top32-teacher-pairwise-softplus",
              "code_rate": {"blocks": 128, "dimensions_per_block": 3, "bits_per_block": 3,
                            "adc_side_bytes": 48, "thq4_bytes": 96, "total_payload_bytes": 144},
              "loss": {"pairwise": "softplus(-(score_rank_0_9-score_rank_10_31)/0.02)",
                       "reconstruction_fraction_of_initial_pair_loss": 0.10,
                       "optimizer": "Adam", "epochs": 4, "learning_rate": 0.003,
                       "batch_groups": 8, "group_order": "deterministically shuffled per epoch",
                       "seeds": list(pairwise_seeds)},
              "histories": histories, "training_diagnostics": diagnostics,
              "pretraining_parity": parity, "summaries_by_seed": summaries, "summary": summary, "rows": rows,
              "evidence_status": "four_fold_shuffled_multi_seed_pairwise_reference_quality",
              "limitations": ["teacher score order rather than qrels labels", "candidate-local replay",
                               "hard assignment refresh rather than differentiable assignment", "no native latency or persistent materialization"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
