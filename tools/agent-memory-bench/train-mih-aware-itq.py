#!/usr/bin/env python3
"""Train document-only ITQ refinements with a radius-one MIH work surrogate."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy

sys.dont_write_bytecode = True

TRAINER_ID = "agent-memory-cpp:mih-aware-itq-trainer"
TRAINER_VERSION = "v1"
ARTIFACT_FAMILY = "mih_aware_itq_v1"
REQUIREMENTS_LOCK = "requirements-learned-binary-adc-trainer.txt"


class TrainingError(RuntimeError):
    """Raised when the frozen document-only training contract is invalid."""


def load_base() -> Any:
    path = Path(__file__).with_name("train-learned-binary-adc.py")
    spec = importlib.util.spec_from_file_location("mih_aware_base", path)
    if spec is None or spec.loader is None:
        raise TrainingError("cannot load shared trainer helpers")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


base = load_base()


def sha256_file(path: Path) -> str:
    return base.sha256_file(path)


def source_hashes() -> dict[str, str]:
    paths = {
        "train-mih-aware-itq.py": Path(__file__),
        "train-learned-binary-adc.py": Path(__file__).with_name("train-learned-binary-adc.py"),
        REQUIREMENTS_LOCK: Path(__file__).with_name(REQUIREMENTS_LOCK),
    }
    return {name: sha256_file(path) for name, path in paths.items()}


def radius_one_collision_surrogate(soft: Any, band_count: int, band_width: int) -> Any:
    """Mean pair collision probability for exact-or-one-bit-flip band probes.

    This is a bounded document-only posting-work surrogate.  Direct unique union
    work is deliberately not inferred from it and is measured separately.
    """
    import torch
    if soft.shape[1] != band_count * band_width:
        raise TrainingError("MIH surrogate dimensions are incompatible")
    values = soft.reshape(len(soft), band_count, band_width)
    equal = values[:, None, :, :] * values[None, :, :, :] + (1.0 - values[:, None, :, :]) * (1.0 - values[None, :, :, :])
    different = values[:, None, :, :] * (1.0 - values[None, :, :, :]) + (1.0 - values[:, None, :, :]) * values[None, :, :, :]
    prefix = torch.cat((torch.ones_like(equal[..., :1]), torch.cumprod(equal, dim=3)[..., :-1]), dim=3)
    suffix = torch.cat((torch.cumprod(equal.flip(dims=(3,)), dim=3).flip(dims=(3,))[..., 1:], torch.ones_like(equal[..., :1])), dim=3)
    collision = equal.prod(dim=3) + (different * prefix * suffix).sum(dim=3)
    mask = ~torch.eye(len(values), dtype=torch.bool, device=values.device)
    return collision[mask].mean()


def train(args: Any) -> dict[str, Any]:
    if args.output_root.exists() or args.bit_count != args.band_count * args.band_width:
        raise TrainingError("output exists or MIH dimensions are invalid")
    if args.bit_count <= 0 or args.band_count <= 0 or args.band_width <= 1 or args.epochs <= 0 or args.batch_size < 4:
        raise TrainingError("MIH trainer integer arguments are invalid")
    numeric = (args.learning_rate, args.temperature, args.quantization_weight, args.orthogonality_weight, args.balance_weight, args.semantic_weight, args.mih_work_weight, args.validation_fraction)
    if any(not math.isfinite(value) or value < 0.0 for value in numeric) or args.learning_rate <= 0.0 or args.temperature <= 0.0 or not 0.0 < args.validation_fraction < 0.5:
        raise TrainingError("MIH trainer numeric arguments are invalid")
    base.verify_environment()
    import torch
    manifest, ids, vectors = base.load_materialization(args.materialization_root)
    if args.bit_count > vectors.shape[1]:
        raise TrainingError("bit count exceeds embedding dimension")
    train_indices, validation_indices = base.stable_split(ids, args.seed, args.validation_fraction)
    torch.set_num_threads(args.torch_threads)
    torch.manual_seed(args.seed)
    torch.use_deterministic_algorithms(True)
    initial_weight = base.itq_weights(vectors[train_indices], args.bit_count, args.seed, args.itq_iterations)
    initial_projection = numpy.asarray(vectors[train_indices], dtype=numpy.float32) @ initial_weight.T
    initial_threshold = -numpy.median(initial_projection, axis=0).astype(numpy.float32)
    weight = torch.nn.Parameter(torch.from_numpy(initial_weight.copy()))
    threshold = torch.from_numpy(initial_threshold.copy())
    optimizer = torch.optim.AdamW((weight,), lr=args.learning_rate, weight_decay=0.0)
    identity = torch.eye(args.bit_count, dtype=torch.float32)

    def batch(indices: list[int]) -> Any:
        return torch.from_numpy(numpy.asarray(vectors[indices], dtype=numpy.float32).copy())

    def losses(values: Any) -> tuple[Any, dict[str, Any]]:
        projected = values @ weight.T
        logits = projected + threshold
        soft = torch.sigmoid(args.temperature * logits)
        hard = (logits >= 0.0).to(values.dtype)
        code = soft + (hard - soft).detach()
        teacher = values @ values.T
        student = (code @ code.T) * (2.0 / args.bit_count) - 1.0
        mask = ~torch.eye(len(values), dtype=torch.bool)
        def normalize(scores: Any) -> Any:
            return (scores - scores.mean(dim=1, keepdim=True)) / scores.std(dim=1, keepdim=True, unbiased=False).clamp_min(1.0e-6)
        semantic = torch.mean((normalize(student)[mask] - normalize(teacher)[mask]) ** 2)
        quantization = torch.mean((torch.abs(logits) - 1.0) ** 2)
        orthogonality = torch.mean((weight @ weight.T - identity) ** 2)
        balance = torch.mean((soft.mean(dim=0) - 0.5) ** 2)
        work = radius_one_collision_surrogate(soft, args.band_count, args.band_width)
        total = args.semantic_weight * semantic + args.quantization_weight * quantization + args.orthogonality_weight * orthogonality + args.balance_weight * balance + args.mih_work_weight * work
        return total, {"semantic": semantic, "quantization": quantization, "orthogonality": orthogonality, "balance": balance, "mih_radius_one_collision_surrogate": work}

    def validation_loss() -> tuple[float, dict[str, float]]:
        totals: dict[str, float] = {}; count = 0
        for start in range(0, len(validation_indices), args.batch_size):
            values = batch(validation_indices[start:start + args.batch_size])
            total, parts = losses(values); size = len(values); count += size
            totals["total"] = totals.get("total", 0.0) + float(total) * size
            for name, value in parts.items(): totals[name] = totals.get(name, 0.0) + float(value) * size
        if not count: raise TrainingError("document validation split is empty")
        return totals["total"] / count, {name: value / count for name, value in totals.items()}

    best_loss = math.inf; best_parts: dict[str, float] | None = None; best_state: dict[str, Any] | None = None
    for epoch in range(args.epochs):
        permutation = base.epoch_permutation(train_indices, args.seed, epoch)
        for start in range(0, len(train_indices), args.batch_size):
            values = batch(permutation[start:start + args.batch_size])
            optimizer.zero_grad(set_to_none=True); total, _ = losses(values); total.backward(); optimizer.step()
        with torch.no_grad():
            current, parts = validation_loss()
            if current < best_loss:
                best_loss = current; best_parts = parts; best_state = {"weight": weight.detach().clone(), "threshold": threshold.detach().clone(), "epoch": epoch + 1}
            print(f"epoch {epoch + 1}/{args.epochs}: document-only validation loss {current:.8f}", flush=True)
    if best_state is None or best_parts is None: raise TrainingError("trainer did not select a document-only checkpoint")
    train_health = base.health(vectors, train_indices, best_state["weight"], best_state["threshold"], args.batch_size)
    validation_health = base.health(vectors, validation_indices, best_state["weight"], best_state["threshold"], args.batch_size)
    if train_health["unique_code_count"] < 2 or validation_health["unique_code_count"] < 2 or train_health["constant_bit_count"] or validation_health["constant_bit_count"]:
        raise TrainingError(f"MIH-aware code health rejected collapsed code: train={train_health}; validation={validation_health}")
    args.output_root.mkdir(parents=True)
    files = {"projection_weights": args.output_root / "projection-weights.f32", "thresholds": args.output_root / "thresholds.f32"}
    base.write_f32(files["projection_weights"], best_state["weight"]); base.write_f32(files["thresholds"], best_state["threshold"])
    descriptors = {
        "projection_weights": {"path": files["projection_weights"].name, "sha256": sha256_file(files["projection_weights"]), "shape": [args.bit_count, vectors.shape[1]], "layout": "row_major_out_by_in", "dtype": "float32_le"},
        "thresholds": {"path": files["thresholds"].name, "sha256": sha256_file(files["thresholds"]), "shape": [args.bit_count], "dtype": "float32_le"},
    }
    artifact = {
        "schema_version": 1, "trainer": {"id": TRAINER_ID, "version": TRAINER_VERSION, "source_files_sha256": source_hashes()},
        "input_materialization_manifest_sha256": sha256_file(args.materialization_root / "manifest.json"), "prepared_study_manifest_sha256": manifest["prepared_study_manifest_sha256"],
        "architecture": {"family": ARTIFACT_FAMILY, "input_dimension": vectors.shape[1], "bit_count": args.bit_count, "band_count": args.band_count, "band_width_bits": args.band_width, "input_transform": "identity_normalized_e5_v1", "document_quantizer": "learned_threshold_hard_step_v1"},
        "training": {"seed": args.seed, "epochs": args.epochs, "batch_size": args.batch_size, "learning_rate": args.learning_rate, "temperature": args.temperature, "itq_iterations": args.itq_iterations, "queries_or_qrels_used": False, "objective": "document_semantic_itq_quantization_radius_one_mih_work_surrogate_v1", "loss_weights": {"semantic": args.semantic_weight, "quantization": args.quantization_weight, "orthogonality": args.orthogonality_weight, "balance": args.balance_weight, "mih_work": args.mih_work_weight}, "validation": {"id": "stable_sha256_document_split_v1", "fraction": args.validation_fraction, "train_document_ids_sha256": base.canonical_ids_sha256([ids[index] for index in train_indices]), "validation_document_ids_sha256": base.canonical_ids_sha256([ids[index] for index in validation_indices]), "selected_epoch": best_state["epoch"], "selected_document_only_total_loss": best_loss, "selected_document_only_loss_components": best_parts}, "hard_code_health": {"train": train_health, "validation": validation_health}, "torch_threads": args.torch_threads},
        "weights": descriptors,
    }
    (args.output_root / "artifact.json").write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    vectors._mmap.close()
    return artifact


def self_test() -> int:
    import torch
    values = torch.full((4, 8), 0.5)
    result = radius_one_collision_surrogate(values, 2, 4)
    if not torch.isfinite(result) or float(result) <= 0.0:
        print("MIH-aware ITQ trainer self-test failed: surrogate", file=sys.stderr); return 1
    print("MIH-aware ITQ trainer self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--materialization-root", type=Path); parser.add_argument("--output-root", type=Path); parser.add_argument("--bit-count", type=int, default=256); parser.add_argument("--band-count", type=int, default=32); parser.add_argument("--band-width", type=int, default=8); parser.add_argument("--seed", type=int, default=52); parser.add_argument("--epochs", type=int, default=16); parser.add_argument("--batch-size", type=int, default=192); parser.add_argument("--learning-rate", type=float, default=1.0e-5); parser.add_argument("--temperature", type=float, default=4.0); parser.add_argument("--quantization-weight", type=float, default=0.1); parser.add_argument("--orthogonality-weight", type=float, default=0.05); parser.add_argument("--balance-weight", type=float, default=0.5); parser.add_argument("--semantic-weight", type=float, default=1.0); parser.add_argument("--mih-work-weight", type=float, default=0.05); parser.add_argument("--validation-fraction", type=float, default=0.2); parser.add_argument("--itq-iterations", type=int, default=50); parser.add_argument("--torch-threads", type=int, default=1); args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test()
        if args.materialization_root is None or args.output_root is None: parser.error("--materialization-root and --output-root are required")
        print(f"trained MIH-aware ITQ with {train(args)['architecture']['bit_count']} bits")
    except (TrainingError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"train-mih-aware-itq: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
