#!/usr/bin/env python3
"""Train a qrels-aware NLB encoder without touching held-out MIRACL dev data.

The trainer is intentionally separate from document-only NLB experiments.  It
uses frozen E5 hard negatives, keeps threshold calibration label-free, and
selects checkpoints only with the predeclared supervised validation contract.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

TRAINER_ID = "agent-memory-cpp:nlb-qrels-supervised-trainer"
TRAINER_VERSION = "v1"
ARTIFACT_FAMILY = "nlb_qrels_supervised_v1"
OBJECTIVE = "qrels_soft_hamming_triplet_v1"
SELECTION_ID = "qrels_lexicographic_hard_code_v1"
MINING_ID = "frozen_e5_cosine_topk_nonpositive_v1"
QUERY_SPLIT_ID = "stable_sha256_query_split_v1"
FIXED_CANDIDATE_LIMIT = 512


class SupervisedTrainingError(RuntimeError):
    """Raised when supervised training would violate its reproducibility contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_ids_sha256(ids: list[str]) -> str:
    return hashlib.sha256("".join(f"{value}\n" for value in sorted(ids)).encode("utf-8")).hexdigest()


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def load_base() -> Any:
    path = Path(__file__).resolve().parent / "train-binary-autoencoder.py"
    spec = importlib.util.spec_from_file_location("agent_memory_supervised_nlb_base", path)
    if spec is None or spec.loader is None:
        raise SupervisedTrainingError(f"cannot load base trainer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_ids(path: Path, base: Any) -> list[str]:
    return base.load_ids(path)


def require_output(root: Path, manifest: dict[str, Any], name: str, base: Any) -> tuple[Path, dict[str, Any]]:
    try:
        entry = manifest["outputs"][name]
        relative = Path(entry["path"])
    except (KeyError, TypeError) as exc:
        raise SupervisedTrainingError(f"materialization output is missing: {name}") from exc
    if relative.is_absolute() or relative.name != str(relative):
        raise SupervisedTrainingError(f"materialization output path must be plain: {name}")
    path = root / relative
    if not path.is_file() or sha256_file(path) != base.require_sha256(entry.get("sha256"), f"outputs.{name}.sha256"):
        raise SupervisedTrainingError(f"materialization output hash mismatch: {name}")
    return path, entry


def load_supervised_materialization(root: Path, base: Any, numpy: Any) -> dict[str, Any]:
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SupervisedTrainingError(f"cannot read materialization manifest: {exc}") from exc
    if manifest.get("schema_version") != 1 or manifest.get("vector_format", {}).get("dtype") != "float32_le":
        raise SupervisedTrainingError("unsupported materialization manifest")
    dimension = manifest.get("vector_format", {}).get("dimension")
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
        raise SupervisedTrainingError("materialization dimension is invalid")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise SupervisedTrainingError("materialization outputs are invalid")
    train_ids_path, train_ids_entry = require_output(root, manifest, "train_ids", base)
    train_vectors_path, train_vectors_entry = require_output(root, manifest, "train_vectors", base)
    document_ids_path, document_ids_entry = require_output(root, manifest, "evaluation_document_ids", base)
    document_vectors_path, document_vectors_entry = require_output(root, manifest, "evaluation_document_vectors", base)
    query_ids_path, query_ids_entry = require_output(root, manifest, "evaluation_query_ids", base)
    query_vectors_path, query_vectors_entry = require_output(root, manifest, "evaluation_query_vectors", base)
    qrels_path, qrels_entry = require_output(root, manifest, "evaluation_qrels", base)
    train_ids = load_ids(train_ids_path, base)
    document_ids = load_ids(document_ids_path, base)
    query_ids = load_ids(query_ids_path, base)
    for entry, ids, name in ((train_vectors_entry, train_ids, "train"), (document_vectors_entry, document_ids, "document"), (query_vectors_entry, query_ids, "query")):
        if entry.get("count") != len(ids) or entry.get("dimension") != dimension or entry.get("dtype") != "float32_le":
            raise SupervisedTrainingError(f"materialization {name} vector shape is invalid")
    def vector_map(path: Path, count: int) -> Any:
        if path.stat().st_size != count * dimension * 4:
            raise SupervisedTrainingError("materialization vector byte size is invalid")
        return numpy.memmap(path, dtype="<f4", mode="r", shape=(count, dimension))
    qrels: dict[str, dict[str, int]] = {}
    query_id_set = set(query_ids)
    document_id_set = set(document_ids)
    with qrels_path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, line in enumerate(handle, 1):
            fields = line.rstrip("\n").split()
            if len(fields) != 4:
                raise SupervisedTrainingError(f"qrels line {line_number} is invalid")
            query_id, _, document_id, grade_text = fields
            try:
                grade = int(grade_text)
            except ValueError as exc:
                raise SupervisedTrainingError(f"qrels line {line_number} grade is invalid") from exc
            if query_id not in query_id_set or document_id not in document_id_set:
                raise SupervisedTrainingError("qrels references an unavailable supervised row")
            qrels.setdefault(query_id, {})[document_id] = grade
    if qrels_entry.get("count") != sum(len(values) for values in qrels.values()):
        raise SupervisedTrainingError("qrels count is invalid")
    positive = {query: {doc: grade for doc, grade in rows.items() if grade > 0} for query, rows in qrels.items()}
    if set(positive) != set(query_ids) or any(not values for values in positive.values()):
        raise SupervisedTrainingError("every supervised query must have a positive qrel")
    return {
        "manifest": manifest, "manifest_sha256": sha256_file(root / "manifest.json"), "dimension": dimension,
        "train_ids": train_ids, "document_ids": document_ids, "query_ids": query_ids,
        "train_vectors": vector_map(train_vectors_path, len(train_ids)),
        "document_vectors": vector_map(document_vectors_path, len(document_ids)),
        "query_vectors": vector_map(query_vectors_path, len(query_ids)),
        "positive": positive, "qrels": qrels,
        "output_hashes": {name: entry["sha256"] for name, entry in outputs.items()},
    }


def split_query_ids(query_ids: list[str], seed: int, validation_fraction: float) -> tuple[list[str], list[str]]:
    if not 0.0 < validation_fraction < 0.5:
        raise SupervisedTrainingError("supervised validation fraction must be in (0, 0.5)")
    validation = [value for value in query_ids if int.from_bytes(hashlib.sha256(f"{seed}\0nlb-qrels-validation-v1\0{value}".encode()).digest()[:8], "big") / float(1 << 64) < validation_fraction]
    train = [value for value in query_ids if value not in set(validation)]
    if not train or not validation:
        raise SupervisedTrainingError("supervised query split is empty")
    return train, validation


def mine_hard_negatives(*, query_ids: list[str], query_vectors: Any, document_ids: list[str], document_vectors: Any, positives: dict[str, dict[str, int]], count: int, numpy: Any) -> dict[str, list[str]]:
    if count <= 0:
        raise SupervisedTrainingError("hard negative count must be positive")
    document_positions = {value: index for index, value in enumerate(document_ids)}
    result: dict[str, list[str]] = {}
    document_matrix = numpy.asarray(document_vectors, dtype=numpy.float32)
    for query_index, query_id in enumerate(query_ids):
        query = numpy.asarray(query_vectors[query_index], dtype=numpy.float32)
        scores = document_matrix @ query
        excluded = {document_positions[value] for value in positives[query_id]}
        if len(excluded) >= len(document_ids):
            raise SupervisedTrainingError("query has no non-positive hard-negative candidates")
        scores[list(excluded)] = -numpy.inf
        width = min(count, len(document_ids) - len(excluded))
        candidates = numpy.argpartition(-scores, width - 1)[:width]
        ordered = sorted(candidates.tolist(), key=lambda index: (-float(scores[index]), document_ids[index]))
        negatives = [document_ids[index] for index in ordered]
        if set(negatives).intersection(positives[query_id]) or len(set(negatives)) != len(negatives):
            raise SupervisedTrainingError("mining failed positive exclusion")
        result[query_id] = negatives
    return result


def hard_codes(values: Any, weights: Any, bias: Any, numpy: Any) -> Any:
    return (numpy.clip(values, -1.0, 1.0) @ weights.T + bias) >= 0.0


def rank_validation(*, query_ids: list[str], query_vectors: Any, document_ids: list[str], document_vectors: Any, positives: dict[str, dict[str, int]], weights: Any, bias: Any, candidate_limit: int, numpy: Any) -> dict[str, float]:
    if candidate_limit != FIXED_CANDIDATE_LIMIT:
        raise SupervisedTrainingError("checkpoint candidate budget must equal 512")
    doc_codes = hard_codes(document_vectors, weights, bias, numpy)
    query_codes = hard_codes(query_vectors, weights, bias, numpy)
    document_matrix = numpy.asarray(document_vectors, dtype=numpy.float32)
    positive_covered = 0
    ndcg_sum = 0.0
    for query_index, query_id in enumerate(query_ids):
        distance = numpy.count_nonzero(doc_codes != query_codes[query_index], axis=1)
        candidate_indices = sorted(range(len(document_ids)), key=lambda index: (int(distance[index]), document_ids[index]))[:candidate_limit]
        candidate_ids = {document_ids[index] for index in candidate_indices}
        grades = positives[query_id]
        positive_covered += int(bool(candidate_ids.intersection(grades)))
        ordered = sorted(candidate_indices, key=lambda index: (-float(document_matrix[index] @ query_vectors[query_index]), document_ids[index]))[:10]
        dcg = sum(((2.0 ** grades.get(document_ids[index], 0) - 1.0) / math.log2(rank + 2.0)) for rank, index in enumerate(ordered))
        ideal = sorted(grades.values(), reverse=True)[:10]
        idcg = sum(((2.0 ** grade - 1.0) / math.log2(rank + 2.0)) for rank, grade in enumerate(ideal))
        ndcg_sum += dcg / idcg if idcg else 0.0
    return {"positive_qrels_query_coverage_at_512": positive_covered / len(query_ids), "reranked_ndcg_at_10": ndcg_sum / len(query_ids)}


def select_better(candidate: dict[str, Any], incumbent: dict[str, Any] | None) -> bool:
    if incumbent is None:
        return True
    a, b = candidate["selection_metrics"], incumbent["selection_metrics"]
    return (candidate["health_passes"], a["positive_qrels_query_coverage_at_512"], a["reranked_ndcg_at_10"], -candidate["occupancy_deviation"], -candidate["epoch"]) > (incumbent["health_passes"], b["positive_qrels_query_coverage_at_512"], b["reranked_ndcg_at_10"], -incumbent["occupancy_deviation"], -incumbent["epoch"])


def train(*, materialization_root: Path, output_root: Path, bit_count: int, seed: int, epochs: int, batch_size: int, learning_rate: float, margin: float, validation_fraction: float, hard_negative_count: int, consumed_negatives_per_query: int, torch_threads: int, reconstruction_weight: float, decorrelation_weight: float, row_orthogonality_weight: float) -> dict[str, Any]:
    if output_root.exists():
        raise SupervisedTrainingError(f"output directory already exists: {output_root}")
    if bit_count <= 0 or epochs < 0 or batch_size <= 0 or learning_rate <= 0.0 or margin <= 0.0 or torch_threads <= 0 or consumed_negatives_per_query <= 0 or consumed_negatives_per_query > hard_negative_count:
        raise SupervisedTrainingError("training parameters must be positive")
    base = load_base()
    try:
        base.verify_environment()
        import numpy
        import torch
        import torch.nn.functional as functional
    except (ImportError, base.TrainingError) as exc:
        raise SupervisedTrainingError(f"pinned trainer environment is required: {exc}") from exc
    data = load_supervised_materialization(materialization_root, base, numpy)
    try:
        prepared_study = json.loads((materialization_root / "prepared-study-manifest.json").read_text(encoding="utf-8"))
        excluded_document_ids_sha256 = prepared_study["split"]["external_excluded_document_ids_set_sha256"]
        if not isinstance(excluded_document_ids_sha256, str) or len(excluded_document_ids_sha256) != 64:
            raise ValueError("invalid external exclusion digest")
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        raise SupervisedTrainingError(f"supervised prepared-study exclusion provenance is invalid: {exc}") from exc
    if bit_count > data["dimension"]:
        raise SupervisedTrainingError("bit count cannot exceed input dimension")
    train_query_ids, validation_query_ids = split_query_ids(data["query_ids"], seed, validation_fraction)
    query_positions = {value: index for index, value in enumerate(data["query_ids"])}
    document_positions = {value: index for index, value in enumerate(data["document_ids"])}
    train_negatives = mine_hard_negatives(query_ids=train_query_ids, query_vectors=numpy.asarray(data["query_vectors"])[[query_positions[value] for value in train_query_ids]], document_ids=data["document_ids"], document_vectors=data["document_vectors"], positives=data["positive"], count=hard_negative_count, numpy=numpy)
    validation_negatives = mine_hard_negatives(query_ids=validation_query_ids, query_vectors=numpy.asarray(data["query_vectors"])[[query_positions[value] for value in validation_query_ids]], document_ids=data["document_ids"], document_vectors=data["document_vectors"], positives=data["positive"], count=hard_negative_count, numpy=numpy)
    # Query vectors are passed in split order above; mining needs their IDs in that same order.
    if set(train_negatives).intersection(validation_negatives) or set(train_negatives) != set(train_query_ids):
        raise SupervisedTrainingError("query split must precede and partition mining")
    torch.set_num_threads(torch_threads)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    # Deterministic PCA+median initialization on label-free document-only vectors.
    values = numpy.asarray(data["train_vectors"], dtype=numpy.float32)
    centered = values.astype(numpy.float64) - values.mean(axis=0)
    _, _, right = numpy.linalg.svd(centered, full_matrices=False)
    initial_weight = right[:bit_count].astype(numpy.float32)
    initial_bias = (-numpy.median(numpy.clip(values, -1.0, 1.0) @ initial_weight.T, axis=0)).astype(numpy.float32)
    weight = torch.nn.Parameter(torch.from_numpy(initial_weight.copy()))
    bias = torch.nn.Parameter(torch.from_numpy(initial_bias.copy()), requires_grad=False)
    decoder_bias = torch.nn.Parameter(torch.zeros(data["dimension"], dtype=torch.float32))
    optimizer = torch.optim.AdamW([weight, decoder_bias], lr=learning_rate, weight_decay=0.0)
    ordered_train = sorted(train_query_ids)
    best: dict[str, Any] | None = None
    history: list[dict[str, Any]] = []
    def recalibrate() -> None:
        projections = numpy.clip(values, -1.0, 1.0) @ weight.detach().cpu().numpy().T
        with torch.no_grad(): bias.copy_(torch.from_numpy((-numpy.median(projections, axis=0)).astype(numpy.float32)))
    def consider_checkpoint(epoch: int) -> None:
        nonlocal best
        weight_array, bias_array = weight.detach().cpu().numpy(), bias.detach().cpu().numpy()
        health = base.hard_code_health(vectors=data["train_vectors"], indices=list(range(len(data["train_ids"]))), encoder_weight=weight, encoder_bias=bias, batch_size=batch_size, clip_inputs=True)
        occupancy_deviation = float(numpy.mean(numpy.abs(numpy.clip(data["train_vectors"], -1.0, 1.0) @ weight_array.T + bias_array >= 0.0).mean(axis=0) - 0.5))
        metrics = rank_validation(query_ids=validation_query_ids, query_vectors=numpy.asarray(data["query_vectors"])[[query_positions[value] for value in validation_query_ids]], document_ids=data["document_ids"], document_vectors=data["document_vectors"], positives=data["positive"], weights=weight_array, bias=bias_array, candidate_limit=FIXED_CANDIDATE_LIMIT, numpy=numpy)
        record = {"epoch": epoch, "health": health, "health_passes": health["unique_code_count"] >= 2 and health["constant_bit_fraction"] <= 0.10, "occupancy_deviation": occupancy_deviation, "selection_metrics": metrics, "encoder_weight": weight.detach().clone(), "encoder_bias": bias.detach().clone(), "decoder_bias": decoder_bias.detach().clone()}
        history.append({key: value for key, value in record.items() if key not in ("encoder_weight", "encoder_bias", "decoder_bias")})
        if select_better(record, best): best = record

    recalibrate()
    consider_checkpoint(-1)
    for epoch in range(epochs):
        order = ordered_train[:]
        random.Random(int.from_bytes(hashlib.sha256(f"{seed}\0qrels-supervised\0{epoch}".encode()).digest()[:16], "big")).shuffle(order)
        for start in range(0, len(order), batch_size):
            ids = order[start:start + batch_size]
            q_rows = [query_positions[value] for value in ids for _ in range(consumed_negatives_per_query)]
            pos_rows = [document_positions[sorted(data["positive"][value], key=lambda document: (-data["positive"][value][document], document))[0]] for value in ids for _ in range(consumed_negatives_per_query)]
            neg_rows = [document_positions[train_negatives[value][(epoch * consumed_negatives_per_query + offset) % len(train_negatives[value])]] for value in ids for offset in range(consumed_negatives_per_query)]
            q = torch.from_numpy(numpy.asarray(data["query_vectors"][q_rows], dtype=numpy.float32).copy())
            pos = torch.from_numpy(numpy.asarray(data["document_vectors"][pos_rows], dtype=numpy.float32).copy())
            neg = torch.from_numpy(numpy.asarray(data["document_vectors"][neg_rows], dtype=numpy.float32).copy())
            def soft(input_values: Any) -> Any:
                return torch.tanh(torch.clamp(input_values, -1.0, 1.0) @ weight.T + bias)
            q_code, pos_code, neg_code = soft(q), soft(pos), soft(neg)
            similarity = lambda lhs, rhs: torch.mean(lhs * rhs, dim=1)
            triplet = functional.softplus(margin - similarity(q_code, pos_code) + similarity(q_code, neg_code)).mean()
            reconstruction = torch.mean((torch.tanh(((pos_code + 1.0) * 0.5) @ weight + decoder_bias) - torch.clamp(pos, -1.0, 1.0)) ** 2)
            centered_codes = pos_code - pos_code.mean(dim=0, keepdim=True)
            covariance = centered_codes.T @ centered_codes / max(1, len(ids))
            decorrelation = (torch.sum(covariance ** 2) - torch.sum(torch.diagonal(covariance) ** 2)) / max(1, bit_count * (bit_count - 1))
            gram = weight @ weight.T
            orthogonality = torch.mean((gram - torch.eye(bit_count)) ** 2)
            loss = triplet + reconstruction_weight * reconstruction + decorrelation_weight * decorrelation + row_orthogonality_weight * orthogonality
            optimizer.zero_grad(set_to_none=True); loss.backward(); optimizer.step()
        recalibrate()
        consider_checkpoint(epoch)
    if best is None or not best["health_passes"]:
        raise SupervisedTrainingError("no checkpoint passed hard-code health gates")
    output_root.mkdir(parents=True)
    paths = {"encoder_weights": output_root / "encoder-weights.f32", "encoder_bias": output_root / "encoder-bias.f32", "decoder_bias": output_root / "decoder-bias.f32"}
    base.write_f32(paths["encoder_weights"], best["encoder_weight"]); base.write_f32(paths["encoder_bias"], best["encoder_bias"]); base.write_f32(paths["decoder_bias"], best["decoder_bias"])
    negative_payload = {"train": train_negatives, "validation": validation_negatives}
    negatives_path = output_root / "frozen-hard-negatives.json"
    negatives_path.write_text(json.dumps(negative_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    artifact = {"schema_version": 1, "trainer": {"id": TRAINER_ID, "version": TRAINER_VERSION, "source_hash": sha256_file(Path(__file__)), "base_trainer_source_hash": sha256_file(Path(__file__).with_name("train-binary-autoencoder.py")), "requirements_lock": "requirements-binary-autoencoder-trainer.txt;sha256=" + sha256_file(Path(__file__).with_name("requirements-binary-autoencoder-trainer.txt"))}, "input_materialization_manifest_sha256": data["manifest_sha256"], "prepared_study_manifest_sha256": data["manifest"]["prepared_study_manifest_sha256"], "source_encoder_artifact_sha256": data["manifest_sha256"], "architecture": {"family": ARTIFACT_FAMILY, "input_dimension": data["dimension"], "bit_count": bit_count, "encoder_activation": "affine_hard_step_document_median_v1", "decoder": "tied_transpose_tanh", "code_value_encoding": "zero_one", "input_transform": "clip_minus_one_one_v1"}, "training": {"seed": seed, "epochs": epochs, "batch_size": batch_size, "learning_rate": learning_rate, "objective": OBJECTIVE, "queries_or_qrels_used": True, "candidate_limit": FIXED_CANDIDATE_LIMIT, "margin": margin, "optimizer": {"id": "adamw", "weight_decay": 0.0}, "shuffle_recipe": {"id": "python_fisher_yates_sha256_seed_v1", "per_epoch": True}, "initialization": {"mode": "pca_median_document_only_v1", "source_artifact_sha256": data["manifest_sha256"], "source_family": "label_free_document_only_e5_v1", "itq_iterations": 0}, "calibration": {"policy": "per_bit_projection_median_v1", "source": "label_free_document_only_train_v1", "document_count": len(data["train_ids"]), "document_ids_sha256": canonical_ids_sha256(data["train_ids"])}, "teacher": {"id": data["manifest"]["embedding"]["model_id"], "revision": data["manifest"]["embedding"]["model_revision"], "normalized": data["manifest"]["embedding"]["normalized"]}, "supervision": {"qrels_sha256": data["output_hashes"]["evaluation_qrels"], "positive_qrels": "grade_gt_zero_v1"}, "query_split": {"id": QUERY_SPLIT_ID, "validation_fraction": validation_fraction, "train_query_ids_sha256": canonical_ids_sha256(train_query_ids), "validation_query_ids_sha256": canonical_ids_sha256(validation_query_ids), "train_query_count": len(train_query_ids), "validation_query_count": len(validation_query_ids)}, "hard_negative_mining": {"id": MINING_ID, "teacher": "normalized_e5_cosine", "negative_count_per_query": hard_negative_count, "positive_exclusion": "all_grade_gt_zero_v1", "path": negatives_path.name, "sha256": sha256_file(negatives_path), "canonical_sha256": canonical_json_sha256(negative_payload), "train_query_ids_sha256": canonical_ids_sha256(train_query_ids), "validation_query_ids_sha256": canonical_ids_sha256(validation_query_ids)}, "selection": {"id": SELECTION_ID, "candidate_limit": FIXED_CANDIDATE_LIMIT, "lexicographic_order": ["hard_code_health", "positive_qrels_query_coverage_at_512", "reranked_ndcg_at_10", "lower_occupancy_deviation", "earlier_epoch"], "selected_epoch": best["epoch"], "metrics": best["selection_metrics"], "hard_code_health": best["health"], "occupancy_deviation": best["occupancy_deviation"]}, "loss_weights": {"reconstruction": reconstruction_weight, "decorrelation": decorrelation_weight, "row_orthogonality": row_orthogonality_weight}, "torch_threads": torch_threads, "source_materialization_outputs_sha256": canonical_json_sha256(data["output_hashes"])}, "weights": {"encoder_weights": {"path": paths["encoder_weights"].name, "sha256": sha256_file(paths["encoder_weights"]), "shape": [bit_count, data["dimension"]], "layout": "row_major_out_by_in", "dtype": "float32_le"}, "encoder_bias": {"path": paths["encoder_bias"].name, "sha256": sha256_file(paths["encoder_bias"]), "shape": [bit_count], "dtype": "float32_le"}, "decoder_bias": {"path": paths["decoder_bias"].name, "sha256": sha256_file(paths["decoder_bias"]), "shape": [data["dimension"]], "dtype": "float32_le"}}}
    # PCA is built inside this trainer, so its provenance is a materialization,
    # not a fictitious source encoder artifact.
    del artifact["source_encoder_artifact_sha256"]
    initialization = artifact["training"]["initialization"]
    del initialization["source_artifact_sha256"]
    initialization["source_materialization_manifest_sha256"] = data["manifest_sha256"]
    artifact["training"]["teacher"].update({
        "query_prefix": data["manifest"]["embedding"]["query_prefix"],
        "document_prefix": data["manifest"]["embedding"]["document_prefix"],
    })
    artifact["training"]["hard_negative_mining"].update({
        "mined_negative_count_per_query": hard_negative_count,
        "consumed_negative_count_per_query": min(epochs * consumed_negatives_per_query, hard_negative_count),
        "sampling_policy": "epoch_indexed_without_replacement_multi_negative_v1",
    })
    artifact["training"]["held_out_exclusion"] = {
        "id": "external_excluded_document_ids_set_v1",
        "document_ids_set_sha256": excluded_document_ids_sha256,
    }
    (output_root / "artifact.json").write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    (output_root / "training-history.json").write_text(json.dumps(history, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return artifact


def run_self_test() -> int:
    try:
        train_ids, validation_ids = split_query_ids([f"q{index}" for index in range(100)], 42, 0.2)
        if set(train_ids).intersection(validation_ids) or not train_ids or not validation_ids:
            raise SupervisedTrainingError("split is not a partition")
        if split_query_ids([f"q{index}" for index in range(100)], 42, 0.2) != (train_ids, validation_ids):
            raise SupervisedTrainingError("split is not deterministic")
        try:
            rank_validation(query_ids=["q"], query_vectors=None, document_ids=[], document_vectors=None, positives={}, weights=None, bias=None, candidate_limit=128, numpy=None)
        except SupervisedTrainingError:
            pass
        else:
            raise SupervisedTrainingError("non-512 selection budget was accepted")
        healthy = {"epoch": 1, "health_passes": True, "occupancy_deviation": 0.1, "selection_metrics": {"positive_qrels_query_coverage_at_512": 0.4, "reranked_ndcg_at_10": 0.2}}
        weaker = {"epoch": 0, "health_passes": True, "occupancy_deviation": 0.0, "selection_metrics": {"positive_qrels_query_coverage_at_512": 0.3, "reranked_ndcg_at_10": 1.0}}
        if not select_better(healthy, weaker): raise SupervisedTrainingError("selection order is wrong")
    except SupervisedTrainingError as exc:
        print(f"self-test failed: {exc}", file=sys.stderr); return 1
    print("qrels-supervised NLB trainer self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--materialization-root", type=Path); parser.add_argument("--output-root", type=Path)
    parser.add_argument("--bit-count", type=int, default=128); parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--epochs", type=int, default=8); parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--learning-rate", type=float, default=1.0e-4); parser.add_argument("--margin", type=float, default=0.1)
    parser.add_argument("--validation-fraction", type=float, default=0.2); parser.add_argument("--hard-negative-count", type=int, default=64); parser.add_argument("--consumed-negatives-per-query", type=int, default=8)
    parser.add_argument("--torch-threads", type=int, default=18); parser.add_argument("--reconstruction-weight", type=float, default=0.01)
    parser.add_argument("--decorrelation-weight", type=float, default=0.01); parser.add_argument("--row-orthogonality-weight", type=float, default=0.001)
    parser.add_argument("--self-test", action="store_true"); args = parser.parse_args(argv)
    if args.self_test: return run_self_test()
    if args.materialization_root is None or args.output_root is None: parser.error("materialization-root and output-root are required")
    try:
        artifact = train(materialization_root=args.materialization_root, output_root=args.output_root, bit_count=args.bit_count, seed=args.seed, epochs=args.epochs, batch_size=args.batch_size, learning_rate=args.learning_rate, margin=args.margin, validation_fraction=args.validation_fraction, hard_negative_count=args.hard_negative_count, consumed_negatives_per_query=args.consumed_negatives_per_query, torch_threads=args.torch_threads, reconstruction_weight=args.reconstruction_weight, decorrelation_weight=args.decorrelation_weight, row_orthogonality_weight=args.row_orthogonality_weight)
    except SupervisedTrainingError as exc:
        print(f"train-nlb-qrels-supervised: {exc}", file=sys.stderr); return 1
    print(f"trained {artifact['architecture']['bit_count']}-bit qrels-supervised NLB")
    return 0


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
