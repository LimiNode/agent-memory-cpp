#!/usr/bin/env python3
"""Package fail-closed evidence for the scale-aware native MIH calibration."""

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

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()
ROOT = THIS.parents[2]
MEASURED_SOURCES = (
    "tools/agent-memory-bench/scale-aware-native-mih-protocol.example.json",
    "tools/agent-memory-bench/preflight-scale-aware-native-mih.py",
    "tools/agent-memory-bench/run-scale-aware-native-mih.py",
    "tools/agent-memory-bench/materialize-mih-storage-input.py",
    "tools/agent-memory-bench/mih_native_sparse_arbitrary_m.cpp",
    "tools/agent-memory-bench/evaluate-native-ann-shortlists.py",
    "tools/agent-memory-bench/evaluate-projection-quantization.py",
    "tools/agent-memory-bench/requirements-e5-materializer.txt",
    "tools/agent-memory-bench/CMakeLists.txt",
    "cmake/AgentMemoryOptions.cmake",
    "CMakeLists.txt",
    ".gitmodules",
)
REPLAY_SOURCES = (
    "tools/agent-memory-bench/preflight-scale-aware-native-mih.py",
    "tools/agent-memory-bench/run-scale-aware-native-mih.py",
)
CONTRIBUTION_FIELDS = {
    "coverage_at_hamming_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10",
    "e5_oracle_survival_after_adc", "query_ids", "identity_json",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def snapshot(ref: str, relative: str) -> bytes:
    return subprocess.run(["git", "show", f"{ref}:{relative}"], cwd=ROOT, check=True, capture_output=True).stdout


def load_runner() -> Any:
    path = THIS.with_name("run-scale-aware-native-mih.py")
    spec = importlib.util.spec_from_file_location("scale_aware_evidence_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load scale-aware runner")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    return module


runner = load_runner()


def verify_archive(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("bundle/evidence-manifest.json"))
        names = set(archive.namelist()) - {"bundle/evidence-manifest.json"}
        members = manifest.get("members")
        require(isinstance(members, dict) and names == set(members), "scale-aware evidence member set differs")
        for name, expected in members.items():
            value = archive.read(name)
            require(expected == {"sha256": sha256_bytes(value), "size": len(value)}, f"scale-aware evidence member differs: {name}")
        root = sha256_bytes(canonical(members))
        require(manifest.get("bundle_root_sha256") == root, "scale-aware evidence bundle root differs")
    return manifest


def selected_identifier(rows: list[dict[str, Any]], backend: str) -> str | None:
    eligible = [row for row in rows if row["backend"] == backend and row["admissible"]]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda row: (
            row["candidate_generator_p50_ms_per_query"],
            row["cascade_p50_ms_per_query"],
            row["auxiliary_resident_bytes_per_document"],
            row["id"],
        ),
    )["id"]


