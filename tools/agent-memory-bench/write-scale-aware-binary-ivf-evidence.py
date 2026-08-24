#!/usr/bin/env python3
"""Fail-closed evidence archive for the scale-aware external BinaryIVF matrix."""

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
    "tools/agent-memory-bench/scale-aware-binary-ivf.example.json",
    "tools/agent-memory-bench/run-scale-aware-binary-ivf.py",
    "tools/agent-memory-bench/write-scale-aware-binary-ivf-evidence.py",
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
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load("scale_aware_binary_ivf_runner", "run-scale-aware-binary-ivf.py")
evaluator = load("scale_aware_binary_ivf_evaluator", "evaluate-native-ann-shortlists.py")


def validate_quality(quality_path: Path, contribution_path: Path, shortlist_path: Path, oracle_path: Path, data: dict[str, Any]) -> tuple[float, float]:
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    identity = evaluator.contribution_identity(data, 768, 256, 10)
    sources = runner.evaluator_sources()
    with numpy.load(contribution_path, allow_pickle=False) as archive:
        require(set(archive.files) == {"coverage_at_hamming_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10", "e5_oracle_survival_after_adc", "query_ids", "identity_json"}, "scale BinaryIVF contribution fields differ")
        require(archive["query_ids"].tolist() == data["query_ids"] and json.loads(str(archive["identity_json"].item())) == identity, "scale BinaryIVF contribution identity differs")
        survival = float(numpy.mean(archive["e5_oracle_survival_after_adc"], dtype=numpy.float64))
        ndcg = float(numpy.mean(archive["reranked_ndcg_at_10"], dtype=numpy.float64))
    require(quality.get("schema_version") == 1 and quality.get("family") == "native_ann_shortlist_quality_v1" and quality.get("evaluation_materialization_manifest_sha256") == data["manifest_sha256"] and quality.get("evaluation_qrels_sha256") == data["evaluation_qrels_sha256"] and quality.get("shortlist_export_sha256") == sha256(shortlist_path) and quality.get("oracle_cache_sha256") == sha256(oracle_path) and quality.get("per_query_contributions_sha256") == sha256(contribution_path) and quality.get("per_query_contribution_identity") == identity and quality.get("evaluator_source_files_sha256") == sources and quality.get("evaluator_source_bundle_sha256") == hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest() and abs(survival - float(quality["e5_oracle_survival_after_adc"])) <= 1e-12 and abs(ndcg - float(quality["reranked_ndcg_at_10"])) <= 1e-12, "scale BinaryIVF quality replay differs")
    return survival, ndcg


