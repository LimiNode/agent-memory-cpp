#!/usr/bin/env python3
"""Calibration-only geometry decomposition for the first MIH-aware ITQ path."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()
FAMILY = "mih_aware_itq_geometry_diagnosis_v1"
FRONTIER_MEASURED_COMMIT = "c156fffa213c7e4d8b1221fd88e7028436e13359"
CONTRACT = {
    "schema_version": 1, "family": FAMILY,
    "calibration": {"vector_count": 25000, "validation_fraction": 0.2, "neighbor_anchor_count": 1024, "neighbor_k": 10, "random_pair_count": 10000},
    "encoding": {"code_bits": 256, "band_count": 32, "band_width_bits": 8, "itq_iterations": 50, "seeds": [52, 53, 54, 55, 56]},
    "treatments": ["full-itq-25k", "split-itq-80-no-sgd", "split-init-zero-work", "split-init-work-0.10"],
    "data_access": {"evaluation_root_allowed": False, "query_vectors_allowed": False, "qrels_allowed": False},
}


def load(name: str, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, THIS.with_name(name))
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module); return module


shared = load("evaluate-projection-quantization.py", "mih_aware_geometry_shared")
banding = load("evaluate-mih-banding.py", "mih_aware_geometry_banding")
trainer = load("train-mih-aware-itq.py", "mih_aware_geometry_trainer")
frontier = load("run-mih-aware-itq-frontier.py", "mih_aware_geometry_frontier")


def require(condition: bool, message: str) -> None:
    if not condition: raise ValueError(message)


def sha256_bytes(value: bytes) -> str: return hashlib.sha256(value).hexdigest()
def sha256(path: Path) -> str: return sha256_bytes(path.read_bytes())
def source_bundle(files: dict[str, str]) -> str: return sha256_bytes(json.dumps(files, sort_keys=True, separators=(",", ":")).encode())


def source_files() -> dict[str, str]:
    names = (THIS.name, "evaluate-projection-quantization.py", "evaluate-mih-banding.py", "train-mih-aware-itq.py", "train-learned-binary-adc.py")
    return {name: sha256(THIS.with_name(name)) for name in names}


def measured_bytes(name: str) -> bytes:
    result = subprocess.run(("git", "show", f"{FRONTIER_MEASURED_COMMIT}:tools/agent-memory-bench/{name}"), cwd=THIS.parents[2], check=True, capture_output=True)
    return result.stdout


def measured_hashes(names: tuple[str, ...]) -> dict[str, str]:
    return {name: sha256_bytes(measured_bytes(name)) for name in names}


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8")); require(value == CONTRACT, "geometry contract differs from the predeclared protocol"); return value


def summary(values: Any) -> dict[str, float | int]:
    array = numpy.asarray(values); require(array.size > 0, "empty geometry distribution")
    return {"mean": float(numpy.mean(array)), "p95": float(numpy.quantile(array, .95)), "maximum": int(numpy.max(array))}


def bit_entropy(probability: Any) -> Any:
    value = numpy.asarray(probability, dtype=numpy.float64)
    return -(numpy.where(value > 0, value * numpy.log2(value), 0) + numpy.where(value < 1, (1 - value) * numpy.log2(1 - value), 0))


def union_counts(index: list[dict[int, numpy.ndarray]], codes: numpy.ndarray, ranges: list[tuple[int, int]]) -> tuple[numpy.ndarray, numpy.ndarray]:
    """Count radius-zero/one unions without allocating or sorting candidate sets."""
    count = len(codes); exact_marks = numpy.zeros(count, dtype=numpy.uint32); radius_one_marks = numpy.zeros(count, dtype=numpy.uint32); exact_counts = numpy.empty(count, dtype=numpy.int32); radius_one_counts = numpy.empty(count, dtype=numpy.int32)
    for query_index, code in enumerate(codes):
        stamp = query_index + 1; exact_total = 0; radius_one_total = 0
        for buckets, (start, stop) in zip(index, ranges):
            exact_key = banding.band_key(code, start, stop); exact_posting = buckets.get(exact_key)
            if exact_posting is not None:
                fresh_exact = exact_marks[exact_posting] != stamp; exact_marks[exact_posting[fresh_exact]] = stamp; exact_total += int(numpy.count_nonzero(fresh_exact))
            for key in banding.probe_keys(exact_key, stop - start, 1):
                posting = buckets.get(key)
                if posting is not None:
                    fresh_radius_one = radius_one_marks[posting] != stamp; radius_one_marks[posting[fresh_radius_one]] = stamp; radius_one_total += int(numpy.count_nonzero(fresh_radius_one))
        exact_counts[query_index] = exact_total; radius_one_counts[query_index] = radius_one_total
    return exact_counts, radius_one_counts


def geometry(codes: numpy.ndarray, vectors: numpy.ndarray, seed: int, contract: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    ranges = banding.band_ranges(256, 32); index = banding.build_index(codes, ranges); count = len(codes); occupancy = codes.mean(axis=0); bands = []; exact_visits = numpy.zeros(count, dtype=numpy.int32); radius_one_visits = numpy.zeros(count, dtype=numpy.int32)
    for number, ((start, stop), buckets) in enumerate(zip(ranges, index)):
        sizes = numpy.asarray([len(posting) for posting in buckets.values()], dtype=numpy.int32); probability = sizes / count
        exact = numpy.asarray([len(buckets.get(banding.band_key(code, start, stop), ())) for code in codes], dtype=numpy.int32)
        radius_one = numpy.asarray([sum(len(buckets.get(key, ())) for key in banding.probe_keys(banding.band_key(code, start, stop), 8, 1)) for code in codes], dtype=numpy.int32)
        exact_visits += exact; radius_one_visits += radius_one
        values = codes[:, start:stop].astype(numpy.float64); centered = values - values.mean(axis=0); scale = numpy.sqrt((centered * centered).sum(axis=0)); corr = (centered.T @ centered) / numpy.outer(scale, scale)
        bands.append({"band": number, "bucket_entropy_bits": float(-(numpy.where(probability > 0, probability * numpy.log2(probability), 0)).sum()), "occupied_bucket_count": len(buckets), "posting_size": summary(sizes), "exact_match_probability": float((probability * probability).sum()), "radius_one_match_probability": float(radius_one.mean() / count), "radius_one_posting_visits": summary(radius_one), "mean_absolute_intraband_correlation": float(numpy.abs(corr[numpy.triu_indices(8, 1)]).mean())})
    radius_zero_candidates, radius_one_candidates = union_counts(index, codes, ranges)
    calibration = contract["calibration"]; generator = numpy.random.default_rng(seed); random_left = generator.integers(0, count, calibration["random_pair_count"], dtype=numpy.int32); random_right = (random_left + generator.integers(1, count, calibration["random_pair_count"], dtype=numpy.int32)) % count; random_distance = numpy.count_nonzero(codes[random_left] != codes[random_right], axis=1).astype(numpy.int16)
    anchors = numpy.sort(generator.choice(count, calibration["neighbor_anchor_count"], replace=False)).astype(numpy.int32); neighbours = numpy.empty((len(anchors), calibration["neighbor_k"]), dtype=numpy.int32)
    for start in range(0, len(anchors), 64):
        anchor = anchors[start:start + 64]; scores = vectors[anchor] @ vectors.T; scores[numpy.arange(len(anchor)), anchor] = -numpy.inf; neighbours[start:start + len(anchor)] = numpy.argpartition(-scores, calibration["neighbor_k"], axis=1)[:, :calibration["neighbor_k"]]
    neighbour_distance = numpy.count_nonzero(codes[anchors, None, :] != codes[neighbours], axis=2).astype(numpy.int16)
    raw = {"radius0_candidate_count": radius_zero_candidates, "radius0_posting_visits": exact_visits, "radius1_candidate_count": radius_one_candidates, "radius1_posting_visits": radius_one_visits, "random_pair_hamming": random_distance, "e5_neighbor_hamming": neighbour_distance, "random_pair_left_indices": random_left, "random_pair_right_indices": random_right, "neighbor_anchor_indices": anchors, "e5_neighbor_indices": neighbours}
    report = {"per_bit_probability_one": [float(value) for value in occupancy], "per_bit_entropy": [float(value) for value in bit_entropy(occupancy)], "constant_bit_count": int(((occupancy == 0) | (occupancy == 1)).sum()), "mean_bit_entropy": float(bit_entropy(occupancy).mean()), "bands": bands, "union_work": {"radius_0": {"unique_candidates": summary(raw["radius0_candidate_count"]), "posting_visits": summary(exact_visits)}, "radius_1": {"unique_candidates": summary(raw["radius1_candidate_count"]), "posting_visits": summary(radius_one_visits)}}, "hamming": {"random_document_pairs": summary(random_distance), "e5_calibration_neighbors": summary(neighbour_distance), "neighbor_anchor_count": calibration["neighbor_anchor_count"], "neighbor_k": calibration["neighbor_k"]}}
    return report, raw


def expected_matrix_rows() -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in frontier.rows(frontier.EXPECTED_CONTRACT)}


def validate_matrix(manifest_path: Path, data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")); expected_sources = measured_hashes(("run-mih-aware-itq-frontier.py", "train-mih-aware-itq.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py")); expected_rows = expected_matrix_rows()
    require(set(manifest) == {"schema_version", "family", "contract_sha256", "calibration_materialization_manifest_sha256", "evaluation_materialization_manifest_sha256", "runner_source_files_sha256", "runner_source_bundle_sha256", "rows"} and manifest.get("schema_version") == 1 and manifest.get("family") == frontier.FAMILY and manifest.get("contract_sha256") == sha256_bytes(measured_bytes("mih-aware-itq-frontier.example.json")) and manifest.get("calibration_materialization_manifest_sha256") == data["manifest_sha256"] and manifest.get("runner_source_files_sha256") == expected_sources and manifest.get("runner_source_bundle_sha256") == source_bundle(expected_sources), "frontier matrix provenance differs")
    rows = manifest.get("rows"); require(isinstance(rows, list) and len(rows) == 25, "frontier matrix row count differs"); by_id = {row.get("id"): row for row in rows if isinstance(row, dict)}; require(len(by_id) == 25 and set(by_id) == set(expected_rows), "frontier matrix treatment grid differs")
    for row_id, expected in expected_rows.items():
        actual = by_id[row_id]; require(set(actual) == {"id", "seed", "treatment", "mih_work_weight", "report_sha256", "contributions_sha256", "artifact_sha256"} and actual["id"] == row_id and actual["seed"] == expected["seed"] and actual["treatment"] == row_id.rsplit("-seed", 1)[0] and actual["mih_work_weight"] == expected["mih_work_weight"] and isinstance(actual["report_sha256"], str) and len(actual["report_sha256"]) == 64 and isinstance(actual["contributions_sha256"], str) and len(actual["contributions_sha256"]) == 64 and ((expected["mih_work_weight"] is None and actual["artifact_sha256"] is None) or (expected["mih_work_weight"] is not None and isinstance(actual["artifact_sha256"], str) and len(actual["artifact_sha256"]) == 64)), f"frontier matrix row differs: {row_id}")
    return by_id


def artifact(matrix: Path, manifest_rows: dict[str, dict[str, Any]], treatment: str, seed: int, data: dict[str, Any]) -> tuple[numpy.ndarray, numpy.ndarray, str]:
    row_id = f"{treatment}-seed{seed}"; path = matrix / "artifacts" / row_id / "artifact.json"; value = json.loads(path.read_text(encoding="utf-8")); training = value.get("training"); architecture = value.get("architecture"); weights = value.get("weights"); expected = frontier.EXPECTED_CONTRACT["training"]; sources = measured_hashes(("train-mih-aware-itq.py", "train-learned-binary-adc.py", "requirements-learned-binary-adc-trainer.txt"))
    require(sha256(path) == manifest_rows[row_id]["artifact_sha256"] and value.get("schema_version") == 1 and value.get("input_materialization_manifest_sha256") == data["manifest_sha256"] and value.get("prepared_study_manifest_sha256") == data["prepared_study_manifest_sha256"] and value.get("trainer", {}).get("id") == "agent-memory-cpp:mih-aware-itq-trainer" and value.get("trainer", {}).get("source_files_sha256") == sources, f"artifact provenance differs: {row_id}")
    require(architecture == {"family": "mih_aware_itq_v1", "input_dimension": data["dimension"], "bit_count": 256, "band_count": 32, "band_width_bits": 8, "input_transform": "identity_normalized_e5_v1", "document_quantizer": "learned_threshold_hard_step_v1"}, f"artifact architecture differs: {row_id}")
    work = 0.0 if treatment == "training-path-control-zero-work" else 0.1; require(isinstance(training, dict) and training.get("seed") == seed and training.get("epochs") == expected["epochs"] and training.get("batch_size") == expected["batch_size"] and training.get("learning_rate") == expected["learning_rate"] and training.get("temperature") == expected["temperature"] and training.get("itq_iterations") == 50 and training.get("torch_threads") == 1 and training.get("queries_or_qrels_used") is False and training.get("objective") == "document_semantic_itq_quantization_radius_one_mih_work_surrogate_v1" and training.get("loss_weights") == {"semantic": expected["semantic_weight"], "quantization": expected["quantization_weight"], "orthogonality": expected["orthogonality_weight"], "balance": expected["balance_weight"], "mih_work": work}, f"artifact training contract differs: {row_id}")
    validation = training.get("validation"); require(isinstance(validation, dict) and validation.get("id") == "stable_sha256_document_split_v1" and validation.get("fraction") == .2 and isinstance(validation.get("selected_epoch"), int) and 1 <= validation["selected_epoch"] <= expected["epochs"], f"artifact checkpoint contract differs: {row_id}")
    require(isinstance(weights, dict), f"artifact weights differ: {row_id}"); projection = shared.require_artifact_weight(path.parent, weights.get("projection_weights"), [256, data["dimension"]], "row_major_out_by_in", "projection_weights"); thresholds = shared.require_artifact_weight(path.parent, weights.get("thresholds"), [256], None, "thresholds"); return projection, thresholds, sha256(path)


def contribution_identity(data: dict[str, Any], seed: int, contract_path: Path) -> dict[str, Any]:
    return {"schema_version": 1, "calibration_materialization_manifest_sha256": data["manifest_sha256"], "ordered_pseudoquery_document_ids_sha256": shared.ordered_ids_sha256(data["train_ids"]), "pseudoquery_count": len(data["train_ids"]), "seed": seed, "contract_sha256": sha256(contract_path)}


def write_contribution(path: Path, identity: dict[str, Any], document_ids: list[str], raw: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True); numpy.savez_compressed(path, **raw, pseudoquery_document_ids=numpy.asarray(document_ids, dtype=numpy.str_), identity_json=numpy.asarray(json.dumps(identity, sort_keys=True, separators=(",", ":")))); return sha256(path)


def run(args: Any) -> dict[str, Any]:
    contract = load_contract(args.contract); data = shared.load_root(args.calibration_root); require(len(data["train_ids"]) == 25000, "calibration cardinality differs"); manifest_rows = validate_matrix(args.frontier_matrix_root / "matrix-manifest.json", data); vectors = numpy.asarray(data["train"], dtype=numpy.float32); rows = []
    for seed in contract["encoding"]["seeds"]:
        full = shared.itq_weights(vectors, 256, seed, 50); variants = [("full-itq-25k", full, shared.binary_thresholds(vectors, full), None)]
        split, _ = trainer.base.stable_split(data["train_ids"], seed, .2); split_weights = shared.itq_weights(vectors[split], 256, seed, 50); variants.append(("split-itq-80-no-sgd", split_weights, -numpy.median(vectors[split] @ split_weights.T, axis=0).astype(numpy.float32), None))
        for input_name, output_name in (("training-path-control-zero-work", "split-init-zero-work"), ("mih-aware-work-0.10", "split-init-work-0.10")):
            weights, thresholds, digest = artifact(args.frontier_matrix_root, manifest_rows, input_name, seed, data); variants.append((output_name, weights, thresholds, digest))
        for name, weights, thresholds, digest in variants:
            metrics, raw = geometry(numpy.asarray(vectors @ weights.T + thresholds >= 0, dtype=bool), vectors, seed, contract); identity = contribution_identity(data, seed, args.contract); contribution = args.contributions_root / f"{name}-seed{seed}.npz"; rows.append({"id": contribution.stem, "treatment": name, "seed": seed, "artifact_sha256": digest, "contribution_file": contribution.name, "contribution_sha256": write_contribution(contribution, identity, data["train_ids"], raw), "contribution_identity": identity, "geometry": metrics})
    sources = source_files(); report = {"schema_version": 2, "family": FAMILY, "contract_sha256": sha256(args.contract), "calibration_materialization_manifest_sha256": data["manifest_sha256"], "calibration_train_ids_sha256": shared.ordered_ids_sha256(data["train_ids"]), "frontier_measured_source_commit": FRONTIER_MEASURED_COMMIT, "frontier_matrix_manifest_sha256": sha256(args.frontier_matrix_root / "matrix-manifest.json"), "source_files_sha256": sources, "source_bundle_sha256": source_bundle(sources), "rows": rows}; args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"); return report


def self_test(contract: Path) -> int:
    try:
        require(load_contract(contract) == CONTRACT, "contract differs"); generator = numpy.random.default_rng(52); small = generator.integers(0, 2, size=(16, 8), dtype=numpy.uint8).astype(bool); ranges = banding.band_ranges(8, 2); index = banding.build_index(small, ranges); actual_zero, actual_one = union_counts(index, small, ranges); expected_zero = [len(banding.candidate_union(index, code, ranges, [0, 0])[0]) for code in small]; expected_one = [len(banding.candidate_union(index, code, ranges, [1, 1])[0]) for code in small]; require(numpy.array_equal(actual_zero, expected_zero) and numpy.array_equal(actual_one, expected_one), "generation-array union differs")
        codes = generator.integers(0, 2, size=(1024, 256), dtype=numpy.uint8).astype(bool); vectors = generator.standard_normal((1024, 3), dtype=numpy.float32); local = json.loads(json.dumps(CONTRACT)); local["calibration"]["neighbor_anchor_count"] = 32; local["calibration"]["random_pair_count"] = 64; metrics, raw = geometry(codes, vectors, 52, local); require(len(metrics["bands"]) == 32 and raw["radius1_candidate_count"].shape == (1024,) and raw["e5_neighbor_hamming"].shape == (32, 10), "geometry result differs")
    except (OSError, ValueError, subprocess.CalledProcessError, shared.EvaluationError) as error: print(f"diagnose-mih-aware-itq-geometry self-test failed: {error}", file=sys.stderr); return 1
    print("MIH-aware ITQ geometry diagnosis self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, required=True); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--calibration-root", type=Path); parser.add_argument("--frontier-matrix-root", type=Path); parser.add_argument("--contributions-root", type=Path); parser.add_argument("--output", type=Path); args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test(args.contract)
        require(args.calibration_root and args.frontier_matrix_root and args.contributions_root and args.output, "diagnostic paths are required"); print(json.dumps({"rows": len(run(args)["rows"])}))
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError, shared.EvaluationError) as error: print(f"diagnose-mih-aware-itq-geometry: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
