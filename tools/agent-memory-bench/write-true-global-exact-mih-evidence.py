#!/usr/bin/env python3
"""Fail-closed evidence archive for the true-global-exact MIH matrix."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
FAMILY = "true_global_exact_mih_top_k_v1"
NATIVE_FAMILY = "mih_native_sparse_arbitrary_m_v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("true_global_exact_mih_runner", THIS / "run-true-global-exact-mih.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load true global exact MIH runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load_runner()


def expected_rows(contract: dict[str, Any]) -> set[tuple[str, int, int]]:
    return {(scale["id"], m, k) for scale in contract["scales"] for m in scale["m_values"] for k in contract["ks"]}


def validate(result_root: Path, contract_path: Path) -> dict[str, Any]:
    contract = runner.load_contract(contract_path)
    summary_path = result_root / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    require(summary.get("schema_version") == 1 and summary.get("family") == FAMILY and summary.get("contract_sha256") == sha256(contract_path), "global exact evidence summary identity differs")
    rows = summary.get("rows")
    require(isinstance(rows, list) and {(row.get("scale"), row.get("m"), row.get("k")) for row in rows if isinstance(row, dict)} == expected_rows(contract) and len(rows) == len(expected_rows(contract)), "global exact evidence matrix differs")
    files: dict[str, bytes] = {
        "bundle/contract.json": contract_path.read_bytes(),
        "bundle/summary.json": summary_path.read_bytes(),
        "bundle/conformance-fixture.json": (THIS.parent.parent / "tests" / "eval" / "fixtures" / "mih-global-exact-conformance-v1.json").read_bytes(),
    }
    normalized: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda value: (value["scale"], value["m"], value["k"])):
        scale, m, k = row["scale"], row["m"], row["k"]
        identifier = f"m{m}-k{k}"
        config_path = result_root / scale / "configs" / f"{identifier}.json"
        report_path = result_root / scale / "native-reports" / f"{identifier}.json"
        require(config_path.is_file() and report_path.is_file(), f"global exact evidence member is missing: {scale}/{identifier}")
        config, report = json.loads(config_path.read_text(encoding="utf-8")), json.loads(report_path.read_text(encoding="utf-8"))
        require(config == runner.native_config(contract, Path(config["input_directory"]), m, k), f"global exact evidence config differs: {scale}/{identifier}")
        conformance = report.get("conformance")
        require(report.get("schema_version") == 1 and report.get("family") == NATIVE_FAMILY and report.get("benchmark_config_sha256") == sha256(config_path) and report.get("mih_search_mode") == "global_exact" and report.get("fixed_radius") is None and report.get("fixed_radius_exact_inclusion") is None and report.get("hamming_limit") == k and report.get("query_count") == contract["query_count"] and isinstance(conformance, dict) and conformance.get("global_exact_flat_ordering_checked") is True and conformance.get("global_exact_strict_stop_rule") == "kth_distance_strictly_less_than_covered_radius_plus_one_v1" and conformance.get("checked_query_count") == contract["query_count"] and isinstance(conformance.get("global_exact_cover_radius_mean"), (int, float)), f"global exact evidence report differs: {scale}/{identifier}")
        require(row == {"scale": scale, "m": m, "k": k, "input_manifest_sha256": report["input_manifest_sha256"], "config_sha256": sha256(config_path), "report_sha256": sha256(report_path), "candidate_generator_p50_ms_per_query": report["latency_ms_per_query"]["candidate_generator_total"]["p50"], "unique_candidates_per_query": report["counters_per_query"]["unique_candidates"], "mean_global_exact_cover_radius": conformance["global_exact_cover_radius_mean"]}, f"global exact evidence summary replay differs: {scale}/{identifier}")
        files[f"bundle/configs/{scale}/{identifier}.json"] = config_path.read_bytes()
        files[f"bundle/native-reports/{scale}/{identifier}.json"] = report_path.read_bytes()
        normalized.append(row)
    return {"schema_version": 1, "family": "true_global_exact_mih_evidence_v1", "contract_sha256": sha256(contract_path), "row_count": len(normalized), "rows": normalized, "members": {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}, "_files": files}


def write_archive(output: Path, manifest: dict[str, Any]) -> None:
    files = manifest.pop("_files")
    payload = canonical(manifest)
    files["bundle/evidence-manifest.json"] = payload
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name); info.date_time = (1980, 1, 1, 0, 0, 0); info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value)
    with zipfile.ZipFile(output) as archive:
        require(set(archive.namelist()) == set(files), "global exact evidence archive members differ")
        for name, value in files.items(): require(archive.read(name) == value, f"global exact evidence archive bytes differ: {name}")


def self_test() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "evidence.zip"
        files = {"bundle/value": b"value"}
        manifest = {"schema_version": 1, "family": "true_global_exact_mih_evidence_v1", "members": {"bundle/value": {"sha256": sha256_bytes(b"value"), "size": 5}}, "_files": files}
        write_archive(path, manifest)
        require(path.is_file() and zipfile.ZipFile(path).read("bundle/value") == b"value", "global exact evidence self-test differs")
    print("true global exact MIH evidence packager self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "true-global-exact-mih.example.json"); parser.add_argument("--result-root", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    try:
        if args.self_test: self_test(); return 0
        if args.result_root is None or args.output is None: parser.error("--result-root and --output are required")
        write_archive(args.output, validate(args.result_root, args.contract)); return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"write-true-global-exact-mih-evidence: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
