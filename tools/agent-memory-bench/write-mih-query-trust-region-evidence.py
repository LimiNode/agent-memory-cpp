#!/usr/bin/env python3
"""Package a fail-closed trust-region train-validation gate result."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

THIS = Path(__file__).resolve(); ROOT = THIS.parents[2]
SOURCE_NAMES = (THIS.name, "run-mih-query-trust-region.py", "train-mih-query-trust-region.py", "mih-query-trust-region.example.json", "evaluate-mih-banding.py", "evaluate-projection-quantization.py", "train-nlb-qrels-supervised.py", "requirements-binary-autoencoder-trainer.txt", "write-mih-rerank-cost-evidence.py")


def load(name: str, key: str) -> Any:
    spec = importlib.util.spec_from_file_location(key, THIS.with_name(name)); assert spec and spec.loader
    module = importlib.util.module_from_spec(spec); sys.modules[key] = module; spec.loader.exec_module(module); return module


archive = load("write-mih-rerank-cost-evidence.py", "trust_region_archive")
runner = load("run-mih-query-trust-region.py", "trust_region_writer_runner")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def snapshot_sources(commit: str, directory: Path) -> list[tuple[Path, str]]:
    require(len(commit) == 40 and all(value in "0123456789abcdef" for value in commit), "source commit must be a full lowercase SHA-1")
    files: list[tuple[Path, str]] = []
    for name in SOURCE_NAMES:
        result = subprocess.run(("git", "show", f"{commit}:tools/agent-memory-bench/{name}"), cwd=ROOT, capture_output=True)
        require(result.returncode == 0 and result.stdout, f"source commit does not contain {name}")
        path = directory / name; path.write_bytes(result.stdout); files.append((path, f"bundle/sources/{name}"))
    return files


def source_hashes_at_commit(commit: str, names: tuple[str, ...]) -> dict[str, str]:
    require(len(commit) == 40 and all(value in "0123456789abcdef" for value in commit), "matrix source commit must be a full lowercase SHA-1")
    values: dict[str, str] = {}
    for name in names:
        result = subprocess.run(("git", "show", f"{commit}:tools/agent-memory-bench/{name}"), cwd=ROOT, capture_output=True)
        require(result.returncode == 0 and result.stdout, f"matrix source commit does not contain {name}")
        values[name] = hashlib.sha256(result.stdout).hexdigest()
    return values


def make_bundle(args: Any) -> dict[str, str]:
    contract = runner.load_contract(args.contract)
    manifest_path = args.matrix_root / "matrix-manifest.json"; manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    matrix_sources = source_hashes_at_commit(args.matrix_source_commit, tuple(runner.source_files()))
    trainer_sources = source_hashes_at_commit(args.matrix_source_commit, tuple(runner.trainer.source_hashes()))
    require(manifest.get("family") == runner.FAMILY and manifest.get("contract_sha256") == sha256(args.contract) and manifest.get("outcome") in ("gate_rejected", "mixed_gate_rejected") and manifest.get("held_out_execution") == "forbidden_without_all_five_pareto_admissible_checkpoints_v1" and manifest.get("source_files_sha256") == matrix_sources and manifest.get("source_bundle_sha256") == runner.source_bundle(matrix_sources), "gate matrix provenance differs")
    rows = manifest.get("rows", []); require(isinstance(rows, list) and len(rows) == 5 and {row.get("status") for row in rows}.issubset({"gate_rejected", "accepted"}), "gate matrix outcome differs")
    diagnostic_path = args.matrix_root / "gate-failure-diagnostic.json"
    expected_diagnostic = runner.diagnostic(args.matrix_root, contract)
    actual_diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    require(actual_diagnostic == expected_diagnostic, "gate-failure diagnostic differs")
    files: list[tuple[Path, str]] = [(args.contract, "bundle/contract.json"), (manifest_path, "bundle/matrix-manifest.json"), (diagnostic_path, "bundle/gate-failure-diagnostic.json")]
    for seed in contract["seeds"]:
        directory = runner.output_dir(args.matrix_root, seed); history = directory / "training-history.json"; rejection = directory / "gate-rejection.json"; artifact = directory / "artifact.json"
        row = next(value for value in rows if value["seed"] == seed)
        status = runner.row_status(args.matrix_root, contract, args.training_materialization_root, seed, trainer_sources)
        require(status == row, f"gate replay differs: seed{seed}")
        require(row.get("history_sha256") == sha256(history), f"gate history digest differs: seed{seed}")
        files.append((history, f"bundle/artifacts/seed{seed}/training-history.json"))
        if row.get("status") == "gate_rejected":
            require(row.get("gate_rejection_sha256") == sha256(rejection), f"gate rejection digest differs: seed{seed}")
            files.append((rejection, f"bundle/artifacts/seed{seed}/gate-rejection.json"))
        else:
            require(row.get("artifact_sha256") == sha256(artifact), f"accepted artifact digest differs: seed{seed}")
            files.append((artifact, f"bundle/artifacts/seed{seed}/artifact.json"))
            for name in ("projection-weights.f32", "query-projection-weights.f32", "thresholds.f32"):
                files.append((directory / name, f"bundle/artifacts/seed{seed}/{name}"))
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary); files.extend(snapshot_sources(args.source_commit, directory))
        compact = directory / "compact-manifest.json"; compact.write_text(json.dumps({"schema_version": 2, "family": "mih_query_trust_region_gate_evidence_v2", "source_commit": args.source_commit, "matrix_execution_source_commit": args.matrix_source_commit, "contract_sha256": sha256(args.contract), "matrix_manifest_sha256": sha256(manifest_path), "gate_failure_diagnostic_sha256": sha256(diagnostic_path), "outcome": manifest["outcome"], "held_out_execution": "forbidden_without_all_five_pareto_admissible_checkpoints_v1", "source_snapshot_policy": "git_show_exact_evidence_commit_v1"}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        files.append((compact, "bundle/compact-manifest.json")); archive_manifest = archive.archive_manifest(files); archive_manifest["family"] = "mih_query_trust_region_gate_evidence_v2"; args.output.parent.mkdir(parents=True, exist_ok=True); archive.write_archive(args.output, files, archive_manifest)
    return {"sha256": sha256(args.output), "bundle_root_sha256": archive_manifest["bundle_root_sha256"]}


def self_test() -> int:
    try:
        if archive.self_test() != 0: return 1
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"write-mih-query-trust-region-evidence self-test failed: {error}", file=sys.stderr); return 1
    print("MIH query trust-region evidence packager self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--contract", type=Path); parser.add_argument("--matrix-root", type=Path); parser.add_argument("--training-materialization-root", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--source-commit"); parser.add_argument("--matrix-source-commit"); args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test()
        require(all((args.contract, args.matrix_root, args.training_materialization_root, args.output, args.source_commit, args.matrix_source_commit)), "evidence paths are required")
        print(json.dumps(make_bundle(args), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(f"write-mih-query-trust-region-evidence: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
