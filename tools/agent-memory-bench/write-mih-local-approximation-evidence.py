#!/usr/bin/env python3
"""Write a fail-closed, portable evidence bundle for a MIH approximation study."""

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

sys.dont_write_bytecode = True

import numpy


BOOTSTRAP_REPLICATES = 10000
BOOTSTRAP_SEED = 20260811
CONTRIBUTION_KEYS = {
    "hamming_top_k_recall", "coverage_at_candidate_limit", "reranked_ndcg_at_10",
    "full_e5_ndcg_at_10", "candidate_count", "exact_bucket_floor_candidate_count",
    "bucket_probe_count", "posting_visit_count", "e5_oracle_raw_union_coverage",
    "e5_oracle_hamming_top_k_coverage", "e5_oracle_second_stage_coverage",
    "e5_oracle_mean_full_hamming_distance", "query_ids", "identity_json",
}
BOOTSTRAP_METRICS = (
    "e5_oracle_hamming_top_k_coverage", "e5_oracle_second_stage_coverage",
    "coverage_at_candidate_limit", "reranked_ndcg_at_10",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def load_module(filename: str, name: str) -> Any:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = load_module("evaluate-projection-quantization.py", "mih_local_evidence_shared")


def study_spec(kind: str) -> tuple[str, str, dict[str, tuple[str, str]]]:
    if kind == "weighted-hamming":
        return (
            "run-mih-calibrated-weighted-hamming-matrix.py",
            "mih_local_weighted_hamming_evidence_v1",
            {
                f"mih256-weighted-hamming-vs-uniform-seed{seed}": (
                    f"mih256-confidence-target12288-h768-adc256-uniform-seed{seed}.npz",
                    f"mih256-confidence-target12288-h768-adc256-calibrated-centroid-separation-seed{seed}.npz",
                ) for seed in (42, 43, 44, 45, 46)
            },
        )
    if kind == "adc-guided-probing":
        return (
            "run-mih-adc-guided-probing-matrix.py",
            "mih_local_adc_guided_probing_evidence_v1",
            {
                f"mih256-adc-guided-vs-confidence-target{target}-seed{seed}": (
                    f"mih256-budgeted-confidence-target{target}-h768-adc256-seed{seed}.npz",
                    f"mih256-budgeted-adc-target{target}-h768-adc256-seed{seed}.npz",
                ) for target in (8192, 12288, 16384) for seed in (42, 43, 44, 45, 46)
            },
        )
    if kind == "calibration-balanced-bands":
        return (
            "run-mih-calibration-balanced-bands-matrix.py",
            "mih_local_calibration_balanced_bands_evidence_v1",
            {
                **{f"mih256-balanced-vs-identity-seed{seed}": (
                    f"mih256-contiguous-target12288-h768-adc256-seed{seed}.npz",
                    f"mih256-calibration-correlation-balanced-target12288-h768-adc256-seed{seed}.npz",
                ) for seed in (52, 53, 54, 55, 56)},
                **{f"mih256-balanced-vs-random-seed{seed}": (
                    f"mih256-fixed-random-target12288-h768-adc256-seed{seed}.npz",
                    f"mih256-calibration-correlation-balanced-target12288-h768-adc256-seed{seed}.npz",
                ) for seed in (52, 53, 54, 55, 56)},
            },
        )
    if kind == "adc-best-first":
        return (
            "run-mih-adc-best-first-matrix.py",
            "mih_adc_best_first_evidence_v1",
            {
                f"mih256-adc-best-first-{bits}-vs-confidence-target{target}-seed{seed}": (
                    f"mih256-budgeted-confidence-target{target}-p{postings}-h768-adc256-seed{seed}.npz",
                    f"mih256-adc-best-first-{bits}-target{target}-p{postings}-h768-adc256-seed{seed}.npz",
                ) for bits in (2, 3) for target, postings in ((8192, 11000), (12288, 19000), (16384, 30000)) for seed in (42, 43, 44, 45, 46)
            },
        )
    raise ValueError("evidence kind is invalid")


def expected_rows(kind: str, matrix: Path) -> tuple[dict[str, dict[str, Any]], Any, str]:
    runner_name, _, _ = study_spec(kind)
    runner = load_module(runner_name, f"mih_local_{kind}_runner")
    rows = dict(runner.rows(runner.load_matrix(matrix)))
    require(len(rows) == {"weighted-hamming": 10, "adc-guided-probing": 30, "calibration-balanced-bands": 15, "adc-best-first": 45}[kind], "matrix row count differs")
    return rows, runner, runner_name


def json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"JSON root is invalid: {path}")
    return value


