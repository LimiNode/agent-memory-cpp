#!/usr/bin/env python3
"""Measure query- versus semantic-anchor-centred binary search.

The runner is deliberately a ceiling experiment.  It never changes document
identity: ``document_codes`` remain frozen ITQ codes and anchors only provide
candidate membership and alternative Hamming centres.  A real input is an NPZ
with float32 ``documents``/``queries``, uint8 ``document_codes``/``query_codes``
and, for each of ``centroid`` and ``prototype``, ``*_vectors``, ``*_codes``,
``*_offsets`` and ``*_documents`` posting arrays.  ``target_documents`` may be
provided as a ``Q x K`` array; otherwise exact FP32 top ten is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy


THIS = Path(__file__).resolve().parent
POPCOUNT = numpy.asarray([int(value).bit_count() for value in range(256)], dtype=numpy.uint8)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1
            and value.get("family") == "neuroute_semantic_anchor_relocation_ceiling",
            "semantic-anchor contract differs")
    require(value.get("controls") == ["q_global", "q_restricted", "c_seeded",
                                        "p_seeded", "p_oracle"],
            "semantic-anchor controls differ")
    return value


def hamming(left: numpy.ndarray, right: numpy.ndarray) -> numpy.ndarray:
    xor = numpy.bitwise_xor(left, right)
    return POPCOUNT[xor].sum(axis=1, dtype=numpy.uint16)


def probe_estimate(bits: int, radius: int, bands: int = 8) -> int:
    """Analytic equal-band probe count, reported as a diagnostic only."""
    widths = [bits // bands] * bands
    for index in range(bits % bands):
        widths[index] += 1
    result = 0
    for width in widths:
        result += sum(math.comb(width, depth) for depth in range(min(radius, width) + 1))
    return int(result)


def top_anchor_indices(query: numpy.ndarray, vectors: numpy.ndarray, count: int) -> numpy.ndarray:
    scores = numpy.asarray(vectors @ query, dtype=numpy.float32)
    count = min(count, len(scores))
    order = numpy.lexsort((numpy.arange(len(scores), dtype=numpy.int64), -scores))
    return order[:count].astype(numpy.int64)


def posting_union(offsets: numpy.ndarray, postings: numpy.ndarray,
                  anchors: numpy.ndarray) -> tuple[numpy.ndarray, int]:
    chunks = [postings[int(offsets[index]):int(offsets[index + 1])] for index in anchors]
    if not chunks:
        return numpy.empty(0, dtype=numpy.int64), 0
    raw = numpy.concatenate(chunks).astype(numpy.int64, copy=False)
    return numpy.unique(raw), int(raw.size)


def exact_targets(documents: numpy.ndarray, queries: numpy.ndarray, count: int = 10) -> numpy.ndarray:
    result = numpy.empty((len(queries), min(count, len(documents))), dtype=numpy.int64)
    ids = numpy.arange(len(documents), dtype=numpy.int64)
    for row, query in enumerate(queries):
        scores = numpy.asarray(documents @ query, dtype=numpy.float32)
        order = numpy.lexsort((ids, -scores))
        result[row] = order[:result.shape[1]]
    return result


def summarize_distances(distances: numpy.ndarray, radii: list[int]) -> dict[str, Any]:
    values = numpy.asarray(distances, dtype=numpy.float64)
    return {
        "r50": float(numpy.quantile(values, 0.50)),
        "r90": float(numpy.quantile(values, 0.90)),
        "r95": float(numpy.quantile(values, 0.95)),
        "r99": float(numpy.quantile(values, 0.99)),
        "within_radius": {str(radius): float(numpy.mean(values <= radius))
                          for radius in radii},
    }


def evaluate(npz: Any, contract: dict[str, Any]) -> dict[str, Any]:
    files = set(npz.files) if hasattr(npz, "files") else set(npz)
    required = {"documents", "queries", "document_codes", "query_codes"}
    require(required.issubset(files), "semantic-anchor NPZ is missing base arrays")
    documents = numpy.asarray(npz["documents"], dtype=numpy.float32)
    queries = numpy.asarray(npz["queries"], dtype=numpy.float32)
    document_codes = numpy.asarray(npz["document_codes"], dtype=numpy.uint8)
    query_codes = numpy.asarray(npz["query_codes"], dtype=numpy.uint8)
    require(documents.ndim == 2 and queries.ndim == 2 and documents.shape[1] == queries.shape[1],
            "semantic-anchor vector dimensions differ")
    bits = int(document_codes.shape[1] * 8)
    require(bits == contract["code_bits"] and document_codes.shape[0] == len(documents)
            and query_codes.shape == (len(queries), document_codes.shape[1]),
            "semantic-anchor code dimensions differ")
    targets = (numpy.asarray(npz["target_documents"], dtype=numpy.int64)
               if "target_documents" in files else exact_targets(documents, queries))
    require(targets.ndim == 2 and targets.shape[0] == len(queries),
            "semantic-anchor target shape differs")
    radii = [int(value) for value in contract["radii"]]
    budgets = [int(value) for value in contract["budgets"]]
    output: dict[str, Any] = {"family": contract["family"], "schema_version": 1,
                              "queries": len(queries), "documents": len(documents),
                              "code_bits": bits, "controls": {}}
    has_adc = {"query_projection", "adc_centroids"}.issubset(files)
    if has_adc:
        query_projection = numpy.asarray(npz["query_projection"], dtype=numpy.float32)
        adc_centroids = numpy.asarray(npz["adc_centroids"], dtype=numpy.float32)
        require(query_projection.shape == (len(queries), 256)
                and adc_centroids.shape == (256, 2),
                "semantic-anchor ADC arrays differ")

    anchor_data: dict[str, dict[str, numpy.ndarray]] = {}
    for kind in ("centroid", "prototype"):
        names = {name: f"{kind}_{name}" for name in ("vectors", "codes", "offsets", "documents")}
        if not all(name in files for name in names.values()):
            continue
        anchor_data[kind] = {name: numpy.asarray(npz[array], dtype=(numpy.float32 if name == "vectors" else None))
                             for name, array in names.items()}
        anchor_data[kind]["codes"] = numpy.asarray(npz[names["codes"]], dtype=numpy.uint8)
        anchor_data[kind]["offsets"] = numpy.asarray(npz[names["offsets"]], dtype=numpy.int64)
        anchor_data[kind]["documents"] = numpy.asarray(npz[names["documents"]], dtype=numpy.int64)

    def one(control: str, query_index: int, requested_count: int) -> tuple[set[int], numpy.ndarray, int, int, int]:
        query = queries[query_index]
        qcode = query_codes[query_index]
        if control == "q_global":
            candidate = numpy.arange(len(documents), dtype=numpy.int64)
            centers = qcode[None, :]
        else:
            kind = "centroid" if control in ("q_restricted", "c_seeded") else "prototype"
            require(kind in anchor_data, f"{kind} anchor arrays are required for {control}")
            data = anchor_data[kind]
            if control == "p_oracle":
                target_vectors = documents[targets[query_index]]
                oracle_scores = numpy.asarray(target_vectors @ data["vectors"].T,
                                              dtype=numpy.float32).max(axis=0)
                order = numpy.lexsort((numpy.arange(len(oracle_scores)), -oracle_scores))
                selected = order[:min(requested_count, len(order))]
            else:
                selected = top_anchor_indices(query, data["vectors"], requested_count)
            if control == "q_restricted":
                centers = qcode[None, :]
            else:
                centers = data["codes"][selected]
            candidate, raw_count = posting_union(data["offsets"], data["documents"], selected)
            return (set(map(int, candidate.tolist())), centers, int(len(selected)),
                    raw_count, int(len(candidate)))
        return set(map(int, candidate.tolist())), centers, 0, int(len(candidate)), int(len(candidate))

    for control in contract["controls"]:
        variants: dict[str, Any] = {}
        counts = [0] if control == "q_global" else [int(value) for value in contract["anchor_counts"]]
        for requested_count in counts:
            per_query: list[dict[str, Any]] = []
            for query_index in range(len(queries)):
                (candidate_set, centers, anchor_count, raw_posting_count,
                 unique_posting_count) = one(control, query_index, requested_count)
                candidate = numpy.asarray(sorted(candidate_set), dtype=numpy.int64)
                if candidate.size:
                    distances = numpy.minimum.reduce([hamming(document_codes[candidate], center)
                                                      for center in centers])
                else:
                    distances = numpy.empty(0, dtype=numpy.uint16)
                target = targets[query_index]
                target_set = set(map(int, target.tolist()))
                target_distances = []
                for document_id in target.tolist():
                    if int(document_id) in candidate_set:
                        index = int(numpy.searchsorted(candidate, document_id))
                        target_distances.append(int(distances[index]))
                radius_stats = summarize_distances(numpy.asarray(target_distances or [bits]), radii)
                budget_rows = []
                order = (numpy.lexsort((candidate, distances)) if candidate.size
                         else numpy.empty(0, dtype=numpy.int64))
                for budget in budgets:
                    chosen = (candidate[order[:min(budget, len(order))]]
                              if candidate.size else candidate)
                    chosen_set = set(map(int, chosen.tolist()))
                    budget_rows.append({
                        "budget": budget,
                        "target_survival": float(len(chosen_set & target_set) / max(len(target_set), 1)),
                        "unique_candidates": int(len(chosen)),
                    })
                    if has_adc and chosen.size:
                        chosen_codes = document_codes[chosen]
                        chosen_bits = numpy.unpackbits(chosen_codes, axis=1,
                                                       bitorder="little")[:, :256]
                        table = ((query_projection[query_index, :, None]
                                  - adc_centroids) ** 2)
                        adc_distances = table[numpy.arange(256)[None, :], chosen_bits].sum(axis=1)
                        adc_order = numpy.lexsort((chosen, adc_distances))[:min(64, len(chosen))]
                        adc_docs = chosen[adc_order]
                        exact_order = numpy.lexsort(
                            (adc_docs, -numpy.asarray(documents[adc_docs]
                                                       @ queries[query_index], dtype=numpy.float32)))
                        final_docs = adc_docs[exact_order[:min(10, len(adc_docs))]]
                        budget_rows[-1]["adc_target_survival"] = float(
                            len(set(map(int, adc_docs.tolist())) & target_set)
                            / max(len(target_set), 1))
                        budget_rows[-1]["final_top10_overlap"] = float(
                            len(set(map(int, final_docs.tolist())) & target_set)
                            / max(min(10, len(target)), 1))
                per_query.append({
                    "query_index": query_index, "anchor_count": anchor_count,
                    "raw_posting_entries_scanned": raw_posting_count,
                    "unique_documents_after_union": unique_posting_count,
                    "candidate_count": int(len(candidate)),
                    "target_in_restricted_region": float(len(candidate_set & target_set) / max(len(target_set), 1)),
                    "radius": radius_stats,
                    "budget": budget_rows,
                    "analytic_equal_band_probe_estimate_r3": probe_estimate(bits, 3),
                })
            variants[str(requested_count)] = {
                "mean_candidate_count": float(numpy.mean([row["candidate_count"] for row in per_query])),
                "mean_raw_posting_entries_scanned": float(numpy.mean([
                    row["raw_posting_entries_scanned"] for row in per_query])),
                "mean_unique_documents_after_union": float(numpy.mean([
                    row["unique_documents_after_union"] for row in per_query])),
                "mean_target_region_retention": float(numpy.mean([row["target_in_restricted_region"] for row in per_query])),
                "mean_target_survival": {
                    str(budget): float(numpy.mean([next(item["target_survival"] for item in row["budget"] if item["budget"] == budget)
                                               for row in per_query])) for budget in budgets},
                "mean_final_top10_overlap": ({
                    str(budget): float(numpy.mean([
                        next(item.get("final_top10_overlap", 0.0)
                             for item in row["budget"] if item["budget"] == budget)
                        for row in per_query])) for budget in budgets
                } if has_adc else None),
                "radius": {key: float(numpy.mean([row["radius"][key] for row in per_query]))
                           for key in ("r50", "r90", "r95", "r99")},
                "per_query": per_query,
            }
        output["controls"][control] = variants
    return output


def synthetic() -> dict[str, Any]:
    rng = numpy.random.default_rng(7)
    documents = rng.normal(size=(96, 256)).astype(numpy.float32)
    documents /= numpy.linalg.norm(documents, axis=1, keepdims=True)
    queries = documents[:8] + 0.02 * rng.normal(size=(8, 256)).astype(numpy.float32)
    queries /= numpy.linalg.norm(queries, axis=1, keepdims=True)
    def codes(values: numpy.ndarray) -> numpy.ndarray:
        return numpy.packbits(values >= 0, axis=1, bitorder="little")
    document_codes, query_codes = codes(documents), codes(queries)
    anchors = documents[::8]
    offsets = numpy.arange(0, 97, 8, dtype=numpy.int64)
    postings = numpy.arange(96, dtype=numpy.int64)
    return {"documents": documents, "queries": queries, "document_codes": document_codes,
            "query_codes": query_codes, "centroid_vectors": anchors, "centroid_codes": codes(anchors),
            "centroid_offsets": offsets, "centroid_documents": postings,
            "prototype_vectors": anchors, "prototype_codes": codes(anchors),
            "prototype_offsets": offsets, "prototype_documents": postings,
            "query_projection": numpy.zeros((len(queries), 256), dtype=numpy.float32),
            "adc_centroids": numpy.zeros((256, 2), dtype=numpy.float32)}


def self_test(contract_path: Path) -> int:
    try:
        contract = load_contract(contract_path)
        values = synthetic()
        result = evaluate(values, contract)
        require(set(result["controls"]) == set(contract["controls"]), "control rows differ")
        require(result["controls"]["q_global"]["0"]["mean_candidate_count"] == 96.0,
                "global candidate ceiling differs")
        require(result["controls"]["q_restricted"]["1"]["mean_candidate_count"] < 96.0,
                "restricted control did not restrict")
        require(all(any(variant["per_query"] for variant in result["controls"][name].values())
                     for name in contract["controls"]),
                "per-query diagnostics missing")
        require(result["controls"]["p_seeded"]["8"]["mean_final_top10_overlap"] is not None,
                "full R4 replay diagnostics missing")
        union, raw = posting_union(numpy.asarray([0, 3, 6]),
                                   numpy.asarray([1, 2, 3, 3, 4, 5]),
                                   numpy.asarray([0, 1]))
        require(raw == 6 and len(union) == 5,
                "raw posting-entry accounting differs")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"semantic-anchor runner self-test failed: {error}", file=sys.stderr)
        return 1
    print("Semantic-anchor relocation runner self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "self-test"))
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-semantic-anchor-relocation.example.json")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "self-test":
            return self_test(args.contract)
        require(args.input is not None and args.output is not None,
                "run requires --input and --output")
        contract = load_contract(args.contract)
        result = evaluate(numpy.load(args.input, allow_pickle=False), contract)
        result["input_sha256"] = sha256(args.input)
        result["contract_sha256"] = sha256(args.contract)
        result["runner_sha256"] = sha256(Path(__file__))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"run-neuroute-semantic-anchor-relocation: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
