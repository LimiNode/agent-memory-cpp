#!/usr/bin/env python3
"""Fail-closed archival replay for the binary centroid-routing surrogate."""

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
    "tools/agent-memory-bench/binary-centroid-routing.example.json",
    "tools/agent-memory-bench/float_semantic_ivf_evidence.py",
    "tools/agent-memory-bench/plan-binary-centroid-routing.py",
    "tools/agent-memory-bench/run-binary-centroid-routing.py",
    "tools/agent-memory-bench/write-binary-centroid-routing-evidence.py",
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


runner = load("binary_centroid_routing_evidence_runner", "run-binary-centroid-routing.py")
evaluator = load("binary_centroid_routing_evidence_evaluator", "evaluate-native-ann-shortlists.py")
float_evidence = load("binary_centroid_routing_evidence_float_evidence", "float_semantic_ivf_evidence.py")


def percentile(values: list[float], fraction: float) -> float:
    return float(numpy.quantile(numpy.asarray(values, dtype=numpy.float64), fraction, method="linear"))


def add_file(files: dict[str, bytes], name: str, path: Path) -> None:
    require(path.is_file(), f"binary centroid routing evidence member missing: {name}")
    files[name] = path.read_bytes()


def validate_float_evidence(path: Path) -> tuple[dict[str, Any], bytes]:
    return float_evidence.validate_archive(path)


def validate_quality(quality_path: Path, contribution_path: Path, shortlist_path: Path, oracle_path: Path, data: dict[str, Any]) -> tuple[float, float]:
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    identity = evaluator.contribution_identity(data, 768, 256, 10)
    with numpy.load(contribution_path, allow_pickle=False) as archive:
        fields = {"coverage_at_hamming_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10", "e5_oracle_survival_after_adc", "query_ids", "identity_json"}
        require(set(archive.files) == fields, "binary centroid routing contribution fields differ")
        require(archive["query_ids"].tolist() == data["query_ids"] and json.loads(str(archive["identity_json"].item())) == identity, "binary centroid routing contribution identity differs")
        survival = float(numpy.mean(archive["e5_oracle_survival_after_adc"], dtype=numpy.float64))
        ndcg = float(numpy.mean(archive["reranked_ndcg_at_10"], dtype=numpy.float64))
    sources = {"evaluate-native-ann-shortlists.py": sha256(THIS / "evaluate-native-ann-shortlists.py"), "evaluate-projection-quantization.py": sha256(THIS / "evaluate-projection-quantization.py")}
    require(quality.get("schema_version") == 1 and quality.get("family") == "native_ann_shortlist_quality_v1" and quality.get("shortlist_export_backend") == "binary_centroid_hamming_then_float_rerank" and quality.get("evaluation_materialization_manifest_sha256") == data["manifest_sha256"] and quality.get("evaluation_qrels_sha256") == data["evaluation_qrels_sha256"] and quality.get("shortlist_export_sha256") == sha256(shortlist_path) and quality.get("oracle_cache_sha256") == sha256(oracle_path) and quality.get("per_query_contributions_sha256") == sha256(contribution_path) and quality.get("per_query_contribution_identity") == identity and quality.get("evaluator_source_files_sha256") == sources and quality.get("evaluator_source_bundle_sha256") == hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest() and abs(survival - float(quality["e5_oracle_survival_after_adc"])) <= 1e-12 and abs(ndcg - float(quality["reranked_ndcg_at_10"])) <= 1e-12, "binary centroid routing quality replay differs")
    return survival, ndcg


