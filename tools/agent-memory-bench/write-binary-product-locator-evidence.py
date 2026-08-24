#!/usr/bin/env python3
"""Fail-closed evidence archive for static binary-product locator calibration."""

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
    "tools/agent-memory-bench/write-binary-product-locator-evidence.py",
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


runner = load("binary_product_locator_runner", "run-binary-product-locator.py")
evaluator = load("binary_product_locator_evaluator", "evaluate-native-ann-shortlists.py")


def validate_quality(quality_path: Path, contribution_path: Path, shortlist_path: Path, contract: dict[str, Any], evaluation: dict[str, Any]) -> tuple[float, float]:
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    cascade = contract["cascade"]
    identity = evaluator.contribution_identity(evaluation, cascade["hamming_limit"], cascade["adc_limit"], cascade["oracle_k"])
    with numpy.load(contribution_path, allow_pickle=False) as values:
        require(set(values.files) == {"coverage_at_hamming_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10", "e5_oracle_survival_after_adc", "query_ids", "identity_json"}, "binary-product contribution fields differ")
        require(values["query_ids"].tolist() == evaluation["query_ids"] and json.loads(str(values["identity_json"].item())) == identity, "binary-product contribution identity differs")
        survival = float(numpy.mean(values["e5_oracle_survival_after_adc"], dtype=numpy.float64))
        ndcg = float(numpy.mean(values["reranked_ndcg_at_10"], dtype=numpy.float64))
    require(quality.get("schema_version") == 1 and quality.get("family") == "native_ann_shortlist_quality_v1" and quality.get("shortlist_export_sha256") == sha256(shortlist_path) and quality.get("per_query_contributions_sha256") == sha256(contribution_path) and quality.get("per_query_contribution_identity") == identity and abs(float(quality["e5_oracle_survival_after_adc"]) - survival) <= 1e-12 and abs(float(quality["reranked_ndcg_at_10"]) - ndcg) <= 1e-12, "binary-product quality replay differs")
    return survival, ndcg


