#!/usr/bin/env python3
"""Package a fail-closed MIH-aware ITQ frontier evidence archive."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True


def load(name: str, module: str) -> Any:
    path = Path(__file__).with_name(name); spec = importlib.util.spec_from_file_location(module, path)
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {name}")
    value = importlib.util.module_from_spec(spec); sys.modules[spec.name] = value; spec.loader.exec_module(value); return value


runner = load("run-mih-aware-itq-frontier.py", "mih_aware_evidence_runner")
bootstrap = load("bootstrap-mih-aware-itq-frontier.py", "mih_aware_evidence_bootstrap")
archive = load("write-mih-rerank-cost-evidence.py", "mih_aware_evidence_archive")


def sha256_file(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def require(value: bool, message: str) -> None:
    if not value: raise ValueError(message)


def make_bundle(matrix_root: Path, contract_path: Path, bootstrap_root: Path, output: Path) -> dict[str, Any]:
    contract = runner.load_contract(contract_path); matrix = runner.rows(contract); manifest_path = matrix_root / "matrix-manifest.json"; manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("schema_version") == 1 and manifest.get("family") == runner.FAMILY and manifest.get("contract_sha256") == sha256_file(contract_path) and manifest.get("runner_source_files_sha256") == runner.source_files() and manifest.get("runner_source_bundle_sha256") == runner.source_bundle(runner.source_files()), "frontier matrix manifest provenance differs")
    entries = {entry.get("id"): entry for entry in manifest.get("rows", []) if isinstance(entry, dict)}; require(set(entries) == {row["id"] for row in matrix}, "frontier matrix rows are incomplete")
    files: list[tuple[Path, str]] = [(contract_path, "bundle/contract.json"), (manifest_path, "bundle/matrix-manifest.json")]
    comparisons = []
    for row in matrix:
        report = matrix_root / "reports" / f"{row['id']}.json"; contributions = matrix_root / "contributions" / f"{row['id']}.npz"; entry = entries[row["id"]]
        require(report.is_file() and contributions.is_file() and entry.get("report_sha256") == sha256_file(report) and entry.get("contributions_sha256") == sha256_file(contributions), f"frontier row hashes differ: {row['id']}")
        files.extend(((report, f"bundle/reports/{report.name}"), (contributions, f"bundle/contributions/{contributions.name}")))
        if row["mih_work_weight"] is not None:
            artifact_path = matrix_root / "artifacts" / row["id"] / "artifact.json"; require(artifact_path.is_file() and entry.get("artifact_sha256") == sha256_file(artifact_path), f"frontier artifact hash differs: {row['id']}")
            for payload in (artifact_path, artifact_path.parent / "projection-weights.f32", artifact_path.parent / "thresholds.f32"):
                require(payload.is_file(), f"frontier artifact payload is absent: {row['id']}"); files.append((payload, f"bundle/artifacts/{row['id']}/{payload.name}"))
            control = matrix_root / "contributions" / f"itq-control-seed{row['seed']}.npz"; comparison = bootstrap_root / f"itq-control-vs-{row['id']}.json"; item = json.loads(comparison.read_text(encoding="utf-8")); require(item.get("left_sha256") == sha256_file(control) and item.get("right_sha256") == sha256_file(contributions) and item.get("replicates") == 10000, f"frontier bootstrap is invalid: {comparison.name}"); files.append((comparison, f"bundle/bootstrap/{comparison.name}")); comparisons.append({"id": item["id"], "sha256": sha256_file(comparison)})
    sources = ("train-mih-aware-itq.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py", "run-mih-aware-itq-frontier.py", "bootstrap-mih-aware-itq-frontier.py", Path(__file__).name, "write-mih-rerank-cost-evidence.py", "mih-aware-itq-frontier.example.json", "requirements-learned-binary-adc-trainer.txt")
    files += [(Path(__file__).with_name(name), f"bundle/sources/{name}") for name in sources]
    compact = {"schema_version": 1, "family": "mih_aware_itq_heldout_frontier_evidence_v1", "contract_sha256": sha256_file(contract_path), "matrix_manifest_sha256": sha256_file(manifest_path), "rows": manifest["rows"], "comparisons": comparisons}
    compact_path = matrix_root / "compact-manifest.json"; compact_path.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"); files.append((compact_path, "bundle/compact-manifest.json"))
    archive_manifest = archive.archive_manifest(files); archive_manifest["family"] = "mih_aware_itq_heldout_frontier_evidence_v1"; output.parent.mkdir(parents=True, exist_ok=True); archive.write_archive(output, files, archive_manifest)
    return {"archive": str(output), "sha256": sha256_file(output), "bundle_root_sha256": archive_manifest["bundle_root_sha256"]}


def self_test() -> int:
    try:
        if archive.self_test() != 0: return 1
        require(runner.FAMILY == "mih_aware_itq_heldout_frontier_v1", "family changed")
    except (OSError, ValueError, json.JSONDecodeError) as error: print(f"write-mih-aware-itq-evidence self-test failed: {error}", file=sys.stderr); return 1
    print("MIH-aware ITQ evidence packager self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--matrix-root", type=Path); parser.add_argument("--contract", type=Path); parser.add_argument("--bootstrap-root", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test()
        require(all((args.matrix_root, args.contract, args.bootstrap_root, args.output)), "evidence paths are required"); print(json.dumps(make_bundle(args.matrix_root, args.contract, args.bootstrap_root, args.output), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError, archive.zipfile.BadZipFile) as error: print(f"write-mih-aware-itq-evidence: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
