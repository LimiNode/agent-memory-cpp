#!/usr/bin/env python3
"""Fail-closed evidence validator for the MIH budgeted-confidence K1 sweep."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
import zipfile
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import numpy


def _load_shared() -> Any:
    path = Path(__file__).with_name("evaluate-projection-quantization.py")
    spec = importlib.util.spec_from_file_location("mih_budgeted_validation_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load projection evaluation helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_matrix_runner() -> Any:
    path = Path(__file__).with_name("run-mih-budgeted-confidence-matrix.py")
    spec = importlib.util.spec_from_file_location("mih_budgeted_matrix_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load MIH budgeted-confidence matrix runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = _load_shared()
matrix_runner = _load_matrix_runner()
EvaluationError = shared.EvaluationError
SEEDS = (42, 43, 44, 45, 46)
TARGETS = (8192, 12288, 16384)
HAMMING_LIMITS = (512, 768, 1024, 1536)
BOOTSTRAP_SEED = 20260811
BOOTSTRAP_REPLICATES = 10000
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
        raise EvaluationError(message)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_map(values: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def row_name(target: int, limit: int, seed: int) -> str:
    return f"mih256-confidence-target{target}-h{limit}-adc256-seed{seed}"


def comparison_name(target: int, limit: int, seed: int) -> str:
    return f"mih256-confidence-target{target}-h{limit}-vs-h512-seed{seed}"


def expected_rows() -> dict[str, tuple[int, int, int]]:
    return {row_name(target, limit, seed): (target, limit, seed) for target in TARGETS for limit in HAMMING_LIMITS for seed in SEEDS}


def expected_comparisons() -> dict[str, tuple[str, str]]:
    return {
        comparison_name(target, limit, seed): (row_name(target, 512, seed), row_name(target, limit, seed))
        for target in TARGETS for limit in HAMMING_LIMITS[1:] for seed in SEEDS
    }


def validate_matrix_contract(path: Path) -> dict[str, Any]:
    """Bind the packaged matrix semantics to the evidence row grid."""
    try:
        matrix = matrix_runner.load_matrix(path)
        expanded = matrix_runner.rows(matrix)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise EvaluationError(f"matrix contract is invalid: {path}") from error
    actual: dict[str, tuple[int, int, int]] = {}
    for name, row in expanded:
        target = row.get("soft_candidate_target")
        limit = row.get("hamming_limit")
        seed = row.get("seed")
        require(isinstance(name, str) and all(isinstance(value, int) and not isinstance(value, bool) for value in (target, limit, seed)), "matrix row values are invalid")
        require(name not in actual, "matrix rows are not unique")
        actual[name] = (target, limit, seed)
    require(actual == expected_rows(), "packaged matrix and evidence row grid differ")
    return matrix


def json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationError(f"cannot read JSON: {path}") from error
    require(isinstance(value, dict), f"JSON root is invalid: {path}")
    return value


def load_contribution(path: Path, report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    require(path.is_file() and report.get("per_query_contributions_sha256") == sha256_file(path), f"contribution digest differs: {path}")
    with numpy.load(path, allow_pickle=False) as values:
        require(set(values.files) == CONTRIBUTION_KEYS, f"contribution fields are invalid: {path}")
        result = {name: values[name].copy() for name in values.files}
    count = result["query_ids"].shape[0]
    require(count == 1252 and all(result[name].shape == (count,) for name in CONTRIBUTION_KEYS - {"query_ids", "identity_json"}), f"contribution shapes are invalid: {path}")
    try:
        identity = json.loads(str(result["identity_json"].item()))
    except (ValueError, AttributeError) as error:
        raise EvaluationError(f"contribution identity is invalid: {path}") from error
    shared.validate_contribution_identity(identity, result["query_ids"], count)
    require(report.get("per_query_contribution_identity") == identity, f"report identity differs: {path}")
    return result, identity


def require_summary(report: dict[str, Any], contributions: dict[str, Any]) -> None:
    means = {
        "hamming_top_k_recall": "hamming_top_k_recall",
        "exact_top_k_candidate_coverage": "coverage_at_candidate_limit",
        "reranked_ndcg_at_10": "reranked_ndcg_at_10",
        "full_e5_ndcg_at_10": "full_e5_ndcg_at_10",
        "mean_candidates_per_query": "candidate_count",
        "mean_exact_bucket_floor_candidates_per_query": "exact_bucket_floor_candidate_count",
        "mean_bucket_probes_per_query": "bucket_probe_count",
        "mean_posting_visits_per_query": "posting_visit_count",
    }
    for field, array in means.items():
        require(report.get(field) == float(numpy.mean(contributions[array])), f"report summary differs from contributions: {field}")
    funnel = report.get("e5_oracle_survival")
    require(isinstance(funnel, dict) and set(funnel) == {"raw_union", "hamming_top_k", "second_stage", "mean_full_hamming_distance"}, "report funnel is invalid")
    for field, array in (("raw_union", "e5_oracle_raw_union_coverage"), ("hamming_top_k", "e5_oracle_hamming_top_k_coverage"), ("second_stage", "e5_oracle_second_stage_coverage"), ("mean_full_hamming_distance", "e5_oracle_mean_full_hamming_distance")):
        require(funnel[field] == float(numpy.mean(contributions[array])), f"report funnel differs from contributions: {field}")


def validate_rows(report_dir: Path, contribution_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Path], dict[str, Any], tuple[Any, ...]]:
    rows: dict[str, dict[str, Any]] = {}
    contribution_paths: dict[str, Path] = {}
    common_identity: dict[str, Any] | None = None
    common_contract: tuple[Any, ...] | None = None
    for name, (target, limit, seed) in expected_rows().items():
        report_path = report_dir / f"{name}.json"
        report = json_object(report_path)
        require(report.get("schema_version") == 6 and report.get("family") == "mih_banding_reference_v6", f"row identity is invalid: {name}")
        require(
            report.get("code_bits") == 256 and report.get("band_count") == 32 and report.get("band_width_bits") == [8] * 32 and
            report.get("probe_radius") == 1 and report.get("global_radius") is None and report.get("band_probe_radii") == [1] * 32 and
            report.get("probe_policy") == "budgeted-confidence" and report.get("soft_candidate_target") == target and
            report.get("hamming_limit") == limit and report.get("second_limit") == 256 and report.get("second_stage") == "binary-adc" and
            report.get("candidate_limit") == 512 and report.get("oracle_k") == 10 and report.get("itq_iterations") == 50 and
            report.get("seed") == seed and report.get("query_count") == 1252 and report.get("fixed_radius") is None and report.get("fixed_radius_exact_guarantee") is False,
            f"row contract is invalid: {name}",
        )
        source_files = report.get("evaluator_source_files_sha256")
        require(isinstance(source_files, dict) and set(source_files) == {"evaluate-mih-banding.py", "evaluate-projection-quantization.py"} and all(is_sha256(value) for value in source_files.values()) and report.get("evaluator_source_bundle_sha256") == digest_map(source_files), f"row source provenance is invalid: {name}")
        runtime = report.get("evaluator_runtime")
        require(isinstance(runtime, dict) and set(runtime) == {"python_implementation", "python_version", "numpy_version"} and all(isinstance(value, str) and value for value in runtime.values()), f"row runtime is invalid: {name}")
        contribution_path = contribution_dir / f"{name}.npz"
        contributions, identity = load_contribution(contribution_path, report)
        require_summary(report, contributions)
        contract = (source_files, report["evaluator_source_bundle_sha256"], runtime, report.get("calibration_materialization_manifest_sha256"), report.get("evaluation_materialization_manifest_sha256"), report.get("calibration_train_ids_sha256"), report.get("calibration_vector_count"))
        require(all(is_sha256(value) for value in contract[3:6]) and contract[6] == 25000, f"row calibration provenance is invalid: {name}")
        if common_identity is None:
            common_identity = identity
            common_contract = contract
        else:
            require(identity == common_identity and contract == common_contract, f"rows mix common provenance: {name}")
        rows[name] = report
        contribution_paths[name] = contribution_path
    require(common_identity is not None and common_contract is not None, "row grid is empty")
    return rows, contribution_paths, common_identity, common_contract


def validate_bootstraps(bootstrap_dir: Path, contribution_paths: dict[str, Path], identity: dict[str, Any]) -> tuple[list[dict[str, Any]], tuple[Any, ...]]:
    result: list[dict[str, Any]] = []
    common_source: tuple[Any, ...] | None = None
    for name, (left_row, right_row) in expected_comparisons().items():
        path = bootstrap_dir / f"{name}.json"
        report = json_object(path)
        left = contribution_paths[left_row]
        right = contribution_paths[right_row]
        require(report.get("schema_version") == 1 and report.get("family") == "mih_budgeted_confidence_paired_bootstrap_v1" and report.get("id") == name, f"bootstrap identity is invalid: {name}")
        require(report.get("left_contributions_file") == left.name and report.get("right_contributions_file") == right.name and report.get("left_sha256") == sha256_file(left) and report.get("right_sha256") == sha256_file(right), f"bootstrap endpoints are invalid: {name}")
        require(report.get("identity") == identity and report.get("query_count") == 1252 and report.get("replicates") == BOOTSTRAP_REPLICATES and report.get("seed") == BOOTSTRAP_SEED, f"bootstrap contract is invalid: {name}")
        with numpy.load(left, allow_pickle=False) as values:
            left_values = {field: values[field].copy() for field in values.files}
        with numpy.load(right, allow_pickle=False) as values:
            right_values = {field: values[field].copy() for field in values.files}
        expected_metrics = shared.paired_bootstrap_metrics(
            left_values, right_values, BOOTSTRAP_METRICS, BOOTSTRAP_REPLICATES, BOOTSTRAP_SEED
        )
        require(report.get("metrics") == expected_metrics, f"bootstrap metrics differ from deterministic replay: {name}")
        source = (report.get("bootstrap_source_files_sha256"), report.get("bootstrap_source_bundle_sha256"), report.get("bootstrap_runtime"))
        files, digest, runtime = source
        require(isinstance(files, dict) and set(files) == {"bootstrap-mih-budgeted-confidence.py", "evaluate-projection-quantization.py"} and all(is_sha256(value) for value in files.values()) and digest == digest_map(files), f"bootstrap source provenance is invalid: {name}")
        require(isinstance(runtime, dict) and set(runtime) == {"python_implementation", "python_version", "numpy_version"} and all(isinstance(value, str) and value for value in runtime.values()), f"bootstrap runtime is invalid: {name}")
        if common_source is None:
            common_source = source
        else:
            require(source == common_source, f"bootstraps mix source provenance: {name}")
        result.append({"id": name, "file": path.name, "sha256": sha256_file(path), "left": left.name, "right": right.name, "replicates": BOOTSTRAP_REPLICATES, "seed": BOOTSTRAP_SEED, "metrics": report.get("metrics")})
    require(common_source is not None, "bootstrap grid is empty")
    return result, common_source


def validate_source_snapshots(
    source_root: Path,
    evaluator_files: dict[str, str],
    bootstrap_files: dict[str, str],
) -> None:
    """Require bundle snapshots to be the exact evaluator/bootstrap sources."""
    for name, digest in {**evaluator_files, **bootstrap_files}.items():
        path = source_root / name
        require(path.is_file() and sha256_file(path) == digest, f"packaged provenance source differs: {name}")


def bundle_root(entries: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_written_archive(archive_path: Path, archive_names: list[str], bundle_entries: list[dict[str, Any]], bundle_manifest: dict[str, Any]) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        require(archive.namelist() == archive_names, "archive names differ after writing")
        for entry in bundle_entries:
            data = archive.read(entry["path"])
            require(len(data) == entry["size"] and hashlib.sha256(data).hexdigest() == entry["sha256"], f"archive member differs after writing: {entry['path']}")
        try:
            archived_manifest = json.loads(archive.read("bundle/evidence-bundle-manifest.json"))
        except (KeyError, json.JSONDecodeError) as error:
            raise EvaluationError("archive bundle manifest is invalid") from error
    require(archived_manifest == bundle_manifest and bundle_manifest.get("bundle_root_sha256") == bundle_root(bundle_entries), "archive bundle root differs after writing")


def write_manifest(args: Any) -> None:
    rows, paths, identity, contract = validate_rows(args.report_dir, args.contribution_dir)
    comparisons, bootstrap_contract = validate_bootstraps(args.bootstrap_dir, paths, identity)
    validate_matrix_contract(args.matrix)
    manifest = {
        "schema_version": 1,
        "family": "mih_budgeted_confidence_evidence_v1",
        "matrix_sha256": sha256_file(args.matrix),
        "evaluation_identity": identity,
        "common_contract": {"evaluator_source_files_sha256": contract[0], "evaluator_source_bundle_sha256": contract[1], "evaluator_runtime": contract[2], "calibration_materialization_manifest_sha256": contract[3], "evaluation_materialization_manifest_sha256": contract[4], "calibration_train_ids_sha256": contract[5], "bootstrap_source_files_sha256": bootstrap_contract[0], "bootstrap_source_bundle_sha256": bootstrap_contract[1], "bootstrap_runtime": bootstrap_contract[2]},
        "rows": [{"id": name, "report_file": f"{name}.json", "report_sha256": sha256_file(args.report_dir / f"{name}.json"), "contributions_file": path.name, "contributions_sha256": sha256_file(path), "soft_candidate_target": report["soft_candidate_target"], "hamming_limit": report["hamming_limit"], "seed": report["seed"]} for name, report in sorted(rows.items()) for path in (paths[name],)],
        "comparisons": sorted(comparisons, key=lambda value: value["id"]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def write_bundle(args: Any) -> None:
    write_manifest(args)
    manifest = json_object(args.output)
    root = args.output.parent
    common_contract = manifest.get("common_contract")
    require(isinstance(common_contract, dict), "compact manifest common contract is invalid")
    evaluator_files = common_contract.get("evaluator_source_files_sha256")
    bootstrap_files = common_contract.get("bootstrap_source_files_sha256")
    require(isinstance(evaluator_files, dict) and isinstance(bootstrap_files, dict), "compact manifest source contracts are invalid")
    validate_source_snapshots(Path(__file__).parent, evaluator_files, bootstrap_files)
    files: list[tuple[Path, str]] = [(args.output, "bundle/compact-manifest.json"), (args.matrix, "bundle/matrix.json")]
    for row in manifest["rows"]:
        files.extend(((args.report_dir / row["report_file"], f"bundle/reports/{row['report_file']}"), (args.contribution_dir / row["contributions_file"], f"bundle/contributions/{row['contributions_file']}")))
    for comparison in manifest["comparisons"]:
        files.append((args.bootstrap_dir / comparison["file"], f"bundle/bootstrap/{comparison['file']}"))
    for name in ("evaluate-mih-banding.py", "run-mih-budgeted-confidence-matrix.py", "bootstrap-mih-budgeted-confidence.py", Path(__file__).name, "evaluate-projection-quantization.py"):
        files.append((Path(__file__).with_name(name), f"bundle/sources/{name}"))
    archive_names = [name for _, name in files]
    require(len(archive_names) == len(set(archive_names)) and all("\\" not in name for name in archive_names), "archive names are invalid")
    bundle_entries = [{"path": name, "sha256": sha256_file(path), "size": path.stat().st_size} for path, name in files]
    root_digest = bundle_root(bundle_entries)
    bundle_manifest = {"schema_version": 1, "family": "mih_budgeted_confidence_bundle_v1", "bundle_root_sha256": root_digest, "entries": bundle_entries}
    bundle_manifest_path = root / "evidence-bundle-manifest.json"
    bundle_manifest_path.write_text(json.dumps(bundle_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    files.append((bundle_manifest_path, "bundle/evidence-bundle-manifest.json"))
    archive_names.append("bundle/evidence-bundle-manifest.json")
    args.archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, name in files:
            archive.write(path, name)
    validate_written_archive(args.archive, archive_names, bundle_entries, bundle_manifest)


def self_test() -> int:
    try:
        require(len(expected_rows()) == 60, "row grid is invalid")
        require(len(expected_comparisons()) == 45, "comparison grid is invalid")
        require(row_name(12288, 768, 44) == "mih256-confidence-target12288-h768-adc256-seed44", "row naming is invalid")
        with __import__("tempfile").TemporaryDirectory() as directory:
            root = Path(directory)
            matrix_path = Path(__file__).with_name("mih-budgeted-confidence-k1.example.json")
            validate_matrix_contract(matrix_path)
            invalid_matrix = json_object(matrix_path)
            invalid_matrix["evaluation"]["soft_candidate_targets"] = [4096]
            invalid_matrix_path = root / "invalid-matrix.json"
            invalid_matrix_path.write_text(json.dumps(invalid_matrix), encoding="utf-8")
            try:
                validate_matrix_contract(invalid_matrix_path)
            except EvaluationError:
                pass
            else:
                raise EvaluationError("wrong packaged matrix was accepted")

            evaluator_files = {"evaluate-mih-banding.py": hashlib.sha256(b"evaluator").hexdigest(), "evaluate-projection-quantization.py": hashlib.sha256(b"shared").hexdigest()}
            bootstrap_files = {"bootstrap-mih-budgeted-confidence.py": hashlib.sha256(b"bootstrap").hexdigest(), "evaluate-projection-quantization.py": hashlib.sha256(b"shared").hexdigest()}
            for name, content in (("evaluate-mih-banding.py", b"evaluator"), ("evaluate-projection-quantization.py", b"shared"), ("bootstrap-mih-budgeted-confidence.py", b"bootstrap")):
                (root / name).write_bytes(content)
            validate_source_snapshots(root, evaluator_files, bootstrap_files)
            (root / "evaluate-mih-banding.py").write_bytes(b"wrong evaluator")
            try:
                validate_source_snapshots(root, evaluator_files, bootstrap_files)
            except EvaluationError:
                pass
            else:
                raise EvaluationError("wrong packaged evaluator source was accepted")

            summary_arrays = {name: numpy.asarray([0.25, 0.75], dtype=numpy.float64) for name in CONTRIBUTION_KEYS - {"query_ids", "identity_json", "candidate_count", "exact_bucket_floor_candidate_count", "bucket_probe_count", "posting_visit_count"}}
            summary_arrays.update({name: numpy.asarray([2, 4], dtype=numpy.int32) for name in ("candidate_count", "exact_bucket_floor_candidate_count", "bucket_probe_count", "posting_visit_count")})
            summary_report = {
                "hamming_top_k_recall": 0.5, "exact_top_k_candidate_coverage": 0.5,
                "reranked_ndcg_at_10": 0.5, "full_e5_ndcg_at_10": 0.5,
                "mean_candidates_per_query": 3.0, "mean_exact_bucket_floor_candidates_per_query": 3.0,
                "mean_bucket_probes_per_query": 3.0, "mean_posting_visits_per_query": 3.0,
                "e5_oracle_survival": {"raw_union": 0.5, "hamming_top_k": 0.5, "second_stage": 0.5, "mean_full_hamming_distance": 0.5},
            }
            require_summary(summary_report, summary_arrays)
            summary_report["mean_candidates_per_query"] = 4.0
            try:
                require_summary(summary_report, summary_arrays)
            except EvaluationError:
                pass
            else:
                raise EvaluationError("tampered report summary was accepted")

            member = root / "member.bin"
            member.write_bytes(b"expected member")
            entries = [{"path": "bundle/member.bin", "sha256": sha256_file(member), "size": member.stat().st_size}]
            bundle_manifest = {"schema_version": 1, "family": "mih_budgeted_confidence_bundle_v1", "bundle_root_sha256": bundle_root(entries), "entries": entries}
            archive_path = root / "self-test.zip"
            names = ["bundle/member.bin", "bundle/evidence-bundle-manifest.json"]
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.write(member, names[0])
                archive.writestr(names[1], json.dumps(bundle_manifest, sort_keys=True))
            validate_written_archive(archive_path, names, entries, bundle_manifest)
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(names[0], b"tampered member")
                archive.writestr(names[1], json.dumps(bundle_manifest, sort_keys=True))
            try:
                validate_written_archive(archive_path, names, entries, bundle_manifest)
            except EvaluationError:
                pass
            else:
                raise EvaluationError("tampered archive member was accepted")
    except EvaluationError as error:
        print(f"validate-mih-budgeted-confidence-evidence self-test failed: {error}", file=sys.stderr)
        return 1
    print("MIH budgeted-confidence evidence validator self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("write-manifest", "write-bundle"):
        child = subparsers.add_parser(command)
        child.add_argument("--matrix", type=Path, required=True)
        child.add_argument("--report-dir", type=Path, required=True)
        child.add_argument("--contribution-dir", type=Path, required=True)
        child.add_argument("--bootstrap-dir", type=Path, required=True)
        child.add_argument("--output", type=Path, required=True)
        if command == "write-bundle":
            child.add_argument("--archive", type=Path, required=True)
    subparsers.add_parser("self-test")
    args = parser.parse_args(argv)
    try:
        if args.command == "self-test":
            return self_test()
        if args.command == "write-manifest":
            write_manifest(args)
        else:
            write_bundle(args)
    except (EvaluationError, OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"validate-mih-budgeted-confidence-evidence: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
