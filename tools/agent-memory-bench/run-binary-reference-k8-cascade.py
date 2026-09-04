#!/usr/bin/env python3
"""Replay RaBitQ-RR-1 and BBQ-block-1 through the semantic K8 cascade.

The input is the frozen semantic-anchor NPZ used by the NeuRoute studies.  The
script derives prototype-to-address ownership from the authoritative centroid
and prototype postings, ranks addresses with each binary reference, optionally
applies exact local K8 refinement, and executes the frozen Hamming768 -> ADC64
-> exact top-10 document cascade.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from binary_code_references import BBQLikeReference, RabitQReference, format_manifest


POPCOUNT = np.asarray([int(value).bit_count() for value in range(256)], dtype=np.uint8)


def top(values: np.ndarray, count: int, largest: bool = False) -> np.ndarray:
    count = min(int(count), values.size)
    key = -values if largest else values
    selected = np.argpartition(key, count - 1)[:count]
    return selected[np.argsort(key[selected], kind="stable")]


def percentile(values: list[float], q: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=np.float64), q))


def prototype_owners(data: object) -> np.ndarray:
    centroid_offsets = np.asarray(data["centroid_offsets"], dtype=np.int64)
    centroid_documents = np.asarray(data["centroid_documents"], dtype=np.int64)
    prototype_offsets = np.asarray(data["prototype_offsets"], dtype=np.int64)
    prototype_documents = np.asarray(data["prototype_documents"], dtype=np.int64)
    document_owner = np.full(int(np.max(centroid_documents)) + 1, -1, dtype=np.int32)
    for address in range(centroid_offsets.size - 1):
        document_owner[centroid_documents[centroid_offsets[address] : centroid_offsets[address + 1]]] = address
    first_documents = prototype_documents[prototype_offsets[:-1]]
    owners = document_owner[first_documents]
    if np.any(owners < 0):
        raise ValueError("prototype posting has no authoritative address owner")
    # Validate that every prototype posting remains inside its derived address.
    for prototype in range(prototype_offsets.size - 1):
        docs = prototype_documents[prototype_offsets[prototype] : prototype_offsets[prototype + 1]]
        if np.any(document_owner[docs] != owners[prototype]):
            raise ValueError("prototype posting crosses authoritative addresses")
    return owners


def address_documents(addresses: np.ndarray, offsets: np.ndarray, postings: np.ndarray) -> np.ndarray:
    chunks = [postings[offsets[value] : offsets[value + 1]] for value in addresses]
    return np.unique(np.concatenate(chunks)) if chunks else np.empty(0, dtype=np.int64)


def cascade(data: object, query_index: int, candidates: np.ndarray) -> tuple[np.ndarray, float, float]:
    started = time.perf_counter()
    codes = np.asarray(data["document_codes"])[candidates]
    qcode = np.asarray(data["query_codes"])[query_index]
    hamming = POPCOUNT[np.bitwise_xor(codes, qcode)].sum(axis=1)
    hamming_docs = candidates[top(hamming, 768)]
    hamming_ms = (time.perf_counter() - started) * 1000.0
    started = time.perf_counter()
    bits = np.unpackbits(np.asarray(data["document_codes"])[hamming_docs], axis=1, bitorder="little")[:, :256]
    projection = np.asarray(data["query_projection"])[query_index]
    centroids = np.asarray(data["adc_centroids"])
    table = (projection[:, None] - centroids) ** 2
    adc = table[np.arange(256)[None, :], bits].sum(axis=1)
    adc_docs = hamming_docs[top(adc, 64)]
    adc_ms = (time.perf_counter() - started) * 1000.0
    query = np.asarray(data["queries"])[query_index]
    exact = np.asarray(data["documents"])[adc_docs] @ query
    return adc_docs[top(exact, 10, largest=True)], hamming_ms, adc_ms


def rank_ndcg(actual: np.ndarray, target: np.ndarray) -> float:
    gain = {int(doc): float(10 - rank) for rank, doc in enumerate(target)}
    dcg = sum(gain.get(int(doc), 0.0) / np.log2(rank + 2.0) for rank, doc in enumerate(actual))
    ideal = sum(float(10 - rank) / np.log2(rank + 2.0) for rank in range(10))
    return float(dcg / ideal)


def evaluate_codec(name: str, codec: object, data: object, owners: np.ndarray, exact_scores: np.ndarray, budgets: list[int]) -> list[dict[str, object]]:
    approximate = codec.scores_batch(np.asarray(data["queries"], dtype=np.float32))  # type: ignore[attr-defined]
    address_count = int(np.asarray(data["centroid_offsets"]).size - 1)
    offsets = np.asarray(data["centroid_offsets"], dtype=np.int64)
    postings = np.asarray(data["centroid_documents"], dtype=np.int64)
    targets = np.asarray(data["target_documents"], dtype=np.int64)
    rows: list[dict[str, object]] = []
    for budget in budgets:
        for exact_local in (False, True):
            overlaps: list[float] = []
            ndcgs: list[float] = []
            route_ms: list[float] = []
            hamming_ms: list[float] = []
            adc_ms: list[float] = []
            candidate_counts: list[int] = []
            for query_index in range(approximate.shape[0]):
                started = time.perf_counter()
                approximate_address = np.full(address_count, -np.inf, dtype=np.float32)
                np.maximum.at(approximate_address, owners, approximate[query_index])
                pool_size = min(address_count, budget * int(codec.oversample))  # type: ignore[attr-defined]
                pool = top(approximate_address, pool_size, largest=True)
                if exact_local:
                    exact_address = np.full(address_count, -np.inf, dtype=np.float32)
                    np.maximum.at(exact_address, owners, exact_scores[query_index])
                    addresses = pool[top(exact_address[pool], budget, largest=True)]
                else:
                    addresses = pool[:budget]
                route_ms.append((time.perf_counter() - started) * 1000.0)
                candidates = address_documents(addresses, offsets, postings)
                candidate_counts.append(int(candidates.size))
                result, h_ms, a_ms = cascade(data, query_index, candidates)
                target = targets[query_index]
                overlaps.append(float(np.intersect1d(result, target).size) / 10.0)
                ndcgs.append(rank_ndcg(result, target))
                hamming_ms.append(h_ms)
                adc_ms.append(a_ms)
            rows.append({
                "method": name,
                "spec": codec.spec,  # type: ignore[attr-defined]
                "format": format_manifest(codec),
                "bits": int(codec.bits),  # type: ignore[attr-defined]
                "address_budget": budget,
                "with_exact_local_k8": exact_local,
                "mean_final_top10_overlap": float(np.mean(overlaps)),
                "final_top10_overlap_p05": percentile(overlaps, 0.05),
                "final_top10_overlap_worst_query": float(np.min(overlaps)),
                "mean_rank_ndcg_at_10": float(np.mean(ndcgs)),
                "mean_candidate_documents": float(np.mean(candidate_counts)),
                "route_ms_p95": percentile(route_ms, 0.95),
                "hamming768_ms_p95": percentile(hamming_ms, 0.95),
                "adc64_ms_p95": percentile(adc_ms, 0.95),
                "payload_bytes": int(codec.payload_bytes),  # type: ignore[attr-defined]
                "model_bytes": int(codec.model_bytes),  # type: ignore[attr-defined]
                "index_bytes": int(owners.nbytes + offsets.nbytes + postings.nbytes),
            })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bits", default="128,192,256")
    parser.add_argument("--budgets", default="1024,2048,4096,8192")
    parser.add_argument("--blocks", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--oversample", type=int, default=4)
    args = parser.parse_args()
    archive = np.load(args.input, allow_pickle=False)
    required = (
        "documents", "queries", "document_codes", "query_codes",
        "target_documents", "query_projection", "adc_centroids",
        "centroid_offsets", "centroid_documents", "prototype_vectors",
        "prototype_offsets", "prototype_documents",
    )
    data = {name: np.asarray(archive[name]) for name in required}
    prototypes = np.asarray(data["prototype_vectors"], dtype=np.float32)
    queries = np.asarray(data["queries"], dtype=np.float32)
    owners = prototype_owners(data)
    exact_scores = queries @ prototypes.T
    rows: list[dict[str, object]] = []
    for bits in [int(value) for value in args.bits.split(",")]:
        rows.extend(evaluate_codec("rabitq_reference", RabitQReference.fit(prototypes, bits, args.seed, args.oversample), data, owners, exact_scores, [int(value) for value in args.budgets.split(",")]))
        rows.extend(evaluate_codec("bbq_like_reference", BBQLikeReference.fit(prototypes, bits, args.blocks, args.seed, args.oversample), data, owners, exact_scores, [int(value) for value in args.budgets.split(",")]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"schema_version": 1, "family": "binary_reference_k8_cascade", "rows": rows}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
