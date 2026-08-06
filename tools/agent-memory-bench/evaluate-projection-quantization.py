#!/usr/bin/env python3
"""Evaluate label-free scalar projection codes on materialized E5 data.

This is a NumPy reference harness, not a production search implementation.  It
keeps stable-ID tie breaking, emits per-query contributions for paired
bootstrap, and implements packed base-N ADC lookup scoring for scalar codes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
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


def canonical_ids_sha256(ids: list[str]) -> str:
    return hashlib.sha256("".join(f"{value}\n" for value in sorted(ids)).encode("utf-8")).hexdigest()


def ordered_ids_sha256(ids: list[str]) -> str:
    return hashlib.sha256("".join(f"{value}\n" for value in ids).encode("utf-8")).hexdigest()


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise EvaluationError(f"{field} must be a lowercase SHA-256")
    return value


def require_plain_path(value: Any, field: str) -> Path:
    if not isinstance(value, str):
        raise EvaluationError(f"{field} must be a path string")
    path = Path(value)
    if path.is_absolute() or path.name != value:
        raise EvaluationError(f"{field} must be a plain file name")
    return path


def read_ids(path: Path) -> list[str]:
    values = [json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines()]
    if not values or len(values) != len(set(values)):
        raise EvaluationError("materialized IDs must be nonempty and unique")
    return values


def load_root(root: Path) -> dict[str, Any]:
    try:
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"cannot read materialization manifest: {root}: {exc}") from exc
    if manifest.get("schema_version") != 1:
        raise EvaluationError("unsupported materialization schema")
    vector_format = manifest.get("vector_format")
    embedding = manifest.get("embedding")
    outputs = manifest.get("outputs")
    if not isinstance(vector_format, dict) or vector_format.get("dtype") != "float32_le" or vector_format.get("endianness") != "little":
        raise EvaluationError("unsupported materialization vector format")
    dimension = vector_format.get("dimension")
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
        raise EvaluationError("materialization dimension is invalid")
    if not isinstance(embedding, dict) or not all(isinstance(embedding.get(name), str) and embedding[name] for name in ("model_id", "model_revision", "query_prefix", "document_prefix")) or embedding.get("normalized") is not True:
        raise EvaluationError("materialization embedding contract is invalid")
    if not isinstance(outputs, dict):
        raise EvaluationError("materialization outputs are invalid")
    def entry(name: str) -> dict[str, Any]:
        value = outputs.get(name)
        if not isinstance(value, dict):
            raise EvaluationError(f"materialization output is missing: {name}")
        path = root / require_plain_path(value.get("path"), f"outputs.{name}.path")
        if not path.is_file() or sha256_file(path) != require_sha256(value.get("sha256"), f"outputs.{name}.sha256"):
            raise EvaluationError(f"materialization output hash mismatch: {name}")
        return value
    train = entry("train_vectors"); documents = entry("evaluation_document_vectors"); queries = entry("evaluation_query_vectors")
    train_id_entry = entry("train_ids"); document_id_entry = entry("evaluation_document_ids"); query_id_entry = entry("evaluation_query_ids")
    qrels_entry = entry("evaluation_qrels")
    train_ids = read_ids(root / require_plain_path(train_id_entry.get("path"), "outputs.train_ids.path"))
    document_ids = read_ids(root / require_plain_path(document_id_entry.get("path"), "outputs.evaluation_document_ids.path"))
    query_ids = read_ids(root / require_plain_path(query_id_entry.get("path"), "outputs.evaluation_query_ids.path"))
    for name, value, ids in (("train_ids", train_id_entry, train_ids), ("evaluation_document_ids", document_id_entry, document_ids), ("evaluation_query_ids", query_id_entry, query_ids)):
        if value.get("count") != len(ids):
            raise EvaluationError(f"materialization {name} count is invalid")
    def vectors(value: dict[str, Any], count: int) -> Any:
        path = root / require_plain_path(value.get("path"), "vector output path")
        if value.get("count") != count or value.get("dimension") != dimension or value.get("dtype") != "float32_le" or path.stat().st_size != count * dimension * 4:
            raise EvaluationError("materialized vector shape is invalid")
        return numpy.memmap(path, dtype="<f4", mode="r", shape=(count, dimension))
    qrels: dict[str, dict[str, int]] = {value: {} for value in query_ids}
    document_set = set(document_ids)
    for line_number, line in enumerate((root / require_plain_path(qrels_entry.get("path"), "outputs.evaluation_qrels.path")).read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split()
        if len(fields) != 4:
            raise EvaluationError(f"qrels line {line_number} is invalid")
        query_id, _, document_id, grade = fields
        if query_id not in qrels or document_id not in document_set:
            raise EvaluationError("qrels references unavailable materialized IDs")
        try:
            qrels[query_id][document_id] = int(grade)
        except ValueError as exc:
            raise EvaluationError(f"qrels line {line_number} has an invalid grade") from exc
    if qrels_entry.get("count") != sum(len(values) for values in qrels.values()) or any(not values for values in qrels.values()):
        raise EvaluationError("materialization qrels coverage is invalid")
    embedding_identity = {name: embedding[name] for name in ("model_id", "model_revision", "query_prefix", "document_prefix", "normalized")}
    output_hashes = {name: require_sha256(value.get("sha256"), f"outputs.{name}.sha256") for name, value in outputs.items() if isinstance(value, dict)}
    if len(output_hashes) != len(outputs):
        raise EvaluationError("materialization output descriptors are invalid")
    return {"root": root, "manifest": manifest, "manifest_sha256": sha256_file(manifest_path), "prepared_study_manifest_sha256": require_sha256(manifest.get("prepared_study_manifest_sha256"), "prepared_study_manifest_sha256"), "dimension": dimension, "train": vectors(train, len(train_ids)), "documents": vectors(documents, len(document_ids)), "queries": vectors(queries, len(query_ids)), "train_ids": train_ids, "document_ids": numpy.asarray(document_ids), "query_ids": query_ids, "qrels": qrels, "embedding_identity": embedding_identity, "output_hashes": output_hashes, "evaluation_document_ids_sha256": canonical_ids_sha256(document_ids), "evaluation_qrels_sha256": require_sha256(qrels_entry.get("sha256"), "outputs.evaluation_qrels.sha256")}


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


def conditional_centers(values: Any, codes: Any, symbol_count: int) -> Any:
    centers = numpy.empty((values.shape[1], symbol_count), dtype=numpy.float32)
    for symbol in range(symbol_count):
        selected = codes == symbol
        counts = selected.sum(axis=0)
        if numpy.any(counts == 0):
            raise EvaluationError("quantizer has an empty coordinate-symbol cell")
        centers[:, symbol] = (values * selected).sum(axis=0) / counts
    return centers


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


def equal_mass_thresholds(values: Any, symbol_count: int) -> Any:
    if symbol_count < 2:
        raise EvaluationError("scalar quantizer needs at least two symbols")
    return numpy.quantile(
        values,
        numpy.arange(1, symbol_count, dtype=numpy.float64) / symbol_count,
        axis=0,
    ).astype(numpy.float32)


def scalar_codes(values: Any, centers: Any | None = None, thresholds: Any | None = None) -> Any:
    if centers is not None:
        return numpy.abs(values[:, :, None] - centers[None, :, :]).argmin(axis=2).astype(numpy.uint8)
    if thresholds is None:
        raise EvaluationError("scalar quantizer is missing centers or thresholds")
    if thresholds.ndim != 2 or thresholds.shape[1] != values.shape[1]:
        raise EvaluationError("scalar quantizer thresholds are invalid")
    return numpy.count_nonzero(
        values[:, :, None] > thresholds.T[None, :, :], axis=2
    ).astype(numpy.uint8)


def pack_codes(codes: Any, symbol_count: int, symbols_per_byte: int) -> Any:
    count = codes.shape[1]
    result = numpy.zeros((codes.shape[0], (count + symbols_per_byte - 1) // symbols_per_byte), dtype=numpy.uint8)
    for offset in range(symbols_per_byte):
        indices = numpy.arange(offset, count, symbols_per_byte)
        if indices.size:
            result[:, :indices.size] += codes[:, indices] * (symbol_count ** offset)
    return result


def packed_lut(query: Any, centers: Any, symbol_count: int, symbols_per_byte: int) -> list[Any]:
    result: list[Any] = []
    for start in range(0, centers.shape[0], symbols_per_byte):
        width = min(symbols_per_byte, centers.shape[0] - start)
        table = numpy.empty(symbol_count ** width, dtype=numpy.float32)
        for value in range(symbol_count ** width):
            digits = [(value // (symbol_count ** offset)) % symbol_count for offset in range(width)]
            table[value] = sum(float((query[start + offset] - centers[start + offset, digit]) ** 2) for offset, digit in enumerate(digits))
        result.append(table)
    return result


def packed_adc_scores(packed_codes: Any, query: Any, centers: Any, symbol_count: int, symbols_per_byte: int) -> Any:
    """Scores packed scalar codes with query-specific asymmetric distance LUTs."""
    scores = numpy.zeros(packed_codes.shape[0], dtype=numpy.float32)
    for group, table in enumerate(packed_lut(query, centers, symbol_count, symbols_per_byte)):
        scores += table[packed_codes[:, group]]
    return scores


def total_marginal_entropy_bits(codes: Any, symbol_count: int) -> float:
    result = 0.0
    for coordinate in range(codes.shape[1]):
        probabilities = [float(numpy.mean(codes[:, coordinate] == symbol)) for symbol in range(symbol_count)]
        result += -sum(probability * math.log2(probability) for probability in probabilities if probability > 0.0)
    return result


def symbol_frequencies(codes: Any, symbol_count: int) -> list[float]:
    return [float(numpy.mean(codes == symbol)) for symbol in range(symbol_count)]


def dcg_at_10(ranked_ids: Any, grades: dict[str, int]) -> float:
    value = 0.0
    for rank, document_id in enumerate(ranked_ids[:10]):
        value += (2.0 ** grades.get(str(document_id), 0) - 1.0) / math.log2(rank + 2.0)
    ideal = sorted(grades.values(), reverse=True)[:10]
    denominator = sum((2.0 ** grade - 1.0) / math.log2(rank + 2.0) for rank, grade in enumerate(ideal))
    return value / denominator if denominator else 0.0


def contribution_identity(data: dict[str, Any], candidate_limit: int, oracle_k: int) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "ordered_query_ids_sha256": ordered_ids_sha256(data["query_ids"]),
        "query_count": len(data["query_ids"]),
        "evaluation_materialization_manifest_sha256": data["manifest_sha256"],
        "evaluation_qrels_sha256": data["evaluation_qrels_sha256"],
        "oracle_k": oracle_k,
        "candidate_limit": candidate_limit,
    }


def validate_contribution_identity(identity: Any, query_ids: Any, count: int) -> dict[str, Any]:
    if not isinstance(identity, dict) or identity.get("schema_version") != 1:
        raise EvaluationError("paired contribution identity schema is invalid")
    for name in ("ordered_query_ids_sha256", "evaluation_materialization_manifest_sha256", "evaluation_qrels_sha256"):
        require_sha256(identity.get(name), f"paired contribution identity.{name}")
    if identity.get("query_count") != count or identity.get("ordered_query_ids_sha256") != ordered_ids_sha256(query_ids.tolist()):
        raise EvaluationError("paired contribution query identity is invalid")
    if any(isinstance(identity.get(name), bool) or not isinstance(identity.get(name), int) or identity[name] <= 0 for name in ("oracle_k", "candidate_limit")):
        raise EvaluationError("paired contribution candidate identity is invalid")
    return identity


def evaluate_candidates(data: dict[str, Any], candidate_scores: Any, candidate_limit: int, oracle_k: int) -> tuple[dict[str, Any], dict[str, Any]]:
    if candidate_limit <= 0 or candidate_limit > len(data["document_ids"]) or oracle_k <= 0:
        raise EvaluationError("candidate limit or oracle K is invalid")
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
    return ({"exact_top_k_candidate_coverage": float(numpy.mean(coverage)), "reranked_ndcg_at_10": float(numpy.mean(rerank_ndcg)), "full_e5_ndcg_at_10": float(numpy.mean(full_ndcg)), "reference_candidate_search_seconds_including_query_encoding_and_full_ordering": candidate_seconds, "query_count": len(coverage)}, contributions)


def write_result(path: Path, report: dict[str, Any], contributions: dict[str, Any], contribution_path: Path, identity: dict[str, Any], query_ids: list[str]) -> None:
    contribution_path.parent.mkdir(parents=True, exist_ok=True)
    numpy.savez_compressed(
        contribution_path,
        **contributions,
        query_ids=numpy.asarray(query_ids, dtype=numpy.str_),
        identity_json=numpy.asarray(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
    )
    report["per_query_contributions_path"] = str(contribution_path)
    report["per_query_contributions_sha256"] = sha256_file(contribution_path)
    report["per_query_contribution_identity"] = identity
    report["evaluator_source_sha256"] = sha256_file(Path(__file__))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def require_artifact_weight(root: Path, entry: Any, expected_shape: list[int], expected_layout: str | None, name: str) -> Any:
    if not isinstance(entry, dict) or entry.get("dtype") != "float32_le" or entry.get("shape") != expected_shape or (expected_layout is not None and entry.get("layout") != expected_layout):
        raise EvaluationError(f"artifact weight descriptor is invalid: {name}")
    path = root / require_plain_path(entry.get("path"), f"artifact.weights.{name}.path")
    if not path.is_file() or path.stat().st_size != math.prod(expected_shape) * 4 or sha256_file(path) != require_sha256(entry.get("sha256"), f"artifact.weights.{name}.sha256"):
        raise EvaluationError(f"artifact weight payload is invalid: {name}")
    values = numpy.fromfile(path, dtype="<f4")
    if not numpy.isfinite(values).all():
        raise EvaluationError(f"artifact weight payload is non-finite: {name}")
    return values.reshape(expected_shape)


def validate_calibration_evaluation_pair(calibration: dict[str, Any], data: dict[str, Any]) -> None:
    if calibration["embedding_identity"] != data["embedding_identity"] or set(calibration["train_ids"]).intersection(data["document_ids"].tolist()):
        raise EvaluationError("calibration and evaluation roots violate the held-out embedding contract")


def load_artifact_for_evaluation(path: Path, data: dict[str, Any], calibration: dict[str, Any] | None) -> tuple[dict[str, Any], Any, Any]:
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"cannot read artifact: {exc}") from exc
    if not isinstance(artifact, dict) or artifact.get("schema_version") != 1:
        raise EvaluationError("artifact schema is unsupported")
    architecture = artifact.get("architecture")
    training = artifact.get("training")
    weights = artifact.get("weights")
    if not isinstance(architecture, dict) or not isinstance(training, dict) or not isinstance(weights, dict):
        raise EvaluationError("artifact sections are invalid")
    bit_count = architecture.get("bit_count")
    if isinstance(bit_count, bool) or not isinstance(bit_count, int) or bit_count <= 0 or architecture.get("input_dimension") != data["dimension"] or architecture.get("input_transform") != "clip_minus_one_one_v1":
        raise EvaluationError("artifact architecture is incompatible with evaluation vectors")
    family = architecture.get("family")
    if family in ("nlb_qrels_supervised_v1", "nlb_qrels_supervised_v2"):
        if calibration is None:
            raise EvaluationError("qrels-supervised artifact evaluation requires a calibration root")
        validate_calibration_evaluation_pair(calibration, data)
        teacher = training.get("teacher")
        exclusion = training.get("held_out_exclusion")
        loss_weights = training.get("loss_weights")
        calibration_contract = training.get("calibration")
        if artifact.get("input_materialization_manifest_sha256") != calibration["manifest_sha256"] or artifact.get("prepared_study_manifest_sha256") != calibration["prepared_study_manifest_sha256"] or not isinstance(teacher, dict) or {name: teacher.get(name) for name in data["embedding_identity"]} != data["embedding_identity"]:
            raise EvaluationError("artifact teacher embedding contract differs from evaluation")
        if not isinstance(calibration_contract, dict) or calibration_contract.get("document_ids_sha256") != canonical_ids_sha256(calibration["train_ids"]) or training.get("source_materialization_outputs_sha256") != canonical_json_sha256(calibration["output_hashes"]):
            raise EvaluationError("artifact calibration provenance differs from its materialization root")
        if not isinstance(exclusion, dict) or exclusion.get("id") != "external_excluded_document_ids_set_v1" or exclusion.get("document_ids_set_sha256") != data["evaluation_document_ids_sha256"]:
            raise EvaluationError("artifact held-out document exclusion differs from evaluation")
        has_triplet_weight = isinstance(loss_weights, dict) and "triplet" in loss_weights
        has_optimization_qrels_used = "optimization_qrels_used" in training
        if family == "nlb_qrels_supervised_v2" and (not has_triplet_weight or not has_optimization_qrels_used):
            raise EvaluationError("qrels-supervised v2 artifact requires explicit triplet provenance")
        if has_triplet_weight != has_optimization_qrels_used:
            raise EvaluationError("artifact qrels optimization provenance is incomplete")
        if has_triplet_weight and (not isinstance(loss_weights.get("triplet"), (int, float)) or isinstance(loss_weights.get("triplet"), bool) or not math.isfinite(loss_weights["triplet"]) or loss_weights["triplet"] < 0.0 or not isinstance(training.get("optimization_qrels_used"), bool) or training["optimization_qrels_used"] != (loss_weights["triplet"] > 0.0)):
            raise EvaluationError("artifact qrels optimization provenance is invalid")
    encoder_weights = require_artifact_weight(path.parent, weights.get("encoder_weights"), [bit_count, data["dimension"]], "row_major_out_by_in", "encoder_weights")
    encoder_bias = require_artifact_weight(path.parent, weights.get("encoder_bias"), [bit_count], None, "encoder_bias")
    return artifact, encoder_weights, encoder_bias


def evaluate_artifact(args: Any) -> None:
    data = load_root(args.evaluation_root)
    calibration = load_root(args.calibration_root) if args.calibration_root is not None else None
    artifact, weights, bias = load_artifact_for_evaluation(args.artifact, data, calibration)
    document_codes = (numpy.clip(data["documents"], -1.0, 1.0) @ weights.T + bias >= 0.0)
    def candidates(index: int, query: Any) -> Any:
        query_code = numpy.clip(query, -1.0, 1.0) @ weights.T + bias >= 0.0
        distance = numpy.count_nonzero(document_codes != query_code, axis=1)
        return numpy.lexsort((data["document_ids"], distance))
    metrics, contributions = evaluate_candidates(data, candidates, args.candidate_limit, args.oracle_k)
    identity = contribution_identity(data, args.candidate_limit, args.oracle_k)
    write_result(args.output, {"schema_version": 2, "family": "binary_artifact_hamming_reference_v2", "artifact_sha256": sha256_file(args.artifact), "evaluation_materialization_manifest_sha256": data["manifest_sha256"], "evaluation_qrels_sha256": data["evaluation_qrels_sha256"], "oracle_k": args.oracle_k, "candidate_limit": args.candidate_limit, **metrics}, contributions, args.contributions_output, identity, data["query_ids"])


def evaluate_ternary(args: Any) -> None:
    calibration = load_root(args.calibration_root); data = load_root(args.evaluation_root)
    validate_calibration_evaluation_pair(calibration, data)
    weights = itq_weights(calibration["train"], args.coordinate_count, args.seed, args.itq_iterations) if args.projection == "itq" else pca_weights(calibration["train"], args.coordinate_count)
    calibration_projection = numpy.clip(calibration["train"], -1.0, 1.0) @ weights.T
    document_projection = numpy.clip(data["documents"], -1.0, 1.0) @ weights.T
    if args.quantizer == "binary":
        thresholds = binary_thresholds(calibration["train"], weights)
        calibration_codes = (calibration_projection + thresholds >= 0.0).astype(numpy.uint8)
        document_codes = (document_projection + thresholds >= 0.0).astype(numpy.uint8)
        symbol_count = 2; symbols_per_byte = 8; code_assignment = "per_coordinate_median_threshold_v1"; centers = conditional_centers(calibration_projection, calibration_codes, symbol_count)
        if args.scoring == "symmetric":
            def candidates(index: int, query: Any) -> Any:
                query_code = numpy.clip(query, -1.0, 1.0) @ weights.T + thresholds >= 0.0
                return numpy.lexsort((data["document_ids"], numpy.count_nonzero(document_codes != query_code, axis=1)))
            scoring = "binary_hamming_reference_v2"
        else:
            packed = pack_codes(document_codes, symbol_count, symbols_per_byte)
            def candidates(index: int, query: Any) -> Any:
                query_projection = numpy.clip(query, -1.0, 1.0) @ weights.T
                return numpy.lexsort((data["document_ids"], packed_adc_scores(packed, query_projection, centers, symbol_count, symbols_per_byte)))
            scoring = "binary_adc_packed_base2_lut_v1"
    elif args.quantizer == "kmeans":
        if args.scoring != "adc":
            raise EvaluationError("Lloyd-Max ternary codes require ADC scoring")
        centers = kmeans_centers(calibration_projection, args.kmeans_iterations)
        calibration_codes = scalar_codes(calibration_projection, centers=centers)
        document_codes = scalar_codes(document_projection, centers=centers)
        symbol_count = 3; symbols_per_byte = 5; code_assignment = "per_coordinate_lloyd_max_kmeans_v1"; packed = pack_codes(document_codes, symbol_count, symbols_per_byte)
        def candidates(index: int, query: Any) -> Any:
            query_projection = numpy.clip(query, -1.0, 1.0) @ weights.T
            cost = packed_adc_scores(packed, query_projection, centers, symbol_count, symbols_per_byte)
            return numpy.lexsort((data["document_ids"], cost))
        scoring = "ternary_adc_packed_base3_lut_v1"
    elif args.quantizer == "tertiles":
        thresholds = equal_mass_thresholds(calibration_projection, 3)
        calibration_codes = scalar_codes(calibration_projection, thresholds=thresholds)
        document_codes = scalar_codes(document_projection, thresholds=thresholds)
        symbol_count = 3; symbols_per_byte = 5; code_assignment = "per_coordinate_tertile_threshold_v1"; centers = conditional_centers(calibration_projection, calibration_codes, symbol_count)
        if args.scoring == "symmetric":
            def candidates(index: int, query: Any) -> Any:
                query_projection = numpy.asarray([numpy.clip(query, -1.0, 1.0) @ weights.T])
                query_code = scalar_codes(query_projection, thresholds=thresholds)[0]
                return numpy.lexsort((data["document_ids"], numpy.abs(document_codes.astype(numpy.int16) - query_code).sum(axis=1)))
            scoring = "ternary_symmetric_l1_v1"
        else:
            packed = pack_codes(document_codes, symbol_count, symbols_per_byte)
            def candidates(index: int, query: Any) -> Any:
                query_projection = numpy.clip(query, -1.0, 1.0) @ weights.T
                return numpy.lexsort((data["document_ids"], packed_adc_scores(packed, query_projection, centers, symbol_count, symbols_per_byte)))
            scoring = "ternary_adc_packed_base3_lut_v1"
    elif args.quantizer == "quartiles":
        if args.scoring != "adc":
            raise EvaluationError("quartile scalar codes require ADC scoring")
        thresholds = equal_mass_thresholds(calibration_projection, 4)
        calibration_codes = scalar_codes(calibration_projection, thresholds=thresholds)
        document_codes = scalar_codes(document_projection, thresholds=thresholds)
        symbol_count = 4; symbols_per_byte = 4
        code_assignment = "per_coordinate_quartile_threshold_v1"
        centers = conditional_centers(calibration_projection, calibration_codes, symbol_count)
        packed = pack_codes(document_codes, symbol_count, symbols_per_byte)
        def candidates(index: int, query: Any) -> Any:
            query_projection = numpy.clip(query, -1.0, 1.0) @ weights.T
            cost = packed_adc_scores(packed, query_projection, centers, symbol_count, symbols_per_byte)
            return numpy.lexsort((data["document_ids"], cost))
        scoring = "quaternary_adc_packed_base4_lut_v1"
    metrics, contributions = evaluate_candidates(data, candidates, args.candidate_limit, args.oracle_k)
    payload_bytes = (args.coordinate_count + symbols_per_byte - 1) // symbols_per_byte
    identity = contribution_identity(data, args.candidate_limit, args.oracle_k)
    total_entropy = total_marginal_entropy_bits(document_codes, symbol_count)
    maximum_entropy = args.coordinate_count * math.log2(symbol_count)
    write_result(args.output, {"schema_version": 2, "family": "scalar_projection_reference_v2", "projection": args.projection, "quantizer": args.quantizer, "code_assignment": code_assignment, "scoring": scoring, "symbol_count": symbol_count, "coordinate_count": args.coordinate_count, "packed_payload_bytes_per_document": payload_bytes, "evaluation_materialization_manifest_sha256": data["manifest_sha256"], "evaluation_qrels_sha256": data["evaluation_qrels_sha256"], "calibration_materialization_manifest_sha256": calibration["manifest_sha256"], "projection_weights_sha256": hashlib.sha256(numpy.asarray(weights, dtype="<f4").tobytes()).hexdigest(), "centroids_sha256": hashlib.sha256(numpy.asarray(centers, dtype="<f4").tobytes()).hexdigest(), "seed": args.seed, "itq_iterations": args.itq_iterations if args.projection == "itq" else 0, "kmeans_iterations": args.kmeans_iterations if args.quantizer == "kmeans" else 0, "oracle_k": args.oracle_k, "candidate_limit": args.candidate_limit, "symbol_frequencies": symbol_frequencies(document_codes, symbol_count), "total_marginal_symbol_entropy_bits": total_entropy, "maximum_marginal_symbol_entropy_bits": maximum_entropy, "normalized_marginal_symbol_entropy": total_entropy / maximum_entropy, **metrics}, contributions, args.contributions_output, identity, data["query_ids"])


def bootstrap(args: Any) -> None:
    left = numpy.load(args.left_contributions, allow_pickle=False); right = numpy.load(args.right_contributions, allow_pickle=False)
    generator = numpy.random.default_rng(args.seed); count = left["coverage_at_candidate_limit"].shape[0]
    required = {"coverage_at_candidate_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10", "query_ids", "identity_json"}
    if set(left.files) != required or set(right.files) != required or count != right["coverage_at_candidate_limit"].shape[0] or not numpy.array_equal(left["query_ids"], right["query_ids"]):
        raise EvaluationError("paired contribution query identities differ")
    if any(left[name].shape != (count,) or right[name].shape != (count,) for name in ("coverage_at_candidate_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10")):
        raise EvaluationError("paired contribution metric shapes differ")
    try:
        identity = json.loads(str(left["identity_json"].item()))
        right_identity = json.loads(str(right["identity_json"].item()))
    except (ValueError, AttributeError) as exc:
        raise EvaluationError("paired contribution identity metadata is invalid") from exc
    validate_contribution_identity(identity, left["query_ids"], count)
    validate_contribution_identity(right_identity, right["query_ids"], count)
    if identity != right_identity:
        raise EvaluationError("paired contribution evaluation contract differs")
    report: dict[str, Any] = {"schema_version": 2, "family": "paired_query_bootstrap_v2", "id": args.comparison_id, "left_sha256": sha256_file(args.left_contributions), "right_sha256": sha256_file(args.right_contributions), "identity": identity, "query_count": count, "replicates": args.replicates, "seed": args.seed, "evaluator_source_sha256": sha256_file(Path(__file__)), "metrics": {}}
    for name in ("coverage_at_candidate_limit", "reranked_ndcg_at_10"):
        difference = right[name] - left[name]
        samples = numpy.empty(args.replicates, dtype=numpy.float64)
        for index in range(args.replicates): samples[index] = difference[generator.integers(0, count, size=count)].mean()
        report["metrics"][name] = {"observed_difference": float(difference.mean()), "percentile_95_ci": [float(numpy.quantile(samples, 0.025)), float(numpy.quantile(samples, 0.975))]}
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def write_compact_manifest(args: Any) -> None:
    rows: list[dict[str, Any]] = []
    contribution_hashes: set[str] = set()
    reference_identity: dict[str, Any] | None = None
    reference_calibration_manifest_sha256: str | None = None
    reference_evaluator_source_sha256: str | None = None
    for report_path in args.report:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvaluationError(f"cannot read scalar projection report: {report_path}: {exc}") from exc
        if report.get("schema_version") != 2 or report.get("family") != "scalar_projection_reference_v2":
            raise EvaluationError(f"scalar projection report schema is unsupported: {report_path}")
        evaluator_source_sha256 = require_sha256(
            report.get("evaluator_source_sha256"),
            "scalar projection report evaluator_source_sha256",
        )
        calibration_manifest_sha256 = require_sha256(
            report.get("calibration_materialization_manifest_sha256"),
            "scalar projection report calibration_materialization_manifest_sha256",
        )
        contribution_path = report_path.parent / Path(report.get("per_query_contributions_path", "")).name
        if not contribution_path.is_file() or report.get("per_query_contributions_sha256") != sha256_file(contribution_path):
            raise EvaluationError(f"scalar projection contribution hash differs: {report_path}")
        with numpy.load(contribution_path, allow_pickle=False) as contributions:
            required = {"coverage_at_candidate_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10", "query_ids", "identity_json"}
            if set(contributions.files) != required:
                raise EvaluationError(f"scalar projection contribution schema is invalid: {report_path}")
            query_count = report.get("query_count")
            if isinstance(query_count, bool) or not isinstance(query_count, int) or query_count <= 0:
                raise EvaluationError(f"scalar projection query count is invalid: {report_path}")
            identity = validate_contribution_identity(report.get("per_query_contribution_identity"), contributions["query_ids"], query_count)
            means: dict[str, float] = {}
            for contribution_name, report_name in (("coverage_at_candidate_limit", "exact_top_k_candidate_coverage"), ("reranked_ndcg_at_10", "reranked_ndcg_at_10"), ("full_e5_ndcg_at_10", "full_e5_ndcg_at_10")):
                values = contributions[contribution_name]
                if values.shape != (query_count,) or not numpy.isfinite(values).all() or numpy.any(values < 0.0) or numpy.any(values > 1.0):
                    raise EvaluationError(f"scalar projection contribution values are invalid: {report_path}")
                reported = report.get(report_name)
                if isinstance(reported, bool) or not isinstance(reported, (int, float)) or not math.isfinite(reported):
                    raise EvaluationError(f"scalar projection aggregate metric is invalid: {report_path}")
                means[report_name] = float(numpy.mean(values))
                if not math.isclose(float(reported), means[report_name], rel_tol=0.0, abs_tol=1.0e-12):
                    raise EvaluationError(f"scalar projection aggregate metric differs from contributions: {report_path}")
        frequencies = report.get("symbol_frequencies")
        if not isinstance(frequencies, list) or len(frequencies) != report.get("symbol_count") or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0.0 or value > 1.0 for value in frequencies) or not math.isclose(sum(float(value) for value in frequencies), 1.0, rel_tol=0.0, abs_tol=1.0e-12):
            raise EvaluationError(f"scalar projection symbol frequencies are invalid: {report_path}")
        entropy = report.get("total_marginal_symbol_entropy_bits")
        maximum_entropy = report.get("maximum_marginal_symbol_entropy_bits")
        normalized_entropy = report.get("normalized_marginal_symbol_entropy")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in (entropy, maximum_entropy, normalized_entropy)) or entropy < 0.0 or maximum_entropy <= 0.0 or entropy > maximum_entropy or not math.isclose(float(normalized_entropy), float(entropy) / float(maximum_entropy), rel_tol=0.0, abs_tol=1.0e-12):
            raise EvaluationError(f"scalar projection marginal entropy is invalid: {report_path}")
        if reference_identity is None:
            reference_identity = identity
        elif identity != reference_identity:
            raise EvaluationError("scalar projection manifest mixes evaluation identities")
        if reference_calibration_manifest_sha256 is None:
            reference_calibration_manifest_sha256 = calibration_manifest_sha256
        elif calibration_manifest_sha256 != reference_calibration_manifest_sha256:
            raise EvaluationError("scalar projection manifest mixes calibration identities")
        if reference_evaluator_source_sha256 is None:
            reference_evaluator_source_sha256 = evaluator_source_sha256
        elif evaluator_source_sha256 != reference_evaluator_source_sha256:
            raise EvaluationError("scalar projection manifest mixes evaluator sources")
        contribution_sha256 = sha256_file(contribution_path)
        contribution_hashes.add(contribution_sha256)
        rows.append({
            "report_file": report_path.name,
            "report_sha256": sha256_file(report_path),
            "contributions_file": contribution_path.name,
            "contributions_sha256": contribution_sha256,
            "projection": report["projection"],
            "quantizer": report["quantizer"],
            "code_assignment": report["code_assignment"],
            "scoring": report["scoring"],
            "symbol_count": report["symbol_count"],
            "coordinate_count": report["coordinate_count"],
            "packed_payload_bytes_per_document": report["packed_payload_bytes_per_document"],
            "calibration_materialization_manifest_sha256": report[
                "calibration_materialization_manifest_sha256"
            ],
            "seed": report["seed"],
            "itq_iterations": report["itq_iterations"],
            "kmeans_iterations": report["kmeans_iterations"],
            "projection_weights_sha256": report["projection_weights_sha256"],
            "centroids_sha256": report["centroids_sha256"],
            "symbol_frequencies": frequencies,
            "total_marginal_symbol_entropy_bits": entropy,
            "maximum_marginal_symbol_entropy_bits": maximum_entropy,
            "normalized_marginal_symbol_entropy": normalized_entropy,
            "exact_top_k_candidate_coverage": means["exact_top_k_candidate_coverage"],
            "reranked_ndcg_at_10": means["reranked_ndcg_at_10"],
            "full_e5_ndcg_at_10": means["full_e5_ndcg_at_10"],
        })
    if reference_identity is None or reference_calibration_manifest_sha256 is None or reference_evaluator_source_sha256 is None:
        raise EvaluationError("scalar projection manifest needs at least one report")
    comparisons: list[dict[str, Any]] = []
    comparison_ids: set[str] = set()
    for comparison_path in args.comparison:
        try:
            comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvaluationError(f"cannot read bootstrap comparison: {comparison_path}: {exc}") from exc
        comparison_id = comparison.get("id")
        if not isinstance(comparison_id, str) or not comparison_id or comparison_id in comparison_ids or comparison.get("schema_version") != 2 or comparison.get("family") != "paired_query_bootstrap_v2" or comparison.get("identity") != reference_identity or comparison.get("evaluator_source_sha256") != reference_evaluator_source_sha256:
            raise EvaluationError(f"bootstrap comparison contract is invalid: {comparison_path}")
        left_sha256 = require_sha256(comparison.get("left_sha256"), "bootstrap left_sha256")
        right_sha256 = require_sha256(comparison.get("right_sha256"), "bootstrap right_sha256")
        if left_sha256 not in contribution_hashes or right_sha256 not in contribution_hashes:
            raise EvaluationError(f"bootstrap comparison inputs are not in this manifest: {comparison_path}")
        metrics = comparison.get("metrics")
        if not isinstance(metrics, dict) or not isinstance(comparison.get("replicates"), int) or comparison["replicates"] <= 0 or not isinstance(comparison.get("seed"), int):
            raise EvaluationError(f"bootstrap comparison metrics are invalid: {comparison_path}")
        entry: dict[str, Any] = {"id": comparison_id, "left_contributions_sha256": left_sha256, "right_contributions_sha256": right_sha256, "bootstrap_report_file": comparison_path.name, "bootstrap_report_sha256": sha256_file(comparison_path), "replicates": comparison["replicates"], "seed": comparison["seed"]}
        for source_name, target_name in (("coverage_at_candidate_limit", "coverage"), ("reranked_ndcg_at_10", "ndcg")):
            metric = metrics.get(source_name)
            if not isinstance(metric, dict):
                raise EvaluationError(f"bootstrap comparison metric is invalid: {comparison_path}")
            difference = metric.get("observed_difference")
            interval = metric.get("percentile_95_ci")
            if isinstance(difference, bool) or not isinstance(difference, (int, float)) or not math.isfinite(difference) or not isinstance(interval, list) or len(interval) != 2 or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in interval) or interval[0] > interval[1]:
                raise EvaluationError(f"bootstrap comparison interval is invalid: {comparison_path}")
            entry[f"{target_name}_delta"] = float(difference)
            entry[f"{target_name}_percentile_95_ci"] = [float(interval[0]), float(interval[1])]
        comparison_ids.add(comparison_id)
        comparisons.append(entry)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"schema_version": 2, "family": "scalar_projection_result_manifest_v2", "evaluator_source_sha256": reference_evaluator_source_sha256, "calibration_materialization_manifest_sha256": reference_calibration_manifest_sha256, "evaluation_identity": reference_identity, "rows": sorted(rows, key=lambda row: row["report_file"]), "comparisons": sorted(comparisons, key=lambda entry: entry["id"])}, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def run_self_test() -> int:
    codes = numpy.asarray([[0, 1, 2, 0, 1, 2], [2, 1, 0, 2, 1, 0]], dtype=numpy.uint8)
    packed = pack_codes(codes, 3, 5)
    if packed.tolist() != [[102, 2], [140, 0]]:
        print("self-test failed: base-3 packing", file=__import__("sys").stderr); return 1
    centers = numpy.asarray([[0.0, 1.0, 2.0]] * 6, dtype=numpy.float32)
    lookup = packed_lut(numpy.asarray([0.0] * 6, dtype=numpy.float32), centers, 3, 5)
    if not numpy.isclose(sum(table[packed[0, group]] for group, table in enumerate(lookup)), 10.0):
        print("self-test failed: packed ADC LUT", file=__import__("sys").stderr); return 1
    direct = numpy.asarray([
        sum((0.0 - centers[coordinate, symbol]) ** 2 for coordinate, symbol in enumerate(row))
        for row in codes
    ], dtype=numpy.float32)
    if not numpy.allclose(packed_adc_scores(packed, numpy.zeros(6, dtype=numpy.float32), centers, 3, 5), direct):
        print("self-test failed: packed ADC scalar parity", file=__import__("sys").stderr); return 1
    binary_codes = numpy.asarray([[0, 1, 1, 0, 1, 0, 1, 0]], dtype=numpy.uint8)
    binary_packed = pack_codes(binary_codes, 2, 8)
    binary_centers = numpy.asarray([[0.0, 1.0]] * 8, dtype=numpy.float32)
    if binary_packed.tolist() != [[86]] or not numpy.isclose(packed_adc_scores(binary_packed, numpy.zeros(8, dtype=numpy.float32), binary_centers, 2, 8)[0], 4.0):
        print("self-test failed: packed binary ADC LUT", file=__import__("sys").stderr); return 1
    quaternary_codes = numpy.asarray([[0, 1, 2, 3]], dtype=numpy.uint8)
    quaternary_packed = pack_codes(quaternary_codes, 4, 4)
    quaternary_centers = numpy.asarray([[0.0, 1.0, 2.0, 3.0]] * 4, dtype=numpy.float32)
    if quaternary_packed.tolist() != [[228]] or not numpy.isclose(packed_adc_scores(quaternary_packed, numpy.zeros(4, dtype=numpy.float32), quaternary_centers, 4, 4)[0], 14.0):
        print("self-test failed: packed quaternary ADC LUT", file=__import__("sys").stderr); return 1
    entropy = total_marginal_entropy_bits(numpy.asarray([[0, 0], [0, 1], [1, 0], [1, 1]], dtype=numpy.uint8), 2)
    if not numpy.isclose(entropy, 2.0):
        print("self-test failed: total marginal entropy", file=__import__("sys").stderr); return 1
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        query_ids = numpy.asarray(["q0", "q1"], dtype=numpy.str_)
        identity = {"schema_version": 1, "ordered_query_ids_sha256": ordered_ids_sha256(query_ids.tolist()), "query_count": 2, "evaluation_materialization_manifest_sha256": "a" * 64, "evaluation_qrels_sha256": "b" * 64, "oracle_k": 10, "candidate_limit": 512}
        payload = {"coverage_at_candidate_limit": numpy.asarray([0.5, 1.0]), "reranked_ndcg_at_10": numpy.asarray([0.5, 1.0]), "full_e5_ndcg_at_10": numpy.asarray([0.5, 1.0]), "query_ids": query_ids, "identity_json": numpy.asarray(json.dumps(identity, sort_keys=True, separators=(",", ":")))}
        left_path = root / "left.npz"; right_path = root / "right.npz"
        numpy.savez_compressed(left_path, **payload); numpy.savez_compressed(right_path, **payload)
        bootstrap(argparse.Namespace(left_contributions=left_path, right_contributions=right_path, output=root / "bootstrap.json", replicates=8, seed=42, comparison_id="self_test"))
        def report_for(path: Path, contribution_path: Path) -> None:
            path.write_text(json.dumps({"schema_version": 2, "family": "scalar_projection_reference_v2", "projection": "pca", "quantizer": "binary", "code_assignment": "self_test", "scoring": "binary_adc_packed_base2_lut_v1", "symbol_count": 2, "coordinate_count": 2, "packed_payload_bytes_per_document": 1, "evaluation_materialization_manifest_sha256": "a" * 64, "evaluation_qrels_sha256": "b" * 64, "calibration_materialization_manifest_sha256": "c" * 64, "projection_weights_sha256": "d" * 64, "centroids_sha256": "e" * 64, "seed": 42, "itq_iterations": 0, "kmeans_iterations": 0, "oracle_k": 10, "candidate_limit": 512, "query_count": 2, "symbol_frequencies": [0.5, 0.5], "total_marginal_symbol_entropy_bits": 2.0, "maximum_marginal_symbol_entropy_bits": 2.0, "normalized_marginal_symbol_entropy": 1.0, "exact_top_k_candidate_coverage": 0.75, "reranked_ndcg_at_10": 0.75, "full_e5_ndcg_at_10": 0.75, "per_query_contributions_path": contribution_path.name, "per_query_contributions_sha256": sha256_file(contribution_path), "per_query_contribution_identity": identity, "evaluator_source_sha256": sha256_file(Path(__file__))}, sort_keys=True), encoding="utf-8", newline="\n")
        left_report = root / "left.json"; right_report = root / "right.json"
        report_for(left_report, left_path); report_for(right_report, right_path)
        write_compact_manifest(argparse.Namespace(report=[left_report, right_report], comparison=[root / "bootstrap.json"], output=root / "manifest.json"))
        corrupted = json.loads(left_report.read_text(encoding="utf-8")); corrupted["exact_top_k_candidate_coverage"] = 0.5
        left_report.write_text(json.dumps(corrupted, sort_keys=True), encoding="utf-8", newline="\n")
        try:
            write_compact_manifest(argparse.Namespace(report=[left_report, right_report], comparison=[], output=root / "unexpected-manifest.json"))
            print("self-test failed: aggregate metric mismatch accepted", file=__import__("sys").stderr); return 1
        except EvaluationError:
            pass
        payload["query_ids"] = query_ids[::-1]
        numpy.savez_compressed(right_path, **payload)
        try:
            bootstrap(argparse.Namespace(left_contributions=left_path, right_contributions=right_path, output=root / "unexpected.json", replicates=8, seed=42, comparison_id="self_test"))
            print("self-test failed: mismatched query IDs accepted", file=__import__("sys").stderr); return 1
        except EvaluationError:
            pass
    print("projection quantization evaluator self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False); common.add_argument("--evaluation-root", type=Path, required=True); common.add_argument("--output", type=Path, required=True); common.add_argument("--contributions-output", type=Path, required=True); common.add_argument("--oracle-k", type=int, default=10); common.add_argument("--candidate-limit", type=int, default=512)
    binary = sub.add_parser("binary", parents=[common]); binary.add_argument("--artifact", type=Path, required=True); binary.add_argument("--calibration-root", type=Path)
    ternary = sub.add_parser("ternary", parents=[common]); ternary.add_argument("--calibration-root", type=Path, required=True); ternary.add_argument("--projection", choices=("pca", "itq"), required=True); ternary.add_argument("--quantizer", choices=("binary", "tertiles", "kmeans", "quartiles"), required=True); ternary.add_argument("--scoring", choices=("symmetric", "adc"), required=True); ternary.add_argument("--coordinate-count", "--trit-count", dest="coordinate_count", type=int, required=True); ternary.add_argument("--seed", type=int, default=42); ternary.add_argument("--itq-iterations", type=int, default=50); ternary.add_argument("--kmeans-iterations", type=int, default=25)
    boot = sub.add_parser("bootstrap"); boot.add_argument("--left-contributions", type=Path, required=True); boot.add_argument("--right-contributions", type=Path, required=True); boot.add_argument("--output", type=Path, required=True); boot.add_argument("--comparison-id", required=True); boot.add_argument("--replicates", type=int, default=10000); boot.add_argument("--seed", type=int, default=42)
    manifest = sub.add_parser("write-manifest"); manifest.add_argument("--report", type=Path, action="append", required=True); manifest.add_argument("--comparison", type=Path, action="append", default=[]); manifest.add_argument("--output", type=Path, required=True)
    sub.add_parser("self-test"); args = parser.parse_args(argv)
    try:
        if args.command == "binary": evaluate_artifact(args)
        elif args.command == "ternary": evaluate_ternary(args)
        elif args.command == "bootstrap": bootstrap(args)
        elif args.command == "write-manifest": write_compact_manifest(args)
        else: return run_self_test()
    except (EvaluationError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"evaluate-projection-quantization: {error}", file=__import__("sys").stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main(__import__("sys").argv[1:]))
