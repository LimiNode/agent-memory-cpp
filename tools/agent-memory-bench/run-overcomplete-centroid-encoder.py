#!/usr/bin/env python3
"""Measure explicitly overcomplete ensembles of strict ITQ centroid codes."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import numpy


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    return module


planner = load("overcomplete_centroid_encoder_planner", "plan-overcomplete-centroid-encoder.py")
base = load("overcomplete_centroid_encoder_base", "run-centroid-encoder-intrinsic.py")


def block_widths(bit_count: int) -> list[int]:
    require(bit_count in (512, 768, 1024), "overcomplete bit count differs")
    widths: list[int] = []
    remaining = bit_count
    while remaining:
        width = min(384, remaining); widths.append(width); remaining -= width
    return widths


def ensemble(encoder: str, bit_count: int, seed: int, iterations: int, centroids: numpy.ndarray, calibration_queries: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray, dict[str, Any]]:
    source = centroids if encoder == "itq_centroids_ensemble" else numpy.concatenate((centroids, calibration_queries), axis=0)
    mean, full_basis = base.pca_basis(source, 384)
    projections: list[numpy.ndarray] = []
    for index, width in enumerate(block_widths(bit_count)):
        basis = full_basis[:width]
        rotation = base.itq_rotation((source - mean) @ basis.T, iterations, seed + 384 + index * 1000003)
        projections.append((rotation.T @ basis).astype(numpy.float32))
    projection = numpy.concatenate(projections, axis=0)
    return mean, projection, {"training_source": "frozen_centroids_only_v1" if encoder == "itq_centroids_ensemble" else "frozen_centroids_plus_train_split_calibration_queries_v1", "construction": "concatenated_seeded_strict_itq_rotation_blocks_v1", "block_widths": block_widths(bit_count)}


def parent_evidence(path: Path, expected_sha256: str) -> None:
    require(path.is_file() and sha256(path) == expected_sha256, "overcomplete centroid parent evidence archive differs")
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("bundle/evidence-manifest.json"))
    require(manifest.get("family") == "centroid_encoder_intrinsic_evidence_v1" and manifest.get("row_count") == 15 and manifest.get("selected_count") == 0, "overcomplete centroid parent result does not authorize follow-up")


def complete(root: Path, config: dict[str, Any]) -> bool:
    report, artifact, audit = root / "report.json", root / "artifact.npz", root / "audit.npz"
    if not all(path.is_file() for path in (report, artifact, audit)):
        return False
    try:
        value = json.loads(report.read_text(encoding="utf-8"))
        return value.get("config") == config and value.get("artifact_sha256") == sha256(artifact) and value.get("audit_sha256") == sha256(audit)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract); parent_evidence(args.parent_intrinsic_evidence, contract["parent_intrinsic_evidence_sha256"])
    parent_contract = base.planner.load_contract(THIS / "centroid-encoder-intrinsic.example.json")
    centroids, _, float_identity = base.load_float_artifact(args.float_root, args.float_evidence)
    _, scale_identity = base.load_train(args.scale_root, parent_contract)
    queries, query_identity = base.load_calibration_queries(args.calibration_query_root, parent_contract)
    float_orders = numpy.empty((queries.shape[0], 16), dtype=numpy.int16)
    for position, query in enumerate(queries): float_orders[position] = base.stable_score_order(centroids @ query)[:16]
    reports: list[dict[str, Any]] = []
    for encoder in contract["encoders"]:
        for bit_count in contract["bit_counts"]:
            identifier = f"{encoder}-b{bit_count}"; root = args.output_root / identifier
            config = {"schema_version": 1, "family": contract["family"], "id": identifier, "encoder": encoder, "bit_count": bit_count, "seed": contract["seed"], "itq_iterations": contract["itq_iterations"], "parent_intrinsic_evidence_sha256": contract["parent_intrinsic_evidence_sha256"], **float_identity, **scale_identity, **query_identity, "float_oracle": "exact_float_inner_product_top16_centroids_v1", "binary_tie_rule": "hamming_ascending_then_centroid_id_ascending_v1"}
            if complete(root, config): reports.append(json.loads((root / "report.json").read_text(encoding="utf-8"))); continue
            mean, projection, training = ensemble(encoder, bit_count, contract["seed"], contract["itq_iterations"], centroids, queries)
            centroid_codes, query_codes = base.pack((centroids - mean) @ projection.T), base.pack((queries - mean) @ projection.T)
            root.mkdir(parents=True, exist_ok=True); artifact = root / "artifact.npz"; metadata = {"config": config, "training": training, "projection_shape": list(projection.shape), "mean_shape": list(mean.shape), "centroid_code_shape": list(centroid_codes.shape)}
            numpy.savez_compressed(artifact, metadata_json=numpy.asarray(json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))), mean=mean, projection=projection, centroid_codes=centroid_codes)
            with numpy.load(artifact, allow_pickle=False) as stored: require(json.loads(str(stored["metadata_json"].item())) == metadata and numpy.array_equal(stored["centroid_codes"], centroid_codes), f"serialized overcomplete artifact differs: {identifier}")
            orders = numpy.empty((queries.shape[0], 128), dtype=numpy.int16); times: list[float] = []
            for position, code in enumerate(query_codes):
                started = time.perf_counter(); distances = base.hamming_distances(centroid_codes, code); orders[position] = numpy.lexsort((numpy.arange(4096), distances))[:128]; times.append((time.perf_counter() - started) * 1000.0)
            audit = root / "audit.npz"; numpy.savez_compressed(audit, float_top16=float_orders, binary_top128=orders)
            metrics: dict[str, float] = {}
            for shortlist in contract["shortlist_sizes"]:
                metrics[f"float_top16_recall_at_binary_top{shortlist}"] = float(numpy.mean([numpy.isin(float_orders[position], orders[position, :shortlist]).sum() / 16.0 for position in range(queries.shape[0])], dtype=numpy.float64))
            report = {"schema_version": 1, "family": contract["family"], "config": config, "training": training, "artifact_sha256": sha256(artifact), "audit_sha256": sha256(audit), "query_count": 2162, "binary_centroid_scan_p50_ms_per_query": base.percentile(times, .50), "binary_centroid_scan_p95_ms_per_query": base.percentile(times, .95), **metrics}
            (root / "report.json").write_bytes(canonical(report)); reports.append(report)
    reports.sort(key=lambda row: row["config"]["id"]); gate = contract["selection"]
    eligible = [row for row in reports if row["float_top16_recall_at_binary_top64"] >= gate["minimum_top64_recall"] and row["float_top16_recall_at_binary_top32"] >= gate["minimum_top32_recall"]]
    eligible.sort(key=lambda row: (-row["float_top16_recall_at_binary_top64"], -row["float_top16_recall_at_binary_top32"], row["config"]["bit_count"], row["config"]["encoder"]))
    selected = [{"id": row["config"]["id"], "report_sha256": sha256(args.output_root / row["config"]["id"] / "report.json"), "artifact_sha256": row["artifact_sha256"], "top32": row["float_top16_recall_at_binary_top32"], "top64": row["float_top16_recall_at_binary_top64"]} for row in eligible[:gate["maximum_selected_configurations"]]]
    args.output_root.mkdir(parents=True, exist_ok=True); args.output_root.joinpath("summary.json").write_bytes(canonical({"schema_version": 1, "family": contract["family"], "contract_sha256": sha256(args.contract), "query_count": 2162, "rows": reports, "selected": selected, "selection_gate": gate}))


def self_test() -> None:
    require(block_widths(512) == [384, 128] and block_widths(768) == [384, 384] and block_widths(1024) == [384, 384, 256], "overcomplete block widths differ")
    print("overcomplete centroid encoder runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "overcomplete-centroid-encoder.example.json"); parser.add_argument("--parent-intrinsic-evidence", type=Path); parser.add_argument("--scale-root", type=Path); parser.add_argument("--float-root", type=Path); parser.add_argument("--float-evidence", type=Path); parser.add_argument("--calibration-query-root", type=Path); parser.add_argument("--output-root", type=Path); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test: self_test(); return 0
        if any(value is None for value in (args.parent_intrinsic_evidence, args.scale_root, args.float_root, args.float_evidence, args.calibration_query_root, args.output_root)): parser.error("all overcomplete encoder inputs are required")
        run(args); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"run-overcomplete-centroid-encoder: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
