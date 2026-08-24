#!/usr/bin/env python3
"""Evidence-bound external BinaryIVF calibration at frozen corpus scales."""

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
FAMILY = "scale_aware_binary_ivf_v2"


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_evaluator() -> Any:
    spec = importlib.util.spec_from_file_location("scale_aware_binary_ivf_evaluator", THIS / "evaluate-native-ann-shortlists.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load native ANN shortlist evaluator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


evaluator = load_evaluator()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value["family"] == FAMILY and value["faiss_version"] == "1.13.2" and faiss.__version__ == "1.13.2", "scale BinaryIVF contract differs")
    require(value["candidate_fractions"] == [0.05, 0.10, 0.25] and value["cascade"] == {"hamming_limit": 768, "adc_limit": 256, "exact_limit": 256, "oracle_k": 10}, "scale BinaryIVF cascade differs")
    return value


def codes(path: Path, count: int) -> numpy.ndarray:
    words = numpy.fromfile(path, dtype="<u8")
    require(words.size == count * 4, "scale BinaryIVF code payload differs")
    return words.reshape(count, 4).view(numpy.uint8).reshape(count, 32).copy()


def percentile(values: list[float], fraction: float) -> float:
    return float(numpy.quantile(numpy.asarray(values, dtype=numpy.float64), fraction, method="linear"))


def train_and_reload(documents: numpy.ndarray, nlist: int, seed: int, path: Path) -> faiss.IndexBinaryIVF:
    index = faiss.IndexBinaryIVF(faiss.IndexBinaryFlat(256), 256, nlist)
    index.cp.seed = seed
    index.train(documents)
    index.add(documents)
    require(index.is_trained and index.ntotal == documents.shape[0], "scale BinaryIVF training differs")
    path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index_binary(index, str(path))
    loaded = faiss.read_index_binary(str(path))
    require(loaded.d == 256 and loaded.ntotal == documents.shape[0] and loaded.nlist == nlist, "scale BinaryIVF serialized index differs")
    return loaded


def adc_positions(document_bits: numpy.ndarray, query_projection: numpy.ndarray, centroids: numpy.ndarray, candidates: numpy.ndarray) -> numpy.ndarray:
    table = (query_projection[:, None] - centroids) ** 2
    distances = table[numpy.arange(256)[None, :], document_bits[candidates]].sum(axis=1)
    return candidates[numpy.lexsort((candidates, distances))[:256]]


def export_shortlist(index: faiss.IndexBinaryIVF, document_bits: numpy.ndarray, queries: numpy.ndarray, projections: numpy.ndarray, centroids: numpy.ndarray, nprobe: int) -> tuple[list[dict[str, Any]], list[int], list[float]]:
    index.nprobe = nprobe
    _, list_ids = index.quantizer.search(queries, nprobe)
    counts = [sum(index.invlists.list_size(int(item)) for item in row if item >= 0) for row in list_ids]
    rows: list[dict[str, Any]] = []
    samples: list[float] = []
    for position, query in enumerate(queries):
        start = time.perf_counter()
        distances, identifiers = index.search(query.reshape(1, -1), 768)
        samples.append((time.perf_counter() - start) * 1000.0)
        valid = identifiers[0] >= 0
        order = numpy.lexsort((identifiers[0, valid], distances[0, valid]))
        hamming = identifiers[0, valid][order].astype(numpy.int64)
        require(hamming.size == 768, "scale BinaryIVF candidates below Hamming@768")
        rows.append({"query_position": position, "hamming_shortlist_positions": hamming.tolist(), "binary_adc_positions": adc_positions(document_bits, projections[position], centroids, hamming).tolist()})
    return rows, counts, samples


def evaluator_sources() -> dict[str, str]:
    return {"evaluate-native-ann-shortlists.py": sha256(THIS / "evaluate-native-ann-shortlists.py"), "evaluate-projection-quantization.py": sha256(THIS / "evaluate-projection-quantization.py")}


