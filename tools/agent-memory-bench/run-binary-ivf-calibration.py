#!/usr/bin/env python3
"""External Faiss BinaryIVF calibration runner; it never changes library code."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import faiss
import numpy


# The frozen cascade requires a complete 768-entry Hamming shortlist, so the
# comparable frontier begins above 768 / 25k = 3.072% candidates.
TARGETS = (0.05, 0.10, 0.25)
NLISTS = (1024, 4096)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def codes(path: Path, count: int) -> numpy.ndarray:
    words = numpy.fromfile(path, dtype="<u8")
    require(words.size == count * 4, "ITQ code payload differs")
    return words.reshape(count, 4).view(numpy.uint8).reshape(count, 32).copy()


def percentile(values: list[float], fraction: float) -> float:
    return float(numpy.quantile(numpy.asarray(values, dtype=numpy.float64), fraction, method="linear"))


def shortlist(index: faiss.IndexBinaryIVF, documents: numpy.ndarray, document_bits: numpy.ndarray, queries: numpy.ndarray, query_projections: numpy.ndarray, centroids: numpy.ndarray, positions: list[int], nprobe: int) -> tuple[list[dict[str, object]], list[int], list[float]]:
    index.nprobe = nprobe
    selected = queries[numpy.asarray(positions, dtype=numpy.int64)]
    _, lists = index.quantizer.search(selected, nprobe)
    counts = [sum(index.invlists.list_size(int(item)) for item in row if item >= 0) for row in lists]
    samples: list[float] = []
    rows: list[dict[str, object]] = []
    for query_position, query in zip(positions, selected):
        start = time.perf_counter()
        distances, identifiers = index.search(query.reshape(1, -1), 768)
        elapsed = (time.perf_counter() - start) * 1000.0
        valid = identifiers[0] >= 0
        order = numpy.lexsort((identifiers[0, valid], distances[0, valid]))
        ordered = identifiers[0, valid][order].astype(numpy.int64)
        require(ordered.size == 768, "BinaryIVF candidate union is below the required Hamming limit")
        table = (query_projections[query_position, :, None] - centroids) ** 2
        adc_distances = table[numpy.arange(256)[None, :], document_bits[ordered]].sum(axis=1)
        adc_order = numpy.lexsort((ordered, adc_distances))[:256]
        rows.append({"query_position": int(query_position), "hamming_shortlist_positions": ordered.tolist(), "binary_adc_positions": ordered[adc_order].tolist()})
        samples.append(elapsed)
    return rows, counts, samples


def run(args: argparse.Namespace) -> None:
    manifest_path = args.input_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest["document_count"] == 25000 and manifest["query_count"] == 648 and manifest["code_bits"] == 256, "BinaryIVF input scope differs")
    documents = codes(args.input_root / manifest["document_codes_file"], manifest["document_count"])
    queries = codes(args.input_root / manifest["query_codes_file"], manifest["query_count"])
    document_bits = numpy.unpackbits(documents, bitorder="little", axis=1)
    query_projections = numpy.fromfile(args.input_root / manifest["query_itq_projections_file"], dtype="<f4").reshape(manifest["query_count"], 256)
    centroids = numpy.fromfile(args.input_root / manifest["binary_adc_centroids_file"], dtype="<f4").reshape(256, 2)
    reference = json.loads(args.reference_shortlist.read_text(encoding="utf-8"))
    positions = [int(row["query_position"]) for row in reference["rows"]]
    require(len(positions) == 648 and len(set(positions)) == 648, "BinaryIVF reference query positions differ")
    args.output_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for nlist in NLISTS:
        quantizer = faiss.IndexBinaryFlat(256)
        index = faiss.IndexBinaryIVF(quantizer, 256, nlist)
        index.train(documents); index.add(documents)
        for target in TARGETS:
            nprobe = max(1, round(target * nlist))
            exports, candidate_counts, latency = shortlist(index, documents, document_bits, queries, query_projections, centroids, positions, nprobe)
            identifier = f"binaryivf-nlist{nlist}-nprobe{nprobe}"
            export_path = args.output_root / "shortlists" / f"{identifier}.json"
            export_path.parent.mkdir(parents=True, exist_ok=True)
            export_path.write_text(json.dumps({"schema_version": 1, "family": "native_ann_hamming_shortlist_export_v1", "backend": "binary_ivf_faiss", "input_manifest_sha256": sha256(manifest_path), "query_seed": reference["query_seed"], "hamming_limit": 768, "rows": exports}, indent=2) + "\n", encoding="utf-8", newline="\n")
            rows.append({"id": identifier, "nlist": nlist, "nprobe": nprobe, "target_candidate_fraction": target, "actual_candidate_fraction": float(numpy.mean(candidate_counts)) / manifest["document_count"], "candidate_count_p95": percentile([float(value) for value in candidate_counts], 0.95), "search_p50_ms_per_query": percentile(latency, 0.50), "search_p95_ms_per_query": percentile(latency, 0.95), "shortlist_sha256": sha256(export_path)})
    summary = {"schema_version": 1, "family": "binary_ivf_faiss_calibration_v1", "faiss_version": faiss.__version__, "input_manifest_sha256": sha256(manifest_path), "reference_shortlist_sha256": sha256(args.reference_shortlist), "rows": rows}
    (args.output_root / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True); parser.add_argument("--reference-shortlist", type=Path, required=True); parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args); return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"run-binary-ivf-calibration: {error}"); return 1


if __name__ == "__main__":
    raise SystemExit(main())
