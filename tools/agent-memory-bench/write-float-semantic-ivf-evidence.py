#!/usr/bin/env python3
"""Fail-closed archival replay for the float semantic IVF control."""

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
    "tools/agent-memory-bench/float-semantic-ivf.example.json",
    "tools/agent-memory-bench/plan-float-semantic-ivf.py",
    "tools/agent-memory-bench/run-float-semantic-ivf.py",
    "tools/agent-memory-bench/write-float-semantic-ivf-evidence.py",
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


runner = load("float_semantic_ivf_runner", "run-float-semantic-ivf.py")
evaluator = load("float_semantic_ivf_evaluator", "evaluate-native-ann-shortlists.py")


def validate_quality(quality_path: Path, contribution_path: Path, shortlist_path: Path, oracle_path: Path, data: dict[str, Any]) -> tuple[float, float]:
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    identity = evaluator.contribution_identity(data, 768, 256, 10)
    with numpy.load(contribution_path, allow_pickle=False) as archive:
        require(set(archive.files) == {"coverage_at_hamming_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10", "e5_oracle_survival_after_adc", "query_ids", "identity_json"}, "float semantic IVF contribution fields differ")
        require(archive["query_ids"].tolist() == data["query_ids"] and json.loads(str(archive["identity_json"].item())) == identity, "float semantic IVF contribution identity differs")
        survival = float(numpy.mean(archive["e5_oracle_survival_after_adc"], dtype=numpy.float64))
        ndcg = float(numpy.mean(archive["reranked_ndcg_at_10"], dtype=numpy.float64))
    sources = runner.evaluator_sources()
    require(quality.get("schema_version") == 1 and quality.get("family") == "native_ann_shortlist_quality_v1" and quality.get("evaluation_materialization_manifest_sha256") == data["manifest_sha256"] and quality.get("evaluation_qrels_sha256") == data["evaluation_qrels_sha256"] and quality.get("shortlist_export_sha256") == sha256(shortlist_path) and quality.get("oracle_cache_sha256") == sha256(oracle_path) and quality.get("per_query_contributions_sha256") == sha256(contribution_path) and quality.get("per_query_contribution_identity") == identity and quality.get("evaluator_source_files_sha256") == sources and quality.get("evaluator_source_bundle_sha256") == hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest() and abs(survival - float(quality["e5_oracle_survival_after_adc"])) <= 1e-12 and abs(ndcg - float(quality["reranked_ndcg_at_10"])) <= 1e-12, "float semantic IVF quality replay differs")
    return survival, ndcg


def validate_shortlist(payload: dict[str, Any], index: faiss.IndexFlatIP, assignments: numpy.ndarray, document_codes: numpy.ndarray, query_codes: numpy.ndarray, document_bits: numpy.ndarray, projections: numpy.ndarray, adc_centroids: numpy.ndarray, query_vectors: numpy.ndarray, nprobe: int, centroid_hash: str, assignment_hash: str, input_hash: str) -> tuple[list[int], list[float]]:
    require(payload.get("schema_version") == 1 and payload.get("family") == "native_ann_hamming_shortlist_export_v1" and payload.get("backend") == "float_semantic_ivf_exact_centroid_scan" and payload.get("input_manifest_sha256") == input_hash and payload.get("hamming_limit") == 768 and payload.get("centroid_index_sha256") == centroid_hash and payload.get("assignment_sha256") == assignment_hash and payload.get("nprobe") == nprobe and payload.get("centroid_tie_rule") == "score_descending_then_centroid_id_ascending_v1" and payload.get("candidate_union_rule") == "document_position_ascending_v1", "float semantic IVF shortlist identity differs")
    order, offsets = runner.build_lists(assignments, index.ntotal)
    counts: list[int] = []
    routing_times: list[float] = []
    rows = payload.get("rows")
    require(isinstance(rows, list) and len(rows) == len(query_vectors), "float semantic IVF shortlist rows differ")
    for position, row in enumerate(rows):
        require(row.get("query_position") == position, "float semantic IVF shortlist query order differs")
        started = runner.time.perf_counter()
        scores, identifiers = index.search(query_vectors[position:position + 1], index.ntotal)
        selected = runner.stable_centroid_order(scores[0], identifiers[0])[:nprobe]
        expected_candidates = numpy.sort(numpy.concatenate([order[offsets[item]:offsets[item + 1]] for item in selected], dtype=numpy.int64))
        routing_times.append((runner.time.perf_counter() - started) * 1000.0)
        require(row.get("selected_centroid_ids") == selected.tolist(), "float semantic IVF selected centroids differ")
        expected_hamming = runner.hamming_positions(document_codes, query_codes[position], expected_candidates)
        require(row.get("hamming_shortlist_positions") == expected_hamming.tolist(), "float semantic IVF Hamming replay differs")
        expected_adc = runner.adc_positions(document_bits, projections[position], adc_centroids, expected_hamming)
        require(row.get("binary_adc_positions") == expected_adc.tolist(), "float semantic IVF ADC replay differs")
        counts.append(int(expected_candidates.size))
    return counts, routing_times