def write_quality(data: dict[str, Any], shortlist: Path, contribution: Path, quality: Path, oracle: Path, backend: str) -> dict[str, Any]:
    _, rows = evaluator.load_export(shortlist, len(data["query_ids"]), len(data["document_ids"]), 768, 256)
    exact_top, full_ndcg = evaluator.load_or_create_oracle_cache(data, oracle, 10)
    report, contributions = evaluator.evaluate(data, rows, 768, 256, 10, exact_top, full_ndcg)
    identity = evaluator.contribution_identity(data, 768, 256, 10)
    contribution.parent.mkdir(parents=True, exist_ok=True)
    numpy.savez_compressed(contribution, **contributions, query_ids=numpy.asarray(data["query_ids"], dtype=numpy.str_), identity_json=numpy.asarray(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
    sources = evaluator_sources()
    payload = {"schema_version": 1, "family": "native_ann_shortlist_quality_v1", "evaluation_materialization_manifest_sha256": data["manifest_sha256"], "evaluation_qrels_sha256": data["evaluation_qrels_sha256"], "shortlist_export_sha256": sha256(shortlist), "shortlist_export_backend": backend, "oracle_cache_sha256": sha256(oracle), "hamming_limit": 768, "adc_limit": 256, "oracle_k": 10, "per_query_contributions_path": str(contribution), "per_query_contributions_sha256": sha256(contribution), "per_query_contribution_identity": identity, "evaluator_source_files_sha256": sources, "evaluator_source_bundle_sha256": hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(), **report}
    quality.parent.mkdir(parents=True, exist_ok=True)
    quality.write_bytes(canonical(payload))
    return payload


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    all_rows: list[dict[str, Any]] = []
    for scale in contract["scales"]:
        scale_id, count = scale["id"], scale["documents"]
        root = args.scale_root / scale_id
        input_root, evaluation_root = root / "input", root / "e5"
        input_manifest, evaluation_manifest = input_root / "manifest.json", evaluation_root / "manifest.json"
        manifest = json.loads(input_manifest.read_text(encoding="utf-8"))
        require(sha256(input_manifest) == scale["input_manifest_sha256"] and sha256(evaluation_manifest) == scale["evaluation_manifest_sha256"] and manifest["document_count"] == count and manifest["query_count"] == 648, "scale BinaryIVF frozen manifests differ")
        data = evaluator.shared.load_root(evaluation_root)
        require(data["manifest_sha256"] == scale["evaluation_manifest_sha256"] and len(data["document_ids"]) == count and len(data["query_ids"]) == 648, "scale BinaryIVF evaluation payload differs")
        documents, queries = codes(input_root / manifest["document_codes_file"], count), codes(input_root / manifest["query_codes_file"], 648)
        document_bits = numpy.unpackbits(documents, bitorder="little", axis=1)
        projections = numpy.fromfile(input_root / manifest["query_itq_projections_file"], dtype="<f4").reshape(648, 256)
        centroids = numpy.fromfile(input_root / manifest["binary_adc_centroids_file"], dtype="<f4").reshape(256, 2)
        scale_output = args.output_root / scale_id
        oracle = scale_output / "oracle.npz"
        for nlist in scale["nlist_values"]:
            index_path = scale_output / "indexes" / f"nlist{nlist}.faiss"
            index = train_and_reload(documents, nlist, contract["training_seed"], index_path)
            index_hash = sha256(index_path)
            for fraction in contract["candidate_fractions"]:
                nprobe = max(1, round(fraction * nlist))
                identifier = f"binaryivf-nlist{nlist}-nprobe{nprobe}"
                config = {"schema_version": 1, "family": FAMILY, "scale": scale_id, "nlist": nlist, "nprobe": nprobe, "target_candidate_fraction": fraction, "input_manifest_sha256": sha256(input_manifest), "evaluation_manifest_sha256": sha256(evaluation_manifest), "index_sha256": index_hash, "cascade": contract["cascade"]}
                config_path = scale_output / "configs" / f"{identifier}.json"
                shortlist = scale_output / "shortlists" / f"{identifier}.json"
                quality = scale_output / "quality" / f"{identifier}.json"
                contribution = scale_output / "contributions" / f"{identifier}.npz"
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.write_bytes(canonical(config))
                exports, counts, samples = export_shortlist(index, document_bits, queries, projections, centroids, nprobe)
                shortlist.parent.mkdir(parents=True, exist_ok=True)
                shortlist.write_bytes(canonical({"schema_version": 1, "family": "native_ann_hamming_shortlist_export_v1", "backend": "binary_ivf_faiss", "input_manifest_sha256": sha256(input_manifest), "hamming_limit": 768, "binaryivf_index_sha256": index_hash, "nlist": nlist, "nprobe": nprobe, "rows": exports}))
                measured = write_quality(data, shortlist, contribution, quality, oracle, "binary_ivf_faiss")
                all_rows.append({"scale": scale_id, "id": identifier, "nlist": nlist, "nprobe": nprobe, "target_candidate_fraction": fraction, "actual_candidate_fraction": float(numpy.mean(counts)) / count, "candidate_count_p95": percentile([float(value) for value in counts], .95), "search_p50_ms_per_query": percentile(samples, .50), "search_p95_ms_per_query": percentile(samples, .95), "config_sha256": sha256(config_path), "index_sha256": index_hash, "shortlist_sha256": sha256(shortlist), "quality_sha256": sha256(quality), "contribution_sha256": sha256(contribution), "e5_oracle_survival_after_adc": measured["e5_oracle_survival_after_adc"], "reranked_ndcg_at_10": measured["reranked_ndcg_at_10"]})
    args.output_root.mkdir(parents=True, exist_ok=True)
    args.output_root.joinpath("summary.json").write_bytes(canonical({"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "faiss_version": faiss.__version__, "rows": all_rows}))


def self_test() -> None:
    contract = load_contract(THIS / "scale-aware-binary-ivf.example.json")
    require([scale["id"] for scale in contract["scales"]] == ["es-100k", "es-1m"], "scale BinaryIVF contract self-test differs")
    print("scale-aware BinaryIVF runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "scale-aware-binary-ivf.example.json")
    parser.add_argument("--scale-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if args.scale_root is None or args.output_root is None:
            parser.error("--scale-root and --output-root are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, evaluator.EvaluationError) as error:
        print(f"run-scale-aware-binary-ivf: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