def validate_audit(payload: dict[str, Any], config_hash: str, input_hash: str, centroids: numpy.ndarray, assignments: numpy.ndarray, centroid_codes: numpy.ndarray, matrix: numpy.ndarray, queries: numpy.ndarray, float_rows: list[dict[str, Any]], target_count: int, multiplier: int) -> tuple[list[dict[str, Any]], list[int], list[float]]:
    require(payload.get("schema_version") == 1 and payload.get("family") == runner.FAMILY and payload.get("backend") == "binary_centroid_hamming_then_float_rerank" and payload.get("input_manifest_sha256") == input_hash and payload.get("config_sha256") == config_hash, "binary centroid routing audit identity differs")
    rows = payload.get("rows")
    require(isinstance(rows, list) and len(rows) == len(queries), "binary centroid routing audit row count differs")
    order, offsets = runner.build_lists(assignments, centroids.shape[0])
    query_codes = runner.pack_codes(queries, matrix)
    counts: list[int] = []
    recalls: list[float] = []
    expected_rows: list[dict[str, Any]] = []
    for position, row in enumerate(rows):
        require(row.get("query_position") == position, "binary centroid routing audit query order differs")
        exact_selected = numpy.asarray(float_rows[position]["selected_centroid_ids"], dtype=numpy.int64)
        distances = runner.hamming_distances(centroid_codes, query_codes[position])
        hamming_order = numpy.lexsort((numpy.arange(centroids.shape[0]), distances))
        binary_selected = hamming_order[:min(centroids.shape[0], multiplier * exact_selected.size)]
        reranked = runner.stable_order(centroids[binary_selected] @ queries[position], binary_selected)
        sizes = offsets[reranked + 1] - offsets[reranked]
        selected_count = int(numpy.searchsorted(numpy.cumsum(sizes), target_count, side="left") + 1)
        target_reached = selected_count <= reranked.size
        selected = reranked[:selected_count] if target_reached else reranked
        candidate_count = int((offsets[selected + 1] - offsets[selected]).sum())
        expected = {"query_position": position, "binary_centroid_shortlist_ids": binary_selected.tolist(), "selected_centroid_ids": selected.tolist(), "candidate_count": candidate_count, "target_candidate_count": target_count, "target_reached": target_reached}
        require(row == expected, "binary centroid routing audit replay differs")
        expected_rows.append(expected)
        counts.append(candidate_count)
        recalls.append(float(numpy.isin(exact_selected, selected).sum()) / exact_selected.size)
    return expected_rows, counts, recalls


def validate_shortlist(payload: dict[str, Any], audit_rows: list[dict[str, Any]], order: numpy.ndarray, offsets: numpy.ndarray, document_codes: numpy.ndarray, query_codes: numpy.ndarray, document_bits: numpy.ndarray, projections: numpy.ndarray, adc_centroids: numpy.ndarray, input_hash: str, config_hash: str) -> None:
    require(payload.get("schema_version") == 1 and payload.get("family") == "native_ann_hamming_shortlist_export_v1" and payload.get("backend") == "binary_centroid_hamming_then_float_rerank" and payload.get("input_manifest_sha256") == input_hash and payload.get("hamming_limit") == 768 and payload.get("config_sha256") == config_hash, "binary centroid routing shortlist identity differs")
    rows = payload.get("rows")
    require(isinstance(rows, list) and len(rows) == len(audit_rows), "binary centroid routing shortlist row count differs")
    for position, (row, audit) in enumerate(zip(rows, audit_rows)):
        candidates = numpy.sort(numpy.concatenate([order[offsets[item]:offsets[item + 1]] for item in audit["selected_centroid_ids"]], dtype=numpy.int64))
        require(candidates.size >= audit["target_candidate_count"] >= 768, "binary centroid routing feasible shortlist is below target mass")
        expected_hamming = runner.hamming_positions(document_codes, query_codes[position], candidates)
        expected_adc = runner.adc_positions(document_bits, projections[position], adc_centroids, expected_hamming)
        expected = {"query_position": position, "binary_centroid_shortlist_ids": audit["binary_centroid_shortlist_ids"], "selected_centroid_ids": audit["selected_centroid_ids"], "hamming_shortlist_positions": expected_hamming.tolist(), "binary_adc_positions": expected_adc.tolist()}
        require(row == expected, "binary centroid routing shortlist replay differs")


