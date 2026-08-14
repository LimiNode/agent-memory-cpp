#!/usr/bin/env python3
"""Package fail-closed evidence for the schedule-aware routing gate."""

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

THIS = Path(__file__.resolve()); ROOT = THIS.parents[2]
SOURCES = (THIS.name, "run-mih-schedule-aware-routing.py", "mih-schedule-aware-routing.example.json", "train-mih-query-trust-region.py", "run-mih-query-trust-region.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py", "train-nlb-qrels-supervised.py", "requirements-binary-autoencoder-trainer.txt", "write-mih-rerank-cost-evidence.py")


def load(name: str, key: str) -> Any:
    spec = importlib.util.spec_from_file_location(key, THIS.with_name(name)); assert spec and spec.loader
    module = importlib.util.module_from_spec(spec); sys.modules[key] = module; spec.loader.exec_module(module); return module


runner = load("run-mih-schedule-aware-routing.py", "schedule_evidence_runner")
archive = load("write-mih-rerank-cost-evidence.py", "schedule_evidence_archive")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def snapshot(commit: str, directory: Path) -> list[tuple[Path, str]]:
    require(len(commit) == 40 and all(value in "0123456789abcdef" for value in commit), "source commit must be a full lowercase SHA-1")
    result: list[tuple[Path, str]] = []
    for name in SOURCES:
        shown = subprocess.run(("git", "show", f"{commit}:tools/agent-memory-bench/{name}"), cwd=ROOT, capture_output=True)
        require(shown.returncode == 0 and shown.stdout, f"source commit does not contain {name}")
        path = directory / name; path.write_bytes(shown.stdout); result.append((path, f"bundle/sources/{name}"))
    return result


def make_bundle(args: Any) -> dict[str, str]:
    contract = runner.load_contract(args.contract)
    manifest_path = args.matrix_root / "matrix-manifest.json"; manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("family") == runner.FAMILY and manifest.get("contract_sha256") == sha256(args.contract) and manifest.get("training_materialization_manifest_sha256") == contract["training_materialization_manifest_sha256"] and manifest.get("source_files_sha256") == runner.source_files() and manifest.get("source_bundle_sha256") == runner.source_bundle(runner.source_files()) and manifest.get("outcome") in ("gate_rejected", "mixed_gate_rejected") and manifest.get("held_out_execution") == "forbidden_without_all_five_pareto_admissible_checkpoints_v1", "schedule-aware matrix provenance differs")
    rows = manifest.get("rows", []); require(isinstance(rows, list) and len(rows) == 5, "schedule-aware rows differ")
    files: list[tuple[Path, str]] = [(args.contract, "bundle/contract.json"), (manifest_path, "bundle/matrix-manifest.json")]
    for seed in contract["seeds"]:
        row = next((value for value in rows if value.get("seed") == seed), None)
        status = runner.status(args.matrix_root, contract, args.training_materialization_root, seed)
        require(row == status and status is not None, f"schedule-aware gate replay differs: seed{seed}")
        directory = runner.output_dir(args.matrix_root, seed); history = directory / "training-history.json"; files.append((history, f"bundle/artifacts/seed{seed}/training-history.json"))
        if status["status"] == "accepted":
            artifact = directory / "artifact.json"; files.append((artifact, f"bundle/artifacts/seed{seed}/artifact.json"))
            for name in ("projection-weights.f32", "query-projection-weights.f32", "thresholds.f32"):
                files.append((directory / name, f"bundle/artifacts/seed{seed}/{name}"))
        else:
            rejection = directory / "gate-rejection.json"; files.append((rejection, f"bundle/artifacts/seed{seed}/gate-rejection.json"))
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary); files.extend(snapshot(args.source_commit, directory))
        compact = directory / "compact-manifest.json"; compact.write_text(json.dumps({"schema_version": 1, "family": "mih_schedule_aware_routing_gate_evidence_v1", "source_commit": args.source_commit, "contract_sha256": sha256(args.contract), "matrix_manifest_sha256": sha256(manifest_path), "outcome": manifest["outcome"], "held_out_execution": manifest["held_out_execution"], "source_snapshot_policy": "git_show_exact_evidence_commit_v1"}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        files.append((compact, "bundle/compact-manifest.json")); archive_manifest = archive.archive_manifest(files); archive_manifest["family"] = "mih_schedule_aware_routing_gate_evidence_v1"; args.output.parent.mkdir(parents=True, exist_ok=True); archive.write_archive(args.output, files, archive_manifest)
    return {"sha256": sha256(args.output), "bundle_root_sha256": archive_manifest["bundle_root_sha256"]}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path); parser.add_argument("--matrix-root", type=Path); parser.add_argument("--training-materialization-root", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--source-commit"); args = parser.parse_args(argv)
    try:
        require(all((args.contract, args.matrix_root, args.training_materialization_root, args.output, args.source_commit)), "evidence paths are required")
        print(json.dumps(make_bundle(args), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(f"write-mih-schedule-aware-routing-evidence: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
