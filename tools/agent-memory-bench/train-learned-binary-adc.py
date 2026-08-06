#!/usr/bin/env python3
"""Train a label-free binary document code directly for float-query ADC."""

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
from pathlib import Path
from typing import Any

import numpy

sys.dont_write_bytecode = True

TRAINER_ID = "agent-memory-cpp:learned-binary-adc-trainer"
TRAINER_VERSION = "v1"
ARTIFACT_FAMILY = "learned_binary_adc_v1"
REQUIREMENTS_LOCK = "requirements-learned-binary-adc-trainer.txt"
F32 = struct.Struct("<f")


class TrainingError(RuntimeError):
    """Raised when a learned ADC input or output violates the experiment contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_ids_sha256(ids: list[str]) -> str:
    return hashlib.sha256("".join(f"{identifier}\n" for identifier in sorted(ids)).encode("utf-8")).hexdigest()


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise TrainingError(f"{field} must be a lowercase SHA-256")
    return value


def require_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise TrainingError(f"{field} must be positive")
    return value


def plain_file(root: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str):
        raise TrainingError(f"{field} must be a file name")
    path = Path(value)
    if path.is_absolute() or path.name != value:
        raise TrainingError(f"{field} must be a plain file name")
    return root / path


def load_ids(path: Path) -> list[str]:
    values = [json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines()]
    if not values or len(values) != len(set(values)) or any(not isinstance(value, str) or not value for value in values):
        raise TrainingError("training IDs must be nonempty and unique")
    return values


def verify_environment() -> None:
    lines = (Path(__file__).parent / REQUIREMENTS_LOCK).read_text(encoding="utf-8").splitlines()
    python_version = next((line.split(":", 1)[1].strip() for line in lines if line.startswith("# python-version:")), "")
    expected = {name: version for line in lines if "==" in line and not line.startswith("#") for name, version in [line.split("==", 1)]}
    if platform.python_version() != python_version:
        raise TrainingError(f"Python version mismatch: expected {python_version}, got {platform.python_version()}")
    for package in ("torch", "numpy"):
        if importlib.metadata.version(package) != expected.get(package):
            raise TrainingError(f"{package} version differs from {REQUIREMENTS_LOCK}")


def load_materialization(root: Path) -> tuple[dict[str, Any], list[str], Any]:
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrainingError(f"cannot read materialization manifest: {exc}") from exc
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("outputs"), dict) or not isinstance(manifest.get("vector_format"), dict):
        raise TrainingError("unsupported materialization manifest")
    vector_format = manifest["vector_format"]
    dimension = require_positive_int(vector_format.get("dimension"), "vector_format.dimension")
    if vector_format.get("dtype") != "float32_le" or vector_format.get("endianness") != "little":
        raise TrainingError("materialization vector format is unsupported")
    outputs = manifest["outputs"]
    ids_entry = outputs.get("train_ids")
    vectors_entry = outputs.get("train_vectors")
    if not isinstance(ids_entry, dict) or not isinstance(vectors_entry, dict):
        raise TrainingError("materialization training outputs are missing")
    ids_path = plain_file(root, ids_entry.get("path"), "outputs.train_ids.path")
    vectors_path = plain_file(root, vectors_entry.get("path"), "outputs.train_vectors.path")
    if not ids_path.is_file() or not vectors_path.is_file() or sha256_file(ids_path) != require_sha256(ids_entry.get("sha256"), "outputs.train_ids.sha256") or sha256_file(vectors_path) != require_sha256(vectors_entry.get("sha256"), "outputs.train_vectors.sha256"):
        raise TrainingError("materialization training output hash differs")
    ids = load_ids(ids_path)
    if vectors_entry.get("count") != len(ids) or vectors_entry.get("dimension") != dimension or vectors_path.stat().st_size != len(ids) * dimension * F32.size:
        raise TrainingError("materialization training vector shape is invalid")
    embeddings = manifest.get("embedding")
    if not isinstance(embeddings, dict) or embeddings.get("normalized") is not True:
        raise TrainingError("materialization embedding contract is unsupported")
    vectors = numpy.memmap(vectors_path, dtype="<f4", mode="r", shape=(len(ids), dimension))
    return manifest, ids, vectors


def stable_split(ids: list[str], seed: int, validation_fraction: float) -> tuple[list[int], list[int]]:
    if not 0.0 < validation_fraction < 0.5:
        raise TrainingError("validation fraction must be between zero and 0.5")
    train: list[int] = []
    validation: list[int] = []
    for index, identifier in enumerate(ids):
        value = int.from_bytes(hashlib.sha256(f"{seed}\0learned-binary-adc-validation\0{identifier}".encode("utf-8")).digest()[:8], "big") / float(1 << 64)
        (validation if value < validation_fraction else train).append(index)
    if not train or not validation:
        raise TrainingError("stable training split is empty")
    return train, validation


def itq_weights(values: Any, bit_count: int, seed: int, iterations: int) -> Any:
    centered = numpy.asarray(values, dtype=numpy.float64) - numpy.asarray(values, dtype=numpy.float64).mean(axis=0)
    _, _, right = numpy.linalg.svd(centered, full_matrices=False)
    weights = right[:bit_count]
    projected = centered @ weights.T
    generator = numpy.random.default_rng(seed)
    rotation, _ = numpy.linalg.qr(generator.standard_normal((bit_count, bit_count)))
    for _ in range(iterations):
        binary = numpy.where(projected @ rotation >= 0.0, 1.0, -1.0)
        left, _, right = numpy.linalg.svd(projected.T @ binary, full_matrices=False)
        rotation = left @ right
    return (rotation.T @ weights).astype(numpy.float32)


def conditional_centers(values: Any, codes: Any) -> Any:
    centers = numpy.empty((values.shape[1], 2), dtype=numpy.float32)
    for symbol in (0, 1):
        selected = codes == symbol
        counts = selected.sum(axis=0)
        if numpy.any(counts == 0):
            raise TrainingError("initial binary quantizer has an empty cell")
        centers[:, symbol] = (values * selected).sum(axis=0) / counts
    return centers


def epoch_permutation(indices: list[int], seed: int, epoch: int) -> list[int]:
    result = list(indices)
    random.Random(int.from_bytes(hashlib.sha256(f"{seed}\0learned-binary-adc-shuffle\0{epoch}".encode("utf-8")).digest()[:16], "big")).shuffle(result)
    return result


def write_f32(path: Path, values: Any) -> None:
    payload = numpy.asarray(values.detach().cpu().numpy(), dtype="<f4")
    if not numpy.isfinite(payload).all():
        raise TrainingError("exported weights are non-finite")
    path.write_bytes(payload.tobytes())


def health(vectors: Any, indices: list[int], weight: Any, threshold: Any, batch_size: int) -> dict[str, Any]:
    weights = weight.detach().cpu().numpy(); thresholds = threshold.detach().cpu().numpy()
    ones = numpy.zeros(weights.shape[0], dtype=numpy.int64); unique: set[bytes] = set()
    for start in range(0, len(indices), batch_size):
        code = numpy.asarray(vectors[indices[start:start + batch_size]], dtype=numpy.float32) @ weights.T + thresholds >= 0.0
        ones += code.sum(axis=0, dtype=numpy.int64); unique.update(row.tobytes() for row in numpy.packbits(code, axis=1, bitorder="little"))
    occupancy = ones / float(len(indices))
    return {"vector_count": len(indices), "unique_code_count": len(unique), "unique_code_fraction": len(unique) / float(len(indices)), "constant_bit_count": int(numpy.count_nonzero((occupancy == 0.0) | (occupancy == 1.0))), "minimum_bit_occupancy": float(occupancy.min()), "maximum_bit_occupancy": float(occupancy.max())}


def train(args: Any) -> dict[str, Any]:
    verify_environment()
    numeric_values = {
        "learning_rate": args.learning_rate,
        "temperature": args.temperature,
        "quantization_weight": args.quantization_weight,
        "orthogonality_weight": args.orthogonality_weight,
        "balance_weight": args.balance_weight,
        "validation_fraction": args.validation_fraction,
    }
    if (
        args.output_root.exists()
        or args.bit_count <= 0
        or args.epochs <= 0
        or args.batch_size <= 1
        or args.torch_threads <= 0
        or args.itq_iterations <= 0
        or any(not math.isfinite(value) or value < 0.0 for value in numeric_values.values())
        or args.learning_rate <= 0.0
        or args.temperature <= 0.0
        or args.validation_fraction <= 0.0
        or args.validation_fraction >= 0.5
    ):
        raise TrainingError("output root exists or training parameters are invalid")
    import torch
    manifest, ids, vectors = load_materialization(args.materialization_root)
    train_indices, validation_indices = stable_split(ids, args.seed, args.validation_fraction)
    if args.bit_count > vectors.shape[1]:
        raise TrainingError("bit count exceeds input dimension")
    torch.set_num_threads(args.torch_threads); torch.manual_seed(args.seed); torch.use_deterministic_algorithms(True)
    initial_weight = itq_weights(vectors[train_indices], args.bit_count, args.seed, args.itq_iterations)
    initial_projection = numpy.asarray(vectors[train_indices], dtype=numpy.float32) @ initial_weight.T
    initial_threshold = -numpy.median(initial_projection, axis=0).astype(numpy.float32)
    initial_codes = (initial_projection + initial_threshold >= 0.0).astype(numpy.uint8)
    initial_centers = conditional_centers(initial_projection, initial_codes)
    weight = torch.nn.Parameter(torch.from_numpy(initial_weight.copy()))
    threshold = torch.nn.Parameter(torch.from_numpy(initial_threshold.copy()))
    centers = torch.nn.Parameter(torch.from_numpy(initial_centers.copy()))
    optimizer = torch.optim.AdamW([weight, threshold, centers], lr=args.learning_rate, weight_decay=0.0)
    identity = torch.eye(args.bit_count, dtype=torch.float32)

    def batch(indices: list[int]) -> Any:
        return torch.from_numpy(numpy.asarray(vectors[indices], dtype=numpy.float32).copy())

    def losses(values: Any) -> tuple[Any, dict[str, Any]]:
        projected = values @ weight.T
        logits = projected + threshold
        soft = torch.sigmoid(args.temperature * logits)
        hard = (logits >= 0.0).to(values.dtype)
        code = soft + (hard - soft).detach()
        decoded = centers[:, 0] + code * (centers[:, 1] - centers[:, 0])
        student = -torch.mean((projected[:, None, :] - decoded[None, :, :]) ** 2, dim=2)
        teacher = values @ values.T
        mask = ~torch.eye(len(values), dtype=torch.bool)
        def normalize(scores: Any) -> Any:
            return (scores - scores.mean(dim=1, keepdim=True)) / scores.std(dim=1, keepdim=True).clamp_min(1.0e-6)
        geometry = torch.mean((normalize(student)[mask] - normalize(teacher)[mask]) ** 2)
        quantization = torch.mean((projected - decoded) ** 2)
        orthogonality = torch.mean((weight @ weight.T - identity) ** 2)
        balance = torch.mean((soft.mean(dim=0) - 0.5) ** 2)
        total = geometry + args.quantization_weight * quantization + args.orthogonality_weight * orthogonality + args.balance_weight * balance
        return total, {"geometry": geometry, "quantization": quantization, "orthogonality": orthogonality, "balance": balance}

    def validation_loss() -> float:
        """Evaluate deterministic, bounded document-only pair batches.

        A full validation pair matrix would be quadratic in the approximately
        five-thousand held-out documents.  Fixed-size chunks preserve the same
        pairwise objective used during training without making checkpoint
        selection depend on available host RAM.
        """
        total = 0.0
        count = 0
        for start in range(0, len(validation_indices), args.batch_size):
            values = batch(validation_indices[start:start + args.batch_size])
            current, _ = losses(values)
            batch_count = len(values)
            total += float(current) * batch_count
            count += batch_count
        if count == 0:
            raise TrainingError("validation split is empty")
        return total / count

    best_loss = math.inf; best_state: dict[str, Any] | None = None
    for epoch in range(args.epochs):
        permutation = epoch_permutation(train_indices, args.seed, epoch)
        for start in range(0, len(train_indices), args.batch_size):
            values = batch(permutation[start:start + args.batch_size])
            optimizer.zero_grad(set_to_none=True); total, _ = losses(values); total.backward(); optimizer.step()
        with torch.no_grad():
            current_validation_loss = validation_loss()
            if current_validation_loss < best_loss:
                best_loss = current_validation_loss; best_state = {"weight": weight.detach().clone(), "threshold": threshold.detach().clone(), "centers": centers.detach().clone(), "epoch": epoch + 1}
    if best_state is None:
        raise TrainingError("trainer did not select a checkpoint")
    train_health = health(vectors, train_indices, best_state["weight"], best_state["threshold"], args.batch_size)
    validation_health = health(vectors, validation_indices, best_state["weight"], best_state["threshold"], args.batch_size)
    initial_weight_tensor = torch.from_numpy(initial_weight.copy())
    initial_threshold_tensor = torch.from_numpy(initial_threshold.copy())
    initial_centers_tensor = torch.from_numpy(initial_centers.copy())
    initial_train_health = health(vectors, train_indices, initial_weight_tensor, initial_threshold_tensor, args.batch_size)
    initial_validation_health = health(vectors, validation_indices, initial_weight_tensor, initial_threshold_tensor, args.batch_size)
    if train_health["unique_code_count"] < 2 or validation_health["unique_code_count"] < 2:
        raise TrainingError("learned ADC hard codes collapsed")
    args.output_root.mkdir(parents=True)
    files = {
        "projection_weights": args.output_root / "projection-weights.f32",
        "thresholds": args.output_root / "thresholds.f32",
        "centroids": args.output_root / "centroids.f32",
        "initial_projection_weights": args.output_root / "initial-projection-weights.f32",
        "initial_thresholds": args.output_root / "initial-thresholds.f32",
        "initial_centroids": args.output_root / "initial-centroids.f32",
    }
    for name, tensor in (("projection_weights", best_state["weight"]), ("thresholds", best_state["threshold"]), ("centroids", best_state["centers"])):
        write_f32(files[name], tensor)
    for name, tensor in (("initial_projection_weights", initial_weight_tensor), ("initial_thresholds", initial_threshold_tensor), ("initial_centroids", initial_centers_tensor)):
        write_f32(files[name], tensor)

    def descriptors(prefix: str) -> dict[str, Any]:
        return {
            "projection_weights": {
                "path": files[f"{prefix}projection_weights"].name,
                "sha256": sha256_file(files[f"{prefix}projection_weights"]),
                "shape": [args.bit_count, vectors.shape[1]],
                "layout": "row_major_out_by_in",
                "dtype": "float32_le",
            },
            "thresholds": {
                "path": files[f"{prefix}thresholds"].name,
                "sha256": sha256_file(files[f"{prefix}thresholds"]),
                "shape": [args.bit_count],
                "dtype": "float32_le",
            },
            "centroids": {
                "path": files[f"{prefix}centroids"].name,
                "sha256": sha256_file(files[f"{prefix}centroids"]),
                "shape": [args.bit_count, 2],
                "layout": "coordinate_symbol",
                "dtype": "float32_le",
            },
        }

    learned_descriptors = descriptors("")
    initial_descriptors = descriptors("initial_")
    artifact = {
        "schema_version": 1,
        "trainer": {
            "id": TRAINER_ID,
            "version": TRAINER_VERSION,
            "source_hash": sha256_file(Path(__file__)),
            "requirements_lock": f"{REQUIREMENTS_LOCK};sha256={sha256_file(Path(__file__).with_name(REQUIREMENTS_LOCK))}",
        },
        "input_materialization_manifest_sha256": sha256_file(args.materialization_root / "manifest.json"),
        "prepared_study_manifest_sha256": manifest["prepared_study_manifest_sha256"],
        "architecture": {
            "family": ARTIFACT_FAMILY,
            "input_dimension": vectors.shape[1],
            "bit_count": args.bit_count,
            "input_transform": "identity_normalized_e5_v1",
            "document_quantizer": "learned_threshold_hard_step_v1",
            "candidate_scoring": "continuous_query_binary_adc_l2_v1",
        },
        "training": {
            "seed": args.seed,
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "learning_rate": args.learning_rate,
            "temperature": args.temperature,
            "itq_iterations": args.itq_iterations,
            "queries_or_qrels_used": False,
            "objective": "document_pairwise_adc_geometry_distillation_v1",
            "loss_weights": {
                "geometry": 1.0,
                "quantization": args.quantization_weight,
                "orthogonality": args.orthogonality_weight,
                "balance": args.balance_weight,
            },
            "validation": {
                "id": "stable_sha256_document_split_v1",
                "fraction": args.validation_fraction,
                "train_document_ids_sha256": canonical_ids_sha256([ids[index] for index in train_indices]),
                "validation_document_ids_sha256": canonical_ids_sha256([ids[index] for index in validation_indices]),
                "selected_epoch": best_state["epoch"],
                "selected_document_only_loss": best_loss,
            },
            "hard_code_health": {"train": train_health, "validation": validation_health},
            "source_materialization_outputs_sha256": canonical_json_sha256({
                name: value.get("sha256")
                for name, value in manifest["outputs"].items()
                if isinstance(value, dict)
            }),
            "torch_threads": args.torch_threads,
        },
        "weights": learned_descriptors,
    }
    initial_artifact = json.loads(json.dumps(artifact))
    initial_artifact["training"]["objective"] = "initial_itq_binary_adc_control_v1"
    initial_artifact["training"]["epochs"] = 0
    initial_artifact["training"]["validation"]["selected_epoch"] = 0
    initial_artifact["training"]["validation"]["selected_document_only_loss"] = None
    initial_artifact["training"]["hard_code_health"] = {
        "train": initial_train_health,
        "validation": initial_validation_health,
    }
    initial_artifact["weights"] = initial_descriptors
    (args.output_root / "artifact.json").write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    (args.output_root / "initial-itq-control-artifact.json").write_text(json.dumps(initial_artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return artifact


def run_self_test() -> int:
    values = numpy.asarray([[0.0, 0.5], [1.0, -0.5], [-1.0, 0.2]], dtype=numpy.float32)
    codes = numpy.asarray([[0, 1], [1, 0], [0, 1]], dtype=numpy.uint8)
    if conditional_centers(values, codes).shape != (2, 2):
        print("self-test failed: conditional centers", file=sys.stderr); return 1
    try:
        stable_split(["only"], 42, 0.2)
        print("self-test failed: empty split accepted", file=sys.stderr); return 1
    except TrainingError:
        pass
    print("learned binary ADC trainer self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--materialization-root", type=Path); parser.add_argument("--output-root", type=Path); parser.add_argument("--bit-count", type=int, default=128); parser.add_argument("--seed", type=int, default=42); parser.add_argument("--epochs", type=int, default=12); parser.add_argument("--batch-size", type=int, default=128); parser.add_argument("--learning-rate", type=float, default=1.0e-3); parser.add_argument("--temperature", type=float, default=4.0); parser.add_argument("--quantization-weight", type=float, default=0.1); parser.add_argument("--orthogonality-weight", type=float, default=0.1); parser.add_argument("--balance-weight", type=float, default=0.1); parser.add_argument("--validation-fraction", type=float, default=0.2); parser.add_argument("--itq-iterations", type=int, default=50); parser.add_argument("--torch-threads", type=int, default=18); args = parser.parse_args(argv)
    try:
        if args.self_test: return run_self_test()
        if args.materialization_root is None or args.output_root is None: parser.error("--materialization-root and --output-root are required")
        artifact = train(args); print(f"trained learned binary ADC with {artifact['architecture']['bit_count']} bits")
    except (TrainingError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"train-learned-binary-adc: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
