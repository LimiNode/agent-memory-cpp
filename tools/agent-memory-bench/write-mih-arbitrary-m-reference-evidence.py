#!/usr/bin/env python3
"""Fail-closed evidence packager for the arbitrary-m MIH reference matrix."""

from __future__ import annotations

import argparse
import hashlib
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


def require(condition: bool, message: str) -> None:
    if not condition: raise ValueError(message)


def sha256_bytes(value: bytes) -> str: return hashlib.sha256(value).hexdigest()
def sha256(path: Path) -> str: return sha256_bytes(path.read_bytes())
def digest(entries: dict[str, str]) -> str: return sha256_bytes(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode())


def git_snapshot(ref: str, relative: str) -> bytes:
    return subprocess.run(["git", "show", f"{ref}:{relative}"], cwd=ROOT, check=True, capture_output=True).stdout


def validate_contract_binding(contract_bytes: bytes, measured_contract_bytes: bytes, contract: dict[str, Any], calibration: dict[str, Any], evaluation: dict[str, Any]) -> None:
    """Bind the supplied experiment inputs to the historical measured contract."""
    require(contract_bytes == measured_contract_bytes, "arbitrary-m contract bytes differ from measured source")
    require(calibration.get("manifest_sha256") == contract.get("training_materialization_manifest_sha256"), "arbitrary-m calibration materialization differs")
    require(evaluation.get("manifest_sha256") == contract.get("held_out_evaluation_manifest_sha256"), "arbitrary-m evaluation materialization differs")


def load_runner() -> Any:
    import importlib.util
    path = THIS.with_name("run-mih-arbitrary-m-reference.py"); spec = importlib.util.spec_from_file_location("arbitrary_m_packager_runner", path)
    if spec is None or spec.loader is None: raise RuntimeError("cannot load arbitrary-m runner")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module); return module


runner = load_runner()


def load_bootstrap() -> Any:
    import importlib.util
    path = THIS.with_name("bootstrap-mih-arbitrary-m-reference.py"); spec = importlib.util.spec_from_file_location("arbitrary_m_packager_bootstrap", path)
    if spec is None or spec.loader is None: raise RuntimeError("cannot load arbitrary-m bootstrap")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module); return module


bootstrap_module = load_bootstrap()


def collect(args: Any) -> dict[str, bytes]:
    contract_bytes = args.contract.read_bytes(); measured_contract_bytes = git_snapshot(args.measured_source_ref, "tools/agent-memory-bench/mih-arbitrary-m-reference.example.json")
    contract = runner.load_contract(args.contract); calibration = runner.shared.load_root(args.calibration_root); evaluation = runner.shared.load_root(args.evaluation_root)
    validate_contract_binding(contract_bytes, measured_contract_bytes, contract, calibration, evaluation); runner.shared.validate_calibration_evaluation_pair(calibration, evaluation)
    manifest_path = args.matrix_root / "matrix-manifest.json"; matrix = json.loads(manifest_path.read_text(encoding="utf-8")); expected_sources = {name: sha256_bytes(git_snapshot(args.measured_source_ref, f"tools/agent-memory-bench/{name}")) for name in ("run-mih-arbitrary-m-reference.py", "mih-arbitrary-m-reference.example.json", "evaluate-mih-banding.py", "evaluate-projection-quantization.py")}; expected_bootstrap_sources = {name: sha256_bytes(git_snapshot(args.measured_source_ref, f"tools/agent-memory-bench/{name}")) for name in ("bootstrap-mih-arbitrary-m-reference.py", "run-mih-arbitrary-m-reference.py", "evaluate-projection-quantization.py")}
    require(matrix.get("schema_version") == 1 and matrix.get("family") == runner.FAMILY and matrix.get("contract_sha256") == sha256(args.contract) and matrix.get("source_files_sha256") == expected_sources and matrix.get("source_bundle_sha256") == digest(expected_sources), "arbitrary-m matrix provenance differs")
    expected_rows = runner.rows(contract); expected_manifest_rows = []
    for row in expected_rows:
        report_path = args.matrix_root / "reports" / f"{row['id']}.json"; contribution_path = args.matrix_root / "contributions" / f"{row['id']}.npz"; require(runner.complete(args.matrix_root, row, contract, calibration, evaluation), f"invalid arbitrary-m row: {row['id']}")
        report = json.loads(report_path.read_text(encoding="utf-8")); require(report.get("evaluator_source_files_sha256") == {name: expected_sources[name] for name in ("evaluate-mih-banding.py", "evaluate-projection-quantization.py")}, f"evaluator snapshot differs: {row['id']}")
        expected_manifest_rows.append({"id": row["id"], "treatment": row["treatment"]["id"], "seed": row["seed"], "report_sha256": sha256(report_path), "contribution_sha256": sha256(contribution_path)})
    require(matrix.get("rows") == expected_manifest_rows, "arbitrary-m matrix row manifest differs")
    files: dict[str, bytes] = {"bundle/contract.json": args.contract.read_bytes(), "bundle/matrix-manifest.json": manifest_path.read_bytes()}
    for row in expected_rows:
        files[f"bundle/reports/{row['id']}.json"] = (args.matrix_root / "reports" / f"{row['id']}.json").read_bytes(); files[f"bundle/contributions/{row['id']}.npz"] = (args.matrix_root / "contributions" / f"{row['id']}.npz").read_bytes()
        bootstrap_path = args.bootstrap_root / f"m19-minus-m16-seed{row['seed']}.json"
        if row["treatment"]["id"] == "m19-uniform-radius2":
            value = json.loads(bootstrap_path.read_text(encoding="utf-8")); control = args.matrix_root / "contributions" / f"m16-canonical-r56-seed{row['seed']}.npz"; challenger = args.matrix_root / "contributions" / f"m19-uniform-radius2-seed{row['seed']}.npz"; expected_bootstrap = bootstrap_module.bootstrap_report(contract, row["seed"], control, challenger)
            require(expected_bootstrap.get("bootstrap_source_files_sha256") == expected_bootstrap_sources and expected_bootstrap.get("bootstrap_source_bundle_sha256") == bootstrap_module.source_bundle(expected_bootstrap_sources) and value == expected_bootstrap, f"bootstrap replay differs: seed{row['seed']}")
            files[f"bundle/bootstrap/{bootstrap_path.name}"] = bootstrap_path.read_bytes()
    for name in sorted(set(expected_sources) | set(expected_bootstrap_sources)):
        files[f"bundle/sources/{name}"] = git_snapshot(args.measured_source_ref, f"tools/agent-memory-bench/{name}")
    files[f"bundle/sources/{THIS.name}"] = THIS.read_bytes(); files["bundle/experiment-note.md"] = args.note.read_bytes()
    return files


