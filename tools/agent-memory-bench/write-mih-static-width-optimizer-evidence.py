#!/usr/bin/env python3
"""Write fail-closed evidence for the calibration-only static MIH optimizer."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


THIS_PATH = Path(__file__).resolve()


def load_module(name: str, module_name: str) -> Any:
    path = THIS_PATH.with_name(name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


optimizer = load_module("optimize-mih-static-width.py", "mih_static_width_evidence_optimizer")
base = load_module("write-mih-rerank-cost-evidence.py", "mih_static_width_evidence_base")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def candidate_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    permutation = candidate["permutation"]
    return optimizer.objective_key(
        candidate["metrics"], tuple(candidate["widths"]),
        optimizer.numpy.asarray(permutation, dtype=optimizer.numpy.intp),
    )


def validate_candidates(row: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    widths_profiles = optimizer.width_profiles(32, 6, 10, 2)
    expected: dict[str, tuple[str, tuple[int, ...], int | None]] = {
        "current-contiguous-32x8-identity": ("current-contiguous-identity", tuple([8] * 32), None),
    }
    for widths in widths_profiles:
        for restart_seed in (20260812, 20260813):
            identifier = f"heuristic-widths-{'-'.join(str(width) for width in widths)}-restart-{restart_seed}"
            expected[identifier] = ("bounded-heuristic", widths, restart_seed)
    actual = row.get("candidates")
    require(isinstance(actual, list) and len(actual) == len(expected), "optimizer candidate grid size is invalid")
    by_id = {candidate.get("id"): candidate for candidate in actual if isinstance(candidate, dict)}
    require(len(by_id) == len(actual) and set(by_id) == set(expected), "optimizer candidate grid differs from contract")
    for identifier, (kind, widths, restart_seed) in expected.items():
        candidate = by_id[identifier]; permutation = candidate.get("permutation"); metrics = candidate.get("metrics")
        require(
            candidate.get("kind") == kind and candidate.get("widths") == list(widths)
            and candidate.get("restart_seed") == restart_seed
            and isinstance(permutation, list) and permutation == [int(value) for value in permutation]
            and sorted(permutation) == list(range(256))
            and candidate.get("permutation_sha256") == optimizer.mih.band_layout_sha256(optimizer.numpy.asarray(permutation, dtype=optimizer.numpy.intp))
            and isinstance(metrics, dict) and metrics.get("pseudoquery_count") == 128 and metrics.get("mean_bucket_probes") == 288.0
            and all(isinstance(metrics.get(name), (int, float)) for name in ("mean_unique_candidates", "mean_posting_visits", "p95_posting_visits", "width_variance"))
            and isinstance(candidate.get("accepted_swaps"), int) and 0 <= candidate["accepted_swaps"] <= 12,
            f"optimizer candidate is invalid: {identifier}",
        )
        if identifier == "current-contiguous-32x8-identity":
            require(permutation == list(range(256)) and candidate["accepted_swaps"] == 0, "current identity control differs from contract")
    winner = min(actual, key=candidate_key)
    selected_permutation = row.get("selected_permutation")
    require(
        row.get("profile_count") == 6 and row.get("heuristic_assignment_evaluations") == 12
        and row.get("total_candidate_evaluations") == 13
        and row.get("selected_id") == winner["id"] and row.get("selected_kind") == winner["kind"]
        and row.get("selected_widths") == winner["widths"] and selected_permutation == winner["permutation"]
        and row.get("selected_permutation_sha256") == winner["permutation_sha256"]
        and row.get("selected_metrics") == winner["metrics"]
        and row.get("selected_restart_seed") == winner["restart_seed"]
        and row.get("selected_accepted_swaps") == winner["accepted_swaps"],
        "optimizer winner does not match the full candidate grid",
    )
    return winner, [by_id[identifier] for identifier in sorted(by_id)]


def validate_report(report: dict[str, Any], contract: Path) -> list[dict[str, Any]]:
    source = optimizer.source_files()
    require(
        report.get("schema_version") == 2 and report.get("family") == optimizer.FAMILY
        and report.get("contract_sha256") == sha256_file(contract)
        and report.get("calibration_vector_count") == 25000
        and report.get("pseudoquery_count") == 128
        and is_sha256(report.get("calibration_materialization_manifest_sha256"))
        and is_sha256(report.get("calibration_train_ids_sha256"))
        and is_sha256(report.get("pseudoquery_ids_sha256"))
        and report.get("source_files_sha256") == source
        and report.get("source_bundle_sha256") == optimizer.source_bundle(source),
        "optimizer report provenance is invalid",
    )
    rows = report.get("rows")
    require(isinstance(rows, list) and len(rows) == 5 and [row.get("seed") for row in rows] == optimizer.EXPECTED_SEEDS, "optimizer seed grid is invalid")
    records = []
    for row in rows:
        winner, candidates = validate_candidates(row)
        records.append({"seed": row["seed"], "selected_id": winner["id"], "selected_kind": winner["kind"], "selected_widths": winner["widths"], "selected_permutation_sha256": winner["permutation_sha256"], "metrics": winner["metrics"], "candidates": candidates})
    return records


def make_bundle(report_path: Path, contract: Path, output: Path) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    rows = validate_report(report, contract)
    source_names = ("optimize-mih-static-width.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py", THIS_PATH.name, "write-mih-rerank-cost-evidence.py")
    sources = [(THIS_PATH.with_name(name), f"bundle/sources/{name}") for name in source_names]
    for path, _ in sources:
        require(path.is_file(), f"source snapshot is absent: {path.name}")
    compact = {
        "schema_version": 2, "family": "mih_static_width_calibration_optimizer_evidence_v2",
        "report_sha256": sha256_file(report_path), "contract_sha256": sha256_file(contract),
        "calibration_provenance": {name: report[name] for name in ("calibration_materialization_manifest_sha256", "calibration_train_ids_sha256", "calibration_vector_count", "pseudoquery_ids_sha256", "pseudoquery_count")},
        "source_files_sha256": report["source_files_sha256"], "rows": rows,
    }
    compact_path = report_path.with_name("compact-manifest.json")
    compact_path.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    files = [(contract, "bundle/contract.json"), (report_path, "bundle/reports/calibration-optimizer.json"), (compact_path, "bundle/compact-manifest.json"), *sources]
    manifest = base.archive_manifest(files); manifest["family"] = "mih_static_width_calibration_optimizer_evidence_v2"
    output.parent.mkdir(parents=True, exist_ok=True); base.write_archive(output, files, manifest)
    return {"archive": str(output), "sha256": sha256_file(output), "bundle_root_sha256": manifest["bundle_root_sha256"]}


def self_test() -> int:
    try:
        if base.self_test() != 0:
            return 1
        metrics = {"mean_unique_candidates": 1.0, "mean_posting_visits": 2.0, "p95_posting_visits": 3.0, "width_variance": 0.0, "mean_bucket_probes": 288.0, "pseudoquery_count": 128}
        identity = optimizer.numpy.arange(256, dtype=optimizer.numpy.intp)
        candidates = [optimizer.candidate_record("current-contiguous-32x8-identity", "current-contiguous-identity", tuple([8] * 32), identity, metrics, None, 0)]
        for widths in optimizer.width_profiles(32, 6, 10, 2):
            for restart_seed in (20260812, 20260813):
                candidate_metrics = {**metrics, "width_variance": float(optimizer.numpy.var(optimizer.numpy.asarray(widths, dtype=optimizer.numpy.float64)))}
                candidates.append(optimizer.candidate_record(f"heuristic-widths-{'-'.join(str(width) for width in widths)}-restart-{restart_seed}", "bounded-heuristic", widths, identity, candidate_metrics, restart_seed, 0))
        winner = min(candidates, key=candidate_key)
        row = {"profile_count": 6, "heuristic_assignment_evaluations": 12, "total_candidate_evaluations": 13, "selected_id": winner["id"], "selected_kind": winner["kind"], "selected_widths": winner["widths"], "selected_permutation": winner["permutation"], "selected_permutation_sha256": winner["permutation_sha256"], "selected_metrics": winner["metrics"], "selected_restart_seed": winner["restart_seed"], "selected_accepted_swaps": winner["accepted_swaps"], "candidates": candidates}
        validate_candidates(row)
        row["selected_id"] = "mutated"
        try:
            validate_candidates(row)
        except ValueError:
            pass
        else:
            raise ValueError("optimizer winner mutation was accepted")
        report = {
            "schema_version": 2, "family": optimizer.FAMILY, "contract_sha256": "a" * 64,
            "calibration_materialization_manifest_sha256": "b" * 64, "calibration_train_ids_sha256": "c" * 64,
            "pseudoquery_ids_sha256": "d" * 64, "calibration_vector_count": 25000, "pseudoquery_count": 128,
            "source_files_sha256": optimizer.source_files(), "source_bundle_sha256": optimizer.source_bundle(optimizer.source_files()), "rows": [],
        }
        try:
            validate_report(report, THIS_PATH)
        except ValueError:
            pass
        else:
            raise ValueError("empty optimizer report was accepted")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"write-mih-static-width-optimizer-evidence self-test failed: {error}", file=sys.stderr)
        return 1
    print("MIH static-width optimizer evidence packager self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--report", type=Path); parser.add_argument("--contract", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        require(args.report is not None and args.contract is not None and args.output is not None, "packaging paths are required")
        print(json.dumps(make_bundle(args.report, args.contract, args.output), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError, base.zipfile.BadZipFile) as error:
        print(f"write-mih-static-width-optimizer-evidence: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
