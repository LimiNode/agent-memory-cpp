#!/usr/bin/env python3
"""Evidence-bound float semantic IVF routing control on frozen E5 vectors."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

import faiss
import numpy


THIS = Path(__file__).resolve().parent
FAMILY = "float_semantic_ivf_routing_control_v1"
POPCOUNT = numpy.asarray([int(value).bit_count() for value in range(256)], dtype=numpy.uint8)


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_evaluator() -> Any:
    spec = importlib.util.spec_from_file_location("float_semantic_ivf_evaluator", THIS / "evaluate-native-ann-shortlists.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load native ANN shortlist evaluator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


evaluator = load_evaluator()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY and value.get("faiss_version") == faiss.__version__ == "1.13.2", "float semantic IVF contract differs")
    require(value.get("training") == {"algorithm": "spherical_kmeans_faiss_cpu_v1", "source": "frozen_train_vectors_only", "seed": 20260824, "iterations": 25, "redoes": 1, "normalized_vectors": True}, "float semantic IVF training differs")
    require(value.get("target_candidate_fractions") == [0.05, 0.10, 0.25] and value.get("centroid_search") == "exact_float_inner_product_scan_all_centroids_v1" and value.get("cascade") == {"hamming_limit": 768, "adc_limit": 256, "exact_limit": 256, "oracle_k": 10}, "float semantic IVF routing differs")
    return value


def codes(path: Path, count: int) -> numpy.ndarray:
    words = numpy.fromfile(path, dtype="<u8")
    require(words.size == count * 4, "float semantic IVF code payload differs")
    return words.reshape(count, 4).view(numpy.uint8).reshape(count, 32).copy()


def percentile(values: list[float], fraction: float) -> float:
    return float(numpy.quantile(numpy.asarray(values, dtype=numpy.float64), fraction, method="linear"))


def stable_centroid_order(scores: numpy.ndarray) -> numpy.ndarray:
    identifiers = numpy.arange(scores.size, dtype=numpy.int64)
    return numpy.lexsort((identifiers, -scores))


def train_centroids(train_vectors: numpy.ndarray, centroid_count: int, training: dict[str, Any], index_path: Path) -> tuple[faiss.IndexFlatIP, numpy.ndarray]:
    kmeans = faiss.Kmeans(train_vectors.shape[1], centroid_count, niter=training["iterations"], nredo=training["redoes"], seed=training["seed"], spherical=True, verbose=False, gpu=False)
    kmeans.train(train_vectors)
    centroids = numpy.asarray(kmeans.centroids, dtype=numpy.float32).reshape(centroid_count, train_vectors.shape[1]).copy()
    index = faiss.IndexFlatIP(train_vectors.shape[1])
    index.add(centroids)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(index_path))
    loaded = faiss.read_index(str(index_path))
    require(loaded.d == train_vectors.shape[1] and loaded.ntotal == centroid_count, "float semantic IVF serialized centroid index differs")
    return loaded, centroids


def assign_documents(index: faiss.IndexFlatIP, documents: numpy.ndarray, path: Path, centroid_count: int) -> numpy.ndarray:
    assigned = numpy.empty(documents.shape[0], dtype=numpy.int32)
    for start in range(0, documents.shape[0], 8192):
        _, identifiers = index.search(documents[start:start + 8192], 1)
        assigned[start:start + identifiers.shape[0]] = identifiers[:, 0]
    require(numpy.all((0 <= assigned) & (assigned < centroid_count)), "float semantic IVF assignment differs")
    path.parent.mkdir(parents=True, exist_ok=True)
    numpy.save(path, assigned, allow_pickle=False)
    reloaded = numpy.load(path, allow_pickle=False)
    require(numpy.array_equal(reloaded, assigned), "float semantic IVF serialized assignments differ")
    return assigned


def build_lists(assignments: numpy.ndarray, centroid_count: int) -> tuple[numpy.ndarray, numpy.ndarray]:
    positions = numpy.arange(assignments.size, dtype=numpy.int64)
    order = numpy.lexsort((positions, assignments))
    counts = numpy.bincount(assignments, minlength=centroid_count)
    offsets = numpy.concatenate((numpy.asarray([0], dtype=numpy.int64), numpy.cumsum(counts, dtype=numpy.int64)))
    return order, offsets


def adc_positions(document_bits: numpy.ndarray, query_projection: numpy.ndarray, adc_centroids: numpy.ndarray, candidates: numpy.ndarray) -> numpy.ndarray:
    table = (query_projection[:, None] - adc_centroids) ** 2
    distances = table[numpy.arange(256)[None, :], document_bits[candidates]].sum(axis=1)
    return candidates[numpy.lexsort((candidates, distances))[:256]]


def hamming_positions(document_codes: numpy.ndarray, query_code: numpy.ndarray, candidates: numpy.ndarray) -> numpy.ndarray:
    distances = POPCOUNT[numpy.bitwise_xor(document_codes[candidates], query_code)].sum(axis=1, dtype=numpy.uint16)
    return candidates[numpy.lexsort((candidates, distances))[:768]]


def export_shortlist(centroid_index: faiss.IndexFlatIP, document_codes: numpy.ndarray, query_codes: numpy.ndarray, document_bits: numpy.ndarray, projections: numpy.ndarray, adc_centroids: numpy.ndarray, query_vectors: numpy.ndarray, list_order: numpy.ndarray, offsets: numpy.ndarray, nprobe: int) -> tuple[list[dict[str, Any]], list[int], list[float]]:
    rows: list[dict[str, Any]] = []
    counts: list[int] = []
    times: list[float] = []
    for position, query in enumerate(query_vectors):
        started = time.perf_counter()
        scores, _ = centroid_index.search(query.reshape(1, -1), centroid_index.ntotal)
        selected = stable_centroid_order(scores[0])[:nprobe]
        parts = [list_order[offsets[item]:offsets[item + 1]] for item in selected]
        candidates = numpy.sort(numpy.concatenate(parts, dtype=numpy.int64))
        times.append((time.perf_counter() - started) * 1000.0)
        counts.append(int(candidates.size))
        require(candidates.size >= 768, "float semantic IVF candidates below Hamming@768")
        hamming = hamming_positions(document_codes, query_codes[position], candidates)
        rows.append({"query_position": position, "selected_centroid_ids": selected.tolist(), "hamming_shortlist_positions": hamming.tolist(), "binary_adc_positions": adc_positions(document_bits, projections[position], adc_centroids, hamming).tolist()})
    return rows, counts, times


def evaluator_sources() -> dict[str, str]:
    return {"evaluate-native-ann-shortlists.py": sha256(THIS / "evaluate-native-ann-shortlists.py"), "evaluate-projection-quantization.py": sha256(THIS / "evaluate-projection-quantization.py")}


def write_quality(data: dict[str, Any], shortlist: Path, contribution: Path, quality: Path, oracle: Path) -> dict[str, Any]:
    _, rows = evaluator.load_export(shortlist, len(data["query_ids"]), len(data["document_ids"]), 768, 256)
    exact_top, full_ndcg = evaluator.load_or_create_oracle_cache(data, oracle, 10)
    report, contributions = evaluator.evaluate(data, rows, 768, 256, 10, exact_top, full_ndcg)
    identity = evaluator.contribution_identity(data, 768, 256, 10)
    contribution.parent.mkdir(parents=True, exist_ok=True)
    numpy.savez_compressed(contribution, **contributions, query_ids=numpy.asarray(data["query_ids"], dtype=numpy.str_), identity_json=numpy.asarray(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
    sources = evaluator_sources()
    payload = {"schema_version": 1, "family": "native_ann_shortlist_quality_v1", "evaluation_materialization_manifest_sha256": data["manifest_sha256"], "evaluation_qrels_sha256": data["evaluation_qrels_sha256"], "shortlist_export_sha256": sha256(shortlist), "shortlist_export_backend": "float_semantic_ivf_exact_centroid_scan", "oracle_cache_sha256": sha256(oracle), "hamming_limit": 768, "adc_limit": 256, "oracle_k": 10, "per_query_contributions_path": str(contribution), "per_query_contributions_sha256": sha256(contribution), "per_query_contribution_identity": identity, "evaluator_source_files_sha256": sources, "evaluator_source_bundle_sha256": hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(), **report}
    quality.parent.mkdir(parents=True, exist_ok=True)
    quality.write_bytes(canonical(payload))
    return payload


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    summary_rows: list[dict[str, Any]] = []
    for scale in contract["scales"]:
        scale_id, document_count = scale["id"], scale["documents"]
        root = args.scale_root / scale_id
        input_root, evaluation_root = root / "input", root / "e5"
        input_manifest, evaluation_manifest = input_root / "manifest.json", evaluation_root / "manifest.json"
        input_data = json.loads(input_manifest.read_text(encoding="utf-8"))
        require(sha256(input_manifest) == scale["input_manifest_sha256"] and sha256(evaluation_manifest) == scale["evaluation_manifest_sha256"], f"float semantic IVF frozen manifests differ: {scale_id}")
        train_path = evaluation_root / "train-vectors.f32"
        require(sha256(train_path) == scale["train_vectors_sha256"], f"float semantic IVF train vectors differ: {scale_id}")
        train = numpy.fromfile(train_path, dtype="<f4").reshape(-1, 384)
        data = evaluator.shared.load_root(evaluation_root)
        require(data["manifest_sha256"] == scale["evaluation_manifest_sha256"] and len(data["document_ids"]) == document_count and len(data["query_ids"]) == 648, f"float semantic IVF evaluation payload differs: {scale_id}")
        document_codes = codes(input_root / input_data["document_codes_file"], document_count)
        query_codes = codes(input_root / input_data["query_codes_file"], 648)
        document_bits = numpy.unpackbits(document_codes, bitorder="little", axis=1)
        projections = numpy.fromfile(input_root / input_data["query_itq_projections_file"], dtype="<f4").reshape(648, 256)
        adc_centroids = numpy.fromfile(input_root / input_data["binary_adc_centroids_file"], dtype="<f4").reshape(256, 2)
        output = args.output_root / scale_id
        oracle = output / "oracle.npz"
        for centroid_count in scale["centroid_counts"]:
            index_path = output / "indexes" / f"centroids-{centroid_count}.faiss"
            index, centroids = train_centroids(train, centroid_count, contract["training"], index_path)
            assignments_path = output / "assignments" / f"centroids-{centroid_count}.npy"
            assignments = assign_documents(index, numpy.asarray(data["documents"], dtype=numpy.float32), assignments_path, centroid_count)
            list_order, offsets = build_lists(assignments, centroid_count)
            centroid_hash, assignment_hash = sha256(index_path), sha256(assignments_path)
            for fraction in contract["target_candidate_fractions"]:
                nprobe = max(1, round(fraction * centroid_count))
                identifier = f"floativf-k{centroid_count}-nprobe{nprobe}"
                config = {"schema_version": 1, "family": FAMILY, "scale": scale_id, "centroid_count": centroid_count, "nprobe": nprobe, "target_candidate_fraction": fraction, "input_manifest_sha256": sha256(input_manifest), "evaluation_manifest_sha256": sha256(evaluation_manifest), "train_vectors_sha256": sha256(train_path), "centroid_index_sha256": centroid_hash, "assignment_sha256": assignment_hash, "cascade": contract["cascade"], "centroid_tie_rule": "score_descending_then_centroid_id_ascending_v1", "candidate_union_rule": "document_position_ascending_v1"}
                config_path, shortlist_path = output / "configs" / f"{identifier}.json", output / "shortlists" / f"{identifier}.json"
                quality_path, contribution_path = output / "quality" / f"{identifier}.json", output / "contributions" / f"{identifier}.npz"
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.write_bytes(canonical(config))
                rows, counts, routing_times = export_shortlist(index, document_codes, query_codes, document_bits, projections, adc_centroids, numpy.asarray(data["queries"], dtype=numpy.float32), list_order, offsets, nprobe)
                shortlist_path.parent.mkdir(parents=True, exist_ok=True)
                shortlist_path.write_bytes(canonical({"schema_version": 1, "family": "native_ann_hamming_shortlist_export_v1", "backend": "float_semantic_ivf_exact_centroid_scan", "input_manifest_sha256": sha256(input_manifest), "hamming_limit": 768, "centroid_index_sha256": centroid_hash, "assignment_sha256": assignment_hash, "centroid_count": centroid_count, "nprobe": nprobe, "centroid_tie_rule": "score_descending_then_centroid_id_ascending_v1", "candidate_union_rule": "document_position_ascending_v1", "rows": rows}))
                measured = write_quality(data, shortlist_path, contribution_path, quality_path, oracle)
                summary_rows.append({"scale": scale_id, "id": identifier, "centroid_count": centroid_count, "nprobe": nprobe, "target_candidate_fraction": fraction, "actual_candidate_fraction": float(numpy.mean(counts)) / document_count, "candidate_count_p95": percentile([float(count) for count in counts], .95), "centroid_routing_p50_ms_per_query": percentile(routing_times, .50), "centroid_routing_p95_ms_per_query": percentile(routing_times, .95), "config_sha256": sha256(config_path), "centroid_index_sha256": centroid_hash, "assignment_sha256": assignment_hash, "shortlist_sha256": sha256(shortlist_path), "quality_sha256": sha256(quality_path), "contribution_sha256": sha256(contribution_path), "e5_oracle_survival_after_adc": measured["e5_oracle_survival_after_adc"], "reranked_ndcg_at_10": measured["reranked_ndcg_at_10"]})
    args.output_root.mkdir(parents=True, exist_ok=True)
    args.output_root.joinpath("summary.json").write_bytes(canonical({"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "faiss_version": faiss.__version__, "rows": summary_rows}))


def self_test() -> None:
    contract = load_contract(THIS / "float-semantic-ivf.example.json")
    require(sum(len(scale["centroid_counts"]) * len(contract["target_candidate_fractions"]) for scale in contract["scales"]) == 12, "float semantic IVF matrix differs")
    require(stable_centroid_order(numpy.asarray([.5, .5, .8], dtype=numpy.float32)).tolist() == [2, 0, 1], "float semantic IVF centroid tie rule differs")
    print("float semantic IVF runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "float-semantic-ivf.example.json")
    parser.add_argument("--scale-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test(); return 0
        if args.scale_root is None or args.output_root is None:
            parser.error("--scale-root and --output-root are required")
        run(args); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, evaluator.EvaluationError) as error:
        print(f"run-float-semantic-ivf: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
