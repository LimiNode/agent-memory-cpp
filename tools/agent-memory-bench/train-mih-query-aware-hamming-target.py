#!/usr/bin/env python3
"""Train a shared-W MIH encoder on train-query to relevant-passage pairs only.

This is deliberately a confirmatory-training primitive, not a dev evaluator.
Checkpoint selection sees a deterministic validation split of the MIRACL train
queries and compares its complete MIH funnel with the unmodified ITQ anchor.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()
FAMILY = "mih_query_aware_hamming_target_shared_w_v1"
OBJECTIVE = "shared_w_qrels_hamming_radius_target_with_mih_frontier_gate_v1"
TRAINER_ID = "agent-memory-cpp:mih-query-aware-hamming-target-trainer"
QUERY_SPLIT_ID = "stable_sha256_query_split_v1"
RADIUS = 56
BANDS = 16
HAMMING_LIMIT = 768
ADC_LIMIT = 256
WORK_MULTIPLIER = 1.05


class TrainingError(RuntimeError):
    """Raised when the query-aware train-only contract is invalid."""


def load_module(name: str, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, THIS.with_name(name))
    if spec is None or spec.loader is None:
        raise TrainingError(f"cannot load helper module: {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


nlb = load_module("train-nlb-qrels-supervised.py", "mih_query_aware_nlb_helpers")
banding = load_module("evaluate-mih-banding.py", "mih_query_aware_banding_helpers")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict[str, str]:
    names = (THIS.name, "train-nlb-qrels-supervised.py", "evaluate-mih-banding.py", "train-binary-autoencoder.py", "requirements-binary-autoencoder-trainer.txt")
    return {name: sha256(THIS.with_name(name)) for name in names}


def canonical_ids_sha256(ids: list[str]) -> str:
    return hashlib.sha256("".join(f"{value}\n" for value in sorted(ids)).encode("utf-8")).hexdigest()


def hard_codes(values: Any, weights: Any, thresholds: Any, numpy: Any) -> Any:
    return (numpy.clip(values, -1.0, 1.0) @ weights.T + thresholds) >= 0.0


def assert_parameters(args: Any) -> None:
    if (args.bit_count != 256 or args.epochs <= 0 or args.batch_size <= 0 or
            args.learning_rate <= 0.0 or args.itq_iterations <= 0 or
            args.hard_negative_count <= 0 or args.validation_fraction <= 0.0 or
            args.validation_fraction >= 0.5 or args.torch_threads <= 0 or
            args.anchor_weight <= 0.0 or args.positive_weight <= 0.0 or
            args.negative_weight <= 0.0 or args.positive_temperature <= 0.0 or
            args.negative_temperature <= 0.0 or args.negative_radius <= RADIUS or
            args.output_root.exists()):
        raise TrainingError("query-aware trainer arguments are invalid")


def paired_rows(data: dict[str, Any], query_ids: list[str]) -> tuple[list[int], list[int]]:
    query_positions = {value: index for index, value in enumerate(data["query_ids"])}
    document_positions = {value: index for index, value in enumerate(data["document_ids"])}
    query_rows: list[int] = []
    positive_rows: list[int] = []
    for query_id in query_ids:
        best = sorted(data["positive"][query_id], key=lambda doc: (-data["positive"][query_id][doc], doc))[0]
        query_rows.append(query_positions[query_id]); positive_rows.append(document_positions[best])
    return query_rows, positive_rows


def frontier(
    *, query_ids: list[str], data: dict[str, Any], weights: Any, thresholds: Any,
    numpy: Any,
) -> dict[str, float]:
    """Measure the selection frontier without examining held-out dev queries."""
    doc_codes = hard_codes(data["document_vectors"], weights, thresholds, numpy)
    query_codes = hard_codes(data["query_vectors"], weights, thresholds, numpy)
    projection = numpy.clip(data["document_vectors"], -1.0, 1.0) @ weights.T + thresholds
    query_projection = numpy.clip(data["query_vectors"], -1.0, 1.0) @ weights.T + thresholds
    index = banding.build_index(doc_codes, banding.band_ranges(weights.shape[0], BANDS))
    radii = banding.global_radius_schedule(RADIUS, BANDS)
    centers = banding.shared.conditional_centers(projection, doc_codes.astype(numpy.uint8), 2)
    positions = {value: index for index, value in enumerate(data["query_ids"])}
    documents = data["document_ids"]
    document_positions = {value: index for index, value in enumerate(documents)}
    positive_positions = {value: [document_positions[document] for document in sorted(data["positive"][value])] for value in query_ids}
    threshold_values: list[float] = []
    raw_values: list[float] = []
    hamming_values: list[float] = []
    adc_values: list[float] = []
    candidates: list[int] = []
    postings: list[int] = []
    for query_id in query_ids:
        row = positions[query_id]
        selected, probes = banding.candidate_union(index, query_codes[row], banding.band_ranges(weights.shape[0], BANDS), radii)
        visits = sum(
            int(index[band].get(key, numpy.empty(0, dtype=numpy.int32)).size)
            for band, ((start, stop), radius) in enumerate(zip(banding.band_ranges(weights.shape[0], BANDS), radii))
            for key in banding.probe_keys(banding.band_key(query_codes[row], start, stop), stop - start, radius)
        )
        restricted = banding.stable_hamming_order(doc_codes, query_codes[row], documents, selected)[:HAMMING_LIMIT]
        second = banding.binary_adc_order(query_projection[row], centers, doc_codes, documents, restricted)[:ADC_LIMIT]
        positives = numpy.asarray(positive_positions[query_id], dtype=numpy.intp)
        distances = numpy.count_nonzero(doc_codes[positives] != query_codes[row], axis=1)
        threshold_values.append(float(numpy.mean(distances <= RADIUS)))
        raw_values.append(float(numpy.mean(numpy.isin(positives, selected))))
        hamming_values.append(float(numpy.mean(numpy.isin(positives, restricted))))
        adc_values.append(float(numpy.mean(numpy.isin(positives, second))))
        candidates.append(int(selected.size)); postings.append(int(visits))
        if probes <= 0:
            raise TrainingError("MIH frontier produced no probes")
    return {
        "positive_hamming_within_radius": float(numpy.mean(threshold_values)),
        "positive_raw_union_survival": float(numpy.mean(raw_values)),
        "positive_hamming_k1_survival": float(numpy.mean(hamming_values)),
        "positive_adc_k2_survival": float(numpy.mean(adc_values)),
        "mean_candidates_per_query": float(numpy.mean(candidates)),
        "mean_posting_visits_per_query": float(numpy.mean(postings)),
    }


def gate(candidate: dict[str, float], baseline: dict[str, float]) -> bool:
    survival_signal = (
        candidate["positive_adc_k2_survival"] > baseline["positive_adc_k2_survival"]
        or (
            candidate["positive_raw_union_survival"] > baseline["positive_raw_union_survival"]
            and candidate["positive_hamming_k1_survival"] > baseline["positive_hamming_k1_survival"]
        )
    )
    return (
        survival_signal
        and candidate["mean_candidates_per_query"] <= baseline["mean_candidates_per_query"] * WORK_MULTIPLIER
        and candidate["mean_posting_visits_per_query"] <= baseline["mean_posting_visits_per_query"] * WORK_MULTIPLIER
    )


def select_better(candidate: dict[str, Any], best: dict[str, Any] | None) -> bool:
    if best is None:
        return True
    return (
        candidate["health_passes"], candidate["frontier_gate_passes"],
        candidate["frontier"]["positive_adc_k2_survival"],
        candidate["frontier"]["positive_hamming_k1_survival"],
        candidate["frontier"]["positive_raw_union_survival"],
        candidate["frontier"]["positive_hamming_within_radius"],
        -candidate["frontier"]["mean_candidates_per_query"],
        -candidate["frontier"]["mean_posting_visits_per_query"], -candidate["epoch"],
    ) > (
        best["health_passes"], best["frontier_gate_passes"],
        best["frontier"]["positive_adc_k2_survival"],
        best["frontier"]["positive_hamming_k1_survival"],
        best["frontier"]["positive_raw_union_survival"],
        best["frontier"]["positive_hamming_within_radius"],
        -best["frontier"]["mean_candidates_per_query"],
        -best["frontier"]["mean_posting_visits_per_query"], -best["epoch"],
    )


def train(args: Any) -> dict[str, Any]:
    assert_parameters(args)
    base = nlb.load_base()
    try:
        base.verify_environment()
        import numpy
        import torch
        import torch.nn.functional as functional
    except (ImportError, base.TrainingError) as error:
        raise TrainingError(f"pinned training environment is required: {error}") from error
    data = nlb.load_supervised_materialization(args.materialization_root, base, numpy)
    try:
        prepared = json.loads((args.materialization_root / "prepared-study-manifest.json").read_text(encoding="utf-8"))
        split = prepared["split"]
        excluded_digest = split["external_excluded_document_ids_set_sha256"]
        if (split["purpose"] != "retrieval_training" or split["qrels_usage"] != "retrieval_training_only" or
                not isinstance(excluded_digest, str) or len(excluded_digest) != 64):
            raise ValueError("prepared study is not train-only retrieval material")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise TrainingError(f"train-only materialization provenance is invalid: {error}") from error
    if args.bit_count > data["dimension"]:
        raise TrainingError("bit count exceeds source dimension")
    train_ids, validation_ids = nlb.split_query_ids(data["query_ids"], args.seed, args.validation_fraction)
    negatives = nlb.mine_hard_negatives(
        query_ids=data["query_ids"], query_vectors=data["query_vectors"], document_ids=data["document_ids"],
        document_vectors=data["document_vectors"], positives=data["positive"], count=args.hard_negative_count, numpy=numpy,
    )
    document_rows = {value: index for index, value in enumerate(data["document_ids"])}
    query_rows, positive_rows = paired_rows(data, train_ids)
    torch.set_num_threads(args.torch_threads); torch.manual_seed(args.seed); torch.use_deterministic_algorithms(True)
    initial_weights, _ = nlb.initialize_itq_median(numpy.asarray(data["train_vectors"]), args.bit_count, args.seed, args.itq_iterations, numpy)
    weight = torch.nn.Parameter(torch.from_numpy(initial_weights.copy()))
    initial = weight.detach().clone(); identity = torch.eye(args.bit_count, dtype=torch.float32)
    optimizer = torch.optim.AdamW((weight,), lr=args.learning_rate, weight_decay=0.0)

    def thresholds_for(current: Any) -> Any:
        values = numpy.clip(data["train_vectors"], -1.0, 1.0) @ current.detach().cpu().numpy().T
        return (-numpy.median(values, axis=0)).astype(numpy.float32)

    thresholds = thresholds_for(weight)
    baseline = frontier(query_ids=validation_ids, data=data, weights=initial_weights, thresholds=thresholds, numpy=numpy)
    history: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None

    def checkpoint(epoch: int) -> None:
        nonlocal best
        weights = weight.detach().cpu().numpy().copy()
        health = base.hard_code_health(vectors=data["train_vectors"], indices=list(range(len(data["train_ids"]))), encoder_weight=weight, encoder_bias=torch.from_numpy(thresholds), batch_size=args.batch_size, clip_inputs=True)
        current = frontier(query_ids=validation_ids, data=data, weights=weights, thresholds=thresholds, numpy=numpy)
        record = {"epoch": epoch, "hard_code_health": health, "health_passes": health["unique_code_count"] >= 2 and health["constant_bit_fraction"] <= 0.10, "frontier": current, "frontier_gate_passes": gate(current, baseline), "weights": weights, "thresholds": thresholds.copy()}
        history.append({key: value for key, value in record.items() if key not in ("weights", "thresholds")})
        if select_better(record, best):
            best = record

    checkpoint(-1)
    for epoch in range(args.epochs):
        order = list(range(len(train_ids)))
        rng = numpy.random.default_rng(int.from_bytes(hashlib.sha256(f"{args.seed}\0query-aware-hamming\0{epoch}".encode()).digest()[:16], "big"))
        rng.shuffle(order)
        for start in range(0, len(order), args.batch_size):
            rows = order[start:start + args.batch_size]
            selected_query_rows = [query_rows[row] for row in rows]
            selected_positive_rows = [positive_rows[row] for row in rows]
            selected_negative_rows = [document_rows[negatives[train_ids[row]][epoch % args.hard_negative_count]] for row in rows]
            query = torch.from_numpy(numpy.asarray(data["query_vectors"][selected_query_rows], dtype=numpy.float32).copy())
            positive = torch.from_numpy(numpy.asarray(data["document_vectors"][selected_positive_rows], dtype=numpy.float32).copy())
            negative = torch.from_numpy(numpy.asarray(data["document_vectors"][selected_negative_rows], dtype=numpy.float32).copy())
            bias = torch.from_numpy(thresholds)
            def bipolar(values: Any) -> Any:
                soft = torch.tanh(torch.clamp(values, -1.0, 1.0) @ weight.T + bias)
                hard = torch.where(soft >= 0.0, torch.ones_like(soft), -torch.ones_like(soft))
                return soft + (hard - soft).detach()
            q_code, positive_code, negative_code = bipolar(query), bipolar(positive), bipolar(negative)
            positive_distance = 0.5 * (args.bit_count - torch.sum(q_code * positive_code, dim=1))
            negative_distance = 0.5 * (args.bit_count - torch.sum(q_code * negative_code, dim=1))
            positive_loss = functional.softplus((positive_distance - RADIUS) / args.positive_temperature).mean()
            negative_loss = functional.softplus((args.negative_radius - negative_distance) / args.negative_temperature).mean()
            anchor = torch.mean((weight - initial) ** 2)
            orthogonality = torch.mean((weight @ weight.T - identity) ** 2)
            loss = args.positive_weight * positive_loss + args.negative_weight * negative_loss + args.anchor_weight * anchor + args.orthogonality_weight * orthogonality
            optimizer.zero_grad(set_to_none=True); loss.backward(); optimizer.step()
        thresholds = thresholds_for(weight)
        checkpoint(epoch)
    if best is None or not best["health_passes"]:
        raise TrainingError("no checkpoint passed code-health requirements")
    args.output_root.mkdir(parents=True)
    weights_path = args.output_root / "projection-weights.f32"; thresholds_path = args.output_root / "thresholds.f32"
    base.write_f32(weights_path, best["weights"]); base.write_f32(thresholds_path, best["thresholds"])
    artifact = {
        "schema_version": 1,
        "trainer": {"id": TRAINER_ID, "version": "v1", "source_files_sha256": source_hashes()},
        "input_materialization_manifest_sha256": data["manifest_sha256"],
        "prepared_study_manifest_sha256": data["manifest"]["prepared_study_manifest_sha256"],
        "architecture": {"family": FAMILY, "input_dimension": data["dimension"], "bit_count": args.bit_count, "band_count": BANDS, "band_width_bits": 16, "shared_projection": True, "input_transform": "clip_minus_one_one_normalized_e5_v1", "document_quantizer": "recalibrated_train_document_median_hard_step_v1"},
        "training": {"seed": args.seed, "epochs": args.epochs, "batch_size": args.batch_size, "learning_rate": args.learning_rate, "itq_iterations": args.itq_iterations, "torch_threads": args.torch_threads, "queries_or_qrels_used": True, "objective": OBJECTIVE, "positive_radius": RADIUS, "negative_radius": args.negative_radius, "threshold_policy": "recalibrate_train_document_median_after_each_epoch", "loss_weights": {"positive_radius": args.positive_weight, "negative_radius": args.negative_weight, "anchor_to_full_itq": args.anchor_weight, "orthogonality": args.orthogonality_weight}, "query_split": {"id": QUERY_SPLIT_ID, "validation_fraction": args.validation_fraction, "train_query_ids_sha256": canonical_ids_sha256(train_ids), "validation_query_ids_sha256": canonical_ids_sha256(validation_ids), "train_query_count": len(train_ids), "validation_query_count": len(validation_ids)}, "hard_negative_mining": {"id": nlb.MINING_ID, "negative_count_per_query": args.hard_negative_count, "teacher": "normalized_e5_cosine", "positive_exclusion": "all_grade_gt_zero_v1"}, "checkpoint": {"policy": "train_query_validation_mih_frontier_gate_v1", "baseline": baseline, "work_multiplier": WORK_MULTIPLIER, "survival_signal": "strict_adc_improvement_or_strict_raw_and_hamming_k1_improvement_v1", "selected_epoch": best["epoch"], "selected_frontier": best["frontier"], "gate_passed": best["frontier_gate_passes"], "hard_code_health": best["hard_code_health"]}, "held_out_exclusion": {"id": "external_excluded_document_ids_set_v1", "document_ids_set_sha256": excluded_digest}},
        "weights": {"projection_weights": {"path": weights_path.name, "sha256": sha256(weights_path), "shape": [args.bit_count, data["dimension"]], "layout": "row_major_out_by_in", "dtype": "float32_le"}, "thresholds": {"path": thresholds_path.name, "sha256": sha256(thresholds_path), "shape": [args.bit_count], "dtype": "float32_le"}},
    }
    (args.output_root / "artifact.json").write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    (args.output_root / "training-history.json").write_text(json.dumps(history, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return artifact


def self_test() -> int:
    baseline = {"positive_adc_k2_survival": .2, "positive_raw_union_survival": .2, "positive_hamming_k1_survival": .2, "mean_candidates_per_query": 100., "mean_posting_visits_per_query": 100.}
    accepted = {"positive_adc_k2_survival": .2, "positive_raw_union_survival": .21, "positive_hamming_k1_survival": .21, "mean_candidates_per_query": 105., "mean_posting_visits_per_query": 105.}
    rejected = {"positive_adc_k2_survival": .21, "positive_raw_union_survival": .2, "positive_hamming_k1_survival": .2, "mean_candidates_per_query": 106., "mean_posting_visits_per_query": 100.}
    if not gate(accepted, baseline) or gate(rejected, baseline):
        print("train-mih-query-aware-hamming-target self-test failed: frontier gate differs", file=sys.stderr); return 1
    print("MIH query-aware Hamming-target trainer self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--materialization-root", type=Path); parser.add_argument("--output-root", type=Path); parser.add_argument("--seed", type=int, default=52); parser.add_argument("--epochs", type=int, default=4); parser.add_argument("--batch-size", type=int, default=192); parser.add_argument("--learning-rate", type=float, default=1.0e-5); parser.add_argument("--itq-iterations", type=int, default=50); parser.add_argument("--hard-negative-count", type=int, default=4); parser.add_argument("--validation-fraction", type=float, default=.10); parser.add_argument("--positive-radius", dest="positive_radius", type=int, default=RADIUS); parser.add_argument("--negative-radius", type=int, default=80); parser.add_argument("--positive-temperature", type=float, default=4.0); parser.add_argument("--negative-temperature", type=float, default=4.0); parser.add_argument("--positive-weight", type=float, default=1.0); parser.add_argument("--negative-weight", type=float, default=1.0); parser.add_argument("--anchor-weight", type=float, default=50.0); parser.add_argument("--orthogonality-weight", type=float, default=.05); parser.add_argument("--torch-threads", type=int, default=1); parser.add_argument("--bit-count", type=int, default=256); args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test()
        if args.materialization_root is None or args.output_root is None or args.positive_radius != RADIUS: parser.error("--materialization-root, --output-root and --positive-radius 56 are required")
        print(f"trained query-aware MIH Hamming-target artifact: {train(args)['architecture']['family']}")
    except (TrainingError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"train-mih-query-aware-hamming-target: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
