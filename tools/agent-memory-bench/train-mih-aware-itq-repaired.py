#!/usr/bin/env python3
"""Anchored, bipolar, calibration-only refinement of full-corpus ITQ."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()
TRAINER_ID = "agent-memory-cpp:mih-aware-itq-repaired-trainer"
REQUIREMENTS_LOCK = "requirements-learned-binary-adc-trainer.txt"


class TrainingError(RuntimeError):
    """Raised when the fixed repaired-control protocol is invalid."""


def load_base() -> Any:
    spec = importlib.util.spec_from_file_location("mih_aware_repaired_base", THIS.with_name("train-learned-binary-adc.py"))
    if spec is None or spec.loader is None: raise TrainingError("cannot load shared trainer helpers")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module); return module


base = load_base()


def source_hashes() -> dict[str, str]:
    names = (THIS.name, "train-learned-binary-adc.py", REQUIREMENTS_LOCK)
    return {name: base.sha256_file(THIS.with_name(name)) for name in names}


def train(args: Any) -> dict[str, Any]:
    if args.output_root.exists() or args.epochs <= 0 or args.batch_size < 4 or args.bit_count != 256 or args.anchor_weight <= 0.0 or args.learning_rate <= 0.0:
        raise TrainingError("repaired-control arguments are invalid")
    if any(not math.isfinite(value) or value < 0.0 for value in (args.learning_rate, args.anchor_weight, args.temperature)) or args.temperature <= 0.0:
        raise TrainingError("repaired-control numeric arguments are invalid")
    base.verify_environment(); import torch
    manifest, ids, vectors = base.load_materialization(args.materialization_root); require_count = len(ids) == 25000 and vectors.shape[1] == 384
    if not require_count: raise TrainingError("repaired-control calibration materialization differs")
    torch.set_num_threads(args.torch_threads); torch.manual_seed(args.seed); torch.use_deterministic_algorithms(True)
    initial_weight = base.itq_weights(vectors, args.bit_count, args.seed, args.itq_iterations); weight = torch.nn.Parameter(torch.from_numpy(initial_weight.copy())); initial = weight.detach().clone(); identity = torch.eye(args.bit_count, dtype=torch.float32); optimizer = torch.optim.AdamW((weight,), lr=args.learning_rate, weight_decay=0.0)
    thresholds = -numpy.median(numpy.asarray(vectors) @ initial_weight.T, axis=0).astype(numpy.float32)

    def recalibrate() -> numpy.ndarray:
        with torch.no_grad(): return -numpy.median(numpy.asarray(vectors) @ weight.detach().cpu().numpy().T, axis=0).astype(numpy.float32)

    def losses(values: Any, current_thresholds: numpy.ndarray) -> tuple[Any, dict[str, Any]]:
        logits = values @ weight.T + torch.from_numpy(current_thresholds); soft = torch.sigmoid(args.temperature * logits); hard = (logits >= 0.0).to(values.dtype); code = soft + (hard - soft).detach(); bipolar = 2.0 * code - 1.0
        teacher = values @ values.T; student = (bipolar @ bipolar.T) / args.bit_count; mask = ~torch.eye(len(values), dtype=torch.bool)
        def normalize(scores: Any) -> Any: return (scores - scores.mean(dim=1, keepdim=True)) / scores.std(dim=1, keepdim=True, unbiased=False).clamp_min(1.0e-6)
        semantic = torch.mean((normalize(student)[mask] - normalize(teacher)[mask]) ** 2); anchor = torch.mean((weight - initial) ** 2); orthogonality = torch.mean((weight @ weight.T - identity) ** 2)
        total = semantic + args.anchor_weight * anchor + args.orthogonality_weight * orthogonality
        return total, {"semantic_bipolar_hamming": semantic, "anchor_to_full_itq": anchor, "orthogonality": orthogonality}

    last_parts: dict[str, float] = {}
    for epoch in range(args.epochs):
        permutation = base.epoch_permutation(list(range(len(ids))), args.seed, epoch)
        totals: dict[str, float] = {}; count = 0
        for start in range(0, len(permutation), args.batch_size):
            values = torch.from_numpy(numpy.asarray(vectors[permutation[start:start + args.batch_size]], dtype=numpy.float32).copy()); optimizer.zero_grad(set_to_none=True); total, parts = losses(values, thresholds); total.backward(); optimizer.step(); count += len(values)
            for name, value in parts.items(): totals[name] = totals.get(name, 0.0) + float(value) * len(values)
        thresholds = recalibrate(); last_parts = {name: value / count for name, value in totals.items()}; print(f"epoch {epoch + 1}/{args.epochs}: calibrated document-only loss {sum(last_parts.values()):.8f}", flush=True)
    projection = weight.detach(); health = base.health(vectors, list(range(len(ids))), projection, torch.from_numpy(thresholds), args.batch_size)
    if health["constant_bit_count"] or health["unique_code_count"] < 2: raise TrainingError(f"repaired control collapsed: {health}")
    args.output_root.mkdir(parents=True); projection_path = args.output_root / "projection-weights.f32"; threshold_path = args.output_root / "thresholds.f32"; base.write_f32(projection_path, projection); base.write_f32(threshold_path, torch.from_numpy(thresholds))
    artifact = {"schema_version": 1, "trainer": {"id": TRAINER_ID, "version": "v1", "source_files_sha256": source_hashes()}, "input_materialization_manifest_sha256": base.sha256_file(args.materialization_root / "manifest.json"), "prepared_study_manifest_sha256": manifest["prepared_study_manifest_sha256"], "architecture": {"family": "mih_aware_itq_repaired_control_v1", "input_dimension": 384, "bit_count": 256, "band_count": 32, "band_width_bits": 8, "input_transform": "identity_normalized_e5_v1", "document_quantizer": "recalibrated_threshold_hard_step_v1"}, "training": {"seed": args.seed, "epochs": args.epochs, "batch_size": args.batch_size, "learning_rate": args.learning_rate, "temperature": args.temperature, "itq_iterations": args.itq_iterations, "torch_threads": args.torch_threads, "queries_or_qrels_used": False, "objective": "bipolar_hamming_semantic_full_itq_anchor_v1", "loss_weights": {"semantic_bipolar_hamming": 1.0, "anchor_to_full_itq": args.anchor_weight, "orthogonality": args.orthogonality_weight, "mih_work": 0.0}, "threshold_policy": "recalibrate_full_calibration_median_after_each_epoch", "checkpoint": {"policy": "fixed_final_epoch", "selected_epoch": args.epochs}, "hard_code_health": health, "final_document_only_loss_components": last_parts}, "weights": {"projection_weights": {"path": projection_path.name, "sha256": base.sha256_file(projection_path), "shape": [256, 384], "layout": "row_major_out_by_in", "dtype": "float32_le"}, "thresholds": {"path": threshold_path.name, "sha256": base.sha256_file(threshold_path), "shape": [256], "dtype": "float32_le"}}}
    (args.output_root / "artifact.json").write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"); vectors._mmap.close(); return artifact


def self_test() -> int:
    try:
        import torch
        values = torch.tensor([[0., 0.], [1., 0.]]); bipolar = 2.0 * values - 1.0; require = float((bipolar @ bipolar.T)[0, 0]) == 2.0 and float((bipolar @ bipolar.T)[0, 1]) == 0.0
        if not require: raise ValueError("bipolar similarity differs")
    except (ValueError, ImportError) as error: print(f"train-mih-aware-itq-repaired self-test failed: {error}", file=sys.stderr); return 1
    print("MIH-aware ITQ repaired trainer self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--materialization-root", type=Path); parser.add_argument("--output-root", type=Path); parser.add_argument("--seed", type=int, default=52); parser.add_argument("--epochs", type=int, default=8); parser.add_argument("--batch-size", type=int, default=192); parser.add_argument("--learning-rate", type=float, default=1.0e-5); parser.add_argument("--temperature", type=float, default=4.0); parser.add_argument("--anchor-weight", type=float, default=50.0); parser.add_argument("--orthogonality-weight", type=float, default=.05); parser.add_argument("--itq-iterations", type=int, default=50); parser.add_argument("--torch-threads", type=int, default=1); parser.add_argument("--bit-count", type=int, default=256); args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test()
        if args.materialization_root is None or args.output_root is None: parser.error("--materialization-root and --output-root are required")
        print(f"trained repaired MIH-aware ITQ with {train(args)['architecture']['bit_count']} bits")
    except (TrainingError, OSError, ValueError, json.JSONDecodeError) as error: print(f"train-mih-aware-itq-repaired: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
