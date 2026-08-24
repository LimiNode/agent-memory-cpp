#!/usr/bin/env python3
"""Fail-closed evidence archive for RM(1,8)/Hadamard locator calibration."""

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

import numpy


THIS = Path(__file__).resolve().parent
ROOT = THIS.parents[1]
SOURCE_PATHS = (
    "tools/agent-memory-bench/binary-product-locator.example.json",
    "tools/agent-memory-bench/run-binary-product-locator.py",
    "tools/agent-memory-bench/rm-hadamard-locator.example.json",
    "tools/agent-memory-bench/run-rm-hadamard-locator.py",
    "tools/agent-memory-bench/write-rm-hadamard-locator-evidence.py",
    "tools/agent-memory-bench/evaluate-native-ann-shortlists.py",
    "tools/agent-memory-bench/evaluate-projection-quantization.py",
)


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    return module


runner = load("rm_hadamard_runner", "run-rm-hadamard-locator.py")
evaluator = load("rm_hadamard_evaluator", "evaluate-native-ann-shortlists.py")


def validate_quality(quality_path: Path, contribution_path: Path, shortlist_path: Path, contract: dict[str, Any], evaluation: dict[str, Any]) -> tuple[float, float]:
    cascade = contract["cascade"]; quality = json.loads(quality_path.read_text(encoding="utf-8"))
    identity = evaluator.contribution_identity(evaluation, cascade["hamming_limit"], cascade["adc_limit"], cascade["oracle_k"])
    with numpy.load(contribution_path, allow_pickle=False) as values:
        require(set(values.files) == {"coverage_at_hamming_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10", "e5_oracle_survival_after_adc", "query_ids", "identity_json"}, "RM/Hadamard contribution fields differ")
        require(values["query_ids"].tolist() == evaluation["query_ids"] and json.loads(str(values["identity_json"].item())) == identity, "RM/Hadamard contribution identity differs")
        survival, ndcg = float(numpy.mean(values["e5_oracle_survival_after_adc"], dtype=numpy.float64)), float(numpy.mean(values["reranked_ndcg_at_10"], dtype=numpy.float64))
    require(quality.get("schema_version") == 1 and quality.get("family") == "native_ann_shortlist_quality_v1" and quality.get("shortlist_export_sha256") == sha256(shortlist_path) and quality.get("per_query_contributions_sha256") == sha256(contribution_path) and quality.get("per_query_contribution_identity") == identity and abs(float(quality["e5_oracle_survival_after_adc"]) - survival) <= 1e-12 and abs(float(quality["reranked_ndcg_at_10"]) - ndcg) <= 1e-12, "RM/Hadamard quality replay differs")
    return survival, ndcg


