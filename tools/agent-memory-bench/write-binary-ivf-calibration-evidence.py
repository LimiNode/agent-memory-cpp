#!/usr/bin/env python3
"""Fail-closed evidence archive for external BinaryIVF calibration."""

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

import faiss
import numpy


THIS = Path(__file__).resolve().parent
ROOT = THIS.parents[1]
SOURCE_PATHS = (
    "tools/agent-memory-bench/binary-ivf-calibration.example.json",
    "tools/agent-memory-bench/run-binary-ivf-calibration.py",
    "tools/agent-memory-bench/write-binary-ivf-calibration-evidence.py",
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


runner = load("binary_ivf_runner", "run-binary-ivf-calibration.py")
evaluator = load("binary_ivf_evaluator", "evaluate-native-ann-shortlists.py")


def evaluator_source_files() -> dict[str, str]:
    return {
        "evaluate-native-ann-shortlists.py": sha256(THIS / "evaluate-native-ann-shortlists.py"),
        "evaluate-projection-quantization.py": sha256(THIS / "evaluate-projection-quantization.py"),
    }


def evaluator_source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_quality(quality_path: Path, contribution_path: Path, shortlist_path: Path, contract: dict[str, Any], evaluation: dict[str, Any]) -> tuple[float, float]:
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    sources = evaluator_source_files()
    identity = evaluator.contribution_identity(evaluation, contract["cascade"]["hamming_limit"], contract["cascade"]["adc_limit"], contract["cascade"]["oracle_k"])
    with numpy.load(contribution_path, allow_pickle=False) as data:
        require(set(data.files) == {"coverage_at_hamming_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10", "e5_oracle_survival_after_adc", "query_ids", "identity_json"}, "BinaryIVF contribution fields differ")
        require(data["query_ids"].tolist() == evaluation["query_ids"] and json.loads(str(data["identity_json"].item())) == identity, "BinaryIVF contribution identity differs")
        survival, ndcg = float(numpy.mean(data["e5_oracle_survival_after_adc"], dtype=numpy.float64)), float(numpy.mean(data["reranked_ndcg_at_10"], dtype=numpy.float64))
    require(quality.get("schema_version") == 1 and quality.get("family") == "native_ann_shortlist_quality_v1" and quality.get("shortlist_export_sha256") == sha256(shortlist_path) and quality.get("per_query_contributions_sha256") == sha256(contribution_path) and quality.get("per_query_contribution_identity") == identity and quality.get("evaluator_source_files_sha256") == sources and quality.get("evaluator_source_bundle_sha256") == evaluator_source_bundle(sources) and abs(survival - float(quality["e5_oracle_survival_after_adc"])) <= 1e-12 and abs(ndcg - float(quality["reranked_ndcg_at_10"])) <= 1e-12, "BinaryIVF quality replay differs")
    return survival, ndcg


def validate(result_root: Path, input_root: Path, evaluation_root: Path, reference_path: Path, contract_path: Path) -> dict[str, Any]:
    contract = runner.load_contract(contract_path)
    require(faiss.__version__ == contract["faiss_version"], "BinaryIVF evidence Faiss version differs")
    manifest_path = input_root / "manifest.json"
    require(sha256(manifest_path) == contract["input"]["manifest_sha256"] and sha256(reference_path) == contract["reference_flat_shortlist_sha256"], "BinaryIVF evidence frozen source differs")
    evaluation = evaluator.shared.load_root(evaluation_root)
    summary_path = result_root / "summary.json"; summary = json.loads(summary_path.read_text(encoding="utf-8"))
    require(summary.get("schema_version") == 1 and summary.get("family") == runner.FAMILY and summary.get("contract_sha256") == sha256(contract_path) and summary.get("faiss_version") == contract["faiss_version"] and summary.get("input_manifest_sha256") == sha256(manifest_path) and summary.get("reference_shortlist_sha256") == sha256(reference_path), "BinaryIVF summary identity differs")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")); reference = json.loads(reference_path.read_text(encoding="utf-8"))
    positions = [int(row["query_position"]) for row in reference["rows"]]
    documents = runner.codes(input_root / manifest["document_codes_file"], 25000); queries = runner.codes(input_root / manifest["query_codes_file"], 648)
    bits = numpy.unpackbits(documents, bitorder="little", axis=1)
    projections = numpy.fromfile(input_root / manifest["query_itq_projections_file"], dtype="<f4").reshape(648, 256)
    centroids = numpy.fromfile(input_root / manifest["binary_adc_centroids_file"], dtype="<f4").reshape(256, 2)
    files: dict[str, bytes] = {"bundle/contract.json": contract_path.read_bytes(), "bundle/summary.json": summary_path.read_bytes(), "bundle/frozen-input-manifest.json": manifest_path.read_bytes(), "bundle/frozen-evaluation-manifest.json": (evaluation_root / "manifest.json").read_bytes(), "bundle/frozen-flat-shortlist.json": reference_path.read_bytes()}
    for source in SOURCE_PATHS:
        files[f"bundle/measured-source/{source}"] = (ROOT / source).read_bytes()
    expected_rows: list[dict[str, Any]] = []
    summary_rows = summary.get("rows"); require(isinstance(summary_rows, list) and len(summary_rows) == len(contract["nlist_values"]) * len(contract["target_candidate_fractions"]), "BinaryIVF row count differs")
    current = 0
    for nlist in contract["nlist_values"]:
        artifact = result_root / "indexes" / f"binaryivf-nlist{nlist}.faiss"
        require(artifact.is_file(), f"BinaryIVF artifact missing: nlist{nlist}")
        index_sha = sha256(artifact); index = faiss.read_index_binary(str(artifact))
        require(index.d == 256 and index.ntotal == 25000 and index.nlist == nlist, f"BinaryIVF artifact metadata differs: nlist{nlist}")
        files[f"bundle/indexes/{artifact.name}"] = artifact.read_bytes()
        for fraction in contract["target_candidate_fractions"]:
            nprobe = max(1, round(fraction * nlist)); identifier = f"binaryivf-nlist{nlist}-nprobe{nprobe}"
            row = summary_rows[current]; current += 1
            shortlist = result_root / "shortlists" / f"{identifier}.json"; quality = result_root / "quality" / f"{identifier}.json"; contribution = result_root / "contributions" / f"{identifier}.npz"
            require(all(item.is_file() for item in (shortlist, quality, contribution)), f"BinaryIVF row member missing: {identifier}")
            exports, counts, _ = runner.export_shortlist(index, bits, queries, projections, centroids, positions, nprobe)
            export = json.loads(shortlist.read_text(encoding="utf-8"))
            expected_export = {"schema_version": 1, "family": "native_ann_hamming_shortlist_export_v1", "backend": "binary_ivf_faiss", "input_manifest_sha256": sha256(manifest_path), "query_seed": reference["query_seed"], "hamming_limit": 768, "binaryivf_index_sha256": index_sha, "nlist": nlist, "nprobe": nprobe, "rows": exports}
            require(export == expected_export, f"BinaryIVF shortlist replay differs: {identifier}")
            survival, ndcg = validate_quality(quality, contribution, shortlist, contract, evaluation)
            require(row["id"] == identifier and row["nlist"] == nlist and row["nprobe"] == nprobe and row["target_candidate_fraction"] == fraction and row["actual_candidate_fraction"] == float(numpy.mean(counts)) / 25000.0 and row["candidate_count_p95"] == runner.percentile([float(value) for value in counts], .95) and row["index_sha256"] == index_sha and row["shortlist_sha256"] == sha256(shortlist) and row["quality_sha256"] == sha256(quality) and row["e5_oracle_survival_after_adc"] == survival and row["reranked_ndcg_at_10"] == ndcg, f"BinaryIVF summary replay differs: {identifier}")
            for category, path in (("shortlists", shortlist), ("quality", quality), ("contributions", contribution)):
                files[f"bundle/{category}/{path.name}"] = path.read_bytes()
            expected_rows.append(row)
    return {"schema_version": 1, "family": "binary_ivf_faiss_calibration_evidence_v1", "contract_sha256": sha256(contract_path), "faiss_version": faiss.__version__, "row_count": len(expected_rows), "rows": expected_rows, "members": {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}, "_files": files}


def write_archive(path: Path, manifest: dict[str, Any]) -> None:
    files = manifest.pop("_files"); files["bundle/evidence-manifest.json"] = canonical(manifest); path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name); info.date_time = (1980, 1, 1, 0, 0, 0); info.compress_type = zipfile.ZIP_DEFLATED; archive.writestr(info, value)


def self_test() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "evidence.zip"; write_archive(output, {"schema_version": 1, "_files": {"bundle/value": b"value"}})
        require(zipfile.ZipFile(output).read("bundle/value") == b"value", "BinaryIVF evidence self-test differs")
    print("BinaryIVF evidence packager self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "binary-ivf-calibration.example.json"); parser.add_argument("--result-root", type=Path); parser.add_argument("--input-root", type=Path); parser.add_argument("--evaluation-root", type=Path); parser.add_argument("--reference-shortlist", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    try:
        if args.self_test: self_test(); return 0
        if None in (args.result_root, args.input_root, args.evaluation_root, args.reference_shortlist, args.output): parser.error("all result/input/evaluation/reference/output paths are required")
        write_archive(args.output, validate(args.result_root, args.input_root, args.evaluation_root, args.reference_shortlist, args.contract)); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"write-binary-ivf-calibration-evidence: {error}", file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main())