def validate(result_root: Path, scale_root: Path, contract_path: Path) -> dict[str, Any]:
    contract = runner.load_contract(contract_path)
    summary_path = result_root / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    require(summary.get("schema_version") == 1 and summary.get("family") == runner.FAMILY and summary.get("contract_sha256") == sha256(contract_path) and summary.get("faiss_version") == faiss.__version__, "float semantic IVF summary identity differs")
    files: dict[str, bytes] = {"bundle/contract.json": contract_path.read_bytes(), "bundle/summary.json": summary_path.read_bytes()}
    for source in SOURCE_PATHS:
        files[f"bundle/measured-source/{source}"] = (ROOT / source).read_bytes()
    expected: list[dict[str, Any]] = []
    for scale in contract["scales"]:
        scale_id, count = scale["id"], scale["documents"]
        input_root, evaluation_root = scale_root / scale_id / "input", scale_root / scale_id / "e5"
        input_manifest, evaluation_manifest = input_root / "manifest.json", evaluation_root / "manifest.json"
        input_data = json.loads(input_manifest.read_text(encoding="utf-8"))
        require(sha256(input_manifest) == scale["input_manifest_sha256"] and sha256(evaluation_manifest) == scale["evaluation_manifest_sha256"] and sha256(evaluation_root / "train-vectors.f32") == scale["train_vectors_sha256"], f"float semantic IVF frozen root differs: {scale_id}")
        data = evaluator.shared.load_root(evaluation_root)
        require(data["manifest_sha256"] == scale["evaluation_manifest_sha256"] and len(data["document_ids"]) == count and len(data["query_ids"]) == 648, f"float semantic IVF evaluation payload differs: {scale_id}")
        document_codes = runner.codes(input_root / input_data["document_codes_file"], count)
        query_codes = runner.codes(input_root / input_data["query_codes_file"], 648)
        document_bits = numpy.unpackbits(document_codes, bitorder="little", axis=1)
        projections = numpy.fromfile(input_root / input_data["query_itq_projections_file"], dtype="<f4").reshape(648, 256)
        adc_centroids = numpy.fromfile(input_root / input_data["binary_adc_centroids_file"], dtype="<f4").reshape(256, 2)
        files[f"bundle/{scale_id}/frozen-input-manifest.json"] = input_manifest.read_bytes()
        files[f"bundle/{scale_id}/frozen-evaluation-manifest.json"] = evaluation_manifest.read_bytes()
        oracle = result_root / scale_id / "oracle.npz"
        require(oracle.is_file(), f"float semantic IVF oracle missing: {scale_id}")
        evaluator.load_or_create_oracle_cache(data, oracle, 10)
        files[f"bundle/{scale_id}/oracle.npz"] = oracle.read_bytes()
        for centroid_count in scale["centroid_counts"]:
            index_path = result_root / scale_id / "indexes" / f"centroids-{centroid_count}.faiss"
            assignments_path = result_root / scale_id / "assignments" / f"centroids-{centroid_count}.npy"
            require(index_path.is_file() and assignments_path.is_file(), f"float semantic IVF centroid artifact missing: {scale_id}/{centroid_count}")
            index = faiss.read_index(str(index_path))
            assignments = numpy.load(assignments_path, allow_pickle=False)
            require(index.d == 384 and index.ntotal == centroid_count and assignments.shape == (count,) and numpy.all((0 <= assignments) & (assignments < centroid_count)), f"float semantic IVF centroid artifact differs: {scale_id}/{centroid_count}")
            centroid_hash, assignment_hash = sha256(index_path), sha256(assignments_path)
            files[f"bundle/{scale_id}/indexes/{index_path.name}"] = index_path.read_bytes()
            files[f"bundle/{scale_id}/assignments/{assignments_path.name}"] = assignments_path.read_bytes()
            for fraction in contract["target_candidate_fractions"]:
                nprobe = max(1, round(fraction * centroid_count))
                identifier = f"floativf-k{centroid_count}-nprobe{nprobe}"
                config_path = result_root / scale_id / "configs" / f"{identifier}.json"
                shortlist_path = result_root / scale_id / "shortlists" / f"{identifier}.json"
                quality_path = result_root / scale_id / "quality" / f"{identifier}.json"
                contribution_path = result_root / scale_id / "contributions" / f"{identifier}.npz"
                require(all(path.is_file() for path in (config_path, shortlist_path, quality_path, contribution_path)), f"float semantic IVF row member missing: {scale_id}/{identifier}")
                config = json.loads(config_path.read_text(encoding="utf-8"))
                require(config == {"schema_version": 1, "family": runner.FAMILY, "scale": scale_id, "centroid_count": centroid_count, "nprobe": nprobe, "target_candidate_fraction": fraction, "input_manifest_sha256": sha256(input_manifest), "evaluation_manifest_sha256": sha256(evaluation_manifest), "train_vectors_sha256": sha256(evaluation_root / "train-vectors.f32"), "centroid_index_sha256": centroid_hash, "assignment_sha256": assignment_hash, "cascade": contract["cascade"], "centroid_tie_rule": "score_descending_then_centroid_id_ascending_v1", "candidate_union_rule": "document_position_ascending_v1"}, f"float semantic IVF config differs: {scale_id}/{identifier}")
                counts, routing_times = validate_shortlist(json.loads(shortlist_path.read_text(encoding="utf-8")), index, assignments, document_codes, query_codes, document_bits, projections, adc_centroids, numpy.asarray(data["queries"], dtype=numpy.float32), nprobe, centroid_hash, assignment_hash, sha256(input_manifest))
                survival, ndcg = validate_quality(quality_path, contribution_path, shortlist_path, oracle, data)
                row = next((item for item in summary["rows"] if item.get("scale") == scale_id and item.get("id") == identifier), None)
                require(row is not None and row["config_sha256"] == sha256(config_path) and row["centroid_index_sha256"] == centroid_hash and row["assignment_sha256"] == assignment_hash and row["shortlist_sha256"] == sha256(shortlist_path) and row["quality_sha256"] == sha256(quality_path) and row["contribution_sha256"] == sha256(contribution_path) and abs(float(row["actual_candidate_fraction"]) - float(numpy.mean(counts)) / count) <= 1e-15 and abs(float(row["candidate_count_p95"]) - runner.percentile([float(item) for item in counts], .95)) <= 1e-12 and abs(float(row["centroid_routing_p50_ms_per_query"]) - runner.percentile(routing_times, .50)) <= 25.0 and abs(float(row["centroid_routing_p95_ms_per_query"]) - runner.percentile(routing_times, .95)) <= 25.0 and row["e5_oracle_survival_after_adc"] == survival and row["reranked_ndcg_at_10"] == ndcg, f"float semantic IVF summary replay differs: {scale_id}/{identifier}")
                expected.append(row)
                for category, path in (("configs", config_path), ("shortlists", shortlist_path), ("quality", quality_path), ("contributions", contribution_path)):
                    files[f"bundle/{scale_id}/{category}/{path.name}"] = path.read_bytes()
    require(len(summary["rows"]) == len(expected) == 12, "float semantic IVF matrix differs")
    return {"schema_version": 1, "family": "float_semantic_ivf_evidence_v1", "contract_sha256": sha256(contract_path), "row_count": len(expected), "rows": sorted(expected, key=lambda item: (item["scale"], item["id"])), "members": {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}, "_files": files}


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
        write_archive(output, {"schema_version": 1, "family": "float_semantic_ivf_evidence_v1", "members": {"bundle/value": {"sha256": sha256_bytes(b"value"), "size": 5}}, "_files": {"bundle/value": b"value"}})
        require(zipfile.ZipFile(output).read("bundle/value") == b"value", "float semantic IVF evidence self-test differs")
    print("float semantic IVF evidence packager self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "float-semantic-ivf.example.json")
    parser.add_argument("--result-root", type=Path); parser.add_argument("--scale-root", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test(); return 0
        if args.result_root is None or args.scale_root is None or args.output is None:
            parser.error("--result-root, --scale-root, and --output are required")
        write_archive(args.output, validate(args.result_root, args.scale_root, args.contract)); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile, evaluator.EvaluationError) as error:
        print(f"write-float-semantic-ivf-evidence: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
