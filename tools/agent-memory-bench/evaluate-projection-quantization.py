#!/usr/bin/env python3
"""Evaluate label-free binary and ternary projection codes on materialized E5 data.

This is a NumPy reference harness, not a production search implementation.  It
keeps stable-ID tie breaking, emits per-query contributions for paired
bootstrap, and implements packed base-3 ADC lookup scoring for ternary codes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy

sys_dont_write_bytecode = True


class EvaluationError(RuntimeError):
    """Raised when an external experiment artifact violates this harness contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_ids(path: Path) -> list[str]:
    values = [json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines()]
    if not values or len(values) != len(set(values)):
        raise EvaluationError("materialized IDs must be nonempty and unique")
    return values


def load_root(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    dimension = manifest["vector_format"]["dimension"]
    outputs = manifest["outputs"]
    def entry(name: str) -> dict[str, Any]:
        value = outputs[name]
        path = root / value["path"]
        if sha256_file(path) != value["sha256"]:
            raise EvaluationError(f"materialization output hash mismatch: {name}")
        return value
    train = entry("train_vectors"); documents = entry("evaluation_document_vectors"); queries = entry("evaluation_query_vectors")
    train_ids = read_ids(root / entry("train_ids")["path"])
    document_ids = read_ids(root / entry("evaluation_document_ids")["path"])
    query_ids = read_ids(root / entry("evaluation_query_ids")["path"])
    def vectors(value: dict[str, Any], count: int) -> Any:
        path = root / value["path"]
        if value["count"] != count or path.stat().st_size != count * dimension * 4:
            raise EvaluationError("materialized vector shape is invalid")
        return numpy.memmap(path, dtype="<f4", mode="r", shape=(count, dimension))
    qrels: dict[str, dict[str, int]] = {value: {} for value in query_ids}
    document_set = set(document_ids)
    for line in (root / entry("evaluation_qrels")["path"]).read_text(encoding="utf-8").splitlines():
        query_id, _, document_id, grade = line.split()
        if query_id not in qrels or document_id not in document_set:
            raise EvaluationError("qrels references unavailable materialized IDs")
        qrels[query_id][document_id] = int(grade)
    return {"root": root, "manifest": manifest, "manifest_sha256": sha256_file(root / "manifest.json"), "dimension": dimension, "train": vectors(train, len(train_ids)), "documents": vectors(documents, len(document_ids)), "queries": vectors(queries, len(query_ids)), "document_ids": numpy.asarray(document_ids), "query_ids": query_ids, "qrels": qrels}


def pca_weights(values: Any, count: int) -> Any:
    centered = numpy.asarray(values, dtype=numpy.float64) - numpy.asarray(values, dtype=numpy.float64).mean(axis=0)
    _, _, right = numpy.linalg.svd(centered, full_matrices=False)
    return right[:count].astype(numpy.float32)


def itq_weights(values: Any, count: int, seed: int, iterations: int) -> Any:
    weights = pca_weights(values, count)
    centered = numpy.asarray(values, dtype=numpy.float64) - numpy.asarray(values, dtype=numpy.float64).mean(axis=0)
    projected = centered @ weights.T
    generator = numpy.random.default_rng(seed)
    rotation, _ = numpy.linalg.qr(generator.standard_normal((count, count)))
    for _ in range(iterations):
        binary = numpy.where(projected @ rotation >= 0.0, 1.0, -1.0)
        left, _, right = numpy.linalg.svd(projected.T @ binary, full_matrices=False)
        rotation = left @ right
    return (rotation.T @ weights).astype(numpy.float32)


def binary_thresholds(values: Any, weights: Any) -> Any:
    return -numpy.median(numpy.clip(values, -1.0, 1.0) @ weights.T, axis=0).astype(numpy.float32)


def kmeans_centers(values: Any, iterations: int) -> Any:
    centers = numpy.quantile(values, (1.0 / 6.0, 0.5, 5.0 / 6.0), axis=0).T.astype(numpy.float32)
    for _ in range(iterations):
        assigned = numpy.abs(values[:, :, None] - centers[None, :, :]).argmin(axis=2)
        next_centers = centers.copy()
        for symbol in range(3):
            selected = assigned == symbol
            counts = selected.sum(axis=0)
            sums = (values * selected).sum(axis=0)
            nonempty = counts > 0
            next_centers[nonempty, symbol] = sums[nonempty] / counts[nonempty]
        centers = next_centers
    return centers


def ternary_tertile_thresholds(values: Any) -> tuple[Any, Any]:
    return tuple(numpy.quantile(values, (1.0 / 3.0, 2.0 / 3.0), axis=0).astype(numpy.float32))


def ternary_codes(values: Any, centers: Any | None = None, thresholds: tuple[Any, Any] | None = None) -> Any:
    if centers is not None:
        return numpy.abs(values[:, :, None] - centers[None, :, :]).argmin(axis=2).astype(numpy.uint8)
    if thresholds is None:
        raise EvaluationError("ternary quantizer is missing centers or thresholds")
    low, high = thresholds
    return numpy.where(values < low, 0, numpy.where(values > high, 2, 1)).astype(numpy.uint8)


def pack_trits(codes: Any) -> Any:
    count = codes.shape[1]
    result = numpy.zeros((codes.shape[0], (count + 4) // 5), dtype=numpy.uint8)
    for offset in range(5):
        indices = numpy.arange(offset, count, 5)
        if indices.size:
            result[:, :indices.size] += codes[:, indices] * (3 ** offset)
    return result


def ternary_lut(query: Any, centers: Any) -> list[Any]:
    result: list[Any] = []
    for start in range(0, centers.shape[0], 5):
        width = min(5, centers.shape[0] - start)
        table = numpy.empty(3 ** width, dtype=numpy.float32)
        for value in range(3 ** width):
            digits = [(value // (3 ** offset)) % 3 for offset in range(width)]
            table[value] = sum(float((query[start + offset] - centers[start + offset, digit]) ** 2) for offset, digit in enumerate(digits))
        result.append(table)
    return result


def packed_adc_scores(packed_codes: Any, query: Any, centers: Any) -> Any:
    """Scores base-3 packed document codes with query-specific ternary ADC LUTs."""
    scores = numpy.zeros(packed_codes.shape[0], dtype=numpy.float32)
    for group, table in enumerate(ternary_lut(query, centers)):
        scores += table[packed_codes[:, group]]
    return scores


def dcg_at_10(ranked_ids: Any, grades: dict[str, int]) -> float:
    value = 0.0
    for rank, document_id in enumerate(ranked_ids[:10]):
        value += (2.0 ** grades.get(str(document_id), 0) - 1.0) / math.log2(rank + 2.0)
    ideal = sorted(grades.values(), reverse=True)[:10]
    denominator = sum((2.0 ** grade - 1.0) / math.log2(rank + 2.0) for rank, grade in enumerate(ideal))
    return value / denominator if denominator else 0.0


def evaluate_candidates(data: dict[str, Any], candidate_scores: Any, candidate_limit: int, oracle_k: int) -> tuple[dict[str, Any], dict[str, Any]]:
    documents = numpy.asarray(data["documents"], dtype=numpy.float32)
    document_ids = data["document_ids"]
    coverage: list[float] = []; rerank_ndcg: list[float] = []; full_ndcg: list[float] = []
    candidate_seconds = 0.0
    for index, query_id in enumerate(data["query_ids"]):
        query = numpy.asarray(data["queries"][index], dtype=numpy.float32)
        exact_scores = documents @ query
        exact_order = numpy.lexsort((document_ids, -exact_scores))
        start = time.perf_counter()
        candidate_order = candidate_scores(index, query)
        candidate_seconds += time.perf_counter() - start
        candidates = candidate_order[:candidate_limit]
        coverage.append(float(numpy.isin(exact_order[:oracle_k], candidates).sum()) / oracle_k)
        rerank_order = candidates[numpy.lexsort((document_ids[candidates], -exact_scores[candidates]))]
        grades = data["qrels"][query_id]
        rerank_ndcg.append(dcg_at_10(document_ids[rerank_order], grades))
        full_ndcg.append(dcg_at_10(document_ids[exact_order], grades))
    contributions = {"coverage_at_candidate_limit": numpy.asarray(coverage, dtype=numpy.float64), "reranked_ndcg_at_10": numpy.asarray(rerank_ndcg, dtype=numpy.float64), "full_e5_ndcg_at_10": numpy.asarray(full_ndcg, dtype=numpy.float64)}
    return ({"exact_top_k_candidate_coverage": float(numpy.mean(coverage)), "reranked_ndcg_at_10": float(numpy.mean(rerank_ndcg)), "full_e5_ndcg_at_10": float(numpy.mean(full_ndcg)), "candidate_scan_seconds": candidate_seconds, "query_count": len(coverage)}, contributions)


def write_result(path: Path, report: dict[str, Any], contributions: dict[str, Any], contribution_path: Path) -> None:
    contribution_path.parent.mkdir(parents=True, exist_ok=True)
    numpy.savez_compressed(contribution_path, **contributions)
    report["per_query_contributions_path"] = str(contribution_path)
    report["per_query_contributions_sha256"] = sha256_file(contribution_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def evaluate_artifact(args: Any) -> None:
    data = load_root(args.evaluation_root)
    artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    weight_path = args.artifact.parent / artifact["weights"]["encoder_weights"]["path"]
    bias_path = args.artifact.parent / artifact["weights"]["encoder_bias"]["path"]
    bit_count = artifact["architecture"]["bit_count"]; dimension = data["dimension"]
    weights = numpy.fromfile(weight_path, dtype="<f4").reshape(bit_count, dimension)
    bias = numpy.fromfile(bias_path, dtype="<f4")
    document_codes = (numpy.clip(data["documents"], -1.0, 1.0) @ weights.T + bias >= 0.0)
    def candidates(index: int, query: Any) -> Any:
        query_code = numpy.clip(query, -1.0, 1.0) @ weights.T + bias >= 0.0
        distance = numpy.count_nonzero(document_codes != query_code, axis=1)
        return numpy.lexsort((data["document_ids"], distance))
    metrics, contributions = evaluate_candidates(data, candidates, args.candidate_limit, args.oracle_k)
    write_result(args.output, {"schema_version": 1, "family": "binary_artifact_hamming_reference_v1", "artifact_sha256": sha256_file(args.artifact), "evaluation_materialization_manifest_sha256": data["manifest_sha256"], "oracle_k": args.oracle_k, "candidate_limit": args.candidate_limit, **metrics}, contributions, args.contributions_output)


def evaluate_ternary(args: Any) -> None:
    calibration = load_root(args.calibration_root); data = load_root(args.evaluation_root)
    weights = itq_weights(calibration["train"], args.trit_count, args.seed, args.itq_iterations) if args.projection == "itq" else pca_weights(calibration["train"], args.trit_count)
    calibration_projection = numpy.clip(calibration["train"], -1.0, 1.0) @ weights.T
    document_projection = numpy.clip(data["documents"], -1.0, 1.0) @ weights.T
    query_projection = numpy.clip(data["queries"], -1.0, 1.0) @ weights.T
    if args.quantizer == "binary":
        thresholds = binary_thresholds(calibration["train"], weights)
        document_codes = numpy.clip(data["documents"], -1.0, 1.0) @ weights.T + thresholds >= 0.0
        def candidates(index: int, query: Any) -> Any:
            query_code = numpy.clip(query, -1.0, 1.0) @ weights.T + thresholds >= 0.0
            distance = numpy.count_nonzero(document_codes != query_code, axis=1)
            return numpy.lexsort((data["document_ids"], distance))
        scoring = "binary_hamming_reference_v1"; zero_fraction = 0.0
    elif args.quantizer == "kmeans":
        centers = kmeans_centers(calibration_projection, args.kmeans_iterations)
        document_codes = ternary_codes(document_projection, centers=centers)
        packed = pack_trits(document_codes)
        def candidates(index: int, query: Any) -> Any:
            cost = packed_adc_scores(packed, query_projection[index], centers)
            return numpy.lexsort((data["document_ids"], cost))
        scoring = "ternary_adc_packed_base3_lut_v1"; zero_fraction = float(numpy.mean(document_codes == 1))
    else:
        thresholds = ternary_tertile_thresholds(calibration_projection)
        document_codes = ternary_codes(document_projection, thresholds=thresholds)
        query_codes = ternary_codes(query_projection, thresholds=thresholds)
        def candidates(index: int, query: Any) -> Any:
            distance = numpy.abs(document_codes.astype(numpy.int16) - query_codes[index]).sum(axis=1)
            return numpy.lexsort((data["document_ids"], distance))
        scoring = "ternary_symmetric_l1_v1"; zero_fraction = float(numpy.mean(document_codes == 1))
    metrics, contributions = evaluate_candidates(data, candidates, args.candidate_limit, args.oracle_k)
    symbol_count = 2 if args.quantizer == "binary" else 3
    entropy = -sum(float(numpy.mean(document_codes == symbol)) * math.log2(float(numpy.mean(document_codes == symbol))) for symbol in range(symbol_count) if numpy.any(document_codes == symbol))
    payload_bytes = (args.trit_count + 7) // 8 if args.quantizer == "binary" else (args.trit_count + 4) // 5
    write_result(args.output, {"schema_version": 1, "family": "binary_projection_reference_v1" if args.quantizer == "binary" else "ternary_projection_reference_v1", "projection": args.projection, "quantizer": args.quantizer, "scoring": scoring, "trit_count": args.trit_count, "packed_payload_bytes_per_document": payload_bytes, "evaluation_materialization_manifest_sha256": data["manifest_sha256"], "calibration_materialization_manifest_sha256": calibration["manifest_sha256"], "oracle_k": args.oracle_k, "candidate_limit": args.candidate_limit, "zero_symbol_fraction": zero_fraction, "symbol_entropy_bits": entropy * args.trit_count, **metrics}, contributions, args.contributions_output)


def bootstrap(args: Any) -> None:
    left = numpy.load(args.left_contributions); right = numpy.load(args.right_contributions)
    generator = numpy.random.default_rng(args.seed); count = left["coverage_at_candidate_limit"].shape[0]
    if count != right["coverage_at_candidate_limit"].shape[0]: raise EvaluationError("paired contribution lengths differ")
    report: dict[str, Any] = {"schema_version": 1, "family": "paired_query_bootstrap_v1", "left_sha256": sha256_file(args.left_contributions), "right_sha256": sha256_file(args.right_contributions), "query_count": count, "replicates": args.replicates, "seed": args.seed, "metrics": {}}
    for name in ("coverage_at_candidate_limit", "reranked_ndcg_at_10"):
        difference = right[name] - left[name]
        samples = numpy.empty(args.replicates, dtype=numpy.float64)
        for index in range(args.replicates): samples[index] = difference[generator.integers(0, count, size=count)].mean()
        report["metrics"][name] = {"observed_difference": float(difference.mean()), "percentile_95_ci": [float(numpy.quantile(samples, 0.025)), float(numpy.quantile(samples, 0.975))]}
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def run_self_test() -> int:
    codes = numpy.asarray([[0, 1, 2, 0, 1, 2], [2, 1, 0, 2, 1, 0]], dtype=numpy.uint8)
    packed = pack_trits(codes)
    if packed.tolist() != [[102, 2], [140, 0]]:
        print("self-test failed: base-3 packing", file=__import__("sys").stderr); return 1
    centers = numpy.asarray([[0.0, 1.0, 2.0]] * 6, dtype=numpy.float32)
    lookup = ternary_lut(numpy.asarray([0.0] * 6, dtype=numpy.float32), centers)
    if not numpy.isclose(sum(table[packed[0, group]] for group, table in enumerate(lookup)), 10.0):
        print("self-test failed: packed ADC LUT", file=__import__("sys").stderr); return 1
    direct = numpy.asarray([
        sum((0.0 - centers[coordinate, symbol]) ** 2 for coordinate, symbol in enumerate(row))
        for row in codes
    ], dtype=numpy.float32)
    if not numpy.allclose(packed_adc_scores(packed, numpy.zeros(6, dtype=numpy.float32), centers), direct):
        print("self-test failed: packed ADC scalar parity", file=__import__("sys").stderr); return 1
    print("projection quantization evaluator self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False); common.add_argument("--evaluation-root", type=Path, required=True); common.add_argument("--output", type=Path, required=True); common.add_argument("--contributions-output", type=Path, required=True); common.add_argument("--oracle-k", type=int, default=10); common.add_argument("--candidate-limit", type=int, default=512)
    binary = sub.add_parser("binary", parents=[common]); binary.add_argument("--artifact", type=Path, required=True)
    ternary = sub.add_parser("ternary", parents=[common]); ternary.add_argument("--calibration-root", type=Path, required=True); ternary.add_argument("--projection", choices=("pca", "itq"), required=True); ternary.add_argument("--quantizer", choices=("binary", "tertiles", "kmeans"), required=True); ternary.add_argument("--trit-count", type=int, required=True); ternary.add_argument("--seed", type=int, default=42); ternary.add_argument("--itq-iterations", type=int, default=50); ternary.add_argument("--kmeans-iterations", type=int, default=25)
    boot = sub.add_parser("bootstrap"); boot.add_argument("--left-contributions", type=Path, required=True); boot.add_argument("--right-contributions", type=Path, required=True); boot.add_argument("--output", type=Path, required=True); boot.add_argument("--replicates", type=int, default=10000); boot.add_argument("--seed", type=int, default=42)
    sub.add_parser("self-test"); args = parser.parse_args(argv)
    try:
        if args.command == "binary": evaluate_artifact(args)
        elif args.command == "ternary": evaluate_ternary(args)
        elif args.command == "bootstrap": bootstrap(args)
        else: return run_self_test()
    except (EvaluationError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"evaluate-projection-quantization: {error}", file=__import__("sys").stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main(__import__("sys").argv[1:]))