def validate(result_root: Path, input_root: Path, evaluation_root: Path, reference_path: Path, contract_path: Path) -> dict[str, Any]:
    contract = runner.load_contract(contract_path); cascade = contract["cascade"]; manifest_path = input_root / "manifest.json"; manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(sha256(manifest_path) == contract["input"]["manifest_sha256"] and sha256(reference_path) == contract["reference_flat_shortlist_sha256"], "RM/Hadamard frozen source differs")
    reference = json.loads(reference_path.read_text(encoding="utf-8")); positions = [int(row["query_position"]) for row in reference["rows"]]; evaluation = evaluator.shared.load_root(evaluation_root)
    summary_path = result_root / "summary.json"; summary = json.loads(summary_path.read_text(encoding="utf-8"))
    require(summary.get("schema_version") == 1 and summary.get("family") == runner.FAMILY and summary.get("contract_sha256") == sha256(contract_path) and summary.get("input_manifest_sha256") == sha256(manifest_path) and summary.get("reference_shortlist_sha256") == sha256(reference_path), "RM/Hadamard summary identity differs")
    document_path = input_root / manifest["document_codes_file"]; query_path = input_root / manifest["query_codes_file"]; document_bits, query_bits = runner.load_codes(document_path, 25000), runner.load_codes(query_path, 648); document_words, query_words = runner.load_words(document_path, 25000), runner.load_words(query_path, 648)
    document_correlation, query_correlation = runner.correlations(document_bits), runner.correlations(query_bits); postings = runner.posting_lists(runner.nearest_cells(document_correlation)); sizes = [int(value.size) for value in postings.values()]
    metadata = {"center_count": 512, "occupied_cell_count": len(postings), "occupied_cell_size_p50": runner.percentile(sizes, .50), "occupied_cell_size_p95": runner.percentile(sizes, .95), "occupied_cell_size_max": max(sizes)}
    projections = numpy.fromfile(input_root / manifest["query_itq_projections_file"], dtype="<f4").reshape(648, 256); centroids = numpy.fromfile(input_root / manifest["binary_adc_centroids_file"], dtype="<f4").reshape(256, 2)
    files: dict[str, bytes] = {"bundle/contract.json": contract_path.read_bytes(), "bundle/summary.json": summary_path.read_bytes(), "bundle/frozen-input-manifest.json": manifest_path.read_bytes(), "bundle/frozen-evaluation-manifest.json": (evaluation_root / "manifest.json").read_bytes(), "bundle/frozen-flat-shortlist.json": reference_path.read_bytes()}
    for source in SOURCE_PATHS:
        files[f"bundle/measured-source/{source}"] = (ROOT / source).read_bytes()
    rows = summary.get("rows"); require(isinstance(rows, list) and len(rows) == len(contract["target_candidate_fractions"]), "RM/Hadamard summary row count differs")
    expected_rows: list[dict[str, Any]] = []
    for fraction, row in zip(contract["target_candidate_fractions"], rows):
        target = max(cascade["hamming_limit"], int(numpy.ceil(fraction * 25000))); exports: list[dict[str, Any]] = []; counts: list[float] = []; probes: list[float] = []; nonempty: list[float] = []
        for position in positions:
            candidates, probe_count, nonempty_count = runner.choose_candidates(runner.cell_order(query_correlation[position]), postings, target); shortlist_values = runner.hamming_shortlist(document_words, query_words[position], candidates, cascade["hamming_limit"])
            exports.append({"query_position": position, "hamming_shortlist_positions": shortlist_values.tolist(), "binary_adc_positions": runner.adc_positions(document_bits, projections[position], centroids, shortlist_values, cascade["adc_limit"]).tolist()})
            counts.append(float(candidates.size)); probes.append(float(probe_count)); nonempty.append(float(nonempty_count))
        identifier = f"rm1-8-target{int(fraction * 100)}"; shortlist = result_root / "shortlists" / f"{identifier}.json"; quality = result_root / "quality" / f"{identifier}.json"; contribution = result_root / "contributions" / f"{identifier}.npz"; require(all(path.is_file() for path in (shortlist, quality, contribution)), f"RM/Hadamard row member missing: {identifier}")
        expected_export = {"schema_version": 1, "family": runner.EXPORT_FAMILY, "backend": "rm_1_8_hadamard_static", "input_manifest_sha256": sha256(manifest_path), "query_seed": reference["query_seed"], "hamming_limit": cascade["hamming_limit"], "code": contract["code"], "target_candidate_fraction": fraction, "rows": exports}
        require(json.loads(shortlist.read_text(encoding="utf-8")) == expected_export, f"RM/Hadamard shortlist replay differs: {identifier}")
        survival, ndcg = validate_quality(quality, contribution, shortlist, contract, evaluation)
        expected = {"id": identifier, "target_candidate_fraction": fraction, "actual_candidate_fraction": float(numpy.mean(counts)) / 25000.0, "candidate_count_p95": runner.percentile(counts, .95), "centroid_probes_p50": runner.percentile(probes, .50), "centroid_probes_p95": runner.percentile(probes, .95), "nonempty_centroid_probes_p50": runner.percentile(nonempty, .50), "nonempty_centroid_probes_p95": runner.percentile(nonempty, .95), "index_metadata": metadata, "shortlist_sha256": sha256(shortlist), "quality_sha256": sha256(quality), "e5_oracle_survival_after_adc": survival, "reranked_ndcg_at_10": ndcg}
        require(row == expected, f"RM/Hadamard summary replay differs: {identifier}")
        for category, path in (("shortlists", shortlist), ("quality", quality), ("contributions", contribution)):
            files[f"bundle/{category}/{path.name}"] = path.read_bytes()
        expected_rows.append(expected)
    return {"schema_version": 1, "family": "rm_1_8_hadamard_locator_calibration_evidence_v1", "contract_sha256": sha256(contract_path), "row_count": len(expected_rows), "rows": expected_rows, "members": {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}, "_files": files}


def write_archive(path: Path, manifest: dict[str, Any]) -> None:
    files = manifest.pop("_files"); files["bundle/evidence-manifest.json"] = canonical(manifest); path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name); info.date_time = (1980, 1, 1, 0, 0, 0); info.compress_type = zipfile.ZIP_DEFLATED; archive.writestr(info, value)


def self_test() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "evidence.zip"; write_archive(path, {"schema_version": 1, "_files": {"bundle/value": b"value"}}); require(zipfile.ZipFile(path).read("bundle/value") == b"value", "RM/Hadamard evidence self-test differs")
    print("RM(1,8)/Hadamard evidence packager self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "rm-hadamard-locator.example.json"); parser.add_argument("--result-root", type=Path); parser.add_argument("--input-root", type=Path); parser.add_argument("--evaluation-root", type=Path); parser.add_argument("--reference-shortlist", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    try:
        if args.self_test: self_test(); return 0
        if None in (args.result_root, args.input_root, args.evaluation_root, args.reference_shortlist, args.output): parser.error("all result/input/evaluation/reference/output paths are required")
        write_archive(args.output, validate(args.result_root, args.input_root, args.evaluation_root, args.reference_shortlist, args.contract)); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"write-rm-hadamard-locator-evidence: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