def collect(args: Any) -> tuple[dict[str, bytes], dict[str, Any]]:
    contract_rel = "tools/agent-memory-bench/scale-aware-native-mih-protocol.example.json"
    require(args.contract.read_bytes() == snapshot(args.measured_source_ref, contract_rel), "scale-aware contract snapshot differs")
    for relative in REPLAY_SOURCES:
        require((ROOT / relative).read_bytes() == snapshot(args.measured_source_ref, relative), f"scale-aware replay source differs: {relative}")
    contract = runner.load_contract(args.contract)
    preflight_path = args.calibration_root / "preflight.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    require(preflight == runner.preflight.preflight(contract, sha256(args.contract)), "scale-aware preflight differs")
    files: dict[str, bytes] = {
        "bundle/contract.json": args.contract.read_bytes(),
        "bundle/preflight.json": preflight_path.read_bytes(),
        "bundle/experiment-note.md": args.note.read_bytes(),
        "bundle/itq-256-artifact.npz": (args.calibration_root / "itq-256-artifact.npz").read_bytes(),
    }
    summaries: dict[str, Any] = {}
    for current in contract["scales"]:
        scale_root = args.calibration_root / current["id"]
        result_path = scale_root / "results" / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        input_path = scale_root / "input" / "manifest.json"
        input_sha = sha256(input_path)
        input_manifest = json.loads(input_path.read_text(encoding="utf-8"))
        document_count = input_manifest["document_count"]
        require(result.get("contract_sha256") == sha256(args.contract) and result.get("preflight_sha256") == sha256(preflight_path) and result.get("input_manifest_sha256") == input_sha, f"scale-aware result provenance differs: {current['id']}")
        require(result.get("itq_artifact_sha256") == sha256(args.calibration_root / "itq-256-artifact.npz"), f"scale-aware ITQ artifact differs: {current['id']}")
        expected = runner.treatments(contract, current, preflight)
        rows = result.get("rows")
        require(isinstance(rows, list) and [row.get("id") for row in rows] == [item["id"] for item in expected], f"scale-aware row matrix differs: {current['id']}")
        files[f"bundle/{current['id']}/result.json"] = result_path.read_bytes()
        files[f"bundle/{current['id']}/input-manifest.json"] = input_path.read_bytes()
        files[f"bundle/{current['id']}/e5-manifest.json"] = (scale_root / "e5" / "manifest.json").read_bytes()
        files[f"bundle/{current['id']}/oracle-cache.npz"] = (scale_root / "results" / "quality" / "full-e5-oracle.npz").read_bytes()
        for ordinal, (treatment, row) in enumerate(zip(expected, rows)):
            identifier = treatment["id"]; results = scale_root / "results"
            config_path = results / "configs" / f"{identifier}.json"; report_path = results / "native-reports" / f"{identifier}.json"; shortlist_path = results / "shortlists" / f"{identifier}.json"; quality_path = results / "quality" / f"{identifier}.json"; contribution_path = results / "contributions" / f"{identifier}.npz"
            config = json.loads(config_path.read_text(encoding="utf-8")); report = json.loads(report_path.read_text(encoding="utf-8")); quality = json.loads(quality_path.read_text(encoding="utf-8")); shortlist = json.loads(shortlist_path.read_text(encoding="utf-8"))
            require(config == runner.native_config(contract, scale_root / "input", shortlist_path, treatment) | {"query_count": input_manifest["query_count"]}, f"scale-aware config differs: {identifier}")
            require(report.get("benchmark_config_sha256") == sha256(config_path) and report.get("input_manifest_sha256") == input_sha and shortlist.get("input_manifest_sha256") == input_sha, f"scale-aware native row provenance differs: {identifier}")
            shortlist_sha = sha256(shortlist_path)
            require(quality.get("shortlist_export_sha256") == shortlist_sha and row.get("shortlist_export_sha256") == shortlist_sha and quality.get("per_query_contributions_sha256") == sha256(contribution_path), f"scale-aware quality binding differs: {identifier}")
            with numpy.load(contribution_path, allow_pickle=False) as archive:
                require(set(archive.files) == CONTRIBUTION_FIELDS, f"scale-aware contribution fields differ: {identifier}")
                adc = numpy.asarray(archive["e5_oracle_survival_after_adc"], dtype=numpy.float64); reranked = numpy.asarray(archive["reranked_ndcg_at_10"], dtype=numpy.float64); full = numpy.asarray(archive["full_e5_ndcg_at_10"], dtype=numpy.float64)
            gates = contract["selection_gates"]
            adc_lb = runner.bootstrap(adc, None, gates["bootstrap_replicates"], gates["bootstrap_seed_base"] + ordinal * 2, gates["confidence_level"])
            ndcg_lb = runner.bootstrap(reranked, full, gates["bootstrap_replicates"], gates["bootstrap_seed_base"] + ordinal * 2 + 1, gates["confidence_level"])
            logical_bytes = int(report["backend"]["backend_index_logical_bytes"])
            logical_bytes_per_document = logical_bytes / document_count
            generator_p50 = report["latency_ms_per_query"]["candidate_generator_total"]["p50"]
            cascade_p50 = report["latency_ms_per_query"]["cascade_total"]["p50"]
            require(row.get("adc_oracle_lb95") == adc_lb and row.get("ndcg_retention_lb95") == ndcg_lb and row.get("auxiliary_resident_bytes") == logical_bytes and row.get("auxiliary_resident_bytes_per_document") == logical_bytes_per_document and row.get("candidate_generator_p50_ms_per_query") == generator_p50 and row.get("cascade_p50_ms_per_query") == cascade_p50 and row.get("native_config_sha256") == sha256(config_path) and row.get("native_report_sha256") == sha256(report_path) and row.get("quality_report_sha256") == sha256(quality_path) and row.get("contributions_sha256") == sha256(contribution_path), f"scale-aware row replay differs: {identifier}")
            expected_admissible = adc_lb >= gates["adc_oracle_lb95_min"] and ndcg_lb >= gates["ndcg_retention_lb95_min"] and logical_bytes_per_document <= gates["auxiliary_resident_bytes_per_document_max"]
            require(row.get("admissible") == expected_admissible, f"scale-aware row gate differs: {identifier}")
            for kind, path in (("configs", config_path), ("native-reports", report_path), ("quality", quality_path), ("contributions", contribution_path)):
                files[f"bundle/{current['id']}/{kind}/{path.name}"] = path.read_bytes()
        summaries[current["id"]] = {"row_count": len(rows), "admissible_mih_rows": sum(row["backend"] == "mih" and row["admissible"] for row in rows), "selected_backend_ids": {backend: selected_identifier(rows, backend) for backend in ("mih", "flat", "hnsw")}}
    for relative in MEASURED_SOURCES:
        files[f"bundle/measured-sources/{relative}"] = snapshot(args.measured_source_ref, relative)
    files[f"bundle/replay-sources/{THIS.name}"] = THIS.read_bytes()
    return files, {"schema_version": 1, "family": "scale_aware_native_mih_calibration_evidence_v1", "measured_source_ref": args.measured_source_ref, "scales": summaries, "shortlist_exports": "omitted_by_design; each quality report binds its supplied shortlist SHA-256"}


