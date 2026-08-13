#!/usr/bin/env python3
"""Write fail-closed evidence for static MIH optimizer held-out evaluation."""

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
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


runner = load_module("run-mih-static-width-heldout.py", "mih_static_width_heldout_evidence_runner")
optimizer_evidence = load_module("write-mih-static-width-optimizer-evidence.py", "mih_static_width_heldout_evidence_optimizer")
bootstrap = load_module("bootstrap-mih-static-width-heldout.py", "mih_static_width_heldout_evidence_bootstrap")
base = load_module("write-mih-rerank-cost-evidence.py", "mih_static_width_heldout_evidence_base")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"JSON object is invalid: {path.name}")
    return value


def expected_rows(optimizer_report: Path, optimizer_contract: Path) -> list[tuple[str, dict[str, Any]]]:
    return runner.rows(optimizer_report, optimizer_contract)


def validate_matrix(root: Path, contract: Path, optimizer_report: Path, optimizer_contract: Path, calibration_root: Path, evaluation_root: Path) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]], dict[str, Any], dict[str, Any]]:
    runner.load_contract(contract)
    report = json_object(optimizer_report); optimizer_evidence.validate_report(report, optimizer_contract)
    calibration = runner.shared.load_root(calibration_root); evaluation = runner.shared.load_root(evaluation_root)
    rows = expected_rows(optimizer_report, optimizer_contract)
    manifest_path = root / "matrix-manifest.json"; manifest = json_object(manifest_path)
    expected_manifest_keys = {"schema_version", "family", "contract_sha256", "optimizer_report_sha256", "calibration_materialization_manifest_sha256", "calibration_train_ids_sha256", "evaluation_materialization_manifest_sha256", "evaluation_query_ids_sha256", "runner_source_files_sha256", "runner_source_bundle_sha256", "rows"}
    require(
        set(manifest) == expected_manifest_keys and manifest["schema_version"] == 1 and manifest["family"] == runner.FAMILY
        and manifest["contract_sha256"] == sha256_file(contract) and manifest["optimizer_report_sha256"] == sha256_file(optimizer_report)
        and manifest["calibration_materialization_manifest_sha256"] == calibration["manifest_sha256"]
        and manifest["calibration_train_ids_sha256"] == runner.shared.ordered_ids_sha256(calibration["train_ids"])
        and manifest["evaluation_materialization_manifest_sha256"] == evaluation["manifest_sha256"]
        and manifest["evaluation_query_ids_sha256"] == runner.shared.ordered_ids_sha256(evaluation["query_ids"])
        and manifest["runner_source_files_sha256"] == runner.source_files() and manifest["runner_source_bundle_sha256"] == runner.source_bundle(runner.source_files()),
        "held-out matrix manifest provenance is invalid",
    )
    entries = manifest["rows"]
    require(isinstance(entries, list) and len(entries) == len(rows), "held-out matrix grid is incomplete")
    by_id = {entry.get("id"): entry for entry in entries if isinstance(entry, dict)}
    require(len(by_id) == len(entries) and set(by_id) == {name for name, _ in rows}, "held-out matrix IDs differ from contract")
    for name, row in rows:
        entry = by_id[name]; report_path = root / "reports" / f"{name}.json"; contribution_path = root / "contributions" / f"{name}.npz"
        expected_selection = runner.selection_provenance(row["selection"]) if row["selection"] else None
        require(
            entry == {"id": name, "kind": row["kind"], "seed": row["seed"], "widths": row["widths"], "selection": expected_selection, "report_file": report_path.name, "report_sha256": sha256_file(report_path), "contributions_file": contribution_path.name, "contributions_sha256": sha256_file(contribution_path)}
            and runner.row_is_complete(report_path, contribution_path, row, calibration, evaluation),
            f"held-out row is invalid: {name}",
        )
    return manifest, rows, calibration, evaluation


def comparison_id(seed: int) -> str:
    return f"current-contiguous-vs-optimizer-selected-seed{seed}"


def validate_bootstraps(root: Path, matrix_rows: list[tuple[str, dict[str, Any]]], matrix_root: Path, evaluation: dict[str, Any]) -> list[tuple[Path, dict[str, Any]]]:
    by_seed: dict[int, dict[str, str]] = {}
    for name, row in matrix_rows:
        by_seed.setdefault(row["seed"], {})[row["kind"]] = name
    require(set(by_seed) == set(runner.SEEDS) and all(set(pair) == {"control", "treatment"} for pair in by_seed.values()), "held-out comparison grid is invalid")
    expected_files = {f"{comparison_id(seed)}.json" for seed in runner.SEEDS}
    actual_files = {path.name for path in root.glob("*.json")}
    require(actual_files == expected_files, "held-out bootstrap grid is incomplete")
    result = []
    source = bootstrap.source_files(); expected_identity = runner.shared.contribution_identity(evaluation, 512, 10)
    for seed in runner.SEEDS:
        left_name = by_seed[seed]["control"]; right_name = by_seed[seed]["treatment"]
        left = matrix_root / "contributions" / f"{left_name}.npz"; right = matrix_root / "contributions" / f"{right_name}.npz"; path = root / f"{comparison_id(seed)}.json"; item = json_object(path)
        left_values = bootstrap.load_contributions(left); right_values = bootstrap.load_contributions(right)
        require(
            item.get("schema_version") == 1 and item.get("family") == "mih_static_width_optimizer_heldout_paired_bootstrap_v1" and item.get("id") == comparison_id(seed)
            and item.get("left_contributions_file") == left.name and item.get("right_contributions_file") == right.name
            and item.get("left_sha256") == sha256_file(left) and item.get("right_sha256") == sha256_file(right)
            and item.get("identity") == expected_identity and item.get("query_count") == len(evaluation["query_ids"])
            and item.get("replicates") == 10000 and item.get("seed") == 20260813
            and item.get("bootstrap_source_files_sha256") == source and item.get("bootstrap_source_bundle_sha256") == bootstrap.source_bundle(source)
            and item.get("metrics") == bootstrap.shared.paired_bootstrap_metrics(left_values, right_values, bootstrap.METRICS, 10000, 20260813),
            f"held-out bootstrap is invalid: {path.name}",
        )
        result.append((path, item))
    return result


