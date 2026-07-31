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
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

TRAINER_ID = "agent-memory-cpp:linear-binary-autoencoder-trainer"
TRAINER_VERSION = "v1"
REQUIREMENTS_LOCK_FILE = "requirements-binary-autoencoder-trainer.txt"
F32 = struct.Struct("<f")


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


def require_positive_int(value: int, field: str) -> int:
    if value <= 0:
        raise TrainingError(f"{field} must be positive")
    return value


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
    with path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                row = require_mapping(json.loads(line), f"{path.name}:{line_number}")
            except json.JSONDecodeError as exc:
                raise TrainingError(f"{path.name}:{line_number}: invalid JSON: {exc.msg}") from exc
            identifier = require_string(row.get("id"), f"{path.name}:{line_number}: id")
            if identifier in ids:
                raise TrainingError(f"{path.name}:{line_number}: duplicate id {identifier}")
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
    vector_format = require_mapping(manifest.get("vector_format"), "manifest.vector_format")
    dimension = vector_format.get("dimension")
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
        raise TrainingError("materialization dimension must be positive")
    outputs = require_mapping(manifest.get("outputs"), "manifest.outputs")
    ids_entry = require_mapping(outputs.get("train_ids"), "manifest.outputs.train_ids")
    vectors_entry = require_mapping(outputs.get("train_vectors"), "manifest.outputs.train_vectors")
    ids_path = root / require_string(ids_entry.get("path"), "manifest.outputs.train_ids.path")
    vectors_path = root / require_string(vectors_entry.get("path"), "manifest.outputs.train_vectors.path")
    if not ids_path.is_file() or not vectors_path.is_file():
        raise TrainingError("materialization train files are missing")
    if ids_entry.get("sha256") != sha256_file(ids_path) or vectors_entry.get("sha256") != sha256_file(vectors_path):
        raise TrainingError("materialization train output hash mismatch")
    ids = load_ids(ids_path)
    if vectors_entry.get("count") != len(ids) or vectors_entry.get("dimension") != dimension:
        raise TrainingError("materialization vector shape disagrees with ids")
    expected_bytes = len(ids) * dimension * F32.size
    if vectors_path.stat().st_size != expected_bytes:
        raise TrainingError("materialization vector file has an invalid byte size")
    prepared_hash = require_string(manifest.get("prepared_study_manifest_sha256"), "prepared_study_manifest_sha256")
    return ids, vectors_path, dimension, prepared_hash


