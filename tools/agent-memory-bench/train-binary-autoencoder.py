#!/usr/bin/env python3
"""Train a document-only linear binary autoencoder from materialized float vectors.

The exported artifact stores both encoder and optional decoder weights. C++
inference in the following PR needs only the encoder; the decoder is retained
for a separately measured approximate-vector retrieval mode.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import random
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

TRAINER_ID = "agent-memory-cpp:linear-binary-autoencoder-trainer"
TRAINER_VERSION = "v1"
NLB_TRAINER_ID = "agent-memory-cpp:nlb-tied-binary-autoencoder-trainer"
NLB_TRAINER_VERSION = "v1"
MATERIALIZER_ID = "agent-memory-cpp:multilingual-e5-materializer"
MATERIALIZER_VERSION = "v1"
REQUIREMENTS_LOCK_FILE = "requirements-binary-autoencoder-trainer.txt"
F32 = struct.Struct("<f")
OBJECTIVE_STE = "tanh_sign_ste_v1"
OBJECTIVE_NLB_PAPER = "nlb_paper_tied_v1"


class TrainingError(RuntimeError):
    """Raised when training inputs, options, or exported artifacts are invalid."""


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TrainingError(f"{field} must be an object")
    return value


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise TrainingError(f"{field} must be a non-empty string")
    return value


def require_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise TrainingError(f"{field} must be positive")
    return value


def require_sha256(value: Any, field: str) -> str:
    result = require_string(value, field)
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise TrainingError(f"{field} must be a lowercase SHA-256 hex digest")
    return result


def resolve_plain_file(root: Path, value: Any, field: str) -> Path:
    relative = Path(require_string(value, field))
    if relative.is_absolute() or relative.name != str(relative) or str(relative) == ".":
        raise TrainingError(f"{field} must be a plain file name")
    return root / relative


def parse_requirements_lock() -> tuple[str, dict[str, str]]:
    path = script_dir() / REQUIREMENTS_LOCK_FILE
    python_version = ""
    packages: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            marker = "# python-version:"
            if line.startswith(marker):
                python_version = line[len(marker):].strip()
            continue
        if "==" not in line:
            raise TrainingError(f"{path.name}:{line_number}: expected package==version")
        package, version = (part.strip() for part in line.split("==", 1))
        if not package or not version or package in packages:
            raise TrainingError(f"{path.name}:{line_number}: invalid or duplicate package pin")
        packages[package] = version
    return python_version, packages


def verify_environment() -> None:
    expected_python, packages = parse_requirements_lock()
    if platform.python_version() != expected_python:
        raise TrainingError(
            f"Python version mismatch: expected {expected_python}, got {platform.python_version()}"
        )
    for package in ("torch", "numpy"):
        if package not in packages:
            raise TrainingError(f"{REQUIREMENTS_LOCK_FILE}: missing package pin {package}")
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as exc:
            raise TrainingError(f"required package is not installed: {package}") from exc
        if actual != packages[package]:
            raise TrainingError(f"{package} version mismatch: expected {packages[package]}, got {actual}")


def stable_split(identifier: str, seed: int, validation_fraction: float) -> bool:
    digest = hashlib.sha256(f"{seed}\0ae-validation\0{identifier}".encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], byteorder="big") / float(1 << 64)
    return value < validation_fraction


def load_ids(path: Path) -> list[str]:
    ids: list[str] = []
    seen_ids: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                row = require_mapping(json.loads(line), f"{path.name}:{line_number}")
            except json.JSONDecodeError as exc:
                raise TrainingError(f"{path.name}:{line_number}: invalid JSON: {exc.msg}") from exc
            identifier = require_string(row.get("id"), f"{path.name}:{line_number}: id")
            if identifier in seen_ids:
                raise TrainingError(f"{path.name}:{line_number}: duplicate id {identifier}")
            seen_ids.add(identifier)
            ids.append(identifier)
    if not ids:
        raise TrainingError("training ids must not be empty")
    return ids


def load_materialization(root: Path) -> tuple[list[str], Path, int, str]:
    try:
        manifest = require_mapping(json.loads((root / "manifest.json").read_text(encoding="utf-8")), "manifest")
    except (OSError, json.JSONDecodeError) as exc:
        raise TrainingError(f"cannot read materialization manifest: {exc}") from exc
    if manifest.get("schema_version") != 1:
        raise TrainingError("materialization manifest schema_version must equal 1")
    materializer = require_mapping(manifest.get("materializer"), "manifest.materializer")
    if (require_string(materializer.get("id"), "manifest.materializer.id") != MATERIALIZER_ID or
            require_string(materializer.get("version"), "manifest.materializer.version") != MATERIALIZER_VERSION):
        raise TrainingError("unsupported materializer identity")
    require_sha256(materializer.get("source_hash"), "manifest.materializer.source_hash")
    execution = require_mapping(manifest.get("execution"), "manifest.execution")
    if (require_positive_int(execution.get("batch_size"), "manifest.execution.batch_size") <= 0 or
            execution.get("device") != "cpu" or
            execution.get("compute_dtype") != "float32" or
            execution.get("deterministic_algorithms") is not True):
        raise TrainingError("materialization execution recipe is unsupported")
    require_positive_int(execution.get("thread_count"), "manifest.execution.thread_count")
    for field in ("backend", "platform", "torch_version", "policy"):
        require_string(execution.get(field), f"manifest.execution.{field}")
    vector_format = require_mapping(manifest.get("vector_format"), "manifest.vector_format")
    if vector_format.get("dtype") != "float32_le" or vector_format.get("endianness") != "little":
        raise TrainingError("materialization vector_format must be little-endian float32")
    dimension = vector_format.get("dimension")
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
        raise TrainingError("materialization dimension must be positive")
    outputs = require_mapping(manifest.get("outputs"), "manifest.outputs")
    ids_entry = require_mapping(outputs.get("train_ids"), "manifest.outputs.train_ids")
    vectors_entry = require_mapping(outputs.get("train_vectors"), "manifest.outputs.train_vectors")
    if vectors_entry.get("dtype") != "float32_le":
        raise TrainingError("materialization train vectors must use float32_le")
    ids_path = resolve_plain_file(root, ids_entry.get("path"), "manifest.outputs.train_ids.path")
    vectors_path = resolve_plain_file(root, vectors_entry.get("path"), "manifest.outputs.train_vectors.path")
    if not ids_path.is_file() or not vectors_path.is_file():
        raise TrainingError("materialization train files are missing")
    if (require_sha256(ids_entry.get("sha256"), "manifest.outputs.train_ids.sha256") != sha256_file(ids_path) or
            require_sha256(vectors_entry.get("sha256"), "manifest.outputs.train_vectors.sha256") != sha256_file(vectors_path)):
        raise TrainingError("materialization train output hash mismatch")
    ids = load_ids(ids_path)
    if vectors_entry.get("count") != len(ids) or vectors_entry.get("dimension") != dimension:
        raise TrainingError("materialization vector shape disagrees with ids")
    expected_bytes = len(ids) * dimension * F32.size
    if vectors_path.stat().st_size != expected_bytes:
        raise TrainingError("materialization vector file has an invalid byte size")
    prepared_hash = require_sha256(manifest.get("prepared_study_manifest_sha256"), "prepared_study_manifest_sha256")
    return ids, vectors_path, dimension, prepared_hash


def deterministic_epoch_permutation(indices: list[int], seed: int, epoch: int) -> list[int]:
    """Returns a complete, reproducible Fisher-Yates order for one training epoch."""
    derived_seed = hashlib.sha256(
        f"{seed}\0linear-binary-autoencoder-shuffle-v1\0{epoch}".encode("utf-8")
    ).digest()
    result = list(indices)
    random.Random(int.from_bytes(derived_seed[:16], byteorder="big")).shuffle(result)
    return result


def write_f32(path: Path, values: Any) -> None:
    with path.open("wb") as handle:
        for value in values.reshape(-1).detach().cpu().tolist():
            narrowed = F32.unpack(F32.pack(float(value)))[0]
            if not math.isfinite(narrowed):
                raise TrainingError("trained weight is non-finite")
            handle.write(F32.pack(0.0 if narrowed == 0.0 else narrowed))


def hard_code_health(
    *,
    vectors: Any,
    indices: list[int],
    encoder_weight: Any,
    encoder_bias: Any,
    batch_size: int,
    clip_inputs: bool = False,
) -> dict[str, Any]:
    """Returns deterministic post-training health for exported hard sign codes."""
    import numpy

    if not indices:
        raise TrainingError("hard-code health requires at least one vector")
    weights = encoder_weight.detach().cpu().numpy()
    bias = encoder_bias.detach().cpu().numpy()
    bit_count = int(weights.shape[0])
    one_counts = numpy.zeros(bit_count, dtype=numpy.int64)
    unique_codes: set[bytes] = set()
    for start in range(0, len(indices), batch_size):
        values = numpy.asarray(vectors[indices[start:start + batch_size]], dtype=numpy.float32)
        if clip_inputs:
            values = numpy.clip(values, -1.0, 1.0)
        hard_code = (values @ weights.T + bias) >= 0.0
        one_counts += hard_code.sum(axis=0, dtype=numpy.int64)
        packed = numpy.packbits(hard_code, axis=1, bitorder="little")
        unique_codes.update(row.tobytes() for row in packed)
    occupancies = one_counts / float(len(indices))
    constant_bit_count = int(numpy.count_nonzero((occupancies == 0.0) | (occupancies == 1.0)))
    return {
        "vector_count": len(indices),
        "unique_code_count": len(unique_codes),
        "unique_code_fraction": len(unique_codes) / float(len(indices)),
        "constant_bit_count": constant_bit_count,
        "constant_bit_fraction": constant_bit_count / float(bit_count),
        "minimum_bit_occupancy": float(occupancies.min()),
        "maximum_bit_occupancy": float(occupancies.max()),
    }


def require_noncollapsed_hard_codes(health: dict[str, Any], field: str) -> None:
    """Rejects an artifact whose hard code cannot distinguish any training row."""
    if health["unique_code_count"] < 2:
        raise TrainingError(
            f"{field} hard binary code collapsed to one exact signature; "
            "adjust the objective or training regime before exporting an artifact"
        )


def validate_hard_code_health(
    value: Any,
    field: str,
    bit_count: int,
) -> None:
    """Validates optional persisted hard-code health without invalidating v1 artifacts."""
    health = require_mapping(value, field)
    vector_count = require_positive_int(health.get("vector_count"), f"{field}.vector_count")
    unique_code_count = require_positive_int(
        health.get("unique_code_count"), f"{field}.unique_code_count"
    )
    if unique_code_count > vector_count:
        raise TrainingError(f"{field}.unique_code_count exceeds vector_count")
    constant_bit_count = health.get("constant_bit_count")
    if (isinstance(constant_bit_count, bool) or not isinstance(constant_bit_count, int) or
            constant_bit_count < 0 or constant_bit_count > bit_count):
        raise TrainingError(f"{field}.constant_bit_count is invalid")
    for name in (
        "unique_code_fraction",
        "constant_bit_fraction",
        "minimum_bit_occupancy",
        "maximum_bit_occupancy",
    ):
        numeric = health.get(name)
        if isinstance(numeric, bool) or not isinstance(numeric, (int, float)) or not math.isfinite(numeric):
            raise TrainingError(f"{field}.{name} must be finite")
    if not 0.0 < health["unique_code_fraction"] <= 1.0:
        raise TrainingError(f"{field}.unique_code_fraction is invalid")
    if not 0.0 <= health["constant_bit_fraction"] <= 1.0:
        raise TrainingError(f"{field}.constant_bit_fraction is invalid")
    if not (0.0 <= health["minimum_bit_occupancy"] <= health["maximum_bit_occupancy"] <= 1.0):
        raise TrainingError(f"{field} bit occupancy range is invalid")


def validate_autoencoder_artifact(root: Path) -> dict[str, Any]:
    """Validates the dependency-free v1 wire contract consumed by C++ inference."""
    artifact_path = root / "artifact.json"
    try:
        artifact = require_mapping(
            json.loads(artifact_path.read_text(encoding="utf-8")),
            "artifact",
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise TrainingError(f"cannot read autoencoder artifact: {exc}") from exc
    if artifact.get("schema_version") != 1:
        raise TrainingError("autoencoder artifact schema_version must equal 1")
    trainer = require_mapping(artifact.get("trainer"), "artifact.trainer")
    architecture = require_mapping(artifact.get("architecture"), "artifact.architecture")
    family = require_string(architecture.get("family"), "artifact.architecture.family")
    is_ste = family == "linear_binary_autoencoder_ste"
    is_nlb_paper = family == OBJECTIVE_NLB_PAPER
    if ((is_ste and
         (require_string(trainer.get("id"), "artifact.trainer.id") != TRAINER_ID or
          require_string(trainer.get("version"), "artifact.trainer.version") != TRAINER_VERSION)) or
            (is_nlb_paper and
             (require_string(trainer.get("id"), "artifact.trainer.id") != NLB_TRAINER_ID or
              require_string(trainer.get("version"), "artifact.trainer.version") != NLB_TRAINER_VERSION)) or
            not (is_ste or is_nlb_paper)):
        raise TrainingError("unsupported autoencoder artifact trainer identity")
    require_sha256(trainer.get("source_hash"), "artifact.trainer.source_hash")
    require_string(trainer.get("requirements_lock"), "artifact.trainer.requirements_lock")
    require_sha256(
        artifact.get("input_materialization_manifest_sha256"),
        "artifact.input_materialization_manifest_sha256",
    )
    require_sha256(
        artifact.get("prepared_study_manifest_sha256"),
        "artifact.prepared_study_manifest_sha256",
    )
    if ((is_ste and
         (require_string(architecture.get("encoder_activation"), "artifact.architecture.encoder_activation") != OBJECTIVE_STE or
          require_string(architecture.get("decoder"), "artifact.architecture.decoder") != "linear")) or
            (is_nlb_paper and
             (require_string(architecture.get("encoder_activation"), "artifact.architecture.encoder_activation") != "hard_step_no_ste_v1" or
              require_string(architecture.get("decoder"), "artifact.architecture.decoder") != "tied_transpose_tanh" or
              require_string(architecture.get("code_value_encoding"), "artifact.architecture.code_value_encoding") != "zero_one" or
              require_string(architecture.get("input_transform"), "artifact.architecture.input_transform") != "clip_minus_one_one_v1"))):
        raise TrainingError("unsupported autoencoder artifact architecture")
    dimension = require_positive_int(architecture.get("input_dimension"), "artifact.architecture.input_dimension")
    bit_count = require_positive_int(architecture.get("bit_count"), "artifact.architecture.bit_count")
    if is_nlb_paper:
        regularizer = require_mapping(
            architecture.get("regularizer"), "artifact.architecture.regularizer"
        )
        if require_string(regularizer.get("id"), "artifact.architecture.regularizer.id") != "paper_w_transpose_w_identity_v1":
            raise TrainingError("unsupported NLB-paper regularizer")
        regularizer_weight = regularizer.get("weight")
        if (isinstance(regularizer_weight, bool) or
                not isinstance(regularizer_weight, (int, float)) or
                not math.isfinite(regularizer_weight) or regularizer_weight < 0.0):
            raise TrainingError("artifact.architecture.regularizer.weight is invalid")
    training = require_mapping(artifact.get("training"), "artifact.training")
    expected_objective = OBJECTIVE_STE if is_ste else OBJECTIVE_NLB_PAPER
    if ("objective" in training and
            require_string(training.get("objective"), "artifact.training.objective") != expected_objective):
        raise TrainingError("artifact.training.objective disagrees with architecture")
    if is_nlb_paper and "objective" not in training:
        raise TrainingError("NLB-paper artifact.training.objective is required")
    if isinstance(training.get("seed"), bool) or not isinstance(training.get("seed"), int) or training["seed"] < 0:
        raise TrainingError("artifact.training.seed must be a non-negative integer")
    shuffle_recipe = require_mapping(training.get("shuffle_recipe"), "artifact.training.shuffle_recipe")
    if (require_string(shuffle_recipe.get("id"), "artifact.training.shuffle_recipe.id") != "python_fisher_yates_sha256_seed_v1" or
            shuffle_recipe.get("per_epoch") is not True):
        raise TrainingError("unsupported autoencoder artifact shuffle recipe")
    if is_nlb_paper:
        optimizer = require_mapping(training.get("optimizer"), "artifact.training.optimizer")
        if (require_string(optimizer.get("id"), "artifact.training.optimizer.id") != "sgd_momentum" or
                optimizer.get("momentum") != 0.95 or
                optimizer.get("per_epoch_learning_rate_decay") != 0.95):
            raise TrainingError("unsupported NLB-paper optimizer recipe")
    weights = require_mapping(artifact.get("weights"), "artifact.weights")

    def validate_weight(
        name: str,
        expected_shape: list[int],
        expected_layout: str | None,
    ) -> None:
        entry = require_mapping(weights.get(name), f"artifact.weights.{name}")
        if entry.get("dtype") != "float32_le":
            raise TrainingError(f"artifact.weights.{name}.dtype must equal float32_le")
        if expected_layout is not None and entry.get("layout") != expected_layout:
            raise TrainingError(f"artifact.weights.{name}.layout mismatch")
        if entry.get("shape") != expected_shape:
            raise TrainingError(f"artifact.weights.{name}.shape mismatch")
        path = resolve_plain_file(root, entry.get("path"), f"artifact.weights.{name}.path")
        if not path.is_file():
            raise TrainingError(f"artifact weight file is missing: {name}")
        if require_sha256(entry.get("sha256"), f"artifact.weights.{name}.sha256") != sha256_file(path):
            raise TrainingError(f"artifact weight hash mismatch: {name}")
        element_count = math.prod(expected_shape)
        if path.stat().st_size != element_count * F32.size:
            raise TrainingError(f"artifact weight byte size mismatch: {name}")
        with path.open("rb") as handle:
            for index in range(element_count):
                value = F32.unpack(handle.read(F32.size))[0]
                if not math.isfinite(value):
                    raise TrainingError(f"artifact weight is non-finite: {name}[{index}]")

    validate_weight("encoder_weights", [bit_count, dimension], "row_major_out_by_in")
    if is_ste:
        validate_weight("encoder_bias", [bit_count], None)
        validate_weight("decoder_weights", [dimension, bit_count], "row_major_out_by_in")
    validate_weight("decoder_bias", [dimension], None)
    if "hard_code_health" in training:
        hard_code_health = require_mapping(
            training["hard_code_health"], "artifact.training.hard_code_health"
        )
        validate_hard_code_health(
            hard_code_health.get("train"),
            "artifact.training.hard_code_health.train",
            bit_count,
        )
        validate_hard_code_health(
            hard_code_health.get("validation"),
            "artifact.training.hard_code_health.validation",
            bit_count,
        )
    return artifact


def train(
    *,
    materialization_root: Path,
    output_root: Path,
    bit_count: int,
    seed: int,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    validation_fraction: float,
    quantization_weight: float,
    balance_weight: float,
    objective: str,
    nlb_regularizer_weight: float,
) -> dict[str, Any]:
    if output_root.exists():
        raise TrainingError(f"output directory already exists: {output_root}")
    for value, name in ((bit_count, "bit_count"), (epochs, "epochs"), (batch_size, "batch_size")):
        require_positive_int(value, name)
    if not 0.0 < validation_fraction < 0.5:
        raise TrainingError("validation_fraction must be in (0, 0.5)")
    if learning_rate <= 0.0 or quantization_weight < 0.0 or balance_weight < 0.0:
        raise TrainingError("training weights and learning_rate are invalid")
    if objective not in (OBJECTIVE_STE, OBJECTIVE_NLB_PAPER):
        raise TrainingError("unsupported autoencoder objective")
    if nlb_regularizer_weight < 0.0 or not math.isfinite(nlb_regularizer_weight):
        raise TrainingError("nlb_regularizer_weight must be finite and non-negative")
    verify_environment()
    try:
        import numpy
        import torch
    except ImportError as exc:
        raise TrainingError(f"training requires packages from {REQUIREMENTS_LOCK_FILE}") from exc

    ids, vectors_path, dimension, materialization_hash = load_materialization(materialization_root)
    validation_indices = [index for index, identifier in enumerate(ids) if stable_split(identifier, seed, validation_fraction)]
    train_indices = [index for index, identifier in enumerate(ids) if not stable_split(identifier, seed, validation_fraction)]
    if not validation_indices or not train_indices:
        raise TrainingError("document-only validation split is empty")
    vectors = numpy.memmap(vectors_path, dtype="<f4", mode="r", shape=(len(ids), dimension))

    torch.set_num_threads(1)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    encoder = torch.nn.Linear(dimension, bit_count, bias=objective == OBJECTIVE_STE)
    decoder = None
    if objective == OBJECTIVE_STE:
        decoder = torch.nn.Linear(bit_count, dimension)
        optimizer = torch.optim.AdamW(
            [*encoder.parameters(), *decoder.parameters()], lr=learning_rate
        )
    else:
        decoder_bias = torch.nn.Parameter(torch.zeros(dimension, dtype=torch.float32))
        optimizer = torch.optim.SGD(
            [encoder.weight, decoder_bias], lr=learning_rate, momentum=0.95
        )
    best_loss = float("inf")
    best_state: dict[str, Any] | None = None

    def batch_tensor(indices: list[int]) -> Any:
        return torch.from_numpy(numpy.asarray(vectors[indices], dtype=numpy.float32).copy())

    def loss_for(values: Any) -> Any:
        if objective == OBJECTIVE_STE:
            assert decoder is not None
            soft_code = torch.tanh(encoder(values))
            hard_code = torch.where(soft_code >= 0.0, torch.ones_like(soft_code), -torch.ones_like(soft_code))
            code = soft_code + (hard_code - soft_code).detach()
            reconstruction = decoder(code)
            reconstruction_loss = torch.mean((reconstruction - values) ** 2)
            quantization_loss = torch.mean((torch.abs(soft_code) - 1.0) ** 2)
            balance_loss = torch.mean(torch.mean(soft_code, dim=0) ** 2)
            return reconstruction_loss + quantization_weight * quantization_loss + balance_weight * balance_loss
        clipped = torch.clamp(values, min=-1.0, max=1.0)
        hard_code = (encoder(clipped) > 0.0).to(clipped.dtype).detach()
        reconstruction = torch.tanh(hard_code @ encoder.weight + decoder_bias)
        reconstruction_loss = torch.mean((reconstruction - clipped) ** 2)
        gram = encoder.weight.T @ encoder.weight
        regularization = 0.5 * torch.sum((gram - torch.eye(
            dimension, dtype=gram.dtype, device=gram.device
        )) ** 2)
        return reconstruction_loss + nlb_regularizer_weight * regularization

    for epoch in range(epochs):
        encoder.train()
        if decoder is not None:
            decoder.train()
        epoch_indices = deterministic_epoch_permutation(train_indices, seed, epoch)
        for start in range(0, len(epoch_indices), batch_size):
            values = batch_tensor(epoch_indices[start:start + batch_size])
            optimizer.zero_grad(set_to_none=True)
            loss_for(values).backward()
            optimizer.step()
        encoder.eval()
        if decoder is not None:
            decoder.eval()
        with torch.no_grad():
            validation_loss = 0.0
            validation_rows = 0
            for start in range(0, len(validation_indices), batch_size):
                values = batch_tensor(validation_indices[start:start + batch_size])
                validation_loss += float(loss_for(values)) * len(values)
                validation_rows += len(values)
            validation_loss /= validation_rows
        if validation_loss < best_loss:
            best_loss = validation_loss
            best_state = {
                "encoder_weight": encoder.weight.detach().clone(),
                "encoder_bias": (
                    encoder.bias.detach().clone()
                    if objective == OBJECTIVE_STE
                    else torch.zeros(bit_count, dtype=torch.float32)
                ),
                "decoder_weight": (
                    decoder.weight.detach().clone()
                    if objective == OBJECTIVE_STE
                    else None
                ),
                "decoder_bias": (
                    decoder.bias.detach().clone()
                    if objective == OBJECTIVE_STE
                    else decoder_bias.detach().clone()
                ),
            }
        if objective == OBJECTIVE_NLB_PAPER:
            for parameter_group in optimizer.param_groups:
                parameter_group["lr"] *= 0.95
    if best_state is None:
        raise TrainingError("trainer did not produce an artifact")

    train_hard_code_health = hard_code_health(
        vectors=vectors,
        indices=train_indices,
        encoder_weight=best_state["encoder_weight"],
        encoder_bias=best_state["encoder_bias"],
        batch_size=batch_size,
        clip_inputs=objective == OBJECTIVE_NLB_PAPER,
    )
    require_noncollapsed_hard_codes(train_hard_code_health, "training")
    validation_hard_code_health = hard_code_health(
        vectors=vectors,
        indices=validation_indices,
        encoder_weight=best_state["encoder_weight"],
        encoder_bias=best_state["encoder_bias"],
        batch_size=batch_size,
        clip_inputs=objective == OBJECTIVE_NLB_PAPER,
    )

    output_root.mkdir(parents=True)
    files = {
        "encoder_weights": output_root / "encoder-weights.f32",
        "decoder_bias": output_root / "decoder-bias.f32",
    }
    if objective == OBJECTIVE_STE:
        files["encoder_bias"] = output_root / "encoder-bias.f32"
        files["decoder_weights"] = output_root / "decoder-weights.f32"
    tensors = {
        "encoder_weights": best_state["encoder_weight"],
        "decoder_bias": best_state["decoder_bias"],
    }
    if objective == OBJECTIVE_STE:
        tensors["encoder_bias"] = best_state["encoder_bias"]
        tensors["decoder_weights"] = best_state["decoder_weight"]
    for name, tensor in tensors.items():
        write_f32(files[name], tensor)
    requirements_path = script_dir() / REQUIREMENTS_LOCK_FILE
    architecture: dict[str, Any]
    weights: dict[str, Any] = {
        "encoder_weights": {
            "path": files["encoder_weights"].name,
            "sha256": sha256_file(files["encoder_weights"]),
            "shape": [bit_count, dimension],
            "layout": "row_major_out_by_in",
            "dtype": "float32_le",
        },
        "decoder_bias": {
            "path": files["decoder_bias"].name,
            "sha256": sha256_file(files["decoder_bias"]),
            "shape": [dimension],
            "dtype": "float32_le",
        },
    }
    if objective == OBJECTIVE_STE:
        architecture = {
            "family": "linear_binary_autoencoder_ste",
            "input_dimension": dimension,
            "bit_count": bit_count,
            "encoder_activation": OBJECTIVE_STE,
            "decoder": "linear",
        }
        weights["encoder_bias"] = {
            "path": files["encoder_bias"].name,
            "sha256": sha256_file(files["encoder_bias"]),
            "shape": [bit_count],
            "dtype": "float32_le",
        }
        weights["decoder_weights"] = {
            "path": files["decoder_weights"].name,
            "sha256": sha256_file(files["decoder_weights"]),
            "shape": [dimension, bit_count],
            "layout": "row_major_out_by_in",
            "dtype": "float32_le",
        }
        trainer_id = TRAINER_ID
        trainer_version = TRAINER_VERSION
    else:
        architecture = {
            "family": OBJECTIVE_NLB_PAPER,
            "input_dimension": dimension,
            "bit_count": bit_count,
            "encoder_activation": "hard_step_no_ste_v1",
            "decoder": "tied_transpose_tanh",
            "code_value_encoding": "zero_one",
            "input_transform": "clip_minus_one_one_v1",
            "regularizer": {
                "id": "paper_w_transpose_w_identity_v1",
                "weight": nlb_regularizer_weight,
            },
        }
        trainer_id = NLB_TRAINER_ID
        trainer_version = NLB_TRAINER_VERSION
    training: dict[str, Any] = {
        "seed": seed,
        "epochs": epochs,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "validation_fraction": validation_fraction,
        "objective": objective,
        "shuffle_recipe": {
            "id": "python_fisher_yates_sha256_seed_v1",
            "per_epoch": True,
        },
        "train_vector_count": len(train_indices),
        "validation_vector_count": len(validation_indices),
        "best_document_only_validation_loss": best_loss,
        "hard_code_health": {
            "train": train_hard_code_health,
            "validation": validation_hard_code_health,
        },
    }
    if objective == OBJECTIVE_STE:
        training.update({
            "optimizer": {"id": "adamw"},
            "quantization_weight": quantization_weight,
            "balance_weight": balance_weight,
        })
    else:
        training.update({
            "optimizer": {
                "id": "sgd_momentum",
                "momentum": 0.95,
                "per_epoch_learning_rate_decay": 0.95,
            },
            "regularizer_weight": nlb_regularizer_weight,
        })
    artifact = {
        "schema_version": 1,
        "trainer": {
            "id": trainer_id,
            "version": trainer_version,
            "source_hash": sha256_file(Path(__file__)),
            "requirements_lock": f"{REQUIREMENTS_LOCK_FILE};sha256={sha256_file(requirements_path)}",
        },
        "input_materialization_manifest_sha256": sha256_file(materialization_root / "manifest.json"),
        "prepared_study_manifest_sha256": materialization_hash,
        "architecture": architecture,
        "training": training,
        "weights": weights,
    }
    (output_root / "artifact.json").write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return validate_autoencoder_artifact(output_root)


def run_self_test() -> int:
    try:
        require_noncollapsed_hard_codes({"unique_code_count": 1}, "self-test")
    except TrainingError:
        pass
    else:
        print("self-test failed: collapsed hard code was accepted", file=sys.stderr)
        return 1
    valid_health = {
        "vector_count": 4,
        "unique_code_count": 2,
        "unique_code_fraction": 0.5,
        "constant_bit_count": 1,
        "constant_bit_fraction": 0.5,
        "minimum_bit_occupancy": 0.0,
        "maximum_bit_occupancy": 1.0,
    }
    validate_hard_code_health(valid_health, "self-test", 2)
    with tempfile.TemporaryDirectory(prefix="agent-memory-ae-trainer-") as raw_root:
        root = Path(raw_root)
        vectors = root / "train-vectors.f32"
        vectors.write_bytes(b"".join(F32.pack(value) for value in (1.0, 0.0, 0.0, 1.0)))
        ids = root / "train-document-ids.jsonl"
        ids.write_text('{"id":"ru:a"}\n{"id":"en:b"}\n', encoding="utf-8", newline="\n")
        manifest = {
            "schema_version": 1,
            "materializer": {
                "id": MATERIALIZER_ID,
                "version": MATERIALIZER_VERSION,
                "source_hash": "b" * 64,
            },
            "execution": {
                "batch_size": 2,
                "device": "cpu",
                "compute_dtype": "float32",
                "deterministic_algorithms": True,
                "thread_count": 3,
                "backend": "self-test",
                "platform": "self-test",
                "torch_version": "not-applicable",
                "policy": "self-test",
            },
            "prepared_study_manifest_sha256": "a" * 64,
            "vector_format": {"dtype": "float32_le", "endianness": "little", "dimension": 2},
            "outputs": {
                "train_ids": {"path": ids.name, "sha256": sha256_file(ids), "count": 2},
                "train_vectors": {"path": vectors.name, "sha256": sha256_file(vectors), "count": 2, "dimension": 2, "dtype": "float32_le"},
            },
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8", newline="\n")
        loaded_ids, loaded_vectors, dimension, _ = load_materialization(root)
        if loaded_ids != ["ru:a", "en:b"] or loaded_vectors != vectors or dimension != 2:
            print("self-test failed: materialization load contract", file=sys.stderr)
            return 1
        manifest["vector_format"]["dtype"] = "float32_be"
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8", newline="\n")
        try:
            load_materialization(root)
        except TrainingError:
            pass
        else:
            print("self-test failed: non-little-endian vector format was accepted", file=sys.stderr)
            return 1
        manifest["vector_format"]["dtype"] = "float32_le"
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8", newline="\n")
        vectors.write_bytes(vectors.read_bytes() + F32.pack(0.0))
        try:
            load_materialization(root)
        except TrainingError:
            pass
        else:
            print("self-test failed: malformed vector file was accepted", file=sys.stderr)
            return 1

        artifact_root = root / "artifact"
        artifact_root.mkdir()
        weight_values = {
            "encoder_weights": [1.0, 0.0, 0.0, 1.0],
            "encoder_bias": [0.0, 0.0],
            "decoder_weights": [1.0, 0.0, 0.0, 1.0],
            "decoder_bias": [0.0, 0.0],
        }
        paths = {
            "encoder_weights": artifact_root / "encoder-weights.f32",
            "encoder_bias": artifact_root / "encoder-bias.f32",
            "decoder_weights": artifact_root / "decoder-weights.f32",
            "decoder_bias": artifact_root / "decoder-bias.f32",
        }
        for name, values in weight_values.items():
            paths[name].write_bytes(b"".join(F32.pack(value) for value in values))
        artifact = {
            "schema_version": 1,
            "trainer": {"id": TRAINER_ID, "version": TRAINER_VERSION, "source_hash": "c" * 64, "requirements_lock": "test"},
            "input_materialization_manifest_sha256": "d" * 64,
            "prepared_study_manifest_sha256": "e" * 64,
            "architecture": {"family": "linear_binary_autoencoder_ste", "input_dimension": 2, "bit_count": 2, "encoder_activation": "tanh_sign_ste_v1", "decoder": "linear"},
            "training": {
                "seed": 42,
                "objective": OBJECTIVE_STE,
                "optimizer": {"id": "adamw"},
                "shuffle_recipe": {"id": "python_fisher_yates_sha256_seed_v1", "per_epoch": True},
            },
            "weights": {
                "encoder_weights": {"path": paths["encoder_weights"].name, "sha256": sha256_file(paths["encoder_weights"]), "shape": [2, 2], "layout": "row_major_out_by_in", "dtype": "float32_le"},
                "encoder_bias": {"path": paths["encoder_bias"].name, "sha256": sha256_file(paths["encoder_bias"]), "shape": [2], "dtype": "float32_le"},
                "decoder_weights": {"path": paths["decoder_weights"].name, "sha256": sha256_file(paths["decoder_weights"]), "shape": [2, 2], "layout": "row_major_out_by_in", "dtype": "float32_le"},
                "decoder_bias": {"path": paths["decoder_bias"].name, "sha256": sha256_file(paths["decoder_bias"]), "shape": [2], "dtype": "float32_le"},
            },
        }
        (artifact_root / "artifact.json").write_text(json.dumps(artifact), encoding="utf-8", newline="\n")
        validate_autoencoder_artifact(artifact_root)
        paths["encoder_weights"].write_bytes(paths["encoder_weights"].read_bytes() + b"\x00")
        try:
            validate_autoencoder_artifact(artifact_root)
        except TrainingError:
            pass
        else:
            print("self-test failed: malformed autoencoder artifact was accepted", file=sys.stderr)
            return 1
        order = deterministic_epoch_permutation(list(range(64)), 42, 3)
        if (order != deterministic_epoch_permutation(list(range(64)), 42, 3) or
                order == deterministic_epoch_permutation(list(range(64)), 43, 3) or
                sorted(order) != list(range(64))):
            print("self-test failed: epoch shuffle contract", file=sys.stderr)
            return 1
    print("binary autoencoder trainer self-test ok")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--materialization-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--bit-count", type=int, default=128)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--learning-rate", type=float, default=1.0e-3)
    parser.add_argument("--validation-fraction", type=float, default=0.05)
    parser.add_argument("--quantization-weight", type=float, default=0.1)
    parser.add_argument("--balance-weight", type=float, default=0.01)
    parser.add_argument(
        "--objective",
        choices=(OBJECTIVE_STE, OBJECTIVE_NLB_PAPER),
        default=OBJECTIVE_STE,
    )
    parser.add_argument("--nlb-regularizer-weight", type=float, default=1.0)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        if args.materialization_root or args.output_root:
            parser.error("--self-test cannot be combined with training arguments")
        return run_self_test()
    if args.materialization_root is None or args.output_root is None:
        parser.error("--materialization-root and --output-root are required")
    try:
        artifact = train(
            materialization_root=args.materialization_root,
            output_root=args.output_root,
            bit_count=args.bit_count,
            seed=args.seed,
            epochs=args.epochs,
            batch_size=args.batch_size,
            learning_rate=args.learning_rate,
            validation_fraction=args.validation_fraction,
            quantization_weight=args.quantization_weight,
            balance_weight=args.balance_weight,
            objective=args.objective,
            nlb_regularizer_weight=args.nlb_regularizer_weight,
        )
    except TrainingError as exc:
        print(f"train-binary-autoencoder: {exc}", file=sys.stderr)
        return 1
    print(
        f"trained {artifact['architecture']['bit_count']}-bit autoencoder on "
        f"{artifact['training']['train_vector_count']} document vectors"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