def make_bundle(matrix_root: Path, matrix_contract: Path, optimizer_report: Path, optimizer_contract: Path, calibration_root: Path, evaluation_root: Path, bootstrap_root: Path, output: Path) -> dict[str, Any]:
    manifest, rows, _, evaluation = validate_matrix(matrix_root, matrix_contract, optimizer_report, optimizer_contract, calibration_root, evaluation_root)
    comparisons = validate_bootstraps(bootstrap_root, rows, matrix_root, evaluation)
    sources = ("optimize-mih-static-width.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py", "write-mih-static-width-optimizer-evidence.py", "run-mih-static-width-heldout.py", "bootstrap-mih-static-width-heldout.py", THIS_PATH.name, "write-mih-rerank-cost-evidence.py")
    source_files = [(THIS_PATH.with_name(name), f"bundle/sources/{name}") for name in sources]
    for path, _ in source_files:
        require(path.is_file(), f"source snapshot is absent: {path.name}")
    compact = {
        "schema_version": 1, "family": "mih_static_width_optimizer_heldout_evidence_v1",
        "optimizer_report_sha256": sha256_file(optimizer_report), "optimizer_contract_sha256": sha256_file(optimizer_contract),
        "matrix_contract_sha256": sha256_file(matrix_contract), "matrix_manifest_sha256": sha256_file(matrix_root / "matrix-manifest.json"),
        "calibration_provenance": {name: manifest[name] for name in ("calibration_materialization_manifest_sha256", "calibration_train_ids_sha256")},
        "evaluation_provenance": {name: manifest[name] for name in ("evaluation_materialization_manifest_sha256", "evaluation_query_ids_sha256")},
        "rows": manifest["rows"],
        "comparisons": [{"id": item["id"], "file": path.name, "sha256": sha256_file(path), "left_sha256": item["left_sha256"], "right_sha256": item["right_sha256"]} for path, item in comparisons],
    }
    compact_path = matrix_root / "compact-manifest.json"; compact_path.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    files = [(optimizer_contract, "bundle/optimizer-contract.json"), (optimizer_report, "bundle/reports/calibration-optimizer.json"), (matrix_contract, "bundle/heldout-contract.json"), (matrix_root / "matrix-manifest.json", "bundle/matrix-manifest.json"), (compact_path, "bundle/compact-manifest.json")]
    files += [(matrix_root / "reports" / f"{name}.json", f"bundle/reports/{name}.json") for name, _ in rows]
    files += [(matrix_root / "contributions" / f"{name}.npz", f"bundle/contributions/{name}.npz") for name, _ in rows]
    files += [(path, f"bundle/bootstrap/{path.name}") for path, _ in comparisons] + source_files
    archive_manifest = base.archive_manifest(files); archive_manifest["family"] = "mih_static_width_optimizer_heldout_evidence_v1"
    output.parent.mkdir(parents=True, exist_ok=True); base.write_archive(output, files, archive_manifest)
    return {"archive": str(output), "sha256": sha256_file(output), "bundle_root_sha256": archive_manifest["bundle_root_sha256"]}


def self_test() -> int:
    try:
        if base.self_test() != 0:
            return 1
        require(comparison_id(52) == "current-contiguous-vs-optimizer-selected-seed52", "bootstrap ID is unstable")
        try:
            validate_bootstraps(Path("."), [], Path("."), {"query_ids": []})
        except ValueError:
            pass
        else:
            raise ValueError("empty held-out comparison grid was accepted")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"write-mih-static-width-heldout-evidence self-test failed: {error}", file=sys.stderr); return 1
    print("MIH static-width held-out evidence packager self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--matrix-root", type=Path); parser.add_argument("--matrix-contract", type=Path); parser.add_argument("--optimizer-report", type=Path); parser.add_argument("--optimizer-contract", type=Path); parser.add_argument("--calibration-root", type=Path); parser.add_argument("--evaluation-root", type=Path); parser.add_argument("--bootstrap-root", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        require(all((args.matrix_root, args.matrix_contract, args.optimizer_report, args.optimizer_contract, args.calibration_root, args.evaluation_root, args.bootstrap_root, args.output)), "packaging paths are required")
        print(json.dumps(make_bundle(args.matrix_root, args.matrix_contract, args.optimizer_report, args.optimizer_contract, args.calibration_root, args.evaluation_root, args.bootstrap_root, args.output), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError, base.zipfile.BadZipFile) as error:
        print(f"write-mih-static-width-heldout-evidence: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
