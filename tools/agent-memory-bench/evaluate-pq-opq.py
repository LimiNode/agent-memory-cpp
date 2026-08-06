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


def require_payload(payload_bytes: int, dimension: int) -> int:
    if payload_bytes <= 0 or dimension % (payload_bytes * 2) != 0:
        raise EvaluationError("4-bit PQ payload must divide the embedding dimension")
    return payload_bytes * 2


def train_kmeans(values: Any, seed: int, iterations: int) -> Any:
    """Deterministic 16-centroid Lloyd k-means with fail-closed empty cells."""
    values = numpy.asarray(values, dtype=numpy.float32)
    if values.ndim != 2 or values.shape[0] < 16 or iterations <= 0:
        raise EvaluationError("invalid PQ k-means input")
    generator = numpy.random.default_rng(seed)
    centers = values[generator.choice(values.shape[0], size=16, replace=False)].copy()
    for _ in range(iterations):
        distances = ((values[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        codes = distances.argmin(axis=1)
        counts = numpy.bincount(codes, minlength=16)
        if numpy.any(counts == 0):
            raise EvaluationError("PQ k-means produced an empty centroid")
        centers = numpy.stack([values[codes == code].mean(axis=0) for code in range(16)]).astype(numpy.float32)
    return centers


def train_pq(values: Any, subspaces: int, seed: int, iterations: int) -> tuple[Any, Any]:
    values = numpy.asarray(values, dtype=numpy.float32)
    if values.ndim != 2 or values.shape[1] % subspaces != 0:
        raise EvaluationError("PQ subspace layout is invalid")
    width = values.shape[1] // subspaces
    centroids = numpy.empty((subspaces, 16, width), dtype=numpy.float32)
    codes = numpy.empty((values.shape[0], subspaces), dtype=numpy.uint8)
    for index in range(subspaces):
        centers = train_kmeans(values[:, index * width:(index + 1) * width], seed + 1009 * index, iterations)
        centroids[index] = centers
        distances = ((values[:, None, index * width:(index + 1) * width] - centers[None, :, :]) ** 2).sum(axis=2)
        codes[:, index] = distances.argmin(axis=1)
    return centroids, codes


def reconstruct(codes: Any, centroids: Any) -> Any:
    subspaces, _, width = centroids.shape
    if codes.ndim != 2 or codes.shape[1] != subspaces:
        raise EvaluationError("PQ code shape is invalid")
    return centroids[numpy.arange(subspaces)[None, :], codes].reshape(codes.shape[0], subspaces * width)


def train_opq(values: Any, subspaces: int, seed: int, kmeans_iterations: int, opq_iterations: int) -> tuple[Any, Any, Any]:
    if opq_iterations <= 0:
        raise EvaluationError("OPQ needs at least one alternating iteration")
    values = numpy.asarray(values, dtype=numpy.float32)
    rotation = numpy.eye(values.shape[1], dtype=numpy.float32)
    for iteration in range(opq_iterations):
        centroids, codes = train_pq(values @ rotation, subspaces, seed + 1000003 * iteration, kmeans_iterations)
        reconstructed = reconstruct(codes, centroids)
        left, _, right = numpy.linalg.svd(numpy.asarray(values, dtype=numpy.float64).T @ numpy.asarray(reconstructed, dtype=numpy.float64), full_matrices=False)
        rotation = (left @ right).astype(numpy.float32)
    centroids, codes = train_pq(values @ rotation, subspaces, seed + 1000003 * opq_iterations, kmeans_iterations)
    return rotation, centroids, codes


def pack_nibbles(codes: Any) -> Any:
    if codes.ndim != 2 or codes.shape[1] % 2 or numpy.any(codes > 15):
        raise EvaluationError("PQ codes cannot be nibble packed")
    return (codes[:, 0::2] | (codes[:, 1::2] << 4)).astype(numpy.uint8)


def adc_scores(query: Any, rotation: Any, centroids: Any, document_codes: Any) -> Any:
    rotated = numpy.asarray(query, dtype=numpy.float32) @ rotation
    subspaces, _, width = centroids.shape
    lookup = ((centroids - rotated.reshape(subspaces, width)[:, None, :]) ** 2).sum(axis=2)
    return lookup[numpy.arange(subspaces)[:, None], document_codes.T].sum(axis=0)


def evaluate(args: Any) -> None:
    calibration = shared.load_root(args.calibration_root)
    data = shared.load_root(args.evaluation_root)
    shared.validate_calibration_evaluation_pair(calibration, data)
    subspaces = require_payload(args.payload_bytes, data["dimension"])
    if data["dimension"] != calibration["dimension"]:
        raise EvaluationError("calibration and evaluation dimensions differ")
    if args.training_sample_count <= 0 or args.training_sample_count > len(calibration["train_ids"]):
        raise EvaluationError("PQ training sample count is invalid")
    sample_indices = numpy.sort(numpy.random.default_rng(args.seed).choice(
        len(calibration["train_ids"]), size=args.training_sample_count, replace=False
    ))
    training_values = numpy.asarray(calibration["train"], dtype=numpy.float32)[sample_indices]
    training_ids = [calibration["train_ids"][index] for index in sample_indices]
    if args.scheme == "pq":
        rotation = numpy.eye(data["dimension"], dtype=numpy.float32)
        centroids, _ = train_pq(training_values, subspaces, args.seed, args.kmeans_iterations)
        opq_iterations = 0
    else:
        rotation, centroids, _ = train_opq(training_values, subspaces, args.seed, args.kmeans_iterations, args.opq_iterations)
        opq_iterations = args.opq_iterations
    width = data["dimension"] // subspaces
    transformed_documents = numpy.asarray(data["documents"], dtype=numpy.float32) @ rotation
    document_codes = numpy.empty((len(data["document_ids"]), subspaces), dtype=numpy.uint8)
    for index in range(subspaces):
        part = transformed_documents[:, index * width:(index + 1) * width]
        document_codes[:, index] = ((part[:, None, :] - centroids[index][None, :, :]) ** 2).sum(axis=2).argmin(axis=1)
    packed = pack_nibbles(document_codes)
    if packed.shape[1] != args.payload_bytes:
        raise EvaluationError("PQ packed payload size differs from the contract")
    metrics, contributions = shared.evaluate_candidates(
        data,
        lambda _index, query: numpy.lexsort((data["document_ids"], adc_scores(query, rotation, centroids, document_codes))),
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
        "centroid_count": 16,
        "code_bits_per_subspace": 4,
        "scoring": "continuous_query_squared_l2_adc",
        "evaluation_materialization_manifest_sha256": data["manifest_sha256"],
        "evaluation_qrels_sha256": data["evaluation_qrels_sha256"],
        "calibration_materialization_manifest_sha256": calibration["manifest_sha256"],
        "calibration_vector_count": len(calibration["train_ids"]),
        "training_sample_count": args.training_sample_count,
        "training_sample_ids_sha256": shared.canonical_ids_sha256(training_ids),
        "seed": args.seed,
        "kmeans_iterations": args.kmeans_iterations,
        "opq_iterations": opq_iterations,
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
    values = generator.normal(size=(96, 8)).astype(numpy.float32)
    centroids, codes = train_pq(values, 2, 42, 4)
    query = generator.normal(size=8).astype(numpy.float32)
    scores = adc_scores(query, numpy.eye(8, dtype=numpy.float32), centroids, codes)
    direct = ((reconstruct(codes, centroids) - query[None, :]) ** 2).sum(axis=1)
    if not numpy.allclose(scores, direct, rtol=1.0e-5, atol=1.0e-5):
        print("self-test failed: PQ ADC does not match reconstructed L2", file=sys.stderr); return 1
    rotation, _, _ = train_opq(values, 2, 42, 3, 2)
    if not numpy.allclose(rotation.T @ rotation, numpy.eye(8), rtol=1.0e-5, atol=1.0e-5):
        print("self-test failed: OPQ rotation is not orthogonal", file=sys.stderr); return 1
    if pack_nibbles(codes).shape != (96, 1):
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
    run.add_argument("--kmeans-iterations", type=int, default=20)
    run.add_argument("--opq-iterations", type=int, default=4)
    run.add_argument("--training-sample-count", type=int, default=8192)
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