def package(args: Any) -> None:
    files, metadata = collect(args)
    members = {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}
    manifest = {**metadata, "bundle_root_sha256": sha256_bytes(canonical(members)), "members": members}
    files["bundle/evidence-manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name); info.date_time = (1980, 1, 1, 0, 0, 0); info.external_attr = 0o100644 << 16; archive.writestr(info, value)
    manifest = verify_archive(args.output)
    print(json.dumps({"archive_sha256": sha256(args.output), "bundle_root_sha256": manifest["bundle_root_sha256"]}, sort_keys=True))


def self_test() -> int:
    try:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evidence.zip"; members = {"bundle/value": b"scale-aware"}; manifest = {"schema_version": 1, "family": "scale_aware_native_mih_calibration_evidence_v1", "bundle_root_sha256": sha256_bytes(canonical({"bundle/value": {"sha256": sha256_bytes(members["bundle/value"]), "size": len(members["bundle/value"])}})), "members": {"bundle/value": {"sha256": sha256_bytes(members["bundle/value"]), "size": len(members["bundle/value"])}}}
            with zipfile.ZipFile(path, "w") as archive: archive.writestr("bundle/value", members["bundle/value"]); archive.writestr("bundle/evidence-manifest.json", json.dumps(manifest))
            verify_archive(path)
            manifest["members"]["bundle/value"]["sha256"] = "0" * 64
            with zipfile.ZipFile(path, "w") as archive: archive.writestr("bundle/value", members["bundle/value"]); archive.writestr("bundle/evidence-manifest.json", json.dumps(manifest))
            try: verify_archive(path)
            except ValueError: pass
            else: raise ValueError("scale-aware evidence digest mutation was accepted")
            rows = [
                {"id": "slow", "backend": "mih", "admissible": True, "candidate_generator_p50_ms_per_query": 2.0, "cascade_p50_ms_per_query": 1.0, "auxiliary_resident_bytes_per_document": 1.0},
                {"id": "winner", "backend": "mih", "admissible": True, "candidate_generator_p50_ms_per_query": 1.0, "cascade_p50_ms_per_query": 2.0, "auxiliary_resident_bytes_per_document": 2.0},
            ]
            require(selected_identifier(rows, "mih") == "winner" and selected_identifier(rows, "hnsw") is None, "scale-aware selection replay differs")
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"scale-aware evidence packager self-test failed: {error}", file=sys.stderr); return 1
    print("scale-aware evidence packager self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--contract", type=Path); parser.add_argument("--calibration-root", type=Path); parser.add_argument("--note", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--measured-source-ref")
    args = parser.parse_args(argv)
    try:
        if args.self_test: return self_test()
        require(all((args.contract, args.calibration_root, args.note, args.output, args.measured_source_ref)), "evidence paths and measured source ref are required")
        package(args)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print(f"write-scale-aware-native-mih-evidence: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
