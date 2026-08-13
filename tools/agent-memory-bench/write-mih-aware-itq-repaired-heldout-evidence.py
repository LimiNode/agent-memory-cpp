#!/usr/bin/env python3
"""Replay-validate and package the repaired-ITQ held-out frontier."""

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
from types import SimpleNamespace
from typing import Any

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()


def load(name: str, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, THIS.with_name(name))
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module); return module


runner = load("run-mih-aware-itq-repaired-heldout-frontier.py", "repaired_frontier_evidence_runner")
bootstrap = load("bootstrap-mih-aware-itq-repaired-heldout-frontier.py", "repaired_frontier_evidence_bootstrap")
archive = load("write-mih-rerank-cost-evidence.py", "repaired_frontier_evidence_archive")


def require(value: bool, message: str) -> None:
    if not value: raise ValueError(message)
def sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def git_snapshot(ref: str, name: str) -> bytes:
    return subprocess.run(("git", "show", f"{ref}:tools/agent-memory-bench/{name}"), cwd=THIS.parents[2], check=True, capture_output=True).stdout
def resolve(ref: str) -> str: return subprocess.run(("git", "rev-parse", "--verify", f"{ref}^{{commit}}"), cwd=THIS.parents[2], check=True, capture_output=True, text=True).stdout.strip()


def replay_row(root: Path, row: dict[str, Any], contract: dict[str, Any], calibration: dict[str, Any], evaluation: dict[str, Any]) -> tuple[Path, Path, dict[str, Any]]:
    report_path = root / "reports" / f"{row['id']}.json"; contribution = root / "contributions" / f"{row['id']}.npz"; require(runner.complete(root, row, contract, calibration, evaluation), f"repaired held-out row differs: {row['id']}")
    report = json.loads(report_path.read_text(encoding="utf-8")); values = bootstrap.load_contribution(contribution); require(report["per_query_contributions_sha256"] == sha256(contribution), f"contribution digest differs: {row['id']}")
    survival = report["e5_oracle_survival"]; require(survival["raw_union"] == float(numpy.mean(values["e5_oracle_raw_union_coverage"])) and survival["hamming_top_k"] == float(numpy.mean(values["e5_oracle_hamming_top_k_coverage"])) and survival["second_stage"] == float(numpy.mean(values["e5_oracle_second_stage_coverage"])) and survival["mean_full_hamming_distance"] == float(numpy.mean(values["e5_oracle_mean_full_hamming_distance"])) and survival["hamming_within_radius"] == {str(radius): float(numpy.mean(values[f"e5_oracle_hamming_within_{radius}"])) for radius in contract["diagnostics"]["oracle_hamming_thresholds"]}, f"report survival replay differs: {row['id']}")
    return report_path, contribution, report