def write_f32(path: Path, values: Any) -> None:
    with path.open("wb") as handle:
        for value in values.reshape(-1).detach().cpu().tolist():
            narrowed = F32.unpack(F32.pack(float(value)))[0]
            if not math.isfinite(narrowed):
                raise TrainingError("trained weight is non-finite")
            handle.write(F32.pack(0.0 if narrowed == 0.0 else narrowed))


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
) -> dict[str, Any]:
    if output_root.exists():
        raise TrainingError(f"output directory already exists: {output_root}")
    for value, name in ((bit_count, "bit_count"), (epochs, "epochs"), (batch_size, "batch_size")):
        require_positive_int(value, name)
    if not 0.0 < validation_fraction < 0.5:
        raise TrainingError("validation_fraction must be in (0, 0.5)")
    if learning_rate <= 0.0 or quantization_weight < 0.0 or balance_weight < 0.0:
        raise TrainingError("training weights and learning_rate are invalid")
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
    encoder = torch.nn.Linear(dimension, bit_count)
    decoder = torch.nn.Linear(bit_count, dimension)
    optimizer = torch.optim.AdamW([*encoder.parameters(), *decoder.parameters()], lr=learning_rate)
    best_loss = float("inf")
    best_state: dict[str, Any] | None = None

    def batch_tensor(indices: list[int]) -> Any:
        return torch.from_numpy(numpy.asarray(vectors[indices], dtype=numpy.float32).copy())

    def loss_for(values: Any) -> Any:
        soft_code = torch.tanh(encoder(values))
        hard_code = torch.where(soft_code >= 0.0, torch.ones_like(soft_code), -torch.ones_like(soft_code))
        code = soft_code + (hard_code - soft_code).detach()
        reconstruction = decoder(code)
        reconstruction_loss = torch.mean((reconstruction - values) ** 2)
        quantization_loss = torch.mean((torch.abs(soft_code) - 1.0) ** 2)
        balance_loss = torch.mean(torch.mean(soft_code, dim=0) ** 2)
        return reconstruction_loss + quantization_weight * quantization_loss + balance_weight * balance_loss

    for _ in range(epochs):
        encoder.train()
        decoder.train()
        for start in range(0, len(train_indices), batch_size):
            values = batch_tensor(train_indices[start:start + batch_size])
            optimizer.zero_grad(set_to_none=True)
            loss_for(values).backward()
            optimizer.step()
        encoder.eval()
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
                "encoder_bias": encoder.bias.detach().clone(),
                "decoder_weight": decoder.weight.detach().clone(),
                "decoder_bias": decoder.bias.detach().clone(),
            }
    if best_state is None:
        raise TrainingError("trainer did not produce an artifact")

    output_root.mkdir(parents=True)
    files = {
        "encoder_weights": output_root / "encoder-weights.f32",
        "encoder_bias": output_root / "encoder-bias.f32",
        "decoder_weights": output_root / "decoder-weights.f32",
        "decoder_bias": output_root / "decoder-bias.f32",
    }
    for name, tensor in (("encoder_weights", best_state["encoder_weight"]), ("encoder_bias", best_state["encoder_bias"]), ("decoder_weights", best_state["decoder_weight"]), ("decoder_bias", best_state["decoder_bias"])):
        write_f32(files[name], tensor)
    requirements_path = script_dir() / REQUIREMENTS_LOCK_FILE
    artifact = {
        "schema_version": 1,
        "trainer": {
            "id": TRAINER_ID,
            "version": TRAINER_VERSION,
            "source_hash": sha256_file(Path(__file__)),
            "requirements_lock": f"{REQUIREMENTS_LOCK_FILE};sha256={sha256_file(requirements_path)}",
        },
        "input_materialization_manifest_sha256": sha256_file(materialization_root / "manifest.json"),
        "prepared_study_manifest_sha256": materialization_hash,
        "architecture": {
            "family": "linear_binary_autoencoder_ste",
            "input_dimension": dimension,
            "bit_count": bit_count,
            "encoder_activation": "tanh_sign_ste_v1",
            "decoder": "linear",
        },
        "training": {
            "seed": seed,
            "epochs": epochs,
            "batch_size": batch_size,
            "learning_rate": learning_rate,
            "validation_fraction": validation_fraction,
            "quantization_weight": quantization_weight,
            "balance_weight": balance_weight,
            "train_vector_count": len(train_indices),
            "validation_vector_count": len(validation_indices),
            "best_document_only_validation_loss": best_loss,
        },
        "weights": {
            "encoder_weights": {"path": files["encoder_weights"].name, "sha256": sha256_file(files["encoder_weights"]), "shape": [bit_count, dimension], "layout": "row_major_out_by_in", "dtype": "float32_le"},
            "encoder_bias": {"path": files["encoder_bias"].name, "sha256": sha256_file(files["encoder_bias"]), "shape": [bit_count], "dtype": "float32_le"},
            "decoder_weights": {"path": files["decoder_weights"].name, "sha256": sha256_file(files["decoder_weights"]), "shape": [dimension, bit_count], "layout": "row_major_out_by_in", "dtype": "float32_le"},
            "decoder_bias": {"path": files["decoder_bias"].name, "sha256": sha256_file(files["decoder_bias"]), "shape": [dimension], "dtype": "float32_le"},
        },
    }
    (output_root / "artifact.json").write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return artifact


def run_self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-memory-ae-trainer-") as raw_root:
        root = Path(raw_root)
        vectors = root / "train-vectors.f32"
        vectors.write_bytes(b"".join(F32.pack(value) for value in (1.0, 0.0, 0.0, 1.0)))
        ids = root / "train-document-ids.jsonl"
        ids.write_text('{"id":"ru:a"}\n{"id":"en:b"}\n', encoding="utf-8", newline="\n")
        manifest = {
            "schema_version": 1,
            "prepared_study_manifest_sha256": "a" * 64,
            "vector_format": {"dimension": 2},
            "outputs": {
                "train_ids": {"path": ids.name, "sha256": sha256_file(ids), "count": 2},
                "train_vectors": {"path": vectors.name, "sha256": sha256_file(vectors), "count": 2, "dimension": 2},
            },
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8", newline="\n")
        loaded_ids, loaded_vectors, dimension, _ = load_materialization(root)
        if loaded_ids != ["ru:a", "en:b"] or loaded_vectors != vectors or dimension != 2:
            print("self-test failed: materialization load contract", file=sys.stderr)
            return 1
        vectors.write_bytes(vectors.read_bytes() + F32.pack(0.0))
        try:
            load_materialization(root)
        except TrainingError:
            pass
        else:
            print("self-test failed: malformed vector file was accepted", file=sys.stderr)
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
