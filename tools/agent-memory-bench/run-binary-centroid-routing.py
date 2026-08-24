#!/usr/bin/env python3
"""Binary routing surrogate over immutable float semantic IVF artifacts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import faiss
import numpy


THIS = Path(__file__).resolve().parent
FAMILY = "binary_centroid_routing_surrogate_v1"
POPCOUNT = numpy.asarray([int(value).bit_count() for value in range(256)], dtype=numpy.uint8)


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    return module


evaluator = load("binary_centroid_routing_evaluator", "evaluate-native-ann-shortlists.py")


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY and value.get("faiss_version") == faiss.__version__ == "1.13.2", "binary centroid routing contract differs")
    require(value.get("binary_code") == {"lengths": [128, 256, 512], "construction": "centroid_component_sign_after_deterministic_rademacher_projection_v1", "seed": 20260825} and value.get("binary_shortlist_multipliers") == [2, 4] and value.get("target_candidate_fractions") == [.05, .10, .25], "binary centroid routing grid differs")
    return value


def codes(path: Path, count: int) -> numpy.ndarray:
    words = numpy.fromfile(path, dtype="<u8")
    require(words.size == count * 4, "binary centroid routing code payload differs")
    return words.reshape(count, 4).view(numpy.uint8).reshape(count, 32).copy()


def percentile(values: list[float], fraction: float) -> float:
    return float(numpy.quantile(numpy.asarray(values, dtype=numpy.float64), fraction, method="linear"))


def projection(dimensions: int, length: int, seed: int, centroid_count: int) -> numpy.ndarray:
    generator = numpy.random.Generator(numpy.random.PCG64(seed + centroid_count * 1009 + length))
    return generator.integers(0, 2, size=(length, dimensions), dtype=numpy.int8).astype(numpy.float32) * 2.0 - 1.0


def pack_codes(values: numpy.ndarray, matrix: numpy.ndarray) -> numpy.ndarray:
    return numpy.packbits((values @ matrix.T >= 0.0).astype(numpy.uint8), axis=1, bitorder="little")


def hamming_distances(codes: numpy.ndarray, query: numpy.ndarray) -> numpy.ndarray:
    return POPCOUNT[numpy.bitwise_xor(codes, query)].sum(axis=1, dtype=numpy.uint16)


def stable_order(scores: numpy.ndarray) -> numpy.ndarray:
    identifiers = numpy.arange(scores.size, dtype=numpy.int64)
    return numpy.lexsort((identifiers, -scores))


def build_lists(assignments: numpy.ndarray, centroid_count: int) -> tuple[numpy.ndarray, numpy.ndarray]:
    positions = numpy.arange(assignments.size, dtype=numpy.int64)
    order = numpy.lexsort((positions, assignments))
    return order, numpy.concatenate((numpy.asarray([0], dtype=numpy.int64), numpy.cumsum(numpy.bincount(assignments, minlength=centroid_count), dtype=numpy.int64)))


def hamming_positions(document_codes: numpy.ndarray, query_code: numpy.ndarray, candidates: numpy.ndarray) -> numpy.ndarray:
    distances = hamming_distances(document_codes[candidates], query_code)
    return candidates[numpy.lexsort((candidates, distances))[:768]]


def adc_positions(document_bits: numpy.ndarray, query_projection: numpy.ndarray, adc_centroids: numpy.ndarray, candidates: numpy.ndarray) -> numpy.ndarray:
    table = (query_projection[:, None] - adc_centroids) ** 2
    distances = table[numpy.arange(256)[None, :], document_bits[candidates]].sum(axis=1)
    return candidates[numpy.lexsort((candidates, distances))[:256]]


def float_evidence_members(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("bundle/evidence-manifest.json"))
    require(manifest.get("family") == "float_semantic_ivf_evidence_v1" and manifest.get("row_count") == 12, "binary centroid routing float evidence differs")
    return manifest["members"]


def artifact_from_float(float_root: Path, members: dict[str, Any], scale: str, count: int) -> tuple[faiss.IndexFlatIP, numpy.ndarray, str, str]:
    index_path = float_root / scale / "indexes" / f"centroids-{count}.faiss"
    assignment_path = float_root / scale / "assignments" / f"centroids-{count}.npy"
    for path, archive_name in ((index_path, f"bundle/{scale}/indexes/{index_path.name}"), (assignment_path, f"bundle/{scale}/assignments/{assignment_path.name}")):
        require(path.is_file() and archive_name in members and sha256(path) == members[archive_name]["sha256"], f"binary centroid routing frozen float artifact differs: {archive_name}")
    index = faiss.read_index(str(index_path)); assignments = numpy.load(assignment_path, allow_pickle=False)
    require(index.ntotal == count and assignments.ndim == 1 and numpy.all((0 <= assignments) & (assignments < count)), "binary centroid routing float artifact metadata differs")
    return index, assignments, sha256(index_path), sha256(assignment_path)


def write_quality(data: dict[str, Any], shortlist: Path, contribution: Path, quality: Path, oracle: Path) -> dict[str, Any]:
    _, rows = evaluator.load_export(shortlist, len(data["query_ids"]), len(data["document_ids"]), 768, 256)
    exact_top, full_ndcg = evaluator.load_or_create_oracle_cache(data, oracle, 10)
    report, contributions = evaluator.evaluate(data, rows, 768, 256, 10, exact_top, full_ndcg)
    identity = evaluator.contribution_identity(data, 768, 256, 10)
    contribution.parent.mkdir(parents=True, exist_ok=True)
    numpy.savez_compressed(contribution, **contributions, query_ids=numpy.asarray(data["query_ids"], dtype=numpy.str_), identity_json=numpy.asarray(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
    sources = {"evaluate-native-ann-shortlists.py": sha256(THIS / "evaluate-native-ann-shortlists.py"), "evaluate-projection-quantization.py": sha256(THIS / "evaluate-projection-quantization.py")}
    payload = {"schema_version": 1, "family": "native_ann_shortlist_quality_v1", "evaluation_materialization_manifest_sha256": data["manifest_sha256"], "evaluation_qrels_sha256": data["evaluation_qrels_sha256"], "shortlist_export_sha256": sha256(shortlist), "shortlist_export_backend": "binary_centroid_hamming_then_float_rerank", "oracle_cache_sha256": sha256(oracle), "hamming_limit": 768, "adc_limit": 256, "oracle_k": 10, "per_query_contributions_path": str(contribution), "per_query_contributions_sha256": sha256(contribution), "per_query_contribution_identity": identity, "evaluator_source_files_sha256": sources, "evaluator_source_bundle_sha256": hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(), **report}
    quality.parent.mkdir(parents=True, exist_ok=True); quality.write_bytes(canonical(payload)); return payload


def run(args: argparse.Namespace) -> None:
    contract, members = load_contract(args.contract), float_evidence_members(args.float_evidence)
    summary: list[dict[str, Any]] = []
    for scale in contract["scales"]:
        scale_id, document_count = scale["id"], scale["documents"]
        root = args.scale_root / scale_id; input_root, evaluation_root = root / "input", root / "e5"
        input_manifest, evaluation_manifest = input_root / "manifest.json", evaluation_root / "manifest.json"
        require(sha256(input_manifest) == ("720fead487f3a7caec62ad190cd93fa79969effd1d0fe825c865ab5d0d437d15" if scale_id == "es-100k" else "697f81bc66b37feb47b413fa168f4ae5efd030b9dbbaeb8d0c67ac8d224a9ae7"), f"binary centroid routing input differs: {scale_id}")
        input_data = json.loads(input_manifest.read_text(encoding="utf-8")); data = evaluator.shared.load_root(evaluation_root)
        document_codes, query_codes = codes(input_root / input_data["document_codes_file"], document_count), codes(input_root / input_data["query_codes_file"], 648)
        document_bits = numpy.unpackbits(document_codes, bitorder="little", axis=1)
        projections = numpy.fromfile(input_root / input_data["query_itq_projections_file"], dtype="<f4").reshape(648, 256); adc_centroids = numpy.fromfile(input_root / input_data["binary_adc_centroids_file"], dtype="<f4").reshape(256, 2)
        for centroid_count in scale["centroid_counts"]:
            index, assignments, float_index_hash, assignment_hash = artifact_from_float(args.float_root, members, scale_id, centroid_count)
            centroids = index.reconstruct_n(0, centroid_count); list_order, offsets = build_lists(assignments, centroid_count)
            for length in contract["binary_code"]["lengths"]:
                matrix = projection(centroids.shape[1], length, contract["binary_code"]["seed"], centroid_count)
                centroid_codes = pack_codes(centroids, matrix); query_binary = pack_codes(numpy.asarray(data["queries"], dtype=numpy.float32), matrix)
                output = args.output_root / scale_id; code_path = output / "centroid-codes" / f"k{centroid_count}-b{length}.npy"; matrix_path = output / "centroid-projections" / f"k{centroid_count}-b{length}.npy"
                code_path.parent.mkdir(parents=True, exist_ok=True); matrix_path.parent.mkdir(parents=True, exist_ok=True); numpy.save(code_path, centroid_codes, allow_pickle=False); numpy.save(matrix_path, matrix, allow_pickle=False)
                for fraction in contract["target_candidate_fractions"]:
                    nprobe = max(1, round(fraction * centroid_count))
                    for multiplier in contract["binary_shortlist_multipliers"]:
                        identifier = f"binarycentroid-k{centroid_count}-b{length}-nprobe{nprobe}-x{multiplier}"
                        rows: list[dict[str, Any]] = []; counts: list[int] = []; scan_times: list[float] = []; rerank_times: list[float] = []; recalls: list[float] = []
                        for query_position, query in enumerate(numpy.asarray(data["queries"], dtype=numpy.float32)):
                            started = time.perf_counter(); distances = hamming_distances(centroid_codes, query_binary[query_position]); hamming_order = numpy.lexsort((numpy.arange(centroid_count), distances)); binary_selected = hamming_order[:min(centroid_count, multiplier * nprobe)]; scan_times.append((time.perf_counter() - started) * 1000.0)
                            started = time.perf_counter(); selected = binary_selected[stable_order(centroids[binary_selected] @ query)[:nprobe]]; rerank_times.append((time.perf_counter() - started) * 1000.0)
                            exact_scores, _ = index.search(query.reshape(1, -1), centroid_count); exact_selected = stable_order(exact_scores[0])[:nprobe]
                            recalls.append(float(numpy.isin(exact_selected, selected).sum()) / nprobe)
                            candidates = numpy.sort(numpy.concatenate([list_order[offsets[item]:offsets[item + 1]] for item in selected], dtype=numpy.int64)); require(candidates.size >= 768, "binary centroid routing candidates below Hamming@768")
                            hamming = hamming_positions(document_codes, query_codes[query_position], candidates)
                            rows.append({"query_position": query_position, "binary_centroid_shortlist_ids": binary_selected.tolist(), "selected_centroid_ids": selected.tolist(), "hamming_shortlist_positions": hamming.tolist(), "binary_adc_positions": adc_positions(document_bits, projections[query_position], adc_centroids, hamming).tolist()}); counts.append(int(candidates.size))
                        config = {"schema_version": 1, "family": FAMILY, "scale": scale_id, "centroid_count": centroid_count, "code_length": length, "nprobe": nprobe, "binary_shortlist_multiplier": multiplier, "target_candidate_fraction": fraction, "float_centroid_index_sha256": float_index_hash, "assignment_sha256": assignment_hash, "centroid_codes_sha256": sha256(code_path), "projection_sha256": sha256(matrix_path), "float_evidence_sha256": sha256(args.float_evidence), "cascade": contract["cascade"]}
                        config_path, shortlist_path = output / "configs" / f"{identifier}.json", output / "shortlists" / f"{identifier}.json"; quality_path, contribution_path = output / "quality" / f"{identifier}.json", output / "contributions" / f"{identifier}.npz"; config_path.parent.mkdir(parents=True, exist_ok=True); config_path.write_bytes(canonical(config)); shortlist_path.parent.mkdir(parents=True, exist_ok=True); shortlist_path.write_bytes(canonical({"schema_version": 1, "family": "native_ann_hamming_shortlist_export_v1", "backend": "binary_centroid_hamming_then_float_rerank", "input_manifest_sha256": sha256(input_manifest), "hamming_limit": 768, "config_sha256": sha256(config_path), "rows": rows}))
                        measured = write_quality(data, shortlist_path, contribution_path, quality_path, output / "oracle.npz")
                        summary.append({"scale": scale_id, "id": identifier, "centroid_count": centroid_count, "code_length": length, "nprobe": nprobe, "binary_shortlist_multiplier": multiplier, "target_candidate_fraction": fraction, "float_top_nprobe_centroid_recall": float(numpy.mean(recalls)), "actual_candidate_fraction": float(numpy.mean(counts)) / document_count, "candidate_count_p95": percentile([float(item) for item in counts], .95), "binary_centroid_scan_p50_ms_per_query": percentile(scan_times, .50), "binary_centroid_scan_p95_ms_per_query": percentile(scan_times, .95), "float_centroid_rerank_p50_ms_per_query": percentile(rerank_times, .50), "float_centroid_rerank_p95_ms_per_query": percentile(rerank_times, .95), "config_sha256": sha256(config_path), "shortlist_sha256": sha256(shortlist_path), "quality_sha256": sha256(quality_path), "contribution_sha256": sha256(contribution_path), "e5_oracle_survival_after_adc": measured["e5_oracle_survival_after_adc"], "reranked_ndcg_at_10": measured["reranked_ndcg_at_10"]})
    args.output_root.mkdir(parents=True, exist_ok=True); args.output_root.joinpath("summary.json").write_bytes(canonical({"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "float_evidence_sha256": sha256(args.float_evidence), "rows": summary}))


def self_test() -> None:
    load_contract(THIS / "binary-centroid-routing.example.json")
    require(stable_order(numpy.asarray([.5, .5, .8], dtype=numpy.float32)).tolist() == [2, 0, 1], "binary centroid routing tie rule differs")
    print("binary centroid routing runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "binary-centroid-routing.example.json"); parser.add_argument("--scale-root", type=Path); parser.add_argument("--float-root", type=Path); parser.add_argument("--float-evidence", type=Path); parser.add_argument("--output-root", type=Path); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    try:
        if args.self_test: self_test(); return 0
        if any(value is None for value in (args.scale_root, args.float_root, args.float_evidence, args.output_root)): parser.error("--scale-root, --float-root, --float-evidence, and --output-root are required")
        run(args); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile, evaluator.EvaluationError) as error:
        print(f"run-binary-centroid-routing: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
