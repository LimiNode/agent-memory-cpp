#!/usr/bin/env python3
"""Calibration-only geometry decomposition for the first MIH-aware ITQ path."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()
FAMILY = "mih_aware_itq_geometry_diagnosis_v1"
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


def require(condition: bool, message: str) -> None:
    if not condition: raise ValueError(message)


def sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files() -> dict[str, str]:
    names = (THIS.name, "evaluate-projection-quantization.py", "evaluate-mih-banding.py", "train-mih-aware-itq.py", "train-learned-binary-adc.py")
    return {name: sha256(THIS.with_name(name)) for name in names}


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8")); require(value == CONTRACT, "geometry contract differs from the predeclared protocol"); return value


def summary(values: Any) -> dict[str, float | int]:
    array = numpy.asarray(values); require(array.size > 0, "empty geometry distribution")
    return {"mean": float(numpy.mean(array)), "p95": float(numpy.quantile(array, .95)), "maximum": int(numpy.max(array))}


def bit_entropy(probability: Any) -> Any:
    value = numpy.asarray(probability, dtype=numpy.float64)
    return -(numpy.where(value > 0, value * numpy.log2(value), 0) + numpy.where(value < 1, (1 - value) * numpy.log2(1 - value), 0))


def geometry(codes: numpy.ndarray, vectors: numpy.ndarray, seed: int, contract: dict[str, Any]) -> dict[str, Any]:
    ranges = banding.band_ranges(256, 32); index = banding.build_index(codes, ranges); count = len(codes)
    occupancy = codes.mean(axis=0); bands = []
    for number, ((start, stop), buckets) in enumerate(zip(ranges, index)):
        sizes = numpy.asarray([len(posting) for posting in buckets.values()]); probability = sizes / count
        radius_one = numpy.asarray([sum(len(buckets.get(key, ())) for key in banding.probe_keys(banding.band_key(code, start, stop), 8, 1)) for code in codes])
        values = codes[:, start:stop].astype(numpy.float64); centered = values - values.mean(axis=0); scale = numpy.sqrt((centered * centered).sum(axis=0)); corr = (centered.T @ centered) / numpy.outer(scale, scale)
        bands.append({"band": number, "bucket_entropy_bits": float(-(numpy.where(probability > 0, probability * numpy.log2(probability), 0)).sum()), "occupied_bucket_count": len(buckets), "posting_size": summary(sizes), "exact_match_probability": float((probability * probability).sum()), "radius_one_match_probability": float(radius_one.mean() / count), "radius_one_posting_visits": summary(radius_one), "mean_absolute_intraband_correlation": float(numpy.abs(corr[numpy.triu_indices(8, 1)]).mean())})
    work = {}
    for radius in (0, 1):
        union, visits = [], []
        for code in codes:
            selected, _ = banding.candidate_union(index, code, ranges, [radius] * 32); union.append(len(selected))
            visits.append(sum(len(buckets.get(key, ())) for buckets, (start, stop) in zip(index, ranges) for key in banding.probe_keys(banding.band_key(code, start, stop), 8, radius)))
        work[f"radius_{radius}"] = {"unique_candidates": summary(union), "posting_visits": summary(visits)}
    calibration = contract["calibration"]; generator = numpy.random.default_rng(seed); left = generator.integers(0, count, calibration["random_pair_count"]); right = (left + generator.integers(1, count, calibration["random_pair_count"])) % count
    random_distance = numpy.count_nonzero(codes[left] != codes[right], axis=1); anchors = numpy.sort(generator.choice(count, calibration["neighbor_anchor_count"], replace=False)); neighbor_distance = []
    for start in range(0, len(anchors), 64):
        anchor = anchors[start:start + 64]; scores = vectors[anchor] @ vectors.T; scores[numpy.arange(len(anchor)), anchor] = -numpy.inf
        nearest = numpy.argpartition(-scores, calibration["neighbor_k"], axis=1)[:, :calibration["neighbor_k"]]
        for row, document in enumerate(anchor): neighbor_distance.extend(numpy.count_nonzero(codes[document] != codes[nearest[row]], axis=1))
    return {"per_bit_probability_one": [float(value) for value in occupancy], "per_bit_entropy": [float(value) for value in bit_entropy(occupancy)], "constant_bit_count": int(((occupancy == 0) | (occupancy == 1)).sum()), "mean_bit_entropy": float(bit_entropy(occupancy).mean()), "bands": bands, "union_work": work, "hamming": {"random_document_pairs": summary(random_distance), "e5_calibration_neighbors": summary(neighbor_distance), "neighbor_anchor_count": calibration["neighbor_anchor_count"], "neighbor_k": calibration["neighbor_k"]}}


def artifact(matrix: Path, treatment: str, seed: int, data: dict[str, Any]) -> tuple[numpy.ndarray, numpy.ndarray, str]:
    path = matrix / "artifacts" / f"{treatment}-seed{seed}" / "artifact.json"; value = json.loads(path.read_text(encoding="utf-8")); require(value.get("input_materialization_manifest_sha256") == data["manifest_sha256"], "artifact calibration differs")
    weights = value.get("weights"); require(isinstance(weights, dict), "artifact weights are invalid")
    projection = shared.require_artifact_weight(path.parent, weights.get("projection_weights"), [256, data["dimension"]], "row_major_out_by_in", "projection_weights"); thresholds = shared.require_artifact_weight(path.parent, weights.get("thresholds"), [256], None, "thresholds")
    return projection, thresholds, sha256(path)


def run(args: Any) -> dict[str, Any]:
    contract = load_contract(args.contract); data = shared.load_root(args.calibration_root); require(len(data["train_ids"]) == 25000, "calibration cardinality differs")
    manifest = json.loads((args.frontier_matrix_root / "matrix-manifest.json").read_text(encoding="utf-8")); require(manifest.get("family") == "mih_aware_itq_heldout_frontier_v1", "frontier matrix differs")
    known = {row.get("id"): row.get("artifact_sha256") for row in manifest.get("rows", [])}; vectors = numpy.asarray(data["train"], dtype=numpy.float32); rows = []
    for seed in contract["encoding"]["seeds"]:
        full = shared.itq_weights(vectors, 256, seed, 50); variants = [("full-itq-25k", full, shared.binary_thresholds(vectors, full), None)]
        split, _ = trainer.base.stable_split(data["train_ids"], seed, .2); split_weights = shared.itq_weights(vectors[split], 256, seed, 50); variants.append(("split-itq-80-no-sgd", split_weights, -numpy.median(vectors[split] @ split_weights.T, axis=0).astype(numpy.float32), None))
        for input_name, output_name in (("training-path-control-zero-work", "split-init-zero-work"), ("mih-aware-work-0.10", "split-init-work-0.10")):
            weights, thresholds, digest = artifact(args.frontier_matrix_root, input_name, seed, data); require(known.get(f"{input_name}-seed{seed}") == digest, "artifact matrix hash differs"); variants.append((output_name, weights, thresholds, digest))
        for name, weights, thresholds, digest in variants:
            rows.append({"id": f"{name}-seed{seed}", "treatment": name, "seed": seed, "artifact_sha256": digest, "geometry": geometry(numpy.asarray(vectors @ weights.T + thresholds >= 0, dtype=bool), vectors, seed, contract)})
    sources = source_files(); report = {"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "calibration_materialization_manifest_sha256": data["manifest_sha256"], "calibration_train_ids_sha256": shared.ordered_ids_sha256(data["train_ids"]), "frontier_matrix_manifest_sha256": sha256(args.frontier_matrix_root / "matrix-manifest.json"), "source_files_sha256": sources, "source_bundle_sha256": hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), "rows": rows}; args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"); return report


def self_test(contract: Path) -> int:
    try:
        require(load_contract(contract) == CONTRACT, "contract differs")
        generator = numpy.random.default_rng(52); codes = generator.integers(0, 2, size=(1024, 256), dtype=numpy.uint8).astype(bool); vectors = generator.standard_normal((1024, 3), dtype=numpy.float32); local = json.loads(json.dumps(CONTRACT)); local["calibration"]["neighbor_anchor_count"] = 32; local["calibration"]["random_pair_count"] = 64
        result = geometry(codes, vectors, 52, local); require(len(result["bands"]) == 32 and result["union_work"]["radius_1"]["unique_candidates"]["maximum"] > 0, "geometry result differs")
    except (ValueError, shared.EvaluationError) as error: print(f"diagnose-mih-aware-itq-geometry self-test failed: {error}", file=sys.stderr); return 1
    print("MIH-aware ITQ geometry diagnosis self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, required=True); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--calibration-root", type=Path); parser.add_argument("--frontier-matrix-root", type=Path); parser.add_argument("--output", type=Path); args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test(args.contract)
        require(args.calibration_root and args.frontier_matrix_root and args.output, "diagnostic paths are required"); print(json.dumps({"rows": len(run(args)["rows"])}))
    except (OSError, ValueError, json.JSONDecodeError, shared.EvaluationError) as error: print(f"diagnose-mih-aware-itq-geometry: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
