#!/usr/bin/env python3
"""Fail-closed evidence packager for the canonical arbitrary-m MIH frontier."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()
ROOT = THIS.parents[2]


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def digest(value: dict[str, str]) -> str:
    return sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def snapshot(ref: str, relative: str) -> bytes:
    return subprocess.run(["git", "show", f"{ref}:{relative}"], cwd=ROOT, check=True, capture_output=True).stdout


def load(name: str, module_name: str) -> Any:
    path = THIS.with_name(name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load("run-mih-canonical-arbitrary-m-frontier.py", "canonical_arbitrary_m_evidence_runner")
bootstrap = load("bootstrap-mih-canonical-arbitrary-m-frontier.py", "canonical_arbitrary_m_evidence_bootstrap")

SOURCE_NAMES = (
    "run-mih-canonical-arbitrary-m-frontier.py",
    "mih-canonical-arbitrary-m-frontier.example.json",
    "evaluate-mih-banding.py",
    "evaluate-projection-quantization.py",
)


def validate_binding(contract_path: Path, contract_snapshot: bytes, contract: dict[str, Any], calibration: dict[str, Any], evaluation: dict[str, Any]) -> None:
    require(contract_path.read_bytes() == contract_snapshot, "canonical arbitrary-m contract snapshot differs")
    require(calibration.get("manifest_sha256") == contract.get("training_materialization_manifest_sha256"), "canonical arbitrary-m calibration provenance differs")
    require(evaluation.get("manifest_sha256") == contract.get("held_out_evaluation_manifest_sha256"), "canonical arbitrary-m evaluation provenance differs")


def expected_bootstrap_paths(contract: dict[str, Any]) -> list[tuple[int, int, int, str]]:
    values = [(16, challenger, seed, f"m{challenger}-minus-m16-seed{seed}.json") for challenger in contract["m_values"] if challenger != 16 for seed in contract["seeds"]]
    values.extend((18, 19, seed, f"m19-minus-m18-seed{seed}.json") for seed in contract["seeds"])
    require(len(values) == 35 and len({value[3] for value in values}) == 35, "canonical arbitrary-m bootstrap matrix differs")
    return values


def collect(args: Any) -> dict[str, bytes]:
    contract = runner.load_contract(args.contract)
    calibration = runner.shared.load_root(args.calibration_root)
    evaluation = runner.shared.load_root(args.evaluation_root)
    runner.shared.validate_calibration_evaluation_pair(calibration, evaluation)
    contract_snapshot = snapshot(args.measured_source_ref, "tools/agent-memory-bench/mih-canonical-arbitrary-m-frontier.example.json")
    validate_binding(args.contract, contract_snapshot, contract, calibration, evaluation)
    diagnostic_snapshot = snapshot(args.diagnostic_source_ref, "tools/agent-memory-bench/mih-canonical-arbitrary-m-frontier-diagnostics.example.json")
    require(args.diagnostic_contract.read_bytes() == diagnostic_snapshot, "canonical arbitrary-m diagnostic contract snapshot differs")
    require(bootstrap.load_diagnostics(args.diagnostic_contract, contract, args.contract) == [(18, 19)], "canonical arbitrary-m diagnostic contract differs")
    expected_sources = {name: sha256_bytes(snapshot(args.measured_source_ref, f"tools/agent-memory-bench/{name}")) for name in SOURCE_NAMES}
    matrix_path = args.matrix_root / "matrix-manifest.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    require(matrix.get("schema_version") == 1 and matrix.get("family") == runner.FAMILY and matrix.get("contract_sha256") == sha256(args.contract) and matrix.get("source_files_sha256") == {name: expected_sources[name] for name in runner.source_files()} and matrix.get("source_bundle_sha256") == digest({name: expected_sources[name] for name in runner.source_files()}), "canonical arbitrary-m matrix provenance differs")
    files: dict[str, bytes] = {
        "bundle/contract.json": args.contract.read_bytes(),
        "bundle/diagnostic-contract.json": args.diagnostic_contract.read_bytes(),
        "bundle/matrix-manifest.json": matrix_path.read_bytes(),
        "bundle/experiment-note.md": args.note.read_bytes(),
    }
    manifest_rows = []
    for row in runner.rows(contract):
        report_path = args.matrix_root / "reports" / f"{row['id']}.json"
        contribution_path = args.matrix_root / "contributions" / f"{row['id']}.npz"
        require(runner.complete(args.matrix_root, row, contract, calibration, evaluation), f"invalid canonical arbitrary-m row: {row['id']}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        require(report.get("evaluator_source_files_sha256") == {name: expected_sources[name] for name in ("evaluate-mih-banding.py", "evaluate-projection-quantization.py")}, f"evaluator source snapshot differs: {row['id']}")
        manifest_rows.append({"id": row["id"], "treatment": row["treatment"]["id"], "seed": row["seed"], "report_sha256": sha256(report_path), "contribution_sha256": sha256(contribution_path)})
        files[f"bundle/reports/{report_path.name}"] = report_path.read_bytes()
        files[f"bundle/contributions/{contribution_path.name}"] = contribution_path.read_bytes()
    require(matrix.get("rows") == manifest_rows, "canonical arbitrary-m matrix row manifest differs")
    expected_bootstrap_sources = {
        "bootstrap-mih-canonical-arbitrary-m-frontier.py": sha256_bytes(snapshot(args.bootstrap_source_ref, "tools/agent-memory-bench/bootstrap-mih-canonical-arbitrary-m-frontier.py")),
        **{name: expected_sources[name] for name in ("run-mih-canonical-arbitrary-m-frontier.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py")},
    }
    for control_m, challenger_m, seed, name in expected_bootstrap_paths(contract):
        path = args.bootstrap_root / name
        expected = bootstrap.bootstrap_report(contract, control_m, challenger_m, seed, args.matrix_root / "contributions" / f"m{control_m}-minimum-probe-r56-seed{seed}.npz", args.matrix_root / "contributions" / f"m{challenger_m}-minimum-probe-r56-seed{seed}.npz")
        value = json.loads(path.read_text(encoding="utf-8"))
        require(expected.get("bootstrap_source_files_sha256") == expected_bootstrap_sources and expected.get("bootstrap_source_bundle_sha256") == bootstrap.source_bundle(expected_bootstrap_sources) and value == expected, f"canonical arbitrary-m bootstrap replay differs: {name}")
        files[f"bundle/bootstrap/{name}"] = path.read_bytes()
    for name in SOURCE_NAMES:
        files[f"bundle/sources/{name}"] = snapshot(args.measured_source_ref, f"tools/agent-memory-bench/{name}")
    files["bundle/sources/bootstrap-mih-canonical-arbitrary-m-frontier.py"] = snapshot(args.bootstrap_source_ref, "tools/agent-memory-bench/bootstrap-mih-canonical-arbitrary-m-frontier.py")
    files["bundle/sources/mih-canonical-arbitrary-m-frontier-diagnostics.example.json"] = diagnostic_snapshot
    files[f"bundle/sources/{THIS.name}"] = THIS.read_bytes()
    return files


def package(args: Any) -> None:
    files = collect(args)
    members = {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}
    root = digest({name: entry["sha256"] for name, entry in members.items()})
    files["bundle/evidence-manifest.json"] = (json.dumps({"schema_version": 1, "family": "mih_canonical_arbitrary_m_frontier_evidence_v1", "measured_source_ref": args.measured_source_ref, "bundle_root_sha256": root, "members": members}, indent=2, sort_keys=True) + "\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name); info.date_time = (1980, 1, 1, 0, 0, 0); info.external_attr = 0o100644 << 16
            archive.writestr(info, value)
    with zipfile.ZipFile(args.output) as archive:
        require(set(archive.namelist()) == set(files), "canonical arbitrary-m evidence member set differs")
        for name, value in files.items():
            require(archive.read(name) == value, f"canonical arbitrary-m evidence member differs: {name}")
    print(json.dumps({"archive_sha256": sha256(args.output), "bundle_root_sha256": root}, sort_keys=True))


def self_test() -> int:
    try:
        require(digest({"a": "b", "c": "d"}) == digest({"c": "d", "a": "b"}), "canonical digest differs")
        with tempfile.TemporaryDirectory() as directory:
            contract_path = Path(directory) / "contract.json"; contract_path.write_bytes(b"contract")
            contract = {"training_materialization_manifest_sha256": "calibration", "held_out_evaluation_manifest_sha256": "evaluation"}
            calibration, evaluation = {"manifest_sha256": "calibration"}, {"manifest_sha256": "evaluation"}
            validate_binding(contract_path, b"contract", contract, calibration, evaluation)
            try: validate_binding(contract_path, b"changed", contract, calibration, evaluation)
            except ValueError: pass
            else: raise ValueError("contract mutation was accepted")
            path = Path(directory) / "archive.zip"
            with zipfile.ZipFile(path, "w") as archive: archive.writestr("bundle/a", b"a")
            with zipfile.ZipFile(path) as archive: require(archive.read("bundle/a") == b"a", "archive reopen differs")
    except (OSError, ValueError):
        print("MIH canonical arbitrary-m frontier evidence packager self-test failed", file=sys.stderr)
        return 1
    print("MIH canonical arbitrary-m frontier evidence packager self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--contract", type=Path); parser.add_argument("--diagnostic-contract", type=Path); parser.add_argument("--matrix-root", type=Path); parser.add_argument("--bootstrap-root", type=Path); parser.add_argument("--calibration-root", type=Path); parser.add_argument("--evaluation-root", type=Path); parser.add_argument("--note", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--measured-source-ref"); parser.add_argument("--bootstrap-source-ref"); parser.add_argument("--diagnostic-source-ref")
    args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test()
        require(all((args.contract, args.diagnostic_contract, args.matrix_root, args.bootstrap_root, args.calibration_root, args.evaluation_root, args.note, args.output, args.measured_source_ref, args.bootstrap_source_ref, args.diagnostic_source_ref)), "canonical arbitrary-m evidence packager arguments are required")
        package(args); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"write-mih-canonical-arbitrary-m-frontier-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