def load_contribution(path: Path, report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    require(path.is_file() and report.get("per_query_contributions_sha256") == sha256_file(path), f"contribution digest differs: {path.name}")
    with numpy.load(path, allow_pickle=False) as values:
        require(set(values.files) == CONTRIBUTION_KEYS, f"contribution fields are invalid: {path.name}")
        result = {name: values[name].copy() for name in values.files}
    count = result["query_ids"].shape[0]
    require(count == 1252 and all(result[name].shape == (count,) for name in CONTRIBUTION_KEYS - {"query_ids", "identity_json"}), f"contribution shapes are invalid: {path.name}")
    identity = json.loads(str(result["identity_json"].item()))
    shared.validate_contribution_identity(identity, result["query_ids"], count)
    require(report.get("per_query_contribution_identity") == identity, f"contribution identity differs: {path.name}")
    return result, identity


def validate_summary(report: dict[str, Any], values: dict[str, Any]) -> None:
    fields = {
        "hamming_top_k_recall": "hamming_top_k_recall", "exact_top_k_candidate_coverage": "coverage_at_candidate_limit",
        "reranked_ndcg_at_10": "reranked_ndcg_at_10", "full_e5_ndcg_at_10": "full_e5_ndcg_at_10",
        "mean_candidates_per_query": "candidate_count", "mean_exact_bucket_floor_candidates_per_query": "exact_bucket_floor_candidate_count",
        "mean_bucket_probes_per_query": "bucket_probe_count", "mean_posting_visits_per_query": "posting_visit_count",
    }
    for report_field, value_field in fields.items():
        require(report.get(report_field) == float(numpy.mean(values[value_field])), f"report summary differs: {report_field}")
    funnel = report.get("e5_oracle_survival")
    require(isinstance(funnel, dict) and set(funnel) == {"raw_union", "hamming_top_k", "second_stage", "mean_full_hamming_distance"}, "oracle funnel is invalid")
    for report_field, value_field in (("raw_union", "e5_oracle_raw_union_coverage"), ("hamming_top_k", "e5_oracle_hamming_top_k_coverage"), ("second_stage", "e5_oracle_second_stage_coverage"), ("mean_full_hamming_distance", "e5_oracle_mean_full_hamming_distance")):
        require(funnel[report_field] == float(numpy.mean(values[value_field])), f"oracle funnel differs: {report_field}")


def validate_row(kind: str, name: str, row: dict[str, Any], report: dict[str, Any]) -> None:
    require(report.get("schema_version") == 6 and report.get("family") == "mih_banding_reference_v6", f"row identity is invalid: {name}")
    common = {
        "code_bits": 256, "band_count": 32, "band_width_bits": [8] * 32, "probe_radius": 1,
        "global_radius": None, "band_probe_radii": [1] * 32, "hamming_limit": 768,
        "second_limit": 256, "second_stage": "binary-adc", "candidate_limit": 512, "oracle_k": 10,
        "itq_iterations": 50, "query_count": 1252, "fixed_radius": None, "fixed_radius_exact_guarantee": False,
        "seed": row["seed"],
    }
    if kind == "weighted-hamming":
        common.update({"probe_policy": "budgeted-confidence", "soft_candidate_target": 12288, "hamming_policy": row["hamming_policy"]})
    elif kind == "adc-guided-probing":
        common.update({"probe_policy": row["probe_policy"], "soft_candidate_target": row["soft_candidate_target"], "hamming_policy": "uniform"})
    elif kind == "calibration-balanced-bands":
        layout = row["band_layout"]
        common.update({"probe_policy": "budgeted-confidence", "soft_candidate_target": 12288, "hamming_policy": "uniform", "band_layout": layout, "band_layout_seed": 20260812 if layout == "fixed-random" else None})
        if layout == "calibration-correlation-balanced":
            common.update({"band_layout_objective": "abs-correlation-plus-entropy-balance-v1", "band_layout_entropy_balance_weight": 0.05})
    else:
        variant = row["variant"]
        policy = "budgeted-confidence" if variant == "budgeted-confidence" else "budgeted-adc-best-first"
        flips = None if variant == "budgeted-confidence" else int(variant[-1])
        common.update({"probe_policy": policy, "soft_candidate_target": row["candidate"], "soft_posting_visit_target": row["postings"] if flips else None, "max_probe_bit_flips": flips, "hamming_policy": "uniform"})
    require(all(report.get(field) == value for field, value in common.items()), f"row contract is invalid: {name}")
    files = report.get("evaluator_source_files_sha256")
    require(isinstance(files, dict) and set(files) == {"evaluate-mih-banding.py", "evaluate-projection-quantization.py"} and all(is_sha256(value) for value in files.values()) and report.get("evaluator_source_bundle_sha256") == digest(files), f"evaluator provenance is invalid: {name}")
    runtime = report.get("evaluator_runtime")
    require(isinstance(runtime, dict) and set(runtime) == {"python_implementation", "python_version", "numpy_version"} and all(isinstance(value, str) and value for value in runtime.values()), f"runtime provenance is invalid: {name}")
    require(all(is_sha256(report.get(field)) for field in ("calibration_materialization_manifest_sha256", "evaluation_materialization_manifest_sha256", "calibration_train_ids_sha256")) and report.get("calibration_vector_count") == 25000, f"calibration provenance is invalid: {name}")


def validate_rows(kind: str, root: Path, matrix: Path) -> tuple[list[dict[str, Any]], dict[str, Path], dict[str, Any], dict[str, str], str, str]:
    expected, _, runner_name = expected_rows(kind, matrix)
    reports = root / "reports"; contributions = root / "contributions"
    require({path.stem for path in reports.glob("*.json")} == set(expected) and {path.stem for path in contributions.glob("*.npz")} == set(expected), "evidence row grid is incomplete")
    rows: list[dict[str, Any]] = []; paths: dict[str, Path] = {}; identity: dict[str, Any] | None = None; contract: tuple[Any, ...] | None = None
    for name, row in sorted(expected.items()):
        report_path = reports / f"{name}.json"; report = json_object(report_path); validate_row(kind, name, row, report)
        contribution_path = contributions / f"{name}.npz"; values, row_identity = load_contribution(contribution_path, report); validate_summary(report, values)
        row_contract = (report["evaluator_source_files_sha256"], report["evaluator_source_bundle_sha256"], report["evaluator_runtime"], report["calibration_materialization_manifest_sha256"], report["evaluation_materialization_manifest_sha256"], report["calibration_train_ids_sha256"])
        if identity is None: identity, contract = row_identity, row_contract
        else: require(identity == row_identity and contract == row_contract, f"rows mix provenance: {name}")
        rows.append({"id": name, "report_file": report_path.name, "report_sha256": sha256_file(report_path), "contributions_file": contribution_path.name, "contributions_sha256": sha256_file(contribution_path), **row})
        paths[contribution_path.name] = contribution_path
    require(identity is not None and contract is not None, "evidence row grid is empty")
    return rows, paths, identity, contract[0], contract[1], runner_name


def validate_bootstraps(kind: str, root: Path, paths: dict[str, Path], identity: dict[str, Any]) -> list[dict[str, Any]]:
    _, _, expected = study_spec(kind)
    actual = {path.stem: path for path in root.glob("*.json")}
    require(set(actual) == set(expected), "bootstrap grid is incomplete")
    output: list[dict[str, Any]] = []
    common_sources: tuple[Any, ...] | None = None
    for name, (left_name, right_name) in sorted(expected.items()):
        report = json_object(actual[name]); left = paths.get(left_name); right = paths.get(right_name)
        require(left is not None and right is not None, f"bootstrap endpoints are unknown: {name}")
        require(report.get("schema_version") == 1 and report.get("family") == "mih_budgeted_confidence_paired_bootstrap_v1" and report.get("id") == name and report.get("left_contributions_file") == left.name and report.get("right_contributions_file") == right.name and report.get("left_sha256") == sha256_file(left) and report.get("right_sha256") == sha256_file(right) and report.get("identity") == identity and report.get("query_count") == 1252 and report.get("replicates") == BOOTSTRAP_REPLICATES and report.get("seed") == BOOTSTRAP_SEED, f"bootstrap contract is invalid: {name}")
        with numpy.load(left, allow_pickle=False) as values: left_values = {field: values[field].copy() for field in values.files}
        with numpy.load(right, allow_pickle=False) as values: right_values = {field: values[field].copy() for field in values.files}
        require(report.get("metrics") == shared.paired_bootstrap_metrics(left_values, right_values, BOOTSTRAP_METRICS, BOOTSTRAP_REPLICATES, BOOTSTRAP_SEED), f"bootstrap replay differs: {name}")
        sources = (report.get("bootstrap_source_files_sha256"), report.get("bootstrap_source_bundle_sha256"), report.get("bootstrap_runtime"))
        require(isinstance(sources[0], dict) and set(sources[0]) == {"bootstrap-mih-budgeted-confidence.py", "evaluate-projection-quantization.py"} and all(is_sha256(value) for value in sources[0].values()) and sources[1] == digest(sources[0]) and isinstance(sources[2], dict), f"bootstrap provenance is invalid: {name}")
        if common_sources is None: common_sources = sources
        else: require(common_sources == sources, f"bootstraps mix provenance: {name}")
        output.append({"id": name, "file": actual[name].name, "sha256": sha256_file(actual[name]), "left": left.name, "right": right.name, "replicates": BOOTSTRAP_REPLICATES, "seed": BOOTSTRAP_SEED, "metrics": report["metrics"]})
    return output


def make_bundle(kind: str, root: Path, matrix: Path, bootstrap: Path, output: Path) -> dict[str, Any]:
    rows, contribution_paths, identity, evaluator_sources, evaluator_bundle, runner_name = validate_rows(kind, root, matrix)
    comparisons = validate_bootstraps(kind, bootstrap, contribution_paths, identity)
    bootstrap_sources = json_object(bootstrap / comparisons[0]["file"])["bootstrap_source_files_sha256"]
    source_names = ["evaluate-mih-banding.py", "evaluate-projection-quantization.py", "bootstrap-mih-budgeted-confidence.py", runner_name, Path(__file__).name]
    source_paths = [(Path(__file__).with_name(name), f"bundle/sources/{name}") for name in source_names]
    for path, name in source_paths:
        require(path.is_file(), f"source snapshot is absent: {name}")
        expected = evaluator_sources.get(path.name, bootstrap_sources.get(path.name))
        if expected is not None: require(sha256_file(path) == expected, f"source snapshot differs: {path.name}")
    files: list[tuple[Path, str]] = [(matrix, "bundle/matrix.json")]
    files += [(root / "reports" / row["report_file"], f"bundle/reports/{row['report_file']}") for row in rows]
    files += [(root / "contributions" / row["contributions_file"], f"bundle/contributions/{row['contributions_file']}") for row in rows]
    files += [(bootstrap / item["file"], f"bundle/bootstrap/{item['file']}") for item in comparisons]
    compact = {"schema_version": 1, "family": study_spec(kind)[1], "matrix_sha256": sha256_file(matrix), "evaluation_identity": identity, "evaluator_source_files_sha256": evaluator_sources, "evaluator_source_bundle_sha256": evaluator_bundle, "bootstrap_source_files_sha256": bootstrap_sources, "rows": rows, "comparisons": comparisons}
    compact_path = root / "compact-manifest.json"; compact_path.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    files.insert(0, (compact_path, "bundle/compact-manifest.json")); files += source_paths
    names = [name for _, name in files]; require(len(names) == len(set(names)) and all("\\" not in name for name in names), "archive names are invalid")
    entries = [{"path": name, "sha256": sha256_file(path), "size": path.stat().st_size} for path, name in files]
    bundle = {"schema_version": 1, "family": f"{study_spec(kind)[1]}_bundle", "bundle_root_sha256": digest(entries), "entries": entries}
    bundle_path = root / "evidence-bundle-manifest.json"; bundle_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    files.append((bundle_path, "bundle/evidence-bundle-manifest.json")); names.append("bundle/evidence-bundle-manifest.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, name in files: archive.write(path, name)
    with zipfile.ZipFile(output) as archive:
        require(archive.namelist() == names, "archive member names differ")
        for entry in entries:
            data = archive.read(entry["path"]); require(len(data) == entry["size"] and hashlib.sha256(data).hexdigest() == entry["sha256"], f"archive member differs: {entry['path']}")
        require(json.loads(archive.read("bundle/evidence-bundle-manifest.json")) == bundle, "archive bundle manifest differs")
    return {"archive": str(output), "sha256": sha256_file(output), "bundle_root_sha256": bundle["bundle_root_sha256"], "rows": len(rows), "comparisons": len(comparisons)}


def self_test() -> int:
    try:
        require(len(study_spec("weighted-hamming")[2]) == 5 and len(study_spec("adc-guided-probing")[2]) == 15 and len(study_spec("calibration-balanced-bands")[2]) == 10 and len(study_spec("adc-best-first")[2]) == 30, "comparison grids are invalid")
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "portable.zip"
            with zipfile.ZipFile(archive, "w") as written: written.writestr("bundle/item.json", "{}")
            with zipfile.ZipFile(archive) as read: require(read.namelist() == ["bundle/item.json"] and "\\" not in read.namelist()[0], "portable archive self-test failed")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"write-mih-local-approximation-evidence self-test failed: {error}", file=sys.stderr); return 1
    print("MIH local-approximation evidence packager self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("weighted-hamming", "adc-guided-probing", "calibration-balanced-bands", "adc-best-first"))
    parser.add_argument("--input-root", type=Path); parser.add_argument("--matrix", type=Path); parser.add_argument("--bootstrap-root", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test()
        require(args.kind is not None and args.input_root is not None and args.matrix is not None and args.bootstrap_root is not None and args.output is not None, "packaging paths are required")
        print(json.dumps(make_bundle(args.kind, args.input_root, args.matrix, args.bootstrap_root, args.output), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"write-mih-local-approximation-evidence: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
