#!/usr/bin/env python3
"""Measure Ball-IVF lower-bound pruning feasibility over external BinaryIVF codebooks."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import faiss
import numpy


NLISTS = (1024, 4096)
KS = (10, 64, 128, 256, 512, 768)
POPCOUNT = numpy.asarray([int(value).bit_count() for value in range(256)], dtype=numpy.uint8)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hamming(left: numpy.ndarray, right: numpy.ndarray) -> numpy.ndarray:
    return POPCOUNT[numpy.bitwise_xor(left, right)].sum(axis=-1, dtype=numpy.uint16)


def codes(path: Path, count: int) -> numpy.ndarray:
    return numpy.fromfile(path, dtype="<u8").reshape(count, 4).view(numpy.uint8).reshape(count, 32).copy()


def run(args: argparse.Namespace) -> None:
    manifest_path = args.input_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["document_count"] != 25000 or manifest["query_count"] != 648 or manifest["code_bits"] != 256:
        raise ValueError("Ball-IVF scope differs")
    documents = codes(args.input_root / manifest["document_codes_file"], 25000)
    queries = codes(args.input_root / manifest["query_codes_file"], 648)
    reference = json.loads(args.reference_shortlist.read_text(encoding="utf-8"))
    positions = [int(row["query_position"]) for row in reference["rows"]]
    if len(positions) != 648 or len(set(positions)) != 648:
        raise ValueError("Ball-IVF reference query order differs")
    result: list[dict[str, object]] = []
    for nlist in NLISTS:
        quantizer = faiss.IndexBinaryFlat(256)
        index = faiss.IndexBinaryIVF(quantizer, 256, nlist)
        index.train(documents); index.add(documents)
        _, assignments = quantizer.search(documents, 1)
        assignments = assignments[:, 0]
        centroids = numpy.vstack([quantizer.reconstruct(item) for item in range(nlist)])
        document_to_centroid = hamming(documents, centroids[assignments])
        radii = numpy.zeros(nlist, dtype=numpy.uint16)
        numpy.maximum.at(radii, assignments, document_to_centroid)
        sizes = numpy.bincount(assignments, minlength=nlist)
        prunable_lists = {k: [] for k in KS}; prunable_documents = {k: [] for k in KS}
        for position in positions:
            distances = hamming(documents, queries[position])
            cutoffs = {k: int(numpy.partition(distances, k - 1)[k - 1]) for k in KS}
            lower_bounds = numpy.maximum(0, hamming(centroids, queries[position]) - radii.astype(numpy.uint16))
            for k, cutoff in cutoffs.items():
                mask = lower_bounds > cutoff  # strict: equality can contain cutoff ties.
                prunable_lists[k].append(float(mask.mean()))
                prunable_documents[k].append(float(sizes[mask].sum()) / 25000.0)
        result.append({"nlist": nlist, "non_empty_lists": int(numpy.count_nonzero(sizes)), "mean_list_radius": float(radii[sizes > 0].mean()), "p95_list_radius": float(numpy.quantile(radii[sizes > 0], .95)), "prunable_list_fraction": {str(k): float(numpy.mean(prunable_lists[k])) for k in KS}, "prunable_document_fraction": {str(k): float(numpy.mean(prunable_documents[k])) for k in KS}})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"schema_version": 1, "family": "ball_ivf_pruning_diagnostic_v1", "faiss_version": faiss.__version__, "input_manifest_sha256": sha256(manifest_path), "reference_shortlist_sha256": sha256(args.reference_shortlist), "strict_pruning_rule": "max(0,hamming(query,centroid)-list_radius)>flat_dk_v1", "rows": result}, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--input-root", type=Path, required=True); parser.add_argument("--reference-shortlist", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
    try:
        run(parser.parse_args()); return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"diagnose-ball-ivf-pruning: {error}"); return 1


if __name__ == "__main__":
    raise SystemExit(main())
