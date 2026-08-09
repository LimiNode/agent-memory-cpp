#!/usr/bin/env python3
"""Fail-closed manifest and portable evidence bundle for the MIH study."""

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
    spec = importlib.util.spec_from_file_location("mih_validation_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load projection evaluation helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = _load_shared()
EvaluationError = shared.EvaluationError
CONTRIBUTION_KEYS = {
    "hamming_top_k_recall", "coverage_at_candidate_limit",
    "reranked_ndcg_at_10", "full_e5_ndcg_at_10", "candidate_count",
    "bucket_probe_count", "query_ids", "identity_json",
}
SEEDS = range(42, 47)
BOOTSTRAP_SEED = 20260809
BOOTSTRAP_REPLICATES = 10000


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_value(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationError(f"cannot read JSON: {path}") from error
    if not isinstance(value, dict):
        raise EvaluationError(f"JSON root is not an object: {path}")
    return value


def expected_reports() -> dict[str, tuple[str, dict[str, Any]]]:
    result: dict[str, tuple[str, dict[str, Any]]] = {}
    frontier = (
        (128, 8, 0), (128, 8, 1), (128, 8, 2), (128, 12, 0),
        (128, 12, 1), (128, 16, 0), (128, 16, 1), (256, 16, 0),
        (256, 16, 1), (256, 24, 0), (256, 24, 1), (256, 32, 0),
        (256, 32, 1),
    )
    for bits, bands, probe in frontier:
        for seed in SEEDS:
            name = f"mih-{bits}b-{bands}band-r{probe}-seed{seed}.json"
            result[name] = ("mih", {"code_bits": bits, "band_count": bands, "probe_radius": probe, "global_radius": None, "second_stage": "hamming", "second_limit": 512, "seed": seed})
    for radius in (48, 56, 64):
        for seed in SEEDS:
            name = f"mih-256b-16x16-globalr{radius}-seed{seed}.json"
            result[name] = ("mih", {"code_bits": 256, "band_count": 16, "probe_radius": 0, "global_radius": radius, "second_stage": "hamming", "second_limit": 512, "seed": seed})
    for radius in (48, 56, 64):
        for stage in ("hamming", "binary-adc"):
            for limit in (64, 128, 256):
                for seed in SEEDS:
                    name = f"mih256-r{radius}-h512-{stage}-k{limit}-seed{seed}.json"
                    result[name] = ("cascade", {"code_bits": 256, "band_count": 16, "probe_radius": 0, "global_radius": radius, "second_stage": stage, "second_limit": limit, "seed": seed})
    return result


def expected_comparisons() -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    def cascade(radius: int, stage: str, limit: int, seed: int) -> str:
        return f"mih256-r{radius}-h512-{stage}-k{limit}-seed{seed}.npz"
    def mih(bits: int, bands: int, probe: int, seed: int) -> str:
        return f"mih-{bits}b-{bands}band-r{probe}-seed{seed}.npz"
    def add(name: str, left: str, right: str) -> None:
        if name in result:
            raise AssertionError("duplicate comparison ID")
        result[name] = (left, right)
    for radius in (48, 56, 64):
        for limit in (64, 128, 256):
            for seed in SEEDS:
                add(f"cascade-r{radius}-adc-vs-hamming-k{limit}-seed{seed}", cascade(radius, "hamming", limit, seed), cascade(radius, "binary-adc", limit, seed))
    for radius in (48, 56, 64):
        for stage in ("hamming", "binary-adc"):
            for seed in SEEDS:
                add(f"cascade-r{radius}-{stage}-k128-vs-k64-seed{seed}", cascade(radius, stage, 64, seed), cascade(radius, stage, 128, seed))
                add(f"cascade-r{radius}-{stage}-k256-vs-k128-seed{seed}", cascade(radius, stage, 128, seed), cascade(radius, stage, 256, seed))
    for stage in ("hamming", "binary-adc"):
        for limit in (64, 128, 256):
            for seed in SEEDS:
                add(f"cascade-{stage}-k{limit}-r56-vs-r48-seed{seed}", cascade(48, stage, limit, seed), cascade(56, stage, limit, seed))
                add(f"cascade-{stage}-k{limit}-r64-vs-r56-seed{seed}", cascade(56, stage, limit, seed), cascade(64, stage, limit, seed))
    for bits, bands, probes in ((128, 8, (0, 1, 2)), (128, 12, (0, 1)), (128, 16, (0, 1)), (256, 16, (0, 1)), (256, 24, (0, 1)), (256, 32, (0, 1))):
        for seed in SEEDS:
            for left, right in zip(probes, probes[1:]):
                add(f"mih-{bits}b-{bands}band-r{right}-vs-r{left}-seed{seed}", mih(bits, bands, left, seed), mih(bits, bands, right, seed))
    for bits, bands in ((128, (8, 12, 16)), (256, (16, 24, 32))):
        for probe in (0, 1):
            for seed in SEEDS:
                for left, right in zip(bands, bands[1:]):
                    add(f"mih-{bits}b-{right}band-vs-{left}band-r{probe}-seed{seed}", mih(bits, left, probe, seed), mih(bits, right, probe, seed))
    for seed in SEEDS:
        add(f"mih-globalr56-vs-globalr48-seed{seed}", f"mih-256b-16x16-globalr48-seed{seed}.npz", f"mih-256b-16x16-globalr56-seed{seed}.npz")
        add(f"mih-globalr64-vs-globalr56-seed{seed}", f"mih-256b-16x16-globalr56-seed{seed}.npz", f"mih-256b-16x16-globalr64-seed{seed}.npz")
    return result


def read_contributions(path: Path, report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not path.is_file() or report.get("per_query_contributions_sha256") != sha256_file(path):
        raise EvaluationError(f"contribution hash differs: {path}")
    with numpy.load(path, allow_pickle=False) as values:
        if set(values.files) != CONTRIBUTION_KEYS:
            raise EvaluationError(f"contribution fields are invalid: {path}")
        payload = {key: values[key].copy() for key in values.files}
    count = payload["query_ids"].shape[0]
    if count <= 0 or any(payload[key].shape != (count,) for key in CONTRIBUTION_KEYS - {"query_ids", "identity_json"}):
        raise EvaluationError(f"contribution shapes are invalid: {path}")
    try:
        identity = json.loads(str(payload["identity_json"].item()))
    except (ValueError, AttributeError) as error:
        raise EvaluationError(f"contribution identity is invalid: {path}") from error
    shared.validate_contribution_identity(identity, payload["query_ids"], count)
    if report.get("per_query_contribution_identity") != identity:
        raise EvaluationError(f"report contribution identity differs: {path}")
    means = {
        "hamming_top_k_recall": float(payload["hamming_top_k_recall"].mean()),
        "exact_top_k_candidate_coverage": float(payload["coverage_at_candidate_limit"].mean()),
        "reranked_ndcg_at_10": float(payload["reranked_ndcg_at_10"].mean()),
        "full_e5_ndcg_at_10": float(payload["full_e5_ndcg_at_10"].mean()),
        "mean_candidates_per_query": float(payload["candidate_count"].mean()),
        "mean_bucket_probes_per_query": float(payload["bucket_probe_count"].mean()),
    }
    for key, value in means.items():
        if not isinstance(report.get(key), (int, float)) or not math.isclose(float(report[key]), value, rel_tol=0.0, abs_tol=1.0e-12):
            raise EvaluationError(f"report aggregate differs from contributions: {path}")
    return payload, identity


def validate_reports(mih_dir: Path, cascade_dir: Path) -> tuple[list[dict[str, Any]], dict[str, tuple[str, dict[str, Any]]], dict[str, Any], str]:
    expected = expected_reports()
    actual: dict[str, tuple[str, dict[str, Any]]] = {}
    source: str | None = None
    identity: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    for scope, root in (("mih", mih_dir), ("cascade", cascade_dir)):
        paths = sorted(root.glob("*.json"))
        for path in paths:
            if path.name not in expected or expected[path.name][0] != scope:
                raise EvaluationError(f"unexpected report: {path}")
            report = json_value(path)
            if report.get("schema_version") != 2 or report.get("family") != "mih_banding_reference_v2":
                raise EvaluationError(f"report schema is invalid: {path}")
            if report.get("hamming_limit") != 512 or report.get("candidate_limit") != 512 or report.get("oracle_k") != 10:
                raise EvaluationError(f"report candidate contract is invalid: {path}")
            for key, value in expected[path.name][1].items():
                if report.get(key) != value:
                    raise EvaluationError(f"report grid field differs: {path}: {key}")
            current_source = report.get("evaluator_source_sha256")
            if not isinstance(current_source, str) or len(current_source) != 64:
                raise EvaluationError(f"report source digest is invalid: {path}")
            if source is None:
                source = current_source
            elif source != current_source:
                raise EvaluationError("reports mix evaluator sources")
            contribution = root / report.get("per_query_contributions_path", "")
            _, current_identity = read_contributions(contribution, report)
            if identity is None:
                identity = current_identity
            elif identity != current_identity:
                raise EvaluationError("reports mix contribution identities")
            actual[path.name] = expected[path.name]
            rows.append({
                "scope": scope, "report_file": path.name, "report_sha256": sha256_file(path),
                "contributions_file": contribution.name, "contributions_sha256": sha256_file(contribution),
                **expected[path.name][1],
                "hamming_top_k_recall": report["hamming_top_k_recall"],
                "coverage_at_512": report["exact_top_k_candidate_coverage"],
                "reranked_ndcg_at_10": report["reranked_ndcg_at_10"],
            })
    if set(actual) != set(expected) or source is None or identity is None:
        raise EvaluationError("report grid is incomplete")
    return rows, actual, identity, source


def validate_comparisons(bootstrap_dir: Path, contribution_paths: dict[str, Path], identity: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    paths = sorted(bootstrap_dir.glob("*.json"))
    expected = expected_comparisons()
    actual: set[str] = set()
    source: str | None = None
    rows: list[dict[str, Any]] = []
    for path in paths:
        report = json_value(path)
        comparison_id = report.get("id")
        if comparison_id not in expected or comparison_id in actual or path.name != f"{comparison_id}.json":
            raise EvaluationError(f"comparison ID is invalid: {path}")
        if report.get("schema_version") != 1 or report.get("family") != "mih_paired_query_bootstrap_v1" or report.get("identity") != identity or report.get("replicates") != BOOTSTRAP_REPLICATES or report.get("seed") != BOOTSTRAP_SEED:
            raise EvaluationError(f"comparison contract is invalid: {path}")
        left = report.get("left_sha256"); right = report.get("right_sha256")
        expected_left, expected_right = expected[comparison_id]
        if report.get("left_contributions_file") != expected_left or report.get("right_contributions_file") != expected_right:
            raise EvaluationError(f"comparison endpoint names differ: {path}")
        if contribution_paths.get(expected_left) is None or contribution_paths.get(expected_right) is None or left != sha256_file(contribution_paths[expected_left]) or right != sha256_file(contribution_paths[expected_right]):
            raise EvaluationError(f"comparison endpoints are invalid: {path}")
        current_source = report.get("evaluator_source_sha256")
        if not isinstance(current_source, str) or len(current_source) != 64:
            raise EvaluationError(f"comparison source digest is invalid: {path}")
        if source is None:
            source = current_source
        elif source != current_source:
            raise EvaluationError("comparisons mix bootstrap sources")
        metrics = report.get("metrics")
        if not isinstance(metrics, dict) or set(metrics) != {"hamming_top_k_recall", "coverage_at_candidate_limit", "reranked_ndcg_at_10"}:
            raise EvaluationError(f"comparison metrics are invalid: {path}")
        actual.add(comparison_id)
        rows.append({"id": comparison_id, "bootstrap_report_file": path.name, "bootstrap_report_sha256": sha256_file(path), "left_contributions_sha256": left, "right_contributions_sha256": right, "replicates": BOOTSTRAP_REPLICATES, "seed": BOOTSTRAP_SEED, "metrics": metrics})
    if actual != set(expected) or source is None:
        raise EvaluationError("comparison grid is incomplete")
    return rows, source


def write_manifest(args: Any) -> None:
    reports, _, identity, evaluator_source = validate_reports(args.mih_dir, args.cascade_dir)
    contribution_paths = {row["contributions_file"]: (args.mih_dir if row["scope"] == "mih" else args.cascade_dir) / row["contributions_file"] for row in reports}
    comparisons, bootstrap_source = validate_comparisons(args.bootstrap_dir, contribution_paths, identity)
    manifest = {
        "schema_version": 1, "family": "mih_banding_evidence_v1",
        "evaluation_identity": identity, "evaluator_source_sha256": evaluator_source,
        "bootstrap_evaluator_source_sha256": bootstrap_source, "report_count": len(reports),
        "comparison_count": len(comparisons), "reports": reports, "comparisons": comparisons,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def write_bundle(args: Any) -> None:
    manifest = json_value(args.manifest)
    if manifest.get("family") != "mih_banding_evidence_v1":
        raise EvaluationError("evidence manifest schema is invalid")
    files: list[tuple[Path, str]] = [(args.manifest, "bundle/compact-manifest.json")]
    roots = {"mih": args.mih_dir, "cascade": args.cascade_dir}
    for row in manifest.get("reports", []):
        if not isinstance(row, dict) or row.get("scope") not in roots:
            raise EvaluationError("evidence report row is invalid")
        root = roots[row["scope"]]
        for name, target, digest in (("report_file", "reports", "report_sha256"), ("contributions_file", "contributions", "contributions_sha256")):
            source = root / row[name]
            if not source.is_file() or sha256_file(source) != row[digest]:
                raise EvaluationError("evidence report hash differs")
            files.append((source, f"bundle/{target}/{row['scope']}-{source.name}"))
    for row in manifest.get("comparisons", []):
        source = args.bootstrap_dir / row["bootstrap_report_file"]
        if not source.is_file() or sha256_file(source) != row["bootstrap_report_sha256"]:
            raise EvaluationError("evidence comparison hash differs")
        files.append((source, f"bundle/bootstrap/{source.name}"))
    for source in (Path(__file__), Path(__file__).with_name("evaluate-mih-banding.py"), Path(__file__).with_name("bootstrap-mih-banding.py"), Path(__file__).with_name("evaluate-projection-quantization.py")):
        files.append((source, f"bundle/sources/{source.name}"))
    names = [name for _, name in files]
    if len(names) != len(set(names)) or any("\\" in name for name in names):
        raise EvaluationError("evidence archive names are invalid")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, name in files:
            archive.write(source, name)
    with zipfile.ZipFile(args.output) as archive:
        if archive.namelist() != names:
            raise EvaluationError("evidence archive contents differ")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False); common.add_argument("--mih-dir", type=Path, required=True); common.add_argument("--cascade-dir", type=Path, required=True); common.add_argument("--bootstrap-dir", type=Path, required=True)
    write = sub.add_parser("write-manifest", parents=[common]); write.add_argument("--output", type=Path, required=True)
    bundle = sub.add_parser("write-bundle"); bundle.add_argument("--manifest", type=Path, required=True); bundle.add_argument("--mih-dir", type=Path, required=True); bundle.add_argument("--cascade-dir", type=Path, required=True); bundle.add_argument("--bootstrap-dir", type=Path, required=True); bundle.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "write-manifest": write_manifest(args)
        else: write_bundle(args)
    except (EvaluationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"validate-mih-banding-evidence: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
