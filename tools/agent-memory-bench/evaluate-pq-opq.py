#!/usr/bin/env python3
"""Reference equal-payload PQ/OPQ asymmetric-distance evaluation.

This NumPy-only harness is deliberately separate from the scalar-code study.
It trains 4-bit product codebooks only on a disjoint calibration root, stores
one packed nibble per subspace, and ranks held-out documents with an exact
query-to-centroid lookup table.  OPQ is an alternating orthogonal Procrustes
rotation, not a PCA projection relabelled as OPQ.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import numpy


def _load_shared() -> Any:
    path = Path(__file__).with_name("evaluate-projection-quantization.py")
    spec = importlib.util.spec_from_file_location("projection_quantization_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load projection quantization helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = _load_shared()
EvaluationError = shared.EvaluationError


def sha256_array(values: Any) -> str:
    return hashlib.sha256(numpy.asarray(values, dtype="<f4").tobytes()).hexdigest()


def require_payload(payload_bytes: int, dimension: int, code_bits: int) -> int:
    if code_bits not in (4, 8) or payload_bytes <= 0:
        raise EvaluationError("PQ code width is unsupported")
    subspaces = payload_bytes * 8 // code_bits
    if subspaces <= 0 or dimension % subspaces:
        raise EvaluationError("PQ payload must divide the embedding dimension")
    return subspaces


def train_kmeans(values: Any, centroid_count: int, seed: int, iterations: int, initial: Any | None = None) -> tuple[Any, Any, float]:
    """Deterministic Lloyd k-means with explicit warm starts for OPQ."""
    values = numpy.asarray(values, dtype=numpy.float32)
    if values.ndim != 2 or values.shape[0] < centroid_count or centroid_count < 2 or iterations <= 0:
        raise EvaluationError("invalid PQ k-means input")
    if initial is None:
        generator = numpy.random.default_rng(seed)
        centers = values[generator.choice(values.shape[0], size=centroid_count, replace=False)].copy()
    else:
        centers = numpy.asarray(initial, dtype=numpy.float32).copy()
        if centers.shape != (centroid_count, values.shape[1]):
            raise EvaluationError("PQ warm-start centroid shape is invalid")
    def assign(current: Any) -> tuple[Any, Any]:
        codes = numpy.empty(values.shape[0], dtype=numpy.uint8)
        errors = numpy.empty(values.shape[0], dtype=numpy.float32)
        for start in range(0, values.shape[0], 1024):
            stop = min(values.shape[0], start + 1024)
            distances = ((values[start:stop, None, :] - current[None, :, :]) ** 2).sum(axis=2)
            chosen = distances.argmin(axis=1)
            codes[start:stop] = chosen
            errors[start:stop] = distances[numpy.arange(stop - start), chosen]
        return codes, errors
    for _ in range(iterations):
        codes, _ = assign(centers)
        counts = numpy.bincount(codes, minlength=centroid_count)
        if numpy.any(counts == 0):
            raise EvaluationError("PQ k-means produced an empty centroid")
        centers = numpy.stack([values[codes == code].mean(axis=0) for code in range(centroid_count)]).astype(numpy.float32)
    codes, errors = assign(centers)
    return centers, codes, float(errors.mean())


def train_pq(values: Any, subspaces: int, centroid_count: int, seed: int, iterations: int, initial: Any | None = None) -> tuple[Any, Any, float]:
    values = numpy.asarray(values, dtype=numpy.float32)
    if values.ndim != 2 or values.shape[1] % subspaces != 0:
        raise EvaluationError("PQ subspace layout is invalid")
    width = values.shape[1] // subspaces
    centroids = numpy.empty((subspaces, centroid_count, width), dtype=numpy.float32)
    codes = numpy.empty((values.shape[0], subspaces), dtype=numpy.uint8)
    for index in range(subspaces):
        centers, assigned, _ = train_kmeans(values[:, index * width:(index + 1) * width], centroid_count, seed + 1009 * index, iterations, None if initial is None else initial[index])
        centroids[index] = centers
        codes[:, index] = assigned
    reconstructed = reconstruct(codes, centroids)
    return centroids, codes, float(numpy.mean((values - reconstructed) ** 2))


def reconstruct(codes: Any, centroids: Any) -> Any:
    subspaces, _, width = centroids.shape
    if codes.ndim != 2 or codes.shape[1] != subspaces:
        raise EvaluationError("PQ code shape is invalid")
    return centroids[numpy.arange(subspaces)[None, :], codes].reshape(codes.shape[0], subspaces * width)


def train_opq(values: Any, subspaces: int, centroid_count: int, seed: int, kmeans_iterations: int, opq_iterations: int) -> tuple[Any, Any, Any, list[dict[str, float]]]:
    if opq_iterations < 0:
        raise EvaluationError("OPQ iteration count is invalid")
    values = numpy.asarray(values, dtype=numpy.float32)
    rotation = numpy.eye(values.shape[1], dtype=numpy.float32)
    centroids, codes, mse = train_pq(values, subspaces, centroid_count, seed, kmeans_iterations)
    history: list[dict[str, float]] = [{"iteration": 0.0, "train_reconstruction_mse": mse, "rotation_displacement_frobenius": 0.0, "code_reassignment_fraction": 0.0}]
    for iteration in range(opq_iterations):
        previous_codes = codes.copy()
        rotated = values @ rotation
        centroids, codes, mse = train_pq(rotated, subspaces, centroid_count, seed, kmeans_iterations, centroids)
        reconstructed = reconstruct(codes, centroids)
        left, _, right = numpy.linalg.svd(numpy.asarray(values, dtype=numpy.float64).T @ numpy.asarray(reconstructed, dtype=numpy.float64), full_matrices=False)
        next_rotation = (left @ right).astype(numpy.float32)
        history.append({"iteration": float(iteration + 1), "train_reconstruction_mse": mse, "rotation_displacement_frobenius": float(numpy.linalg.norm(next_rotation - rotation)), "code_reassignment_fraction": float(numpy.mean(codes != previous_codes))})
        rotation = next_rotation
    centroids, codes, _ = train_pq(values @ rotation, subspaces, centroid_count, seed, kmeans_iterations, centroids)
    return rotation, centroids, codes, history


def pack_codes(codes: Any, code_bits: int) -> Any:
    if codes.ndim != 2 or code_bits not in (4, 8) or numpy.any(codes >= 2 ** code_bits):
        raise EvaluationError("PQ codes cannot be nibble packed")
    if code_bits == 8:
        return codes.astype(numpy.uint8)
    if codes.shape[1] % 2:
        raise EvaluationError("4-bit PQ needs an even subspace count")
    return (codes[:, 0::2] | (codes[:, 1::2] << 4)).astype(numpy.uint8)


def unpack_codes(packed: Any, code_bits: int, subspaces: int) -> Any:
    packed = numpy.asarray(packed, dtype=numpy.uint8)
    if code_bits == 8 and packed.shape[1] == subspaces:
        return packed.copy()
    if code_bits == 4 and packed.shape[1] * 2 == subspaces:
        result = numpy.empty((packed.shape[0], subspaces), dtype=numpy.uint8)
        result[:, 0::2] = packed & 15; result[:, 1::2] = packed >> 4
        return result
    raise EvaluationError("packed PQ shape is invalid")


def adc_scores(query: Any, rotation: Any, centroids: Any, document_codes: Any) -> Any:
    rotated = numpy.asarray(query, dtype=numpy.float32) @ rotation
    subspaces, _, width = centroids.shape
    lookup = ((centroids - rotated.reshape(subspaces, width)[:, None, :]) ** 2).sum(axis=2)
    return lookup[numpy.arange(subspaces)[:, None], document_codes.T].sum(axis=0)


def packed_adc_scores(query: Any, rotation: Any, centroids: Any, packed_codes: Any, code_bits: int) -> Any:
    rotated = numpy.asarray(query, dtype=numpy.float32) @ rotation
    subspaces, _, width = centroids.shape
    lookup = ((centroids - rotated.reshape(subspaces, width)[:, None, :]) ** 2).sum(axis=2)
    packed = numpy.asarray(packed_codes, dtype=numpy.uint8)
    result = numpy.zeros(packed.shape[0], dtype=numpy.float32)
    if code_bits == 8:
        if packed.shape[1] != subspaces:
            raise EvaluationError("packed 8-bit PQ shape is invalid")
        for index in range(subspaces):
            result += lookup[index, packed[:, index]]
        return result
    if code_bits != 4 or packed.shape[1] * 2 != subspaces:
        raise EvaluationError("packed 4-bit PQ shape is invalid")
    for index in range(packed.shape[1]):
        result += lookup[2 * index, packed[:, index] & 15]
        result += lookup[2 * index + 1, packed[:, index] >> 4]
    return result


def encode(values: Any, rotation: Any, centroids: Any) -> Any:
    transformed = numpy.asarray(values, dtype=numpy.float32) @ rotation
    subspaces, _, width = centroids.shape
    codes = numpy.empty((transformed.shape[0], subspaces), dtype=numpy.uint8)
    for index in range(subspaces):
        part = transformed[:, index * width:(index + 1) * width]
        codes[:, index] = ((part[:, None, :] - centroids[index][None, :, :]) ** 2).sum(axis=2).argmin(axis=1)
    return codes


def reconstruction_mse(values: Any, rotation: Any, centroids: Any) -> float:
    transformed = numpy.asarray(values, dtype=numpy.float32) @ rotation
    return float(numpy.mean((transformed - reconstruct(encode(values, rotation, centroids), centroids)) ** 2))


def stable_validation_indices(ids: list[str], validation_count: int, seed: int, salt: str) -> Any:
    """Choose a validation membership independent of calibration input order."""
    if not isinstance(salt, str) or not salt:
        raise EvaluationError("PQ validation split salt is invalid")
    if validation_count < 0 or validation_count >= len(ids):
        raise EvaluationError("PQ validation split count is invalid")
    prefix = f"agent-memory-pq-opq-validation-split-v1\0{salt}\0{seed}\0".encode("utf-8")
    ranked = sorted(
        (hashlib.sha256(prefix + identifier.encode("utf-8")).digest(), index)
        for index, identifier in enumerate(ids)
    )
    return numpy.asarray(sorted(index for _, index in ranked[:validation_count]), dtype=numpy.int64)


def evaluate(args: Any) -> None:
    calibration = shared.load_root(args.calibration_root)
    data = shared.load_root(args.evaluation_root)
    shared.validate_calibration_evaluation_pair(calibration, data)
    subspaces = require_payload(args.payload_bytes, data["dimension"], args.code_bits)
    centroid_count = 2 ** args.code_bits
    if data["dimension"] != calibration["dimension"]:
        raise EvaluationError("calibration and evaluation dimensions differ")
    if args.training_sample_count < 0 or args.training_sample_count > len(calibration["train_ids"]):
        raise EvaluationError("PQ training sample count is invalid")
    sample_count = args.training_sample_count or len(calibration["train_ids"])
    sample_indices = numpy.arange(len(calibration["train_ids"])) if sample_count == len(calibration["train_ids"]) else numpy.sort(numpy.random.default_rng(args.seed).choice(len(calibration["train_ids"]), size=sample_count, replace=False))
    training_values = numpy.asarray(calibration["train"], dtype=numpy.float32)[sample_indices]
    training_ids = [calibration["train_ids"][index] for index in sample_indices]
    calibration_sample_ids = list(training_ids)
    if not 0.0 <= args.validation_fraction < 0.5:
        raise EvaluationError("PQ validation fraction is invalid")
    validation_count = int(round(sample_count * args.validation_fraction))
    if args.validation_split_salt is None:
        split = numpy.random.default_rng(args.seed + 31).permutation(sample_count)
        validation_positions = numpy.sort(split[:validation_count])
        validation_split_algorithm = "legacy_rng_seed_plus_31_v1"
        validation_split_salt = None
    else:
        validation_positions = stable_validation_indices(training_ids, validation_count, args.seed, args.validation_split_salt)
        validation_split_algorithm = "sha256_document_id_rank_v1"
        validation_split_salt = args.validation_split_salt
    validation_mask = numpy.zeros(sample_count, dtype=bool)
    validation_mask[validation_positions] = True
    optimizer_positions = numpy.flatnonzero(~validation_mask)
    if not optimizer_positions.size:
        raise EvaluationError("PQ optimizer split is empty")
    validation_values = training_values[validation_positions]
    validation_ids = [training_ids[index] for index in validation_positions]
    training_values = training_values[optimizer_positions]
    training_ids = [training_ids[index] for index in optimizer_positions]
    if args.scheme == "pq":
        rotation = numpy.eye(data["dimension"], dtype=numpy.float32)
        centroids, _, _ = train_pq(training_values, subspaces, centroid_count, args.seed, args.kmeans_iterations)
        opq_iterations = 0
        convergence = []
    else:
        rotation, centroids, _, convergence = train_opq(training_values, subspaces, centroid_count, args.seed, args.kmeans_iterations, args.opq_iterations)
        opq_iterations = args.opq_iterations
    width = data["dimension"] // subspaces
    document_codes = encode(data["documents"], rotation, centroids)
    packed = pack_codes(document_codes, args.code_bits)
    if packed.shape[1] != args.payload_bytes:
        raise EvaluationError("PQ packed payload size differs from the contract")
    metrics, contributions = shared.evaluate_candidates(
        data,
        lambda _index, query: numpy.lexsort((data["document_ids"], packed_adc_scores(query, rotation, centroids, packed, args.code_bits))),
        args.candidate_limit,
        args.oracle_k,
    )
    identity = shared.contribution_identity(data, args.candidate_limit, args.oracle_k)
    report = {
        "schema_version": 1,
        "family": "pq_opq_adc_reference_v1",
        "scheme": args.scheme,
        "payload_bytes_per_document": args.payload_bytes,
        "subspace_count": subspaces,
        "subspace_dimension": width,
        "centroid_count": centroid_count,
        "code_bits_per_subspace": args.code_bits,
        "codebook_bytes": int(centroids.nbytes),
        "rotation_bytes": int(rotation.nbytes) if args.scheme == "opq" else 0,
        "total_model_bytes": int(centroids.nbytes + (rotation.nbytes if args.scheme == "opq" else 0)),
        "scoring": "continuous_query_squared_l2_adc",
        "evaluation_materialization_manifest_sha256": data["manifest_sha256"],
        "evaluation_qrels_sha256": data["evaluation_qrels_sha256"],
        "calibration_materialization_manifest_sha256": calibration["manifest_sha256"],
        "calibration_vector_count": len(calibration["train_ids"]),
        "training_sample_count": sample_count,
        "calibration_sample_ids_sha256": shared.canonical_ids_sha256(calibration_sample_ids),
        "optimizer_vector_count": len(training_ids),
        "optimizer_ids_sha256": shared.canonical_ids_sha256(training_ids),
        "validation_vector_count": len(validation_ids),
        "validation_sample_ids_sha256": shared.canonical_ids_sha256(validation_ids) if validation_ids else None,
        "validation_reconstruction_mse": reconstruction_mse(validation_values, rotation, centroids) if validation_ids else None,
        "validation_split_algorithm": validation_split_algorithm,
        "validation_split_salt": validation_split_salt,
        "seed": args.seed,
        "kmeans_iterations": args.kmeans_iterations,
        "opq_iterations": opq_iterations,
        "opq_convergence": convergence,
        "rotation_sha256": sha256_array(rotation),
        "centroids_sha256": sha256_array(centroids),
        "document_codes_sha256": hashlib.sha256(packed.tobytes()).hexdigest(),
        "oracle_k": args.oracle_k,
        "candidate_limit": args.candidate_limit,
        **metrics,
    }
    shared.write_result(args.output, report, contributions, args.contributions_output, identity, data["query_ids"])
    report["evaluator_source_sha256"] = shared.sha256_file(Path(__file__))
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def run_self_test() -> int:
    generator = numpy.random.default_rng(42)
    split_ids = [f"document-{index}" for index in range(12)]
    selected = {split_ids[index] for index in stable_validation_indices(split_ids, 4, 42, "opq-step-extension-v1")}
    reordered_ids = list(reversed(split_ids))
    reordered_selected = {reordered_ids[index] for index in stable_validation_indices(reordered_ids, 4, 42, "opq-step-extension-v1")}
    if selected != reordered_selected or selected == {split_ids[index] for index in stable_validation_indices(split_ids, 4, 42, "other-split")}:
        print("self-test failed: validation split is not stable and salt-scoped", file=sys.stderr); return 1
    values = generator.normal(size=(96, 8)).astype(numpy.float32)
    centroids, codes, _ = train_pq(values, 2, 16, 42, 4)
    query = generator.normal(size=8).astype(numpy.float32)
    scores = adc_scores(query, numpy.eye(8, dtype=numpy.float32), centroids, codes)
    direct = ((reconstruct(codes, centroids) - query[None, :]) ** 2).sum(axis=1)
    if not numpy.allclose(scores, direct, rtol=1.0e-5, atol=1.0e-5):
        print("self-test failed: PQ ADC does not match reconstructed L2", file=sys.stderr); return 1
    packed = pack_codes(codes, 4)
    if not numpy.array_equal(unpack_codes(packed, 4, 2), codes) or not numpy.allclose(packed_adc_scores(query, numpy.eye(8, dtype=numpy.float32), centroids, packed, 4), scores, rtol=1.0e-5, atol=1.0e-5):
        print("self-test failed: packed 4-bit ADC parity", file=sys.stderr); return 1
    rotation, opq_centroids, opq_codes, _ = train_opq(values, 2, 16, 42, 3, 2)
    if not numpy.allclose(rotation.T @ rotation, numpy.eye(8), rtol=1.0e-5, atol=1.0e-5):
        print("self-test failed: OPQ rotation is not orthogonal", file=sys.stderr); return 1
    opq_scores = adc_scores(query, rotation, opq_centroids, opq_codes)
    rotated_direct = ((reconstruct(opq_codes, opq_centroids) - query[None, :] @ rotation) ** 2).sum(axis=1)
    if not numpy.allclose(opq_scores, rotated_direct, rtol=1.0e-5, atol=1.0e-5):
        print("self-test failed: OPQ ADC does not match rotated L2", file=sys.stderr); return 1
    wide_centroids, wide_codes, _ = train_pq(values, 2, 32, 42, 4)
    wide_packed = pack_codes(wide_codes, 8)
    wide_scores = adc_scores(query, numpy.eye(8, dtype=numpy.float32), wide_centroids, wide_codes)
    if not numpy.allclose(packed_adc_scores(query, numpy.eye(8, dtype=numpy.float32), wide_centroids, wide_packed, 8), wide_scores, rtol=1.0e-5, atol=1.0e-5):
        print("self-test failed: packed 8-bit ADC parity", file=sys.stderr); return 1
    if pack_codes(codes, 4).shape != (96, 1):
        print("self-test failed: packed payload shape is wrong", file=sys.stderr); return 1
    print("PQ/OPQ evaluator self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("evaluate")
    run.add_argument("--calibration-root", type=Path, required=True)
    run.add_argument("--evaluation-root", type=Path, required=True)
    run.add_argument("--scheme", choices=("pq", "opq"), required=True)
    run.add_argument("--payload-bytes", type=int, required=True)
    run.add_argument("--seed", type=int, required=True)
    run.add_argument("--code-bits", type=int, choices=(4, 8), required=True)
    run.add_argument("--kmeans-iterations", type=int, default=20)
    run.add_argument("--opq-iterations", type=int, default=4)
    run.add_argument("--training-sample-count", type=int, default=0, help="0 uses all calibration vectors")
    run.add_argument("--validation-fraction", type=float, default=0.0)
    run.add_argument("--validation-split-salt", type=str, default=None)
    run.add_argument("--candidate-limit", type=int, default=512)
    run.add_argument("--oracle-k", type=int, default=10)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--contributions-output", type=Path, required=True)
    sub.add_parser("self-test")
    args = parser.parse_args(argv)
    try:
        if args.command == "self-test":
            return run_self_test()
        evaluate(args)
        return 0
    except (EvaluationError, OSError, ValueError, numpy.linalg.LinAlgError) as error:
        print(f"evaluate-pq-opq: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