def validate(result_root: Path, scale_root: Path, float_root: Path, float_evidence: Path, contract_path: Path) -> dict[str, Any]:
    contract = runner.load_contract(contract_path)
    float_manifest, float_zip = validate_float_evidence(float_evidence)
    members = float_manifest["members"]
    summary_path = result_root / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    require(summary.get("schema_version") == 1 and summary.get("family") == runner.FAMILY and summary.get("contract_sha256") == sha256(contract_path) and summary.get("float_evidence_sha256") == sha256(float_evidence), "binary centroid routing summary identity differs")
    files: dict[str, bytes] = {"bundle/contract.json": contract_path.read_bytes(), "bundle/summary.json": summary_path.read_bytes(), "bundle/frozen-float-semantic-ivf/evidence.zip": float_zip, "bundle/frozen-float-semantic-ivf/evidence-manifest.json": canonical(float_manifest)}
    for source in SOURCE_PATHS:
        add_file(files, f"bundle/measured-source/{source}", ROOT / source)
    expected: list[dict[str, Any]] = []
    for scale in contract["scales"]:
        scale_id, count = scale["id"], scale["documents"]
        input_root, evaluation_root = scale_root / scale_id / "input", scale_root / scale_id / "e5"
        input_manifest, evaluation_manifest = input_root / "manifest.json", evaluation_root / "manifest.json"
        input_data = json.loads(input_manifest.read_text(encoding="utf-8"))
        data = evaluator.shared.load_root(evaluation_root)
        expected_input = "720fead487f3a7caec62ad190cd93fa79969effd1d0fe825c865ab5d0d437d15" if scale_id == "es-100k" else "697f81bc66b37feb47b413fa168f4ae5efd030b9dbbaeb8d0c67ac8d224a9ae7"
        require(sha256(input_manifest) == expected_input and len(data["document_ids"]) == count and len(data["query_ids"]) == 648, f"binary centroid routing frozen root differs: {scale_id}")
        add_file(files, f"bundle/{scale_id}/frozen-input-manifest.json", input_manifest)
        add_file(files, f"bundle/{scale_id}/frozen-evaluation-manifest.json", evaluation_manifest)
        oracle_path = result_root / scale_id / "oracle.npz"
        require(oracle_path.is_file(), f"binary centroid routing oracle missing: {scale_id}")
        evaluator.load_or_create_oracle_cache(data, oracle_path, 10)
        add_file(files, f"bundle/{scale_id}/oracle.npz", oracle_path)
        document_codes = runner.codes(input_root / input_data["document_codes_file"], count)
        query_codes = runner.codes(input_root / input_data["query_codes_file"], 648)
        document_bits = numpy.unpackbits(document_codes, bitorder="little", axis=1)
        projections = numpy.fromfile(input_root / input_data["query_itq_projections_file"], dtype="<f4").reshape(648, 256)
        adc_centroids = numpy.fromfile(input_root / input_data["binary_adc_centroids_file"], dtype="<f4").reshape(256, 2)
        queries = numpy.asarray(data["queries"], dtype=numpy.float32)
        for centroid_count in scale["centroid_counts"]:
            index, assignments, index_hash, assignment_hash = runner.artifact_from_float(float_root, members, scale_id, centroid_count)
            index_path = float_root / scale_id / "indexes" / f"centroids-{centroid_count}.faiss"
            assignment_path = float_root / scale_id / "assignments" / f"centroids-{centroid_count}.npy"
            add_file(files, f"bundle/{scale_id}/frozen-float-indexes/{index_path.name}", index_path)
            add_file(files, f"bundle/{scale_id}/frozen-float-assignments/{assignment_path.name}", assignment_path)
            centroids = index.reconstruct_n(0, centroid_count)
            order, offsets = runner.build_lists(assignments, centroid_count)
            for length in contract["binary_code"]["lengths"]:
                output = result_root / scale_id
                code_path = output / "centroid-codes" / f"k{centroid_count}-b{length}.npy"
                matrix_path = output / "centroid-projections" / f"k{centroid_count}-b{length}.npy"
                require(code_path.is_file() and matrix_path.is_file(), f"binary centroid routing generated artifact missing: {scale_id}/{centroid_count}/{length}")
                centroid_codes = numpy.load(code_path, allow_pickle=False)
                matrix = numpy.load(matrix_path, allow_pickle=False)
                expected_matrix = runner.projection(centroids.shape[1], length, contract["binary_code"]["seed"], centroid_count)
                require(numpy.array_equal(matrix, expected_matrix) and numpy.array_equal(centroid_codes, runner.pack_codes(centroids, matrix)), f"binary centroid routing generated artifact replay differs: {scale_id}/{centroid_count}/{length}")
                add_file(files, f"bundle/{scale_id}/centroid-codes/{code_path.name}", code_path)
                add_file(files, f"bundle/{scale_id}/centroid-projections/{matrix_path.name}", matrix_path)
                for fraction in contract["target_candidate_fractions"]:
                    target_count = int(numpy.ceil(fraction * count))
                    float_id = f"floativf-k{centroid_count}-target{target_count}"
                    float_shortlist = float_root / scale_id / "shortlists" / f"{float_id}.json"
                    float_name = f"bundle/{scale_id}/shortlists/{float_shortlist.name}"
                    require(float_name in members and sha256(float_shortlist) == members[float_name]["sha256"], f"binary centroid routing frozen float shortlist differs: {float_id}")
                    float_rows = json.loads(float_shortlist.read_text(encoding="utf-8")).get("rows")
                    require(isinstance(float_rows, list) and len(float_rows) == 648 and all(row.get("query_position") == position for position, row in enumerate(float_rows)), "binary centroid routing frozen float row order differs")
                    for multiplier in contract["binary_shortlist_multipliers"]:
                        identifier = f"binarycentroid-k{centroid_count}-b{length}-target{target_count}-x{multiplier}"
                        config_path = output / "configs" / f"{identifier}.json"
                        audit_path = output / "routing-audits" / f"{identifier}.json"
                        require(config_path.is_file() and audit_path.is_file(), f"binary centroid routing row member missing: {scale_id}/{identifier}")
                        config = json.loads(config_path.read_text(encoding="utf-8"))
                        expected_config = {"schema_version": 1, "family": runner.FAMILY, "scale": scale_id, "centroid_count": centroid_count, "code_length": length, "binary_shortlist_multiplier": multiplier, "target_candidate_fraction": fraction, "target_candidate_count": target_count, "float_shortlist_sha256": sha256(float_shortlist), "float_centroid_index_sha256": index_hash, "assignment_sha256": assignment_hash, "centroid_codes_sha256": sha256(code_path), "projection_sha256": sha256(matrix_path), "float_evidence_sha256": sha256(float_evidence), "cascade": contract["cascade"], "binary_centroid_tie_rule": "hamming_ascending_then_centroid_id_ascending_v1", "float_rerank_tie_rule": "score_descending_then_centroid_id_ascending_v1", "candidate_selection_rule": "ranked_binary_shortlist_lists_until_target_count_v1", "infeasible_treatment_rule": "record_complete_routing_audit_without_budget_expansion_v1"}
                        require(config == expected_config, f"binary centroid routing config differs: {scale_id}/{identifier}")
                        audits, counts, recalls = validate_audit(json.loads(audit_path.read_text(encoding="utf-8")), sha256(config_path), sha256(input_manifest), centroids, assignments, centroid_codes, matrix, queries, float_rows, target_count, multiplier)
                        shortfalls = sum(not row["target_reached"] for row in audits)
                        shared = {"scale": scale_id, "id": identifier, "centroid_count": centroid_count, "code_length": length, "binary_shortlist_multiplier": multiplier, "target_candidate_fraction": fraction, "target_candidate_count": target_count, "queries_below_target_candidate_count": shortfalls, "float_top_nprobe_centroid_recall": float(numpy.mean(recalls)), "actual_candidate_fraction": float(numpy.mean(counts)) / count, "candidate_count_p95": percentile([float(item) for item in counts], .95), "config_sha256": sha256(config_path), "routing_audit_sha256": sha256(audit_path)}
                        row = next((item for item in summary["rows"] if item.get("scale") == scale_id and item.get("id") == identifier), None)
                        require(row is not None and all(row.get(key) == value for key, value in shared.items()), f"binary centroid routing summary routing replay differs: {scale_id}/{identifier}")
                        for timing in ("binary_centroid_scan_p50_ms_per_query", "binary_centroid_scan_p95_ms_per_query", "float_centroid_rerank_p50_ms_per_query", "float_centroid_rerank_p95_ms_per_query"):
                            require(isinstance(row.get(timing), (float, int)) and float(row[timing]) >= 0.0, f"binary centroid routing timing is invalid: {scale_id}/{identifier}")
                        add_file(files, f"bundle/{scale_id}/configs/{config_path.name}", config_path)
                        add_file(files, f"bundle/{scale_id}/routing-audits/{audit_path.name}", audit_path)
                        if shortfalls:
                            require(row == {**shared, "routing_status": "infeasible_target_candidate_mass", "shortlist_sha256": None, "quality_sha256": None, "contribution_sha256": None, "e5_oracle_survival_after_adc": None, "reranked_ndcg_at_10": None, **{key: row[key] for key in ("binary_centroid_scan_p50_ms_per_query", "binary_centroid_scan_p95_ms_per_query", "float_centroid_rerank_p50_ms_per_query", "float_centroid_rerank_p95_ms_per_query")}}, f"binary centroid routing infeasible summary differs: {scale_id}/{identifier}")
                        else:
                            shortlist_path = output / "shortlists" / f"{identifier}.json"
                            quality_path = output / "quality" / f"{identifier}.json"
                            contribution_path = output / "contributions" / f"{identifier}.npz"
                            require(all(path.is_file() for path in (shortlist_path, quality_path, contribution_path)), f"binary centroid routing feasible row member missing: {scale_id}/{identifier}")
                            validate_shortlist(json.loads(shortlist_path.read_text(encoding="utf-8")), audits, order, offsets, document_codes, query_codes, document_bits, projections, adc_centroids, sha256(input_manifest), sha256(config_path))
                            survival, ndcg = validate_quality(quality_path, contribution_path, shortlist_path, oracle_path, data)
                            require(row["routing_status"] == "feasible" and row["shortlist_sha256"] == sha256(shortlist_path) and row["quality_sha256"] == sha256(quality_path) and row["contribution_sha256"] == sha256(contribution_path) and row["e5_oracle_survival_after_adc"] == survival and row["reranked_ndcg_at_10"] == ndcg, f"binary centroid routing feasible summary differs: {scale_id}/{identifier}")
                            for category, path in (("shortlists", shortlist_path), ("quality", quality_path), ("contributions", contribution_path)):
                                add_file(files, f"bundle/{scale_id}/{category}/{path.name}", path)
                        expected.append(row)
    require(len(summary["rows"]) == len(expected) == 72, "binary centroid routing matrix differs")
    return {"schema_version": 1, "family": "binary_centroid_routing_evidence_v1", "contract_sha256": sha256(contract_path), "float_evidence_sha256": sha256(float_evidence), "row_count": len(expected), "rows": sorted(expected, key=lambda item: (item["scale"], item["id"])), "members": {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}, "_files": files}