def make_bundle(args: Any) -> dict[str, Any]:
    contract = runner.load_contract(args.contract); calibration = runner.shared.load_root(args.calibration_root); evaluation = runner.shared.load_root(args.evaluation_root); runner.shared.validate_calibration_evaluation_pair(calibration, evaluation); matrix = runner.rows(contract); manifest_path = args.matrix_root / "matrix-manifest.json"; manifest = json.loads(manifest_path.read_text(encoding="utf-8")); source_ref = resolve(args.measured_source_ref); names = ("run-mih-aware-itq-repaired-heldout-frontier.py", "train-mih-aware-itq-repaired.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py", "bootstrap-mih-aware-itq-repaired-heldout-frontier.py", "requirements-learned-binary-adc-trainer.txt")
    runner_names = ("run-mih-aware-itq-repaired-heldout-frontier.py", "train-mih-aware-itq-repaired.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py", "requirements-learned-binary-adc-trainer.txt")
    source_hashes = {name: hashlib.sha256(git_snapshot(source_ref, name)).hexdigest() for name in runner_names}; require(manifest == {"schema_version": 1, "family": runner.FAMILY, "contract_sha256": sha256(args.contract), "calibration_materialization_manifest_sha256": calibration["manifest_sha256"], "evaluation_materialization_manifest_sha256": evaluation["manifest_sha256"], "source_files_sha256": source_hashes, "source_bundle_sha256": runner.source_bundle(source_hashes), "rows": manifest["rows"]}, "matrix manifest provenance differs")
    expected_entries = []; reports: list[tuple[Path, Path]] = []
    for row in matrix:
        report_path, contribution, _ = replay_row(args.matrix_root, row, contract, calibration, evaluation); entry = {"id": row["id"], "encoder": row["encoder"], "regime": row["regime"]["id"], "seed": row["seed"], "report_sha256": sha256(report_path), "contribution_sha256": sha256(contribution), "artifact_sha256": runner.artifact_weights(runner.artifact_path(args.matrix_root, row["seed"]), calibration, row["seed"], contract) if row["encoder"] == "repaired-control" else None}; expected_entries.append(entry); reports.append((report_path, contribution))
    require(manifest["rows"] == expected_entries, "matrix rows differ")
    args.bootstrap_root.mkdir(parents=True, exist_ok=True); comparisons: list[Path] = []
    for seed in contract["encoding"]["seeds"]:
        for regime in contract["index_regimes"]:
            left = args.matrix_root / "contributions" / f"itq-control--{regime['id']}-seed{seed}.npz"; right = args.matrix_root / "contributions" / f"repaired-control--{regime['id']}-seed{seed}.npz"; output = args.bootstrap_root / f"itq-control-vs-repaired-control--{regime['id']}-seed{seed}.json"; parameters = SimpleNamespace(left_contributions=left, right_contributions=right, output=output, comparison_id=output.stem, replicates=10000, seed=contract["bootstrap"]["seed"]); bootstrap.bootstrap(parameters); actual = json.loads(output.read_text(encoding="utf-8")); expected = bootstrap.expected(parameters, bootstrap.load_contribution(left), bootstrap.load_contribution(right)); require(actual == expected, f"bootstrap replay differs: {output.name}"); comparisons.append(output)
    require({path.name for path in args.bootstrap_root.glob("*.json")} == {path.name for path in comparisons} and len(comparisons) == 20, "bootstrap matrix differs")
    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory); compact = {"schema_version": 1, "family": "mih_aware_itq_repaired_heldout_frontier_evidence_v1", "contract_sha256": sha256(args.contract), "matrix_manifest_sha256": sha256(manifest_path), "measured_source_commit": source_ref, "rows": expected_entries, "bootstrap": [{"file": path.name, "sha256": sha256(path)} for path in comparisons]}; compact_path = stage / "compact-manifest.json"; compact_path.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        files: list[tuple[Path, str]] = [(args.contract, "bundle/contract.json"), (manifest_path, "bundle/matrix-manifest.json"), (compact_path, "bundle/compact-manifest.json")]
        files += [(report, f"bundle/reports/{report.name}") for report, _ in reports] + [(contribution, f"bundle/contributions/{contribution.name}") for _, contribution in reports]
        for seed in contract["encoding"]["seeds"]:
            root = runner.artifact_path(args.matrix_root, seed).parent; files += [(root / name, f"bundle/artifacts/{root.name}/{name}") for name in ("artifact.json", "projection-weights.f32", "thresholds.f32")]
        files += [(path, f"bundle/bootstrap/{path.name}") for path in comparisons]
        for name in names:
            snapshot = stage / "measured" / name; snapshot.parent.mkdir(parents=True, exist_ok=True); snapshot.write_bytes(git_snapshot(source_ref, name)); files.append((snapshot, f"bundle/measured-sources/{name}"))
        validator_names = (Path(__file__).name, "run-mih-aware-itq-repaired-heldout-frontier.py", "bootstrap-mih-aware-itq-repaired-heldout-frontier.py", "write-mih-rerank-cost-evidence.py", "evaluate-projection-quantization.py"); files += [(THIS.with_name(name), f"bundle/validator-sources/{name}") for name in validator_names]; archive_manifest = archive.archive_manifest(files); archive_manifest["family"] = "mih_aware_itq_repaired_heldout_frontier_evidence_v1"; args.output.parent.mkdir(parents=True, exist_ok=True); archive.write_archive(args.output, files, archive_manifest)
    return {"archive": str(args.output), "sha256": sha256(args.output), "bundle_root_sha256": archive_manifest["bundle_root_sha256"], "rows": len(matrix), "comparisons": len(comparisons)}


def self_test() -> int:
    try:
        require(runner.load_contract(THIS.with_name("mih-aware-itq-repaired-heldout-frontier.example.json")) == runner.CONTRACT, "contract differs")
        if archive.self_test() != 0: return 1
    except (OSError, ValueError, json.JSONDecodeError) as error: print(f"write-mih-aware-itq-repaired-heldout-evidence self-test failed: {error}", file=sys.stderr); return 1
    print("MIH-aware ITQ repaired held-out evidence packager self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--matrix-root", type=Path); parser.add_argument("--contract", type=Path); parser.add_argument("--calibration-root", type=Path); parser.add_argument("--evaluation-root", type=Path); parser.add_argument("--bootstrap-root", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--measured-source-ref"); args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test()
        require(all((args.matrix_root, args.contract, args.calibration_root, args.evaluation_root, args.bootstrap_root, args.output, args.measured_source_ref)), "evidence paths are required"); print(json.dumps(make_bundle(args), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError, zipfile.BadZipFile, runner.shared.EvaluationError) as error: print(f"write-mih-aware-itq-repaired-heldout-evidence: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