def package(args: Any) -> None:
    files = collect(args); members = {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}; root = digest({name: item["sha256"] for name, item in members.items()}); manifest = {"schema_version": 3, "family": "mih_arbitrary_m_reference_evidence_v3", "measured_source_ref": args.measured_source_ref, "bundle_root_sha256": root, "members": members}; files["bundle/evidence-manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name); info.date_time = (1980, 1, 1, 0, 0, 0); info.external_attr = 0o100644 << 16; archive.writestr(info, value)
    with zipfile.ZipFile(args.output) as archive:
        require(set(archive.namelist()) == set(files), "evidence archive member set differs")
        for name, value in files.items(): require(archive.read(name) == value, f"evidence archive member differs: {name}")
    print(json.dumps({"archive_sha256": sha256(args.output), "bundle_root_sha256": root}, sort_keys=True))


def self_test() -> int:
    try:
        require(digest({"a": "b", "c": "d"}) == digest({"c": "d", "a": "b"}), "canonical bundle digest differs")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.zip"; contents = {"bundle/a": b"a"}
            with zipfile.ZipFile(path, "w") as archive: archive.writestr("bundle/a", contents["bundle/a"])
            with zipfile.ZipFile(path) as archive: require(archive.read("bundle/a") == b"a", "archive reopen differs")
        sources = bootstrap_module.source_files(); require(bootstrap_module.source_bundle(sources) == digest(sources), "bootstrap source bundle differs")
        contract = {"training_materialization_manifest_sha256": "a" * 64, "held_out_evaluation_manifest_sha256": "b" * 64}; calibration = {"manifest_sha256": "a" * 64}; evaluation = {"manifest_sha256": "b" * 64}
        validate_contract_binding(b"contract", b"contract", contract, calibration, evaluation)
        for contract_bytes, checked_contract, checked_calibration, checked_evaluation in ((b"mutated", b"contract", calibration, evaluation), (b"contract", b"contract", {"manifest_sha256": "c" * 64}, evaluation), (b"contract", b"contract", calibration, {"manifest_sha256": "c" * 64})):
            try: validate_contract_binding(contract_bytes, checked_contract, contract, checked_calibration, checked_evaluation)
            except ValueError: pass
            else: raise ValueError("contract/materialization mutation was accepted")
    except (OSError, ValueError):
        print("MIH arbitrary-m evidence packager self-test failed", file=sys.stderr); return 1
    print("MIH arbitrary-m evidence packager self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--contract", type=Path); parser.add_argument("--matrix-root", type=Path); parser.add_argument("--bootstrap-root", type=Path); parser.add_argument("--calibration-root", type=Path); parser.add_argument("--evaluation-root", type=Path); parser.add_argument("--note", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--measured-source-ref") ; args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test()
        require(all((args.contract, args.matrix_root, args.bootstrap_root, args.calibration_root, args.evaluation_root, args.note, args.output, args.measured_source_ref)), "evidence packager arguments are required"); package(args); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error: print(f"write-mih-arbitrary-m-reference-evidence: {error}", file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
