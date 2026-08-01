#!/usr/bin/env python3
"""Fine-tune median NLB codes against document-only retrieval geometry."""

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

TRAINER_ID = "agent-memory-cpp:nlb-retrieval-finetuner"
TRAINER_VERSION = "v1"
OBJECTIVE = "document_geometry_distillation_v1"
BASE_TRAINER_FILE = "train-binary-autoencoder.py"


class FineTuneError(RuntimeError):
    """Raised when fine-tuning inputs, options, or outputs violate the contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_ids_sha256(ids: list[str]) -> str:
    return hashlib.sha256(
        "".join(f"{identifier}\n" for identifier in ids).encode("utf-8")
    ).hexdigest()


def load_base_trainer() -> Any:
    path = Path(__file__).resolve().parent / BASE_TRAINER_FILE
    spec = importlib.util.spec_from_file_location("agent_memory_base_ae_trainer", path)
    if spec is None or spec.loader is None:
        raise FineTuneError(f"cannot load base trainer module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require_finite_nonnegative(value: float, name: str) -> float:
    if not math.isfinite(value) or value < 0.0:
        raise FineTuneError(f"{name} must be finite and non-negative")
    return value


def temperature_for_epoch(start: float, finish: float, epoch: int, epochs: int) -> float:
    if start <= 0.0 or finish <= 0.0 or not math.isfinite(start) or not math.isfinite(finish):
        raise FineTuneError("soft-code temperatures must be finite and positive")
    if epochs <= 1:
        return finish
    fraction = epoch / (epochs - 1)
    return start * ((finish / start) ** fraction)


def load_weight(
    *, root: Path, descriptor: dict[str, Any], expected_shape: list[int], numpy: Any
) -> Any:
    if descriptor.get("dtype") != "float32_le" or descriptor.get("shape") != expected_shape:
        raise FineTuneError("initialization weight descriptor mismatch")
    relative = Path(str(descriptor.get("path", "")))
    if relative.is_absolute() or relative.name != str(relative) or str(relative) == ".":
        raise FineTuneError("initialization weight path must be a plain file name")
    path = root / relative
    expected_hash = descriptor.get("sha256")
    if not isinstance(expected_hash, str) or sha256_file(path) != expected_hash:
        raise FineTuneError(f"initialization weight SHA-256 mismatch: {relative}")
    values = numpy.fromfile(path, dtype="<f4")
    expected_count = math.prod(expected_shape)
    if values.size != expected_count or not numpy.isfinite(values).all():
        raise FineTuneError(f"invalid initialization weight payload: {relative}")
    return values.reshape(expected_shape).copy()


def load_initialization(
    *, artifact_path: Path, materialization_hash: str, dimension: int, bit_count: int, numpy: Any
) -> tuple[dict[str, Any], Any, Any, Any]:
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FineTuneError(f"cannot load initialization artifact: {exc}") from exc
    architecture = artifact.get("architecture")
    if not isinstance(architecture, dict) or architecture.get("family") != "nlb_median_threshold_v1":
        raise FineTuneError("initialization artifact must be nlb_median_threshold_v1")
    if architecture.get("input_dimension") != dimension or architecture.get("bit_count") != bit_count:
        raise FineTuneError("initialization artifact dimensions disagree with requested training")
    if artifact.get("input_materialization_manifest_sha256") != materialization_hash:
        raise FineTuneError("initialization artifact materialization identity mismatch")
    weights = artifact.get("weights")
    if not isinstance(weights, dict):
        raise FineTuneError("initialization artifact weights must be an object")
    root = artifact_path.parent
    encoder_weight = load_weight(
        root=root, descriptor=weights.get("encoder_weights", {}),
        expected_shape=[bit_count, dimension], numpy=numpy,
    )
    encoder_bias = load_weight(
        root=root, descriptor=weights.get("encoder_bias", {}),
        expected_shape=[bit_count], numpy=numpy,
    )
    decoder_bias = load_weight(
        root=root, descriptor=weights.get("decoder_bias", {}),
        expected_shape=[dimension], numpy=numpy,
    )
    return artifact, encoder_weight, encoder_bias, decoder_bias


def itq_initialization(
    *, values: Any, bit_count: int, seed: int, iterations: int, numpy: Any
) -> tuple[Any, Any]:
    if bit_count > values.shape[1] or iterations <= 0:
        raise FineTuneError("ITQ initialization requires bit_count <= dimension and iterations > 0")
    centered = values.astype(numpy.float64, copy=True)
    mean = centered.mean(axis=0)
    centered -= mean
    covariance = (centered.T @ centered) / max(1, centered.shape[0])
    eigenvalues, eigenvectors = numpy.linalg.eigh(covariance)
    components = eigenvectors[:, numpy.argsort(eigenvalues)[-bit_count:]]
    projected = centered @ components
    generator = numpy.random.default_rng(seed)
    rotation, _ = numpy.linalg.qr(generator.standard_normal((bit_count, bit_count)))
    for _ in range(iterations):
        binary = numpy.where(projected @ rotation >= 0.0, 1.0, -1.0)
        left, _, right = numpy.linalg.svd(projected.T @ binary, full_matrices=False)
        rotation = left @ right
    directions = components @ rotation
    encoder_weight = directions.T.astype(numpy.float32)
    encoder_bias = (-numpy.median(values @ encoder_weight.T, axis=0)).astype(numpy.float32)
    return encoder_weight, encoder_bias


def validate_source_split(
    *, source: dict[str, Any], ids: list[str], train_indices: list[int],
    validation_indices: list[int], train_ids_path: Path | None,
    validation_ids_path: Path | None,
) -> dict[str, Any]:
    training = source.get("training")
    if not isinstance(training, dict):
        raise FineTuneError("initialization artifact training metadata is missing")
    train_ids = [ids[index] for index in train_indices]
    validation_ids = [ids[index] for index in validation_indices]
    if "stable_id_lists" in training:
        descriptor = training["stable_id_lists"]
        if (not isinstance(descriptor, dict) or
                descriptor.get("train_sha256") != canonical_ids_sha256(train_ids) or
                descriptor.get("validation_sha256") != canonical_ids_sha256(validation_ids)):
            raise FineTuneError("selected split differs from initialization artifact stable IDs")
        return {
            "stable_id_lists": {
                "selection": "stable_sha256_id_split_v1",
                "train_sha256": canonical_ids_sha256(train_ids),
                "validation_sha256": canonical_ids_sha256(validation_ids),
            }
        }
    descriptor = training.get("explicit_id_lists")
    if (not isinstance(descriptor, dict) or train_ids_path is None or
            validation_ids_path is None or
            descriptor.get("train_sha256") != sha256_file(train_ids_path) or
            descriptor.get("validation_sha256") != sha256_file(validation_ids_path)):
        raise FineTuneError("selected split differs from initialization artifact explicit IDs")
    return {
        "explicit_id_lists": {
            "selection": "external_canonical_id_lists_v1",
            "train_sha256": sha256_file(train_ids_path),
            "validation_sha256": sha256_file(validation_ids_path),
        }
    }


def train(
    *, materialization_root: Path, initialization_artifact: Path, output_root: Path,
    bit_count: int, seed: int, epochs: int, batch_size: int, learning_rate: float,
    validation_fraction: float, reconstruction_weight: float,
    decorrelation_weight: float, distillation_weight: float,
    row_orthogonality_weight: float, teacher_temperature: float,
    student_temperature: float, soft_temperature_start: float,
    soft_temperature_end: float, initialization_mode: str, itq_iterations: int,
    torch_threads: int, train_ids_path: Path | None = None,
    validation_ids_path: Path | None = None,
) -> dict[str, Any]:
    if output_root.exists():
        raise FineTuneError(f"output directory already exists: {output_root}")
    if min(bit_count, epochs, batch_size, torch_threads) <= 0 or learning_rate <= 0.0:
        raise FineTuneError("bit_count, epochs, batch_size, threads, and learning_rate must be positive")
    if not 0.0 < validation_fraction < 0.5:
        raise FineTuneError("validation_fraction must be in (0, 0.5)")
    for value, name in (
        (reconstruction_weight, "reconstruction_weight"),
        (decorrelation_weight, "decorrelation_weight"),
        (distillation_weight, "distillation_weight"),
        (row_orthogonality_weight, "row_orthogonality_weight"),
    ):
        require_finite_nonnegative(value, name)
    if teacher_temperature <= 0.0 or student_temperature <= 0.0:
        raise FineTuneError("distillation temperatures must be positive")
    if initialization_mode not in ("median_artifact", "itq_median"):
        raise FineTuneError("unsupported initialization mode")

    base = load_base_trainer()
    try:
        base.verify_environment()
        import numpy
        import torch
        import torch.nn.functional as functional
    except (ImportError, base.TrainingError) as exc:
        raise FineTuneError(f"fine-tuning requires the pinned autoencoder environment: {exc}") from exc

    ids, vectors_path, dimension, prepared_study_hash = base.load_materialization(
        materialization_root
    )
    materialization_hash = sha256_file(materialization_root / "manifest.json")
    if (train_ids_path is None) != (validation_ids_path is None):
        raise FineTuneError("explicit train and validation ID lists must be supplied together")
    if train_ids_path is None:
        validation_indices = [
            index for index, identifier in enumerate(ids)
            if base.stable_split(identifier, seed, validation_fraction)
        ]
        train_indices = [
            index for index, identifier in enumerate(ids)
            if not base.stable_split(identifier, seed, validation_fraction)
        ]
    else:
        train_indices, validation_indices = base.select_explicit_indices(
            ids, train_ids_path, validation_ids_path
        )
    if not train_indices or not validation_indices:
        raise FineTuneError("document-only train/validation split is empty")
    vectors = numpy.memmap(vectors_path, dtype="<f4", mode="r", shape=(len(ids), dimension))
    source, initial_weight, initial_bias, initial_decoder_bias = load_initialization(
        artifact_path=initialization_artifact,
        materialization_hash=materialization_hash,
        dimension=dimension,
        bit_count=bit_count,
        numpy=numpy,
    )
    split_provenance = validate_source_split(
        source=source, ids=ids, train_indices=train_indices,
        validation_indices=validation_indices, train_ids_path=train_ids_path,
        validation_ids_path=validation_ids_path,
    )
    if initialization_mode == "itq_median":
        initial_weight, initial_bias = itq_initialization(
            values=numpy.asarray(vectors[train_indices], dtype=numpy.float32),
            bit_count=bit_count, seed=seed, iterations=itq_iterations, numpy=numpy,
        )

    torch.set_num_threads(torch_threads)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    encoder_weight = torch.nn.Parameter(torch.from_numpy(initial_weight.copy()))
    encoder_bias = torch.nn.Parameter(torch.from_numpy(initial_bias.copy()))
    decoder_bias = torch.nn.Parameter(torch.from_numpy(initial_decoder_bias.copy()))
    optimizer = torch.optim.AdamW(
        [encoder_weight, encoder_bias, decoder_bias], lr=learning_rate, weight_decay=0.0
    )

    def batch_tensor(indices: list[int]) -> Any:
        return torch.from_numpy(numpy.asarray(vectors[indices], dtype=numpy.float32).copy())

    def loss_for(values: Any, temperature: float) -> tuple[Any, dict[str, Any]]:
        clipped = torch.clamp(values, min=-1.0, max=1.0)
        logits = clipped @ encoder_weight.T + encoder_bias
        soft_code = torch.tanh(temperature * logits)
        hard_code = torch.where(
            soft_code >= 0.0, torch.ones_like(soft_code), -torch.ones_like(soft_code)
        )
        straight_code = soft_code + (hard_code - soft_code).detach()
        reconstruction = torch.tanh(((straight_code + 1.0) * 0.5) @ encoder_weight + decoder_bias)
        reconstruction_loss = torch.mean((reconstruction - clipped) ** 2)

        decorrelation_loss = torch.zeros((), dtype=clipped.dtype)
        if decorrelation_weight != 0.0 and len(values) > 1:
            centered = soft_code - torch.mean(soft_code, dim=0, keepdim=True)
            covariance = centered.T @ centered / len(values)
            standard_deviation = torch.sqrt(torch.diagonal(covariance).clamp_min(1.0e-6))
            correlation = covariance / (
                standard_deviation[:, None] * standard_deviation[None, :]
            )
            decorrelation_loss = (
                torch.sum(correlation ** 2) - torch.sum(torch.diagonal(correlation) ** 2)
            ) / max(1, bit_count * (bit_count - 1))

        distillation_loss = torch.zeros((), dtype=clipped.dtype)
        if distillation_weight != 0.0 and len(values) > 1:
            teacher_vectors = functional.normalize(clipped, dim=1)
            teacher_scores = teacher_vectors @ teacher_vectors.T
            student_scores = (soft_code @ soft_code.T) / bit_count
            diagonal_mask = torch.eye(len(values), dtype=torch.bool, device=values.device)
            teacher_scores = teacher_scores.masked_fill(diagonal_mask, -1.0e9)
            student_scores = student_scores.masked_fill(diagonal_mask, -1.0e9)
            teacher_distribution = functional.softmax(
                teacher_scores / teacher_temperature, dim=1
            )
            distillation_loss = functional.kl_div(
                functional.log_softmax(student_scores / student_temperature, dim=1),
                teacher_distribution,
                reduction="batchmean",
            )

        row_orthogonality_loss = torch.zeros((), dtype=clipped.dtype)
        if row_orthogonality_weight != 0.0:
            gram = encoder_weight @ encoder_weight.T
            identity = torch.eye(bit_count, dtype=gram.dtype, device=gram.device)
            row_orthogonality_loss = torch.mean((gram - identity) ** 2)

        total = (
            reconstruction_weight * reconstruction_loss
            + decorrelation_weight * decorrelation_loss
            + distillation_weight * distillation_loss
            + row_orthogonality_weight * row_orthogonality_loss
        )
        return total, {
            "reconstruction": reconstruction_loss,
            "decorrelation": decorrelation_loss,
            "distillation": distillation_loss,
            "row_orthogonality": row_orthogonality_loss,
        }

    best_loss = float("inf")
    best_state: dict[str, Any] | None = None
    for epoch in range(epochs):
        temperature = temperature_for_epoch(
            soft_temperature_start, soft_temperature_end, epoch, epochs
        )
        epoch_indices = base.deterministic_epoch_permutation(train_indices, seed, epoch)
        for start in range(0, len(epoch_indices), batch_size):
            values = batch_tensor(epoch_indices[start:start + batch_size])
            optimizer.zero_grad(set_to_none=True)
            loss, _ = loss_for(values, temperature)
            loss.backward()
            optimizer.step()
        with torch.no_grad():
            validation_loss = 0.0
            validation_components = {
                "reconstruction": 0.0,
                "decorrelation": 0.0,
                "distillation": 0.0,
                "row_orthogonality": 0.0,
            }
            validation_rows = 0
            for start in range(0, len(validation_indices), batch_size):
                indices = validation_indices[start:start + batch_size]
                loss, components = loss_for(batch_tensor(indices), temperature)
                validation_loss += float(loss) * len(indices)
                for name, value in components.items():
                    validation_components[name] += float(value) * len(indices)
                validation_rows += len(indices)
            validation_loss /= validation_rows
            for name in validation_components:
                validation_components[name] /= validation_rows
        if validation_loss < best_loss:
            best_loss = validation_loss
            best_state = {
                "epoch": epoch,
                "temperature": temperature,
                "validation_components": validation_components,
                "encoder_weight": encoder_weight.detach().clone(),
                "encoder_bias": encoder_bias.detach().clone(),
                "decoder_bias": decoder_bias.detach().clone(),
            }
    if best_state is None:
        raise FineTuneError("fine-tuner did not produce an artifact")

    train_health = base.hard_code_health(
        vectors=vectors, indices=train_indices,
        encoder_weight=best_state["encoder_weight"],
        encoder_bias=best_state["encoder_bias"], batch_size=batch_size,
        clip_inputs=True,
    )
    base.require_noncollapsed_hard_codes(train_health, "training")
    validation_health = base.hard_code_health(
        vectors=vectors, indices=validation_indices,
        encoder_weight=best_state["encoder_weight"],
        encoder_bias=best_state["encoder_bias"], batch_size=batch_size,
        clip_inputs=True,
    )

    output_root.mkdir(parents=True)
    output_weights = {
        "encoder_weights": output_root / "encoder-weights.f32",
        "encoder_bias": output_root / "encoder-bias.f32",
        "decoder_bias": output_root / "decoder-bias.f32",
    }
    base.write_f32(output_weights["encoder_weights"], best_state["encoder_weight"])
    base.write_f32(output_weights["encoder_bias"], best_state["encoder_bias"])
    base.write_f32(output_weights["decoder_bias"], best_state["decoder_bias"])
    source_hash = sha256_file(initialization_artifact)
    artifact = {
        "schema_version": 1,
        "trainer": {
            "id": TRAINER_ID,
            "version": TRAINER_VERSION,
            "source_hash": sha256_file(Path(__file__)),
            "base_trainer_source_hash": sha256_file(
                Path(__file__).resolve().parent / BASE_TRAINER_FILE
            ),
            "requirements_lock": source["trainer"]["requirements_lock"],
        },
        "input_materialization_manifest_sha256": materialization_hash,
        "prepared_study_manifest_sha256": prepared_study_hash,
        "source_encoder_artifact_sha256": source_hash,
        "architecture": {
            "family": "nlb_retrieval_distilled_v1",
            "input_dimension": dimension,
            "bit_count": bit_count,
            "encoder_activation": "affine_hard_step_learned_bias_v1",
            "decoder": "tied_transpose_tanh",
            "code_value_encoding": "zero_one",
            "input_transform": "clip_minus_one_one_v1",
        },
        "training": {
            "seed": seed,
            "epochs": epochs,
            "batch_size": batch_size,
            "learning_rate": learning_rate,
            "validation_fraction": validation_fraction,
            "objective": OBJECTIVE,
            "optimizer": {"id": "adamw", "weight_decay": 0.0},
            "shuffle_recipe": {
                "id": "python_fisher_yates_sha256_seed_v1", "per_epoch": True,
            },
            "train_vector_count": len(train_indices),
            "validation_vector_count": len(validation_indices),
            "best_document_only_validation_loss": best_loss,
            "best_epoch": best_state["epoch"],
            "best_soft_temperature": best_state["temperature"],
            "best_validation_components": best_state["validation_components"],
            "loss_weights": {
                "reconstruction": reconstruction_weight,
                "decorrelation": decorrelation_weight,
                "document_geometry_distillation": distillation_weight,
                "row_orthogonality": row_orthogonality_weight,
            },
            "distillation": {
                "id": "document_only_in_batch_listwise_kl_v1",
                "teacher": "normalized_clipped_e5_cosine",
                "student": "soft_binary_normalized_dot",
                "teacher_temperature": teacher_temperature,
                "student_temperature": student_temperature,
                "queries_or_qrels_used": False,
            },
            "soft_to_hard": {
                "id": "geometric_tanh_temperature_schedule_v1",
                "start": soft_temperature_start,
                "end": soft_temperature_end,
            },
            "initialization": {
                "mode": initialization_mode,
                "source_artifact_sha256": source_hash,
                "source_family": source["architecture"]["family"],
                "itq_iterations": itq_iterations if initialization_mode == "itq_median" else 0,
            },
            "hard_code_health": {"train": train_health, "validation": validation_health},
            **split_provenance,
            "torch_threads": torch_threads,
        },
        "weights": {
            "encoder_weights": {
                "path": output_weights["encoder_weights"].name,
                "sha256": sha256_file(output_weights["encoder_weights"]),
                "shape": [bit_count, dimension],
                "layout": "row_major_out_by_in",
                "dtype": "float32_le",
            },
            "encoder_bias": {
                "path": output_weights["encoder_bias"].name,
                "sha256": sha256_file(output_weights["encoder_bias"]),
                "shape": [bit_count], "dtype": "float32_le",
            },
            "decoder_bias": {
                "path": output_weights["decoder_bias"].name,
                "sha256": sha256_file(output_weights["decoder_bias"]),
                "shape": [dimension], "dtype": "float32_le",
            },
        },
    }
    (output_root / "artifact.json").write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n",
    )
    return artifact


def run_self_test() -> int:
    if temperature_for_epoch(1.0, 16.0, 0, 5) != 1.0:
        print("self-test failed: schedule start", file=sys.stderr)
        return 1
    if temperature_for_epoch(1.0, 16.0, 4, 5) != 16.0:
        print("self-test failed: schedule finish", file=sys.stderr)
        return 1
    try:
        require_finite_nonnegative(-1.0, "test")
    except FineTuneError:
        return 0
    print("self-test failed: negative loss weight accepted", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--materialization-root", type=Path)
    parser.add_argument("--initialization-artifact", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--bit-count", type=int, default=128)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--learning-rate", type=float, default=1.0e-4)
    parser.add_argument("--validation-fraction", type=float, default=0.05)
    parser.add_argument("--reconstruction-weight", type=float, default=1.0)
    parser.add_argument("--decorrelation-weight", type=float, default=0.0)
    parser.add_argument("--distillation-weight", type=float, default=0.0)
    parser.add_argument("--row-orthogonality-weight", type=float, default=0.0)
    parser.add_argument("--teacher-temperature", type=float, default=0.05)
    parser.add_argument("--student-temperature", type=float, default=0.05)
    parser.add_argument("--soft-temperature-start", type=float, default=1.0)
    parser.add_argument("--soft-temperature-end", type=float, default=8.0)
    parser.add_argument(
        "--initialization-mode", choices=("median_artifact", "itq_median"),
        default="median_artifact",
    )
    parser.add_argument("--itq-iterations", type=int, default=50)
    parser.add_argument("--torch-threads", type=int, default=18)
    parser.add_argument("--train-ids", type=Path)
    parser.add_argument("--validation-ids", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    if args.materialization_root is None or args.initialization_artifact is None or args.output_root is None:
        parser.error("materialization, initialization artifact, and output root are required")
    try:
        artifact = train(
            materialization_root=args.materialization_root,
            initialization_artifact=args.initialization_artifact,
            output_root=args.output_root,
            bit_count=args.bit_count, seed=args.seed, epochs=args.epochs,
            batch_size=args.batch_size, learning_rate=args.learning_rate,
            validation_fraction=args.validation_fraction,
            reconstruction_weight=args.reconstruction_weight,
            decorrelation_weight=args.decorrelation_weight,
            distillation_weight=args.distillation_weight,
            row_orthogonality_weight=args.row_orthogonality_weight,
            teacher_temperature=args.teacher_temperature,
            student_temperature=args.student_temperature,
            soft_temperature_start=args.soft_temperature_start,
            soft_temperature_end=args.soft_temperature_end,
            initialization_mode=args.initialization_mode,
            itq_iterations=args.itq_iterations, torch_threads=args.torch_threads,
            train_ids_path=args.train_ids, validation_ids_path=args.validation_ids,
        )
    except FineTuneError as exc:
        print(f"fine-tune-nlb-retrieval: {exc}", file=sys.stderr)
        return 1
    print(
        f"fine-tuned {artifact['architecture']['bit_count']}-bit NLB on "
        f"{artifact['training']['train_vector_count']} document vectors"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
