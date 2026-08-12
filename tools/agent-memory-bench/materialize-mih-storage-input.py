#!/usr/bin/env python3
"""Materialize a provenance-bound packed ITQ code input for storage benchmarks."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import importlib.util
import json
import platform
import tempfile
from pathlib import Path
from typing import Any

import numpy


def load_shared() -> Any:
    path = Path(__file__).with_name("evaluate-projection-quantization.py")
    spec = importlib.util.spec_from_file_location("mih_storage_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load projection evaluation helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = load_shared()
EvaluationError = shared.EvaluationError


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files_sha256() -> dict[str, str]:
    shared_path = Path(__file__).with_name("evaluate-projection-quantization.py")
    return {
        Path(__file__).name: sha256_file(Path(__file__)),
        shared_path.name: sha256_file(shared_path),
    }


def source_bundle_sha256(files: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def runtime() -> dict[str, str]:
    return {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "numpy_version": numpy.__version__,
    }


def pack_codes(codes: Any, code_bits: int) -> bytes:
    values = numpy.asarray(codes, dtype=numpy.bool_)
    if values.ndim != 2 or values.shape[1] != code_bits or code_bits == 0 or code_bits % 64 != 0:
        raise EvaluationError("storage benchmark code shape is invalid")
    packed = numpy.packbits(values, axis=1, bitorder="little")
    return packed.tobytes(order="C")


def pack_vectors(vectors: Any, dimension: int) -> bytes:
    values = numpy.asarray(vectors, dtype="<f4")
    if values.ndim != 2 or values.shape[1] != dimension or dimension == 0:
        raise EvaluationError("storage benchmark vector shape is invalid")
    return values.tobytes(order="C")


def materialize(args: Any) -> None:
    if args.code_bits != 256 or args.itq_iterations != 50 or args.seed < 0:
        raise EvaluationError("storage benchmark input contract is invalid")
    calibration = shared.load_root(args.calibration_root)
    evaluation = shared.load_root(args.evaluation_root)
    shared.validate_calibration_evaluation_pair(calibration, evaluation)
    if calibration["dimension"] != evaluation["dimension"] or args.code_bits > calibration["dimension"]:
        raise EvaluationError("storage benchmark roots are incompatible")
    weights = shared.itq_weights(
        numpy.asarray(calibration["train"]), args.code_bits, args.seed, args.itq_iterations
    )
    thresholds = shared.binary_thresholds(numpy.asarray(calibration["train"]), weights)
    calibration_projection = numpy.asarray(calibration["train"]) @ weights.T + thresholds
    document_codes = numpy.asarray(evaluation["documents"]) @ weights.T + thresholds >= 0.0
    query_codes = numpy.asarray(evaluation["queries"]) @ weights.T + thresholds >= 0.0
    query_projection = numpy.asarray(evaluation["queries"]) @ weights.T + thresholds
    centers = shared.conditional_centers(calibration_projection, (calibration_projection >= 0.0).astype(numpy.uint8), 2)
    documents = pack_codes(document_codes, args.code_bits)
    queries = pack_codes(query_codes, args.code_bits)
    document_vectors = pack_vectors(evaluation["documents"], evaluation["dimension"])
    query_vectors = pack_vectors(evaluation["queries"], evaluation["dimension"])
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    document_path = output / "document-codes-u64le.bin"
    query_path = output / "query-codes-u64le.bin"
    document_vector_path = output / "document-vectors-f32le.bin"
    query_vector_path = output / "query-vectors-f32le.bin"
    query_projection_path = output / "query-itq-projections-f32le.bin"
    centroid_path = output / "itq-binary-adc-centroids-f32le.bin"
    document_path.write_bytes(documents)
    query_path.write_bytes(queries)
    document_vector_path.write_bytes(document_vectors)
    query_vector_path.write_bytes(query_vectors)
    query_projection_path.write_bytes(pack_vectors(query_projection, args.code_bits))
    centroid_path.write_bytes(pack_vectors(centers, 2))
    sources = source_files_sha256()
    manifest = {
        "schema_version": 1,
        "family": "mih_storage_benchmark_input_v1",
        "code_bits": args.code_bits,
        "word_count": args.code_bits // 64,
        "seed": args.seed,
        "itq_iterations": args.itq_iterations,
        "calibration_materialization_manifest_sha256": calibration["manifest_sha256"],
        "evaluation_materialization_manifest_sha256": evaluation["manifest_sha256"],
        "calibration_train_ids_sha256": shared.ordered_ids_sha256(calibration["train_ids"]),
        "document_count": int(document_codes.shape[0]),
        "query_count": int(query_codes.shape[0]),
        "document_codes_file": document_path.name,
        "document_codes_sha256": sha256_file(document_path),
        "query_codes_file": query_path.name,
        "query_codes_sha256": sha256_file(query_path),
        "embedding_dimension": evaluation["dimension"],
        "document_vectors_file": document_vector_path.name,
        "document_vectors_sha256": sha256_file(document_vector_path),
        "query_vectors_file": query_vector_path.name,
        "query_vectors_sha256": sha256_file(query_vector_path),
        "itq_projection_dimension": args.code_bits,
        "query_itq_projections_file": query_projection_path.name,
        "query_itq_projections_sha256": sha256_file(query_projection_path),
        "binary_adc_centroids_file": centroid_path.name,
        "binary_adc_centroids_sha256": sha256_file(centroid_path),
        "source_files_sha256": sources,
        "source_bundle_sha256": source_bundle_sha256(sources),
        "runtime": runtime(),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def self_test() -> int:
    codes = numpy.asarray([[False] * 64, [True] + [False] * 63], dtype=numpy.bool_)
    payload = pack_codes(codes, 64)
    if len(payload) != 16 or int.from_bytes(payload[8:16], "little") != 1:
        print("self-test failed: packed code bit order is invalid", file=sys.stderr)
        return 1
    vectors = numpy.asarray([[1.0, -2.0]], dtype=numpy.float32)
    if len(pack_vectors(vectors, 2)) != 8:
        print("self-test failed: packed vector payload is invalid", file=sys.stderr)
        return 1
    files = source_files_sha256()
    if set(files) != {Path(__file__).name, "evaluate-projection-quantization.py"}:
        print("self-test failed: source map is invalid", file=sys.stderr)
        return 1
    if source_bundle_sha256(files) != source_bundle_sha256(dict(reversed(files.items()))):
        print("self-test failed: source bundle is not canonical", file=sys.stderr)
        return 1
    print("MIH storage input materializer self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("materialize")
    run.add_argument("--calibration-root", type=Path, required=True)
    run.add_argument("--evaluation-root", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--code-bits", type=int, default=256)
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--itq-iterations", type=int, default=50)
    sub.add_parser("self-test")
    args = parser.parse_args(argv)
    try:
        if args.command == "materialize":
            materialize(args)
        else:
            return self_test()
    except (EvaluationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"materialize-mih-storage-input: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
