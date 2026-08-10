#!/usr/bin/env python3
"""Fail-closed packager for the ANN cascade comparison matrix."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path
from typing import Any


def load_runner() -> Any:
    path = Path(__file__).with_name("run-ann-cascade-matrix.py")
    spec = importlib.util.spec_from_file_location("ann_matrix_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load ANN matrix runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load_runner()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def source_files() -> dict[str, Path]:
    root = Path(__file__).parent
    names = (
        "evaluate-ann-cascade.py", "evaluate-mih-banding.py",
        "evaluate-projection-quantization.py", "run-ann-cascade-matrix.py",
        "write-ann-cascade-evidence.py", "requirements-ann-cascade-comparison.txt",
    )
    return {name: root / name for name in names}


def canonical_config(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def validate(root: Path, matrix: Path, bootstrap_root: Path) -> tuple[list[tuple[Path, str]], dict[str, Any]]:
    expected_rows = dict(runner.rows(runner.load_matrix(matrix)))
    source_map = {name: sha256_file(path) for name, path in source_files().items()}
    expected_evaluator_sources = {name: source_map[name] for name in ("evaluate-ann-cascade.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py", "requirements-ann-cascade-comparison.txt")}
    paths: list[tuple[Path, str]] = [(matrix, "bundle/matrix.json")]
    reference: dict[str, Any] | None = None
    seed_payloads: dict[int, tuple[str, str]] = {}
    for name, config in expected_rows.items():
        config_path = root / "configs" / f"{name}.json"
        report_path = root / "reports" / f"{name}.json"
        contribution_path = root / "contributions" / f"{name}.npz"
        require(config_path.is_file() and report_path.is_file() and contribution_path.is_file(), f"matrix row is incomplete: {name}")
        require(config_path.read_bytes() == canonical_config(config), f"matrix config differs: {name}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        require(report.get("schema_version") == 1 and report.get("family") == "ann_cascade_comparison_v1", f"report identity is invalid: {name}")
        require(report.get("config") == config and report.get("config_sha256") == sha256_file(config_path), f"report config provenance is invalid: {name}")
        require(report.get("per_query_contributions_path") == contribution_path.name and report.get("per_query_contributions_sha256") == sha256_file(contribution_path), f"contribution provenance is invalid: {name}")
        require(report.get("evaluator_source_files_sha256") == expected_evaluator_sources, f"evaluator sources differ: {name}")
        seed = config["itq_seed"]
        payloads = (report.get("document_code_payload_sha256"), report.get("query_code_payload_sha256"))
        if seed in seed_payloads:
            require(seed_payloads[seed] == payloads, f"ITQ payload provenance differs within seed: {name}")
        else:
            seed_payloads[seed] = payloads
        if reference is None:
            reference = report
        else:
            for field in ("calibration_materialization_manifest_sha256", "evaluation_materialization_manifest_sha256", "calibration_train_ids_sha256", "thread_count", "timing_scope"):
                require(report.get(field) == reference.get(field), f"shared provenance differs: {name}:{field}")
        paths.extend(((config_path, f"bundle/configs/{name}.json"), (report_path, f"bundle/reports/{name}.json"), (contribution_path, f"bundle/contributions/{name}.npz")))
    require(set(path.stem for path in (root / "reports").glob("*.json")) == set(expected_rows), "unexpected report grid")
    require(reference is not None, "evidence contains no reports")
    bootstrap_paths = sorted(bootstrap_root.glob("*.json"))
    require(len(bootstrap_paths) == 20, "paired bootstrap grid is incomplete")
    for path in bootstrap_paths:
        value = json.loads(path.read_text(encoding="utf-8"))
        require(value.get("schema_version") == 1 and value.get("family") == "ann_cascade_paired_bootstrap_v1" and value.get("replicates") == 10000 and value.get("seed") == 20260811, f"bootstrap contract is invalid: {path.name}")
        paths.append((path, f"bundle/bootstrap/{path.name}"))
    for name, path in source_files().items():
        paths.append((path, f"bundle/sources/{name}"))
    return paths, {"row_count": len(expected_rows), "shared_provenance": {field: reference[field] for field in ("calibration_materialization_manifest_sha256", "evaluation_materialization_manifest_sha256", "calibration_train_ids_sha256", "thread_count", "timing_scope")}, "itq_seed_code_payloads": {str(seed): {"document_code_payload_sha256": values[0], "query_code_payload_sha256": values[1]} for seed, values in sorted(seed_payloads.items())}}


def write(args: Any) -> None:
    paths, summary = validate(args.input_root, args.matrix, args.bootstrap_root)
    names = [name for _, name in paths]
    require(len(names) == len(set(names)) and all("\\" not in name for name in names), "archive names are invalid")
    entries = {name: {"sha256": sha256_file(path), "size": path.stat().st_size} for path, name in paths}
    manifest = {"schema_version": 1, "family": "ann_cascade_evidence_v1", "bundle_root_sha256": hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(), "entries": entries, **summary}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, name in paths:
            archive.write(path, name)
        archive.writestr("bundle/compact-manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    with zipfile.ZipFile(args.output) as archive:
        require(all("\\" not in name for name in archive.namelist()), "archive is not portable")
    print(json.dumps({"archive": str(args.output), "sha256": sha256_file(args.output), "bundle_root_sha256": manifest["bundle_root_sha256"]}, sort_keys=True))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--bootstrap-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        write(args)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"write-ann-cascade-evidence: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
