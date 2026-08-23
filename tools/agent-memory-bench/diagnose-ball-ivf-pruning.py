#!/usr/bin/env python3
"""Diagnose tie-safe single-ball Hamming pruning using pinned BinaryIVF artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import faiss
import numpy


THIS = Path(__file__).resolve().parent
FAMILY = "ball_ivf_pruning_diagnostic_v2"
POPCOUNT = numpy.asarray([value.bit_count() for value in range(256)], dtype=numpy.uint8)


def require(value: bool, message: str) -> None:
    if not value: raise ValueError(message)


def sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def hamming(left: numpy.ndarray, right: numpy.ndarray) -> numpy.ndarray:
    return POPCOUNT[numpy.bitwise_xor(left, right)].sum(axis=-1, dtype=numpy.uint16)


def codes(path: Path, count: int) -> numpy.ndarray:
    return numpy.fromfile(path, dtype="<u8").reshape(count, 4).view(numpy.uint8).reshape(count, 32).copy()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value == {"schema_version": 1, "family": FAMILY, "purpose": "single_centroid_max_radius_triangle_bound_feasibility_only_not_an_exact_index_claim", "faiss_version": "1.13.2", "input_manifest_sha256": "1d3e210edfca62d9019c2849fdb1494566556efd3e57f264d9ef31d599dee987", "reference_flat_shortlist_sha256": "48da713381f0b7b9c36635f6c286541311c524083be4c7bf56223ca2be840ce5", "index_sha256_by_nlist": {"1024": "5f32249faa2c257731177ed7aecc0674a057d0be1a397294687bd52cf5039edf", "4096": "1e94935ec84cb190d6d564209fae5ba416028c1289d76a1f16c5c644781b0d24"}, "k_values": [10, 64, 128, 256, 512, 768], "strict_pruning_rule": "max(0,signed_hamming_query_centroid-list_radius)>flat_dk_v2"}, "Ball-IVF contract differs")
    require(faiss.__version__ == value["faiss_version"], "Ball-IVF Faiss version differs")
    return value


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    manifest_path = args.input_root / "manifest.json"; manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(sha256(manifest_path) == contract["input_manifest_sha256"] and manifest["document_count"] == 25000 and manifest["query_count"] == 648 and manifest["code_bits"] == 256, "Ball-IVF input differs")
    require(sha256(args.reference_shortlist) == contract["reference_flat_shortlist_sha256"], "Ball-IVF Flat reference differs")
    reference = json.loads(args.reference_shortlist.read_text(encoding="utf-8")); positions = [int(row["query_position"]) for row in reference["rows"]]
    require(len(positions) == 648 and len(set(positions)) == 648, "Ball-IVF query order differs")
    documents, queries = codes(args.input_root / manifest["document_codes_file"], 25000), codes(args.input_root / manifest["query_codes_file"], 648)
    result: list[dict[str, Any]] = []
    for nlist_text, expected_hash in contract["index_sha256_by_nlist"].items():
        nlist = int(nlist_text); artifact = args.index_root / f"binaryivf-nlist{nlist}.faiss"
        require(artifact.is_file() and sha256(artifact) == expected_hash, f"Ball-IVF index artifact differs: nlist{nlist}")
        index = faiss.read_index_binary(str(artifact)); require(index.d == 256 and index.ntotal == 25000 and index.nlist == nlist, f"Ball-IVF index metadata differs: nlist{nlist}")
        _, assignments = index.quantizer.search(documents, 1); assignments = assignments[:, 0]
        centroids = numpy.vstack([index.quantizer.reconstruct(item) for item in range(nlist)])
        document_to_centroid = hamming(documents, centroids[assignments])
        radii = numpy.zeros(nlist, dtype=numpy.uint16); numpy.maximum.at(radii, assignments, document_to_centroid)
        sizes = numpy.bincount(assignments, minlength=nlist)
        list_values = {k: [] for k in contract["k_values"]}; document_values = {k: [] for k in contract["k_values"]}
        for position in positions:
            distances = hamming(documents, queries[position]); cutoffs = {k: int(numpy.partition(distances, k - 1)[k - 1]) for k in contract["k_values"]}
            centroid_distance = hamming(centroids, queries[position]).astype(numpy.int32)
            lower_bounds = numpy.maximum(0, centroid_distance - radii.astype(numpy.int32))
            for k, cutoff in cutoffs.items():
                mask = lower_bounds > cutoff
                list_values[k].append(float(mask.mean())); document_values[k].append(float(sizes[mask].sum()) / 25000.0)
        result.append({"nlist": nlist, "index_sha256": expected_hash, "non_empty_lists": int(numpy.count_nonzero(sizes)), "mean_list_radius": float(radii[sizes > 0].mean()), "p95_list_radius": float(numpy.quantile(radii[sizes > 0], .95)), "prunable_list_fraction": {str(k): float(numpy.mean(list_values[k])) for k in contract["k_values"]}, "prunable_document_fraction": {str(k): float(numpy.mean(document_values[k])) for k in contract["k_values"]}})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "faiss_version": faiss.__version__, "input_manifest_sha256": sha256(manifest_path), "reference_shortlist_sha256": sha256(args.reference_shortlist), "strict_pruning_rule": contract["strict_pruning_rule"], "rows": result}, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test() -> None:
    require(numpy.maximum(0, numpy.asarray([10], dtype=numpy.int32) - numpy.asarray([20], dtype=numpy.int32)).tolist() == [0], "Ball-IVF signed lower bound differs")
    print("Ball-IVF pruning diagnostic self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "ball-ivf-pruning.example.json"); parser.add_argument("--input-root", type=Path); parser.add_argument("--reference-shortlist", type=Path); parser.add_argument("--index-root", type=Path); parser.add_argument("--output", type=Path); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    try:
        if args.self_test: self_test(); return 0
        if None in (args.input_root, args.reference_shortlist, args.index_root, args.output): parser.error("--input-root, --reference-shortlist, --index-root, and --output are required")
        run(args); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"diagnose-ball-ivf-pruning: {error}"); return 1


if __name__ == "__main__": raise SystemExit(main())
