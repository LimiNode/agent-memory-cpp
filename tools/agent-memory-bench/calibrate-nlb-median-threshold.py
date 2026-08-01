#!/usr/bin/env python3
"""Materialize a provenance-bound per-bit quantile threshold for a frozen NLB artifact."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


CALIBRATOR_ID = "agent-memory-cpp:nlb-median-threshold-calibrator"
CALIBRATOR_VERSION = "v1"
FAMILY = "nlb_median_threshold_v1"
POLICY = "per_bit_projection_median_v1"
QUANTILE_FAMILY = "nlb_quantile_threshold_v1"
QUANTILE_POLICY = "per_bit_projection_quantile_v1"


class CalibrationError(RuntimeError):
    """Raised when a source artifact or a document-only calibration split is invalid."""


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
        raise CalibrationError(f"{field} must be an object")
    return value


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CalibrationError(f"{field} must be a non-empty string")
    return value


def require_sha256(value: Any, field: str) -> str:
    text = require_string(value, field)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise CalibrationError(f"{field} must be a lowercase SHA-256 digest")
    return text


def load_trainer_module() -> Any:
    path = script_dir() / "train-binary-autoencoder.py"
    spec = importlib.util.spec_from_file_location("agent_memory_binary_ae_trainer", path)
    if spec is None or spec.loader is None:
        raise CalibrationError("cannot load binary autoencoder trainer helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical_ids_sha256(ids: list[str]) -> str:
    return hashlib.sha256("".join(f"{identifier}\n" for identifier in ids).encode("utf-8")).hexdigest()


def load_source_artifact(path: Path) -> dict[str, Any]:
    try:
        root = require_mapping(json.loads(path.read_text(encoding="utf-8")), "source artifact")
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"cannot read source artifact JSON: {exc}") from exc
    if root.get("schema_version") != 1:
        raise CalibrationError("source artifact schema_version must equal 1")
    architecture = require_mapping(root.get("architecture"), "source artifact.architecture")
    if architecture.get("family") != "nlb_paper_tied_v1":
        raise CalibrationError("source artifact must be nlb_paper_tied_v1")
    if architecture.get("encoder_activation") != "hard_step_no_ste_v1":
        raise CalibrationError("source artifact encoder activation is unsupported")
    training = require_mapping(root.get("training"), "source artifact.training")
    if training.get("objective") != "nlb_paper_tied_v1":
        raise CalibrationError("source artifact training objective is unsupported")
    return root


def require_nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CalibrationError(f"{field} must be a non-negative integer")
    return value


def require_quantile(value: float) -> float:
    """Returns a finite open-interval quantile used only with training documents."""
    if not 0.0 < value < 1.0:
        raise CalibrationError("quantile must be strictly between 0 and 1")
    return value


def calibration_contract(quantile: float) -> tuple[str, str, str]:
    """Keeps legacy median artifacts distinct from non-median threshold artifacts."""
    if quantile == 0.5:
        return FAMILY, POLICY, "affine_hard_step_median_threshold_v1"
    return (
        QUANTILE_FAMILY,
        QUANTILE_POLICY,
        "affine_hard_step_quantile_threshold_v1",
    )


def verify_calibration_is_held_out(
    trainer: Any,
    materialization_root: Path,
    calibration_ids: list[str],
) -> None:
    """Reject an artifact if its document-only calibration overlaps evaluation documents."""
    try:
        manifest = require_mapping(
            json.loads((materialization_root / "manifest.json").read_text(encoding="utf-8")),
            "materialization manifest",
        )
        outputs = require_mapping(manifest.get("outputs"), "materialization manifest.outputs")
        evaluation_entry = require_mapping(
            outputs.get("evaluation_document_ids"),
            "materialization manifest.outputs.evaluation_document_ids",
        )
        evaluation_path = trainer.resolve_plain_file(
            materialization_root,
            evaluation_entry.get("path"),
            "materialization manifest.outputs.evaluation_document_ids.path",
        )
        expected_hash = require_sha256(
            evaluation_entry.get("sha256"),
            "materialization manifest.outputs.evaluation_document_ids.sha256",
        )
        if sha256_file(evaluation_path) != expected_hash:
            raise CalibrationError("evaluation document ID hash mismatch")
        evaluation_ids = trainer.load_ids(evaluation_path)
    except (OSError, json.JSONDecodeError, trainer.TrainingError) as exc:
        raise CalibrationError(f"cannot verify held-out evaluation document IDs: {exc}") from exc
    overlap = set(calibration_ids).intersection(evaluation_ids)
    if overlap:
        raise CalibrationError("document-only calibration IDs overlap held-out evaluation documents")


def copy_verified_weight(
    source_root: Path,
    output_root: Path,
    weights: dict[str, Any],
    name: str,
) -> dict[str, Any]:
    entry = require_mapping(weights.get(name), f"source artifact.weights.{name}")
    relative_path = require_string(entry.get("path"), f"source artifact.weights.{name}.path")
    expected_hash = require_sha256(entry.get("sha256"), f"source artifact.weights.{name}.sha256")
    source_path = source_root / relative_path
    if sha256_file(source_path) != expected_hash:
        raise CalibrationError(f"source artifact weight hash mismatch: {name}")
    destination = output_root / source_path.name
    shutil.copyfile(source_path, destination)
    copied = dict(entry)
    copied["path"] = destination.name
    copied["sha256"] = sha256_file(destination)
    return copied


def calibrate(
    source_artifact_path: Path,
    materialization_root: Path,
    output_root: Path,
    train_ids_path: Path | None,
    quantile: float,
) -> None:
    if output_root.exists():
        raise CalibrationError(f"output root already exists: {output_root}")
    trainer = load_trainer_module()
    try:
        trainer.verify_environment()
    except trainer.TrainingError as exc:
        raise CalibrationError(f"unsupported calibration environment: {exc}") from exc
    source = load_source_artifact(source_artifact_path)
    source_root = source_artifact_path.parent
    architecture = require_mapping(source["architecture"], "source artifact.architecture")
    training = require_mapping(source["training"], "source artifact.training")
    weights = require_mapping(source["weights"], "source artifact.weights")
    bit_count = trainer.require_positive_int(architecture.get("bit_count"), "source artifact bit_count")
    dimension = trainer.require_positive_int(architecture.get("input_dimension"), "source artifact input_dimension")
    seed = require_nonnegative_int(training.get("seed"), "source artifact training seed")
    validation_fraction = training.get("validation_fraction")
    if not isinstance(validation_fraction, (int, float)) or not 0.0 < validation_fraction < 0.5:
        raise CalibrationError("source artifact validation_fraction is invalid")
    ids, vectors_path, materialization_dimension, prepared_manifest_hash = trainer.load_materialization(materialization_root)
    if materialization_dimension != dimension:
        raise CalibrationError("source artifact and materialization dimensions differ")
    if trainer.sha256_file(materialization_root / "manifest.json") != source.get("input_materialization_manifest_sha256"):
        raise CalibrationError("source artifact materialization manifest does not match calibration root")
    if prepared_manifest_hash != source.get("prepared_study_manifest_sha256"):
        raise CalibrationError("source artifact prepared-study manifest does not match calibration root")
    explicit = training.get("explicit_id_lists")
    if explicit is not None:
        if train_ids_path is None:
            raise CalibrationError("source artifact requires --train-ids for median calibration")
        explicit = require_mapping(explicit, "source artifact.training.explicit_id_lists")
        if sha256_file(train_ids_path) != require_sha256(explicit.get("train_sha256"), "explicit train hash"):
            raise CalibrationError("explicit calibration train ID hash does not match source artifact")
        try:
            calibration_ids = trainer.load_ids(train_ids_path)
        except (OSError, trainer.TrainingError) as exc:
            raise CalibrationError(f"cannot load explicit calibration IDs: {exc}") from exc
        positions = {identifier: index for index, identifier in enumerate(ids)}
        try:
            calibration_indices = [positions[identifier] for identifier in calibration_ids]
        except KeyError as exc:
            raise CalibrationError("explicit calibration ID is absent from materialization") from exc
        split_id = "external_canonical_id_lists_v1"
    else:
        if train_ids_path is not None:
            raise CalibrationError("--train-ids is only valid for an explicitly trained source artifact")
        calibration_indices = [
            index for index, identifier in enumerate(ids)
            if not trainer.stable_split(identifier, seed, float(validation_fraction))
        ]
        calibration_ids = [ids[index] for index in calibration_indices]
        split_id = "stable_document_only_train_v1"
    if not calibration_ids:
        raise CalibrationError("document-only calibration split is empty")
    verify_calibration_is_held_out(trainer, materialization_root, calibration_ids)
    try:
        import numpy
    except ImportError as exc:
        raise CalibrationError("calibration requires numpy from the trainer requirements lock") from exc
    source_weight = require_mapping(weights.get("encoder_weights"), "source artifact.weights.encoder_weights")
    expected_weight_bytes = bit_count * dimension * 4
    weight_path = source_root / require_string(source_weight.get("path"), "encoder weight path")
    if weight_path.stat().st_size != expected_weight_bytes:
        raise CalibrationError("source encoder weight byte size is invalid")
    matrix = numpy.fromfile(weight_path, dtype="<f4").reshape(bit_count, dimension)
    vectors = numpy.memmap(vectors_path, dtype="<f4", mode="r", shape=(len(ids), dimension))
    projections = numpy.asarray(vectors[calibration_indices], dtype=numpy.float32) @ matrix.T
    if quantile == 0.5:
        thresholds = numpy.median(projections, axis=0).astype(numpy.float32)
    else:
        thresholds = numpy.quantile(
            projections,
            quantile,
            axis=0,
            method="linear",
        ).astype(numpy.float32)
    if not numpy.isfinite(thresholds).all():
        raise CalibrationError("calibration produced a non-finite encoder bias")
    output_root.mkdir(parents=True)
    copied_weights = {
        "encoder_weights": copy_verified_weight(source_root, output_root, weights, "encoder_weights"),
        "decoder_bias": copy_verified_weight(source_root, output_root, weights, "decoder_bias"),
    }
    bias_path = output_root / "encoder-bias.f32"
    (-thresholds).astype("<f4").tofile(bias_path)
    copied_weights["encoder_bias"] = {
        "path": bias_path.name, "sha256": sha256_file(bias_path),
        "shape": [bit_count], "dtype": "float32_le",
    }
    family, policy, encoder_activation = calibration_contract(quantile)
    calibration = {
        "policy": policy,
        "split_id": split_id,
        "document_count": len(calibration_ids),
        "document_ids_sha256": canonical_ids_sha256(calibration_ids),
    }
    if quantile != 0.5:
        calibration["quantile"] = quantile
    artifact = {
        "schema_version": 1,
        "trainer": {
            "id": CALIBRATOR_ID, "version": CALIBRATOR_VERSION,
            "source_hash": sha256_file(Path(__file__)),
            "requirements_lock": f"{trainer.REQUIREMENTS_LOCK_FILE};sha256={trainer.sha256_file(script_dir() / trainer.REQUIREMENTS_LOCK_FILE)}",
        },
        "input_materialization_manifest_sha256": trainer.sha256_file(materialization_root / "manifest.json"),
        "prepared_study_manifest_sha256": prepared_manifest_hash,
        "source_encoder_artifact_sha256": sha256_file(source_artifact_path),
        "architecture": {
            "family": family, "input_dimension": dimension, "bit_count": bit_count,
            "encoder_activation": encoder_activation,
            "decoder": "tied_transpose_tanh", "code_value_encoding": "zero_one",
            "input_transform": "clip_minus_one_one_v1",
        },
        "training": dict(training),
        "calibration": calibration,
        "weights": copied_weights,
    }
    (output_root / "artifact.json").write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def run_self_test() -> int:
    if canonical_ids_sha256(["ru:a", "en:b"]) != canonical_ids_sha256(["ru:a", "en:b"]):
        print("self-test failed: canonical ID hash is unstable", file=sys.stderr)
        return 1
    if canonical_ids_sha256(["ru:a", "en:b"]) == canonical_ids_sha256(["en:b", "ru:a"]):
        print("self-test failed: canonical ID order was ignored", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="agent-memory-nlb-median-calibrator-") as temporary:
        path = Path(temporary) / "wrong-family.json"
        path.write_text(json.dumps({"schema_version": 1, "architecture": {"family": "wrong"}, "training": {}}), encoding="utf-8")
        try:
            load_source_artifact(path)
        except CalibrationError:
            pass
        else:
            print("self-test failed: unsupported source family was accepted", file=sys.stderr)
            return 1
    try:
        require_quantile(0.0)
    except CalibrationError:
        pass
    else:
        print("self-test failed: zero quantile was accepted", file=sys.stderr)
        return 1
    if calibration_contract(0.5) != (
        FAMILY,
        POLICY,
        "affine_hard_step_median_threshold_v1",
    ) or calibration_contract(0.75)[0] != QUANTILE_FAMILY:
        print("self-test failed: threshold artifact families are unstable", file=sys.stderr)
        return 1
    print("NLB median-threshold calibrator self-test ok")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_artifact", type=Path, nargs="?")
    parser.add_argument("materialization_root", type=Path, nargs="?")
    parser.add_argument("output_root", type=Path, nargs="?")
    parser.add_argument("--train-ids", type=Path)
    parser.add_argument(
        "--quantile",
        type=float,
        default=0.5,
        help="Per-bit document-only projection quantile (default: 0.5, the median).",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        if args.source_artifact or args.materialization_root or args.output_root:
            parser.error("--self-test cannot be combined with calibration arguments")
        return run_self_test()
    if not args.source_artifact or not args.materialization_root or not args.output_root:
        parser.error("source_artifact, materialization_root, and output_root are required")
    try:
        calibrate(
            args.source_artifact,
            args.materialization_root,
            args.output_root,
            args.train_ids,
            require_quantile(args.quantile),
        )
    except CalibrationError as exc:
        print(f"calibrate-nlb-median-threshold: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