def validate(result_root: Path, scale_root: Path, contract_path: Path) -> dict[str, Any]:
    contract = runner.load_contract(contract_path)
    require(faiss.__version__ == contract["faiss_version"], "scale BinaryIVF evidence Faiss version differs")
    summary_path = result_root / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    require(summary.get("schema_version") == 1 and summary.get("family") == runner.FAMILY and summary.get("contract_sha256") == sha256(contract_path) and summary.get("faiss_version") == faiss.__version__, "scale BinaryIVF summary identity differs")
    files: dict[str, bytes] = {"bundle/contract.json": contract_path.read_bytes(), "bundle/summary.json": summary_path.read_bytes()}
    for source in SOURCE_PATHS:
        files[f"bundle/measured-source/{source}"] = (ROOT / source).read_bytes()
    expected: list[dict[str, Any]] = []
    for scale in contract["scales"]:
        scale_id, count = scale["id"], scale["documents"]
        input_root, evaluation_root = scale_root / scale_id / "input", scale_root / scale_id / "e5"
        input_manifest, evaluation_manifest = input_root / "manifest.json", evaluation_root / "manifest.json"
        require(sha256(input_manifest) == scale["input_manifest_sha256"] and sha256(evaluation_manifest) == scale["evaluation_manifest_sha256"], f"scale BinaryIVF frozen manifests differ: {scale_id}")
        data = evaluator.shared.load_root(evaluation_root)
        require(data["manifest_sha256"] == scale["evaluation_manifest_sha256"] and len(data["document_ids"]) == count and len(data["query_ids"]) == 648, f"scale BinaryIVF evaluation payload differs: {scale_id}")
        files[f"bundle/{scale_id}/frozen-input-manifest.json"] = input_manifest.read_bytes()
        files[f"bundle/{scale_id}/frozen-evaluation-manifest.json"] = evaluation_manifest.read_bytes()
        oracle = result_root / scale_id / "oracle.npz"
        require(oracle.is_file(), f"scale BinaryIVF oracle cache missing: {scale_id}")
        evaluator.load_or_create_oracle_cache(data, oracle, 10)
        files[f"bundle/{scale_id}/oracle.npz"] = oracle.read_bytes()
        for nlist in scale["nlist_values"]:
            index = result_root / scale_id / "indexes" / f"nlist{nlist}.faiss"
            require(index.is_file(), f"scale BinaryIVF index missing: {scale_id}/nlist{nlist}")
            loaded = faiss.read_index_binary(str(index))
            require(loaded.d == 256 and loaded.ntotal == count and loaded.nlist == nlist, f"scale BinaryIVF index metadata differs: {scale_id}/nlist{nlist}")
            index_hash = sha256(index)
            files[f"bundle/{scale_id}/indexes/{index.name}"] = index.read_bytes()
            for fraction in contract["candidate_fractions"]:
                nprobe = max(1, round(fraction * nlist))
                identifier = f"binaryivf-nlist{nlist}-nprobe{nprobe}"
                config = result_root / scale_id / "configs" / f"{identifier}.json"
                shortlist = result_root / scale_id / "shortlists" / f"{identifier}.json"
                quality = result_root / scale_id / "quality" / f"{identifier}.json"
                contribution = result_root / scale_id / "contributions" / f"{identifier}.npz"
                require(all(path.is_file() for path in (config, shortlist, quality, contribution)), f"scale BinaryIVF row member missing: {scale_id}/{identifier}")
                expected_config = {"schema_version": 1, "family": runner.FAMILY, "scale": scale_id, "nlist": nlist, "nprobe": nprobe, "target_candidate_fraction": fraction, "input_manifest_sha256": sha256(input_manifest), "evaluation_manifest_sha256": sha256(evaluation_manifest), "index_sha256": index_hash, "cascade": contract["cascade"]}
                require(json.loads(config.read_text(encoding="utf-8")) == expected_config, f"scale BinaryIVF config differs: {scale_id}/{identifier}")
                survival, ndcg = validate_quality(quality, contribution, shortlist, oracle, data)
                row = next((item for item in summary["rows"] if item.get("scale") == scale_id and item.get("id") == identifier), None)
                require(row is not None and row["config_sha256"] == sha256(config) and row["index_sha256"] == index_hash and row["shortlist_sha256"] == sha256(shortlist) and row["quality_sha256"] == sha256(quality) and row["contribution_sha256"] == sha256(contribution) and row["e5_oracle_survival_after_adc"] == survival and row["reranked_ndcg_at_10"] == ndcg, f"scale BinaryIVF summary replay differs: {scale_id}/{identifier}")
                expected.append(row)
                for category, path in (("configs", config), ("shortlists", shortlist), ("quality", quality), ("contributions", contribution)):
                    files[f"bundle/{scale_id}/{category}/{path.name}"] = path.read_bytes()
    require(len(summary["rows"]) == len(expected) == sum(len(scale["nlist_values"]) * len(contract["candidate_fractions"]) for scale in contract["scales"]), "scale BinaryIVF matrix differs")
    return {"schema_version": 1, "family": "scale_aware_binary_ivf_evidence_v1", "contract_sha256": sha256(contract_path), "row_count": len(expected), "rows": sorted(expected, key=lambda row: (row["scale"], row["id"])), "members": {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}, "_files": files}


def write_archive(path: Path, manifest: dict[str, Any]) -> None:
    files = manifest.pop("_files")
    files["bundle/evidence-manifest.json"] = canonical(manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name); info.date_time = (1980, 1, 1, 0, 0, 0); info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value)


def self_test() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "evidence.zip"
        write_archive(output, {"schema_version": 1, "family": "scale_aware_binary_ivf_evidence_v1", "members": {"bundle/value": {"sha256": sha256_bytes(b"value"), "size": 5}}, "_files": {"bundle/value": b"value"}})
        require(zipfile.ZipFile(output).read("bundle/value") == b"value", "scale BinaryIVF evidence self-test differs")
    print("scale-aware BinaryIVF evidence packager self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "scale-aware-binary-ivf.example.json")
    parser.add_argument("--result-root", type=Path); parser.add_argument("--scale-root", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test(); return 0
        if args.result_root is None or args.scale_root is None or args.output is None:
            parser.error("--result-root, --scale-root, and --output are required")
        write_archive(args.output, validate(args.result_root, args.scale_root, args.contract)); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile, evaluator.EvaluationError) as error:
        print(f"write-scale-aware-binary-ivf-evidence: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