def write_archive(path: Path, manifest: dict[str, Any]) -> None:
    files = manifest.pop("_files")
    files["bundle/evidence-manifest.json"] = canonical(manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value)


def self_test() -> None:
    float_evidence.self_test()
    with tempfile.TemporaryDirectory() as temporary:
        first, second = Path(temporary) / "one.zip", Path(temporary) / "two.zip"
        manifest = {"schema_version": 1, "family": "binary_centroid_routing_evidence_v1", "members": {"bundle/value": {"sha256": sha256_bytes(b"value"), "size": 5}}, "_files": {"bundle/value": b"value"}}
        write_archive(first, manifest.copy())
        write_archive(second, manifest.copy())
        require(first.read_bytes() == second.read_bytes() and zipfile.ZipFile(first).read("bundle/value") == b"value", "binary centroid routing evidence self-test differs")
    print("binary centroid routing evidence packager self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "binary-centroid-routing.example.json")
    parser.add_argument("--result-root", type=Path)
    parser.add_argument("--scale-root", type=Path)
    parser.add_argument("--float-root", type=Path)
    parser.add_argument("--float-evidence", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for value in (args.result_root, args.scale_root, args.float_root, args.float_evidence, args.output)):
            parser.error("--result-root, --scale-root, --float-root, --float-evidence, and --output are required")
        write_archive(args.output, validate(args.result_root, args.scale_root, args.float_root, args.float_evidence, args.contract))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile, evaluator.EvaluationError) as error:
        print(f"write-binary-centroid-routing-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
