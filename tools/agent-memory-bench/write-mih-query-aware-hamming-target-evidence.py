#!/usr/bin/env python3
"""Validate and package query-aware Hamming-target evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy

THIS = Path(__file__).resolve()
ENCODERS = ("itq-control", "query-aware-hamming-target")
SEEDS = (52, 53, 54, 55, 56)


def load(name: str, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, THIS.with_name(name))
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[module_name] = module; spec.loader.exec_module(module); return module


archive = load("write-mih-rerank-cost-evidence.py", "query_aware_evidence_archive")
runner = load("run-mih-query-aware-hamming-target.py", "query_aware_evidence_runner")
bootstrap = load("bootstrap-mih-query-aware-hamming-target.py", "query_aware_evidence_bootstrap")


def sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def require(value: bool, message: str) -> None:
    if not value: raise ValueError(message)


def validate(root: Path, contract: Path) -> dict[str, Any]:
    value = runner.load_contract(contract); manifest_path = root / "matrix-manifest.json"; manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("family") == runner.FAMILY and manifest.get("contract_sha256") == sha256(contract) and len(manifest.get("rows", [])) == 10, "matrix manifest differs")
    row_map = {row["id"]: row for row in manifest["rows"]}
    for seed in SEEDS:
        for encoder in ENCODERS:
            row_id = f"{encoder}--16x16-r56-seed{seed}"; row = {"id": row_id, "encoder": encoder, "seed": seed}
            calibration = archive.load("evaluate-projection-quantization.py", "query_aware_evidence_validation_shared").load_root(root / "../miracl-ru-25k-current-e5") if False else None
            report_path = root / "reports" / f"{row_id}.json"; contribution_path = root / "contributions" / f"{row_id}.npz"
            require(report_path.is_file() and contribution_path.is_file() and row_map.get(row_id, {}).get("report_sha256") == sha256(report_path) and row_map[row_id].get("contribution_sha256") == sha256(contribution_path), f"matrix row digest differs: {row_id}")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            with numpy.load(contribution_path, allow_pickle=False) as loaded:
                require(report.get("per_query_contributions_sha256") == sha256(contribution_path) and loaded["query_ids"].shape == (1252,), f"report contribution differs: {row_id}")
        pair = root / "bootstrap" / f"itq-vs-query-aware--16x16-r56-seed{seed}.json"
        expected = json.loads(pair.read_text(encoding="utf-8")); left = root / "contributions" / f"itq-control--16x16-r56-seed{seed}.npz"; right = root / "contributions" / f"query-aware-hamming-target--16x16-r56-seed{seed}.npz"
        require(expected.get("left_sha256") == sha256(left) and expected.get("right_sha256") == sha256(right) and expected.get("replicates") == 10000, f"bootstrap differs: seed{seed}")
    return value


def make_bundle(args: Any) -> dict[str, str]:
    contract = validate(args.matrix_root, args.contract)
    files: list[tuple[Path, str]] = [(args.contract, "bundle/contract.json"), (args.matrix_root / "matrix-manifest.json", "bundle/matrix-manifest.json")]
    for seed in SEEDS:
        for encoder in ENCODERS:
            row_id = f"{encoder}--16x16-r56-seed{seed}"; files += [(args.matrix_root / "reports" / f"{row_id}.json", f"bundle/reports/{row_id}.json"), (args.matrix_root / "contributions" / f"{row_id}.npz", f"bundle/contributions/{row_id}.npz")]
            if encoder == "query-aware-hamming-target":
                artifact = args.matrix_root / "artifacts" / f"query-aware-hamming-target-seed{seed}"
                files += [(artifact / "artifact.json", f"bundle/artifacts/seed{seed}/artifact.json"), (artifact / "projection-weights.f32", f"bundle/artifacts/seed{seed}/projection-weights.f32"), (artifact / "thresholds.f32", f"bundle/artifacts/seed{seed}/thresholds.f32"), (artifact / "training-history.json", f"bundle/artifacts/seed{seed}/training-history.json")]
        files.append((args.matrix_root / "bootstrap" / f"itq-vs-query-aware--16x16-r56-seed{seed}.json", f"bundle/bootstrap/itq-vs-query-aware--16x16-r56-seed{seed}.json"))
    for name in (THIS.name, "run-mih-query-aware-hamming-target.py", "train-mih-query-aware-hamming-target.py", "bootstrap-mih-query-aware-hamming-target.py", "mih-query-aware-hamming-target.example.json", "evaluate-mih-banding.py", "evaluate-projection-quantization.py", "write-mih-rerank-cost-evidence.py"):
        files.append((THIS.with_name(name), f"bundle/sources/{name}"))
    with tempfile.TemporaryDirectory() as directory:
        compact = Path(directory) / "compact-manifest.json"; compact.write_text(json.dumps({"schema_version": 1, "family": "mih_query_aware_hamming_target_evidence_v1", "contract_sha256": sha256(args.contract), "matrix_manifest_sha256": sha256(args.matrix_root / "matrix-manifest.json"), "source_commit": args.source_commit}, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        files.append((compact, "bundle/compact-manifest.json")); manifest = archive.archive_manifest(files); manifest["family"] = "mih_query_aware_hamming_target_evidence_v1"; args.output.parent.mkdir(parents=True, exist_ok=True); archive.write_archive(args.output, files, manifest)
    return {"sha256": sha256(args.output), "bundle_root_sha256": manifest["bundle_root_sha256"]}


def self_test() -> int:
    try:
        if archive.self_test() != 0: return 1
    except (OSError, ValueError, json.JSONDecodeError) as error: print(f"write-mih-query-aware-hamming-target-evidence self-test failed: {error}", file=sys.stderr); return 1
    print("MIH query-aware Hamming-target evidence packager self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--contract", type=Path); parser.add_argument("--matrix-root", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--source-commit"); args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test()
        require(args.contract and args.matrix_root and args.output and args.source_commit, "evidence paths and source commit are required"); print(json.dumps(make_bundle(args), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError) as error: print(f"write-mih-query-aware-hamming-target-evidence: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