def expected_row(scheme: dict[str, Any], fraction: float, query_positions: list[int], document_bits: numpy.ndarray, query_bits: numpy.ndarray, document_words: numpy.ndarray, query_words: numpy.ndarray, projections: numpy.ndarray, centroids: numpy.ndarray, cascade: dict[str, int]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    centers = int(scheme["centers_per_block"])
    document_assignment, local_prototypes, bounds = runner.assignments(document_bits, scheme)
    postings = runner.posting_lists(runner.ids(document_assignment, centers))
    sizes = [int(value.size) for value in postings.values()]
    metadata = {"id": scheme["id"], "block_bounds": [list(item) for item in bounds], "occupied_cell_count": len(postings), "occupied_cell_size_p50": runner.percentile(sizes, .50), "occupied_cell_size_p95": runner.percentile(sizes, .95), "maximum_global_cell_count": centers ** int(scheme["block_count"])}
    target = max(cascade["hamming_limit"], int(numpy.ceil(fraction * document_bits.shape[0])))
    exports: list[dict[str, Any]] = []; counts: list[float] = []; probes: list[float] = []; occupied: list[float] = []
    for position in query_positions:
        candidates, probe_count, occupied_count = runner.choose_candidates(query_bits[position], local_prototypes, bounds, centers, postings, target)
        shortlist = runner.hamming_shortlist(document_words, query_words[position], candidates, cascade["hamming_limit"])
        exports.append({"query_position": position, "hamming_shortlist_positions": shortlist.tolist(), "binary_adc_positions": runner.adc_positions(document_bits, projections[position], centroids, shortlist, cascade["adc_limit"]).tolist()})
        counts.append(float(candidates.size)); probes.append(float(probe_count)); occupied.append(float(occupied_count))
    return {"id": f"{scheme['id']}-target{int(fraction * 100)}", "scheme": scheme, "target_candidate_fraction": fraction, "actual_candidate_fraction": float(numpy.mean(counts)) / document_bits.shape[0], "candidate_count_p95": runner.percentile(counts, .95), "global_cell_probes_p50": runner.percentile(probes, .50), "global_cell_probes_p95": runner.percentile(probes, .95), "occupied_cell_probes_p50": runner.percentile(occupied, .50), "occupied_cell_probes_p95": runner.percentile(occupied, .95), "scheme_metadata": metadata}, exports


def validate(result_root: Path, input_root: Path, evaluation_root: Path, reference_path: Path, contract_path: Path) -> dict[str, Any]:
    contract = runner.load_contract(contract_path); cascade = contract["cascade"]
    manifest_path = input_root / "manifest.json"; manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(sha256(manifest_path) == contract["input"]["manifest_sha256"] and sha256(reference_path) == contract["reference_flat_shortlist_sha256"], "binary-product frozen source differs")
    evaluation = evaluator.shared.load_root(evaluation_root); reference = json.loads(reference_path.read_text(encoding="utf-8")); query_positions = [int(row["query_position"]) for row in reference["rows"]]
    summary_path = result_root / "summary.json"; summary = json.loads(summary_path.read_text(encoding="utf-8"))
    require(summary.get("schema_version") == 1 and summary.get("family") == runner.FAMILY and summary.get("contract_sha256") == sha256(contract_path) and summary.get("input_manifest_sha256") == sha256(manifest_path) and summary.get("reference_shortlist_sha256") == sha256(reference_path), "binary-product summary identity differs")
    document_path = input_root / manifest["document_codes_file"]; query_path = input_root / manifest["query_codes_file"]
    document_bits = runner.load_codes(document_path, 25000); query_bits = runner.load_codes(query_path, 648); document_words = runner.load_words(document_path, 25000); query_words = runner.load_words(query_path, 648)
    projections = numpy.fromfile(input_root / manifest["query_itq_projections_file"], dtype="<f4").reshape(648, 256); centroids = numpy.fromfile(input_root / manifest["binary_adc_centroids_file"], dtype="<f4").reshape(256, 2)
    files: dict[str, bytes] = {"bundle/contract.json": contract_path.read_bytes(), "bundle/summary.json": summary_path.read_bytes(), "bundle/frozen-input-manifest.json": manifest_path.read_bytes(), "bundle/frozen-evaluation-manifest.json": (evaluation_root / "manifest.json").read_bytes(), "bundle/frozen-flat-shortlist.json": reference_path.read_bytes()}
    for source in SOURCE_PATHS:
        files[f"bundle/measured-source/{source}"] = (ROOT / source).read_bytes()
    rows = summary.get("rows"); require(isinstance(rows, list) and len(rows) == len(contract["schemes"]) * len(contract["target_candidate_fractions"]), "binary-product summary row count differs")
    expected_rows: list[dict[str, Any]] = []; cursor = 0
    for scheme in contract["schemes"]:
        for fraction in contract["target_candidate_fractions"]:
            expected, exports = expected_row(scheme, fraction, query_positions, document_bits, query_bits, document_words, query_words, projections, centroids, cascade)
            identifier = expected["id"]; row = rows[cursor]; cursor += 1
            shortlist = result_root / "shortlists" / f"{identifier}.json"; quality = result_root / "quality" / f"{identifier}.json"; contribution = result_root / "contributions" / f"{identifier}.npz"
            require(all(path.is_file() for path in (shortlist, quality, contribution)), f"binary-product row member missing: {identifier}")
            export = json.loads(shortlist.read_text(encoding="utf-8"))
            expected_export = {"schema_version": 1, "family": runner.EXPORT_FAMILY, "backend": "binary_product_static", "input_manifest_sha256": sha256(manifest_path), "query_seed": reference["query_seed"], "hamming_limit": cascade["hamming_limit"], "scheme": scheme, "target_candidate_fraction": fraction, "rows": exports}
            require(export == expected_export, f"binary-product shortlist replay differs: {identifier}")
            survival, ndcg = validate_quality(quality, contribution, shortlist, contract, evaluation)
            expected.update({"shortlist_sha256": sha256(shortlist), "quality_sha256": sha256(quality), "e5_oracle_survival_after_adc": survival, "reranked_ndcg_at_10": ndcg})
            require(row == expected, f"binary-product summary replay differs: {identifier}")
            for category, path in (("shortlists", shortlist), ("quality", quality), ("contributions", contribution)):
                files[f"bundle/{category}/{path.name}"] = path.read_bytes()
            expected_rows.append(expected)
    return {"schema_version": 1, "family": "binary_product_locator_static_calibration_evidence_v1", "contract_sha256": sha256(contract_path), "row_count": len(expected_rows), "rows": expected_rows, "members": {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}, "_files": files}


def write_archive(path: Path, manifest: dict[str, Any]) -> None:
    files = manifest.pop("_files"); files["bundle/evidence-manifest.json"] = canonical(manifest); path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name); info.date_time = (1980, 1, 1, 0, 0, 0); info.compress_type = zipfile.ZIP_DEFLATED; archive.writestr(info, value)


def self_test() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "evidence.zip"; write_archive(path, {"schema_version": 1, "_files": {"bundle/value": b"value"}})
        require(zipfile.ZipFile(path).read("bundle/value") == b"value", "binary-product evidence self-test differs")
    print("binary product locator evidence packager self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "binary-product-locator.example.json"); parser.add_argument("--result-root", type=Path); parser.add_argument("--input-root", type=Path); parser.add_argument("--evaluation-root", type=Path); parser.add_argument("--reference-shortlist", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    try:
        if args.self_test: self_test(); return 0
        if None in (args.result_root, args.input_root, args.evaluation_root, args.reference_shortlist, args.output): parser.error("all result/input/evaluation/reference/output paths are required")
        write_archive(args.output, validate(args.result_root, args.input_root, args.evaluation_root, args.reference_shortlist, args.contract)); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"write-binary-product-locator-evidence: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
