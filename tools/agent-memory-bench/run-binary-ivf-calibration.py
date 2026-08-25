#!/usr/bin/env python3
"""Evidence-bound external Faiss BinaryIVF calibration runner."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import faiss
import numpy


THIS = Path(__file__).resolve().parent
FAMILY = "binary_ivf_faiss_calibration_v2"


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value == {
        "schema_version": 1, "family": FAMILY,
        "purpose": "external_calibration_only_binary_ivf_quality_candidate_frontier_not_native_latency_or_confirmation",
        "faiss_version": "1.13.2", "training_seed": 20260823,
        "input": {"document_count": 25000, "query_count": 648, "code_bits": 256, "manifest_sha256": "1d3e210edfca62d9019c2849fdb1494566556efd3e57f264d9ef31d599dee987"},
        "reference_flat_shortlist_sha256": "48da713381f0b7b9c36635f6c286541311c524083be4c7bf56223ca2be840ce5",
        "nlist_values": [1024, 4096], "target_candidate_fractions": [0.05, 0.10, 0.25],
        "cascade": {"hamming_limit": 768, "adc_limit": 256, "exact_limit": 256, "oracle_k": 10},
        "confirmation": "forbidden", "library_dependency": "forbidden_faiss_is_external_benchmark_only",
    }, "BinaryIVF contract differs")
    require(faiss.__version__ == value["faiss_version"], "Faiss version differs from BinaryIVF contract")
    return value


def codes(path: Path, count: int) -> numpy.ndarray:
    words = numpy.fromfile(path, dtype="<u8")
    require(words.size == count * 4, "ITQ code payload differs")
    return words.reshape(count, 4).view(numpy.uint8).reshape(count, 32).copy()


def percentile(values: list[float], fraction: float) -> float:
    return float(numpy.quantile(numpy.asarray(values, dtype=numpy.float64), fraction, method="linear"))


def train_and_reload(documents: numpy.ndarray, nlist: int, seed: int, path: Path) -> faiss.IndexBinaryIVF:
    quantizer = faiss.IndexBinaryFlat(256)
    index = faiss.IndexBinaryIVF(quantizer, 256, nlist)
    index.cp.seed = seed
    index.train(documents)
    index.add(documents)
    require(index.is_trained and index.ntotal == documents.shape[0], "BinaryIVF index training differs")
    path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index_binary(index, str(path))
    loaded = faiss.read_index_binary(str(path))
    require(loaded.d == 256 and loaded.ntotal == documents.shape[0] and loaded.nlist == nlist, "BinaryIVF artifact reload differs")
    return loaded


def adc_positions(document_bits: numpy.ndarray, query_projection: numpy.ndarray, centroids: numpy.ndarray, candidates: numpy.ndarray) -> numpy.ndarray:
    table = (query_projection[:, None] - centroids) ** 2
    distances = table[numpy.arange(256)[None, :], document_bits[candidates]].sum(axis=1)
    return candidates[numpy.lexsort((candidates, distances))[:256]]


def export_shortlist(index: faiss.IndexBinaryIVF, document_bits: numpy.ndarray, queries: numpy.ndarray, projections: numpy.ndarray, centroids: numpy.ndarray, query_positions: list[int], nprobe: int) -> tuple[list[dict[str, object]], list[int], list[float]]:
    index.nprobe = nprobe
    selected = queries[numpy.asarray(query_positions, dtype=numpy.int64)]
    _, list_ids = index.quantizer.search(selected, nprobe)
    list_counts = [sum(index.invlists.list_size(int(item)) for item in row if item >= 0) for row in list_ids]
    rows: list[dict[str, object]] = []
    samples: list[float] = []
    for position, query in zip(query_positions, selected):
        start = time.perf_counter()
        distances, identifiers = index.search(query.reshape(1, -1), 768)
        samples.append((time.perf_counter() - start) * 1000.0)
        valid = identifiers[0] >= 0
        order = numpy.lexsort((identifiers[0, valid], distances[0, valid]))
        candidates = identifiers[0, valid][order].astype(numpy.int64)
        require(candidates.size == 768, "BinaryIVF candidate union is below Hamming@768")
        rows.append({"query_position": int(position), "hamming_shortlist_positions": candidates.tolist(), "binary_adc_positions": adc_positions(document_bits, projections[position], centroids, candidates).tolist()})
    return rows, list_counts, samples


def evaluate(python: Path, evaluation_root: Path, shortlist: Path, quality: Path, contribution: Path, oracle: Path) -> None:
    subprocess.run([str(python), str(THIS / "evaluate-native-ann-shortlists.py"), "evaluate", "--evaluation-root", str(evaluation_root), "--shortlist-export", str(shortlist), "--output", str(quality), "--contributions-output", str(contribution), "--oracle-cache", str(oracle)], check=True)


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    manifest_path = args.input_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(sha256(manifest_path) == contract["input"]["manifest_sha256"] and manifest["document_count"] == 25000 and manifest["query_count"] == 648 and manifest["code_bits"] == 256, "BinaryIVF input differs")
    require(sha256(args.reference_shortlist) == contract["reference_flat_shortlist_sha256"], "BinaryIVF Flat reference differs")
    reference = json.loads(args.reference_shortlist.read_text(encoding="utf-8"))
    query_positions = [int(row["query_position"]) for row in reference["rows"]]
    require(reference.get("backend") == "flat" and len(query_positions) == 648 and len(set(query_positions)) == 648, "BinaryIVF query order differs")
    documents = codes(args.input_root / manifest["document_codes_file"], 25000)
    queries = codes(args.input_root / manifest["query_codes_file"], 648)
    document_bits = numpy.unpackbits(documents, bitorder="little", axis=1)
    projections = numpy.fromfile(args.input_root / manifest["query_itq_projections_file"], dtype="<f4").reshape(648, 256)
    centroids = numpy.fromfile(args.input_root / manifest["binary_adc_centroids_file"], dtype="<f4").reshape(256, 2)
    args.output_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for nlist in contract["nlist_values"]:
        artifact = args.output_root / "indexes" / f"binaryivf-nlist{nlist}.faiss"
        index = train_and_reload(documents, nlist, contract["training_seed"], artifact)
        artifact_sha = sha256(artifact)
        for fraction in contract["target_candidate_fractions"]:
            nprobe = max(1, round(fraction * nlist))
            exports, candidate_counts, samples = export_shortlist(index, document_bits, queries, projections, centroids, query_positions, nprobe)
            identifier = f"binaryivf-nlist{nlist}-nprobe{nprobe}"
            shortlist = args.output_root / "shortlists" / f"{identifier}.json"
            quality = args.output_root / "quality" / f"{identifier}.json"
            contribution = args.output_root / "contributions" / f"{identifier}.npz"
            shortlist.parent.mkdir(parents=True, exist_ok=True)
            shortlist.write_bytes(canonical({"schema_version": 1, "family": "native_ann_hamming_shortlist_export_v1", "backend": "binary_ivf_faiss", "input_manifest_sha256": sha256(manifest_path), "query_seed": reference["query_seed"], "hamming_limit": 768, "binaryivf_index_sha256": artifact_sha, "nlist": nlist, "nprobe": nprobe, "rows": exports}))
            evaluate(args.python, args.evaluation_root, shortlist, quality, contribution, args.output_root / "oracle.npz")
            measured_quality = json.loads(quality.read_text(encoding="utf-8"))
            rows.append({"id": identifier, "nlist": nlist, "nprobe": nprobe, "target_candidate_fraction": fraction, "actual_candidate_fraction": float(numpy.mean(candidate_counts)) / 25000.0, "candidate_count_p95": percentile([float(value) for value in candidate_counts], .95), "search_p50_ms_per_query": percentile(samples, .50), "search_p95_ms_per_query": percentile(samples, .95), "index_sha256": artifact_sha, "shortlist_sha256": sha256(shortlist), "quality_sha256": sha256(quality), "e5_oracle_survival_after_adc": measured_quality["e5_oracle_survival_after_adc"], "reranked_ndcg_at_10": measured_quality["reranked_ndcg_at_10"]})
    (args.output_root / "summary.json").write_bytes(canonical({"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "faiss_version": faiss.__version__, "input_manifest_sha256": sha256(manifest_path), "reference_shortlist_sha256": sha256(args.reference_shortlist), "rows": rows}))


def self_test() -> None:
    contract = load_contract(THIS / "binary-ivf-calibration.example.json")
    require(contract["nlist_values"] == [1024, 4096], "BinaryIVF contract self-test differs")
    print("BinaryIVF calibration runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "binary-ivf-calibration.example.json")
    parser.add_argument("--input-root", type=Path); parser.add_argument("--evaluation-root", type=Path); parser.add_argument("--reference-shortlist", type=Path); parser.add_argument("--output-root", type=Path); parser.add_argument("--python", type=Path, default=Path(sys.executable)); parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test(); return 0
        if None in (args.input_root, args.evaluation_root, args.reference_shortlist, args.output_root):
            parser.error("--input-root, --evaluation-root, --reference-shortlist, and --output-root are required")
        run(args); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"run-binary-ivf-calibration: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
