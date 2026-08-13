#!/usr/bin/env python3
"""Replay-validate and package repaired MIH-aware ITQ calibration evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()
FAMILY = "mih_aware_itq_repaired_calibration_control_v1"
RAW_FIELDS = {
    "radius0_candidate_count", "radius0_posting_visits", "radius1_candidate_count", "radius1_posting_visits",
    "random_pair_hamming", "e5_neighbor_hamming", "random_pair_left_indices", "random_pair_right_indices",
    "neighbor_anchor_indices", "e5_neighbor_indices", "packed_codes", "pseudoquery_document_ids", "identity_json",
}


def load(name: str, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, THIS.with_name(name))
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module); return module


runner = load("run-mih-aware-itq-repaired-control.py", "repaired_control_evidence_runner")
geometry = load("diagnose-mih-aware-itq-geometry.py", "repaired_control_evidence_geometry")
archive = load("write-mih-rerank-cost-evidence.py", "repaired_control_evidence_archive")


def require(condition: bool, message: str) -> None:
    if not condition: raise ValueError(message)


def sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def summary(values: Any) -> dict[str, float | int]:
    array = numpy.asarray(values); return {"mean": float(numpy.mean(array)), "p95": float(numpy.quantile(array, .95)), "maximum": int(numpy.max(array))}


def identity(data: dict[str, Any], seed: int, contract_path: Path) -> dict[str, Any]:
    return {"schema_version": 1, "calibration_materialization_manifest_sha256": data["manifest_sha256"], "ordered_pseudoquery_document_ids_sha256": runner.shared.ordered_ids_sha256(data["train_ids"]), "pseudoquery_count": len(data["train_ids"]), "seed": seed, "contract_sha256": sha256(contract_path)}


def unpack_raw(path: Path, data: dict[str, Any], seed: int, contract_path: Path) -> tuple[dict[str, Any], numpy.ndarray]:
    with numpy.load(path, allow_pickle=False) as values:
        require(set(values.files) == RAW_FIELDS, f"raw contribution fields differ: {path.name}")
        raw = {name: values[name].copy() for name in values.files}
    count = len(data["train_ids"]); calibration = runner.CONTRACT["calibration"]
    require(raw["packed_codes"].shape == (count, 32) and raw["packed_codes"].dtype == numpy.uint8, f"packed codes differ: {path.name}")
    codes = numpy.unpackbits(raw.pop("packed_codes"), axis=1, bitorder="little").astype(bool); require(codes.shape == (count, 256), f"unpacked codes differ: {path.name}")
    one_dimensional = ("radius0_candidate_count", "radius0_posting_visits", "radius1_candidate_count", "radius1_posting_visits")
    require(all(raw[name].shape == (count,) for name in one_dimensional) and raw["random_pair_hamming"].shape == (calibration["random_pair_count"],) and raw["e5_neighbor_hamming"].shape == (calibration["neighbor_anchor_count"], calibration["neighbor_k"]) and raw["random_pair_left_indices"].shape == (calibration["random_pair_count"],) and raw["random_pair_right_indices"].shape == (calibration["random_pair_count"],) and raw["neighbor_anchor_indices"].shape == (calibration["neighbor_anchor_count"],) and raw["e5_neighbor_indices"].shape == (calibration["neighbor_anchor_count"], calibration["neighbor_k"]) and raw["pseudoquery_document_ids"].shape == (count,), f"raw contribution shapes differ: {path.name}")
    require(raw["pseudoquery_document_ids"].tolist() == data["train_ids"] and json.loads(str(raw.pop("identity_json").item())) == identity(data, seed, contract_path), f"raw identity differs: {path.name}")
    for name in ("random_pair_left_indices", "random_pair_right_indices", "neighbor_anchor_indices", "e5_neighbor_indices"):
        require(numpy.all(raw[name] >= 0) and numpy.all(raw[name] < count), f"raw indices differ: {path.name}")
    expected_random = numpy.count_nonzero(codes[raw["random_pair_left_indices"]] != codes[raw["random_pair_right_indices"]], axis=1).astype(numpy.int16)
    expected_neighbours = numpy.count_nonzero(codes[raw["neighbor_anchor_indices"], None, :] != codes[raw["e5_neighbor_indices"]], axis=2).astype(numpy.int16)
    require(numpy.array_equal(raw["random_pair_hamming"], expected_random) and numpy.array_equal(raw["e5_neighbor_hamming"], expected_neighbours), f"raw Hamming values differ: {path.name}")
    return raw, codes


def geometry_from_raw(raw: dict[str, Any], codes: numpy.ndarray) -> dict[str, Any]:
    ranges = geometry.banding.band_ranges(256, 32); index = geometry.banding.build_index(codes, ranges); count = len(codes); occupancy = codes.mean(axis=0); bands = []
    exact_visits = numpy.zeros(count, dtype=numpy.int32); radius_one_visits = numpy.zeros(count, dtype=numpy.int32)
    for number, ((start, stop), buckets) in enumerate(zip(ranges, index)):
        sizes = numpy.asarray([len(posting) for posting in buckets.values()], dtype=numpy.int32); probability = sizes / count
        exact = numpy.asarray([len(buckets.get(geometry.banding.band_key(code, start, stop), ())) for code in codes], dtype=numpy.int32)
        radius_one = numpy.asarray([sum(len(buckets.get(key, ())) for key in geometry.banding.probe_keys(geometry.banding.band_key(code, start, stop), 8, 1)) for code in codes], dtype=numpy.int32)
        exact_visits += exact; radius_one_visits += radius_one
        values = codes[:, start:stop].astype(numpy.float64); centered = values - values.mean(axis=0); scale = numpy.sqrt((centered * centered).sum(axis=0)); corr = (centered.T @ centered) / numpy.outer(scale, scale)
        bands.append({"band": number, "bucket_entropy_bits": float(-(numpy.where(probability > 0, probability * numpy.log2(probability), 0)).sum()), "occupied_bucket_count": len(buckets), "posting_size": summary(sizes), "exact_match_probability": float((probability * probability).sum()), "radius_one_match_probability": float(radius_one.mean() / count), "radius_one_posting_visits": summary(radius_one), "mean_absolute_intraband_correlation": float(numpy.abs(corr[numpy.triu_indices(8, 1)]).mean())})
    zero, one = geometry.union_counts(index, codes, ranges)
    require(numpy.array_equal(raw["radius0_candidate_count"], zero) and numpy.array_equal(raw["radius0_posting_visits"], exact_visits) and numpy.array_equal(raw["radius1_candidate_count"], one) and numpy.array_equal(raw["radius1_posting_visits"], radius_one_visits), "raw MIH work values differ from packed codes")
    entropy = geometry.bit_entropy(occupancy)
    return {"per_bit_probability_one": [float(value) for value in occupancy], "per_bit_entropy": [float(value) for value in entropy], "constant_bit_count": int(((occupancy == 0) | (occupancy == 1)).sum()), "mean_bit_entropy": float(entropy.mean()), "bands": bands, "union_work": {"radius_0": {"unique_candidates": summary(zero), "posting_visits": summary(exact_visits)}, "radius_1": {"unique_candidates": summary(one), "posting_visits": summary(radius_one_visits)}}, "hamming": {"random_document_pairs": summary(raw["random_pair_hamming"]), "e5_calibration_neighbors": summary(raw["e5_neighbor_hamming"]), "neighbor_anchor_count": 1024, "neighbor_k": 10}}


def expected_codes(treatment: str, seed: int, data: dict[str, Any], artifacts_root: Path, contract: dict[str, Any]) -> tuple[numpy.ndarray, str | None]:
    vectors = numpy.asarray(data["train"], dtype=numpy.float32)
    if treatment == "full-itq-25k":
        weights = runner.shared.itq_weights(vectors, 256, seed, 50); thresholds = runner.shared.binary_thresholds(vectors, weights); return numpy.asarray(vectors @ weights.T + thresholds >= 0.0, dtype=bool), None
    root = artifacts_root / f"repaired-control-seed{seed}"; weights, thresholds, artifact_sha = runner.artifact_weights(root / "artifact.json", data, seed, contract)
    return numpy.asarray(vectors @ weights.T + thresholds >= 0.0, dtype=bool), artifact_sha


def make_bundle(args: Any) -> dict[str, Any]:
    contract = runner.load_contract(args.contract); data = runner.shared.load_root(args.calibration_root); report = json.loads(args.report.read_text(encoding="utf-8")); sources = runner.sources()
    expected_keys = {"schema_version", "family", "contract_sha256", "calibration_materialization_manifest_sha256", "calibration_train_ids_sha256", "source_files_sha256", "source_bundle_sha256", "rows", "gate"}
    require(set(report) == expected_keys and report["schema_version"] == 1 and report["family"] == FAMILY and report["contract_sha256"] == sha256(args.contract) and report["calibration_materialization_manifest_sha256"] == data["manifest_sha256"] and report["calibration_train_ids_sha256"] == runner.shared.ordered_ids_sha256(data["train_ids"]) and report["source_files_sha256"] == sources and report["source_bundle_sha256"] == hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), "repaired-control report provenance differs")
    expected_ids = {f"{treatment}-seed{seed}" for treatment in ("full-itq-25k", "repaired-control") for seed in contract["encoding"]["seeds"]}; rows = report["rows"]; require(isinstance(rows, list) and {row.get("id") for row in rows if isinstance(row, dict)} == expected_ids and len(rows) == 10, "repaired-control report grid differs")
    by_id = {row["id"]: row for row in rows}; files: list[tuple[Path, str]] = [(args.contract, "bundle/contract.json"), (args.report, "bundle/reports/repaired-control-report.json")]
    for row_id in sorted(expected_ids):
        row = by_id[row_id]; treatment, seed_text = row_id.rsplit("-seed", 1); seed = int(seed_text); path = args.contributions_root / str(row.get("contribution_file")); require(path.name == f"{row_id}.npz" and sha256(path) == row.get("contribution_sha256"), f"contribution digest differs: {row_id}")
        raw, codes = unpack_raw(path, data, seed, args.contract); expected, artifact_sha = expected_codes(treatment, seed, data, args.artifacts_root, contract)
        require(numpy.array_equal(codes, expected) and row.get("artifact_sha256") == artifact_sha and row.get("geometry") == geometry_from_raw(raw, codes), f"repaired-control row differs: {row_id}")
        files.append((path, f"bundle/contributions/{path.name}"))
        if treatment == "repaired-control":
            root = args.artifacts_root / f"repaired-control-seed{seed}"
            files += [(root / name, f"bundle/artifacts/{root.name}/{name}") for name in ("artifact.json", "projection-weights.f32", "thresholds.f32")]
    rows_by_treatment = {name: [by_id[f"{name}-seed{seed}"] for seed in contract["encoding"]["seeds"]] for name in ("full-itq-25k", "repaired-control")}
    mean = lambda treatment, field: float(numpy.mean([row["geometry"]["union_work"]["radius_1"][field]["mean"] for row in rows_by_treatment[treatment]]))
    entropy = float(numpy.mean([row["geometry"]["mean_bit_entropy"] for row in rows_by_treatment["repaired-control"]])); neighbour = lambda treatment: float(numpy.mean([row["geometry"]["hamming"]["e5_calibration_neighbors"]["mean"] for row in rows_by_treatment[treatment]]))
    decision = {"mean_bit_entropy": entropy, "candidate_work_ratio": mean("repaired-control", "unique_candidates") / mean("full-itq-25k", "unique_candidates"), "posting_work_ratio": mean("repaired-control", "posting_visits") / mean("full-itq-25k", "posting_visits"), "e5_neighbor_hamming_delta": neighbour("repaired-control") - neighbour("full-itq-25k")}
    gate = contract["gate"]; decision["passed"] = decision["mean_bit_entropy"] >= gate["minimum_mean_bit_entropy"] and decision["candidate_work_ratio"] <= gate["maximum_radius_one_candidate_work_ratio"] and decision["posting_work_ratio"] <= gate["maximum_radius_one_posting_work_ratio"] and decision["e5_neighbor_hamming_delta"] < 0.0
    require(report["gate"] == decision, "reported repaired-control gate differs")
    compact = {"schema_version": 1, "family": "mih_aware_itq_repaired_calibration_evidence_v1", "contract_sha256": sha256(args.contract), "report_sha256": sha256(args.report), "gate": decision, "rows": [{"id": row["id"], "contribution_file": row["contribution_file"], "contribution_sha256": row["contribution_sha256"], "artifact_sha256": row["artifact_sha256"]} for row in rows]}
    with tempfile.TemporaryDirectory() as directory:
        compact_path = Path(directory) / "compact-manifest.json"; compact_path.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"); files.append((compact_path, "bundle/compact-manifest.json"))
        names = ("run-mih-aware-itq-repaired-control.py", "train-mih-aware-itq-repaired.py", "diagnose-mih-aware-itq-geometry.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py", "train-learned-binary-adc.py", "requirements-learned-binary-adc-trainer.txt", THIS.name, "write-mih-rerank-cost-evidence.py")
        files += [(THIS.with_name(name), f"bundle/sources/{name}") for name in names]; manifest = archive.archive_manifest(files); manifest["family"] = "mih_aware_itq_repaired_calibration_evidence_v1"; args.output.parent.mkdir(parents=True, exist_ok=True); archive.write_archive(args.output, files, manifest)
    return {"archive": str(args.output), "sha256": sha256(args.output), "bundle_root_sha256": manifest["bundle_root_sha256"], "gate": decision}


def self_test() -> int:
    try:
        require(runner.load_contract(THIS.with_name("mih-aware-itq-repaired-control.example.json")) == runner.CONTRACT, "contract differs")
        if archive.self_test() != 0: return 1
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "broken.npz"; numpy.savez_compressed(broken, identity_json=numpy.asarray("{}"))
            try: unpack_raw(broken, {"train_ids": []}, 52, THIS)
            except ValueError: pass
            else: raise ValueError("incomplete raw contribution was accepted")
    except (OSError, ValueError, json.JSONDecodeError, runner.shared.EvaluationError) as error: print(f"write-mih-aware-itq-repaired-control-evidence self-test failed: {error}", file=sys.stderr); return 1
    print("MIH-aware ITQ repaired-control evidence packager self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--report", type=Path); parser.add_argument("--contract", type=Path); parser.add_argument("--calibration-root", type=Path); parser.add_argument("--artifacts-root", type=Path); parser.add_argument("--contributions-root", type=Path); parser.add_argument("--output", type=Path); args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test()
        require(all((args.report, args.contract, args.calibration_root, args.artifacts_root, args.contributions_root, args.output)), "evidence paths are required"); print(json.dumps(make_bundle(args), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError, runner.shared.EvaluationError, zipfile.BadZipFile) as error: print(f"write-mih-aware-itq-repaired-control-evidence: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
