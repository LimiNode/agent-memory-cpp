#!/usr/bin/env python3
"""Measure binary geometry separately from semantic-anchor posting coverage."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy


THIS = Path(__file__).resolve().parent
POPCOUNT = numpy.asarray([int(value).bit_count() for value in range(256)],
                         dtype=numpy.uint8)
CONTROLS = ["q_global", "q_centroid_restricted", "q_prototype_restricted",
            "centroid_seeded", "prototype_seeded", "prototype_semantic_oracle",
            "prototype_hamming_oracle", "medoid_seeded", "median_seeded"]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and
            value.get("family") == "neuroute_semantic_anchor_geometry_ceiling" and
            value.get("controls") == CONTROLS and
            value.get("anchor_counts") == [1, 2, 4, 8],
            "semantic-anchor geometry contract differs")
    return value


def hamming_rows(left: numpy.ndarray, right: numpy.ndarray) -> numpy.ndarray:
    return POPCOUNT[numpy.bitwise_xor(left, right)].sum(axis=-1,
                                                        dtype=numpy.uint16)


def summarize(values: numpy.ndarray, radii: list[int]) -> dict[str, Any]:
    data = numpy.asarray(values, dtype=numpy.float64)
    return {"r50": float(numpy.quantile(data, .50)),
            "r90": float(numpy.quantile(data, .90)),
            "r95": float(numpy.quantile(data, .95)),
            "r99": float(numpy.quantile(data, .99)),
            "within_radius": {str(radius): float(numpy.mean(data <= radius))
                              for radius in radii}}


def top_indices(query: numpy.ndarray, vectors: numpy.ndarray,
                count: int) -> numpy.ndarray:
    scores = numpy.asarray(vectors @ query, dtype=numpy.float32)
    order = numpy.lexsort((numpy.arange(len(scores), dtype=numpy.int64), -scores))
    return order[:min(count, len(order))].astype(numpy.int64)


def posting_union(offsets: numpy.ndarray, postings: numpy.ndarray,
                  selected: numpy.ndarray) -> tuple[numpy.ndarray, int]:
    chunks = [postings[int(offsets[index]):int(offsets[index + 1])]
              for index in selected]
    if not chunks:
        return numpy.empty(0, dtype=numpy.int64), 0
    raw = numpy.concatenate(chunks).astype(numpy.int64, copy=False)
    return numpy.unique(raw), int(raw.size)


def derive_document_centres(documents: numpy.ndarray, document_codes: numpy.ndarray,
                            offsets: numpy.ndarray, postings: numpy.ndarray,
                            centroid_vectors: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray]:
    """Return document medoids and unconstrained bitwise medians per address."""
    medoid_ids = numpy.empty(len(centroid_vectors), dtype=numpy.int64)
    medoid_codes = numpy.empty((len(centroid_vectors), document_codes.shape[1]),
                               dtype=numpy.uint8)
    median_codes = numpy.empty_like(medoid_codes)
    for address in range(len(centroid_vectors)):
        ids = postings[int(offsets[address]):int(offsets[address + 1])]
        codes = document_codes[ids]
        distances = hamming_rows(codes[:, None, :], codes[None, :, :]).sum(axis=1)
        medoid_ids[address] = int(ids[int(numpy.argmin(distances))])
        medoid_codes[address] = document_codes[medoid_ids[address]]
        bits = numpy.unpackbits(codes, axis=1, bitorder="little")[:, :document_codes.shape[1] * 8]
        median_codes[address] = numpy.packbits(numpy.mean(bits, axis=0) >= .5,
                                               bitorder="little")
    return documents[medoid_ids].astype(numpy.float32), medoid_codes, median_codes


def hamming_oracle_indices(target_codes: numpy.ndarray, anchor_codes: numpy.ndarray,
                           count: int, batch: int = 65536) -> numpy.ndarray:
    """Select anchors nearest to any exact target in the frozen code space."""
    best = numpy.full(len(anchor_codes), numpy.iinfo(numpy.uint16).max,
                      dtype=numpy.uint16)
    anchor_words = anchor_codes.reshape(len(anchor_codes), -1, 8).view("<u8").reshape(len(anchor_codes), -1)
    for start in range(0, len(anchor_codes), batch):
        stop = min(start + batch, len(anchor_codes))
        local = anchor_words[start:stop]
        local_best = numpy.full(stop - start, numpy.iinfo(numpy.uint16).max,
                                dtype=numpy.uint16)
        for target in target_codes:
            target_words = target.reshape(-1, 8).view("<u8").reshape(-1)
            distances = numpy.bitwise_count(local ^ target_words[None, :]).sum(axis=1,
                                                                                dtype=numpy.uint16)
            local_best = numpy.minimum(local_best, distances)
        best[start:stop] = local_best
    order = numpy.lexsort((numpy.arange(len(best), dtype=numpy.int64), best))
    return order[:min(count, len(order))].astype(numpy.int64)


def evaluate(npz: Any, contract: dict[str, Any]) -> dict[str, Any]:
    files = set(npz.files) if hasattr(npz, "files") else set(npz)
    required = {"documents", "queries", "document_codes", "query_codes",
                "target_documents", "query_projection", "adc_centroids"}
    require(required.issubset(files), "geometry input arrays are missing")
    documents = numpy.asarray(npz["documents"], dtype=numpy.float32)
    queries = numpy.asarray(npz["queries"], dtype=numpy.float32)
    document_codes = numpy.asarray(npz["document_codes"], dtype=numpy.uint8)
    query_codes = numpy.asarray(npz["query_codes"], dtype=numpy.uint8)
    targets = numpy.asarray(npz["target_documents"], dtype=numpy.int64)
    query_projection = numpy.asarray(npz["query_projection"], dtype=numpy.float32)
    adc_centroids = numpy.asarray(npz["adc_centroids"], dtype=numpy.float32)
    require(document_codes.shape[0] == len(documents) and
            query_codes.shape[0] == len(queries) and targets.shape[0] == len(queries),
            "geometry base array shapes differ")
    anchor: dict[str, dict[str, numpy.ndarray]] = {}
    for kind in ("centroid", "prototype"):
        names = {name: f"{kind}_{name}" for name in
                 ("vectors", "codes", "offsets", "documents")}
        require(set(names.values()).issubset(files), f"{kind} arrays are missing")
        anchor[kind] = {name: numpy.asarray(npz[array], dtype=(numpy.float32 if name == "vectors" else numpy.int64))
                        for name, array in names.items()}
        anchor[kind]["codes"] = numpy.asarray(npz[names["codes"]], dtype=numpy.uint8)
    medoid_vectors, medoid_codes, median_codes = derive_document_centres(
        documents, document_codes, anchor["centroid"]["offsets"],
        anchor["centroid"]["documents"], anchor["centroid"]["vectors"])
    anchor["medoid"] = {"vectors": medoid_vectors, "codes": medoid_codes,
                         "offsets": anchor["centroid"]["offsets"],
                         "documents": anchor["centroid"]["documents"]}
    anchor["median"] = {"vectors": anchor["centroid"]["vectors"],
                         "codes": median_codes,
                         "offsets": anchor["centroid"]["offsets"],
                         "documents": anchor["centroid"]["documents"]}
    radii = [int(value) for value in contract["radii"]]
    budgets = [int(value) for value in contract["budgets"]]
    result: dict[str, Any] = {"schema_version": 1,
                              "family": contract["family"],
                              "queries": len(queries), "documents": len(documents),
                              "code_bits": int(document_codes.shape[1] * 8),
                              "controls": {}}

    selection_cache: dict[tuple[str, int], tuple[str, numpy.ndarray]] = {}

    def selected_for(control: str, query_index: int, count: int) -> tuple[str, numpy.ndarray]:
        query = queries[query_index]
        if control == "q_global":
            return "global", numpy.empty(0, dtype=numpy.int64)
        cache_key = (control, query_index)
        if cache_key not in selection_cache:
            maximum = max(int(value) for value in contract["anchor_counts"])
            if control == "q_centroid_restricted":
                selected = top_indices(query, anchor["centroid"]["vectors"], maximum)
                selection_cache[cache_key] = ("centroid", selected)
            elif control == "q_prototype_restricted":
                selected = top_indices(query, anchor["prototype"]["vectors"], maximum)
                selection_cache[cache_key] = ("prototype", selected)
            elif control == "centroid_seeded":
                selected = top_indices(query, anchor["centroid"]["vectors"], maximum)
                selection_cache[cache_key] = ("centroid", selected)
            elif control == "prototype_seeded":
                selected = top_indices(query, anchor["prototype"]["vectors"], maximum)
                selection_cache[cache_key] = ("prototype", selected)
            elif control == "medoid_seeded":
                selected = top_indices(query, anchor["medoid"]["vectors"], maximum)
                selection_cache[cache_key] = ("medoid", selected)
            elif control == "median_seeded":
                selected = top_indices(query, anchor["median"]["vectors"], maximum)
                selection_cache[cache_key] = ("median", selected)
            elif control == "prototype_semantic_oracle":
                target_vectors = documents[targets[query_index]]
                scores = numpy.asarray(target_vectors @ anchor["prototype"]["vectors"].T,
                                      dtype=numpy.float32).max(axis=0)
                order = numpy.lexsort((numpy.arange(len(scores)), -scores))
                selection_cache[cache_key] = ("prototype", order[:maximum])
            else:
                target_codes = document_codes[targets[query_index]]
                selection_cache[cache_key] = (
                    "prototype", hamming_oracle_indices(target_codes,
                        anchor["prototype"]["codes"], maximum))
        kind, selected = selection_cache[cache_key]
        return kind, selected[:count]

    for control in CONTROLS:
        variants: dict[str, Any] = {}
        counts = [0] if control == "q_global" else [int(value) for value in contract["anchor_counts"]]
        for count in counts:
            rows = []
            for query_index in range(len(queries)):
                kind, selected = selected_for(control, query_index, count)
                if kind == "global":
                    candidate = numpy.arange(len(documents), dtype=numpy.int64)
                    centers = query_codes[query_index:query_index + 1]
                    raw_count = len(candidate)
                else:
                    data = anchor[kind]
                    candidate, raw_count = posting_union(data["offsets"],
                                                        data["documents"], selected)
                    if control.startswith("q_"):
                        centers = query_codes[query_index:query_index + 1]
                    else:
                        centers = data["codes"][selected]
                target_codes = document_codes[targets[query_index]]
                all_distances = hamming_rows(target_codes[:, None, :],
                                             centers[None, :, :]).min(axis=1)
                target_set = set(map(int, targets[query_index].tolist()))
                candidate_set = set(map(int, candidate.tolist()))
                order = (numpy.lexsort((candidate, hamming_rows(
                    document_codes[candidate, None, :], centers[None, :, :]).min(axis=1)))
                         if candidate.size else numpy.empty(0, dtype=numpy.int64))
                budget_rows = []
                for budget in budgets:
                    chosen = candidate[order[:min(budget, len(order))]] if candidate.size else candidate
                    chosen_codes = document_codes[chosen]
                    if chosen.size:
                        bits = numpy.unpackbits(chosen_codes, axis=1,
                                                bitorder="little")[:, :256]
                        table = (query_projection[query_index, :, None] - adc_centroids) ** 2
                        adc = table[numpy.arange(256)[None, :], bits].sum(axis=1)
                        adc_docs = chosen[numpy.lexsort((chosen, adc))[:min(64, len(chosen))]]
                        exact = numpy.asarray(documents[adc_docs] @ queries[query_index], dtype=numpy.float32)
                        final_docs = adc_docs[numpy.lexsort((adc_docs, -exact))[:min(10, len(adc_docs))]]
                    else:
                        adc_docs = final_docs = chosen
                    budget_rows.append({"budget": budget,
                        "target_survival": float(len(set(map(int, chosen.tolist())) & target_set) / max(len(target_set), 1)),
                        "adc_target_survival": float(len(set(map(int, adc_docs.tolist())) & target_set) / max(len(target_set), 1)),
                        "final_top10_overlap": float(len(set(map(int, final_docs.tolist())) & target_set) / max(min(10, len(target_set)), 1)),
                        "unique_candidates": int(len(chosen))})
                rows.append({"query_index": query_index, "anchor_count": int(len(selected)),
                    "raw_posting_entries_scanned": int(raw_count),
                    "unique_documents_after_union": int(len(candidate)),
                    "target_in_restricted_region": float(len(candidate_set & target_set) / max(len(target_set), 1)),
                    "radius_all_targets": summarize(all_distances, radii),
                    "budget": budget_rows})
            variants[str(count)] = {
                "mean_candidate_count": float(numpy.mean([row["unique_documents_after_union"] for row in rows])),
                "mean_raw_posting_entries_scanned": float(numpy.mean([row["raw_posting_entries_scanned"] for row in rows])),
                "mean_target_region_retention": float(numpy.mean([row["target_in_restricted_region"] for row in rows])),
                "mean_radius_all_targets": {key: float(numpy.mean([row["radius_all_targets"][key] for row in rows]))
                                             for key in ("r50", "r90", "r95", "r99")},
                "mean_final_top10_overlap": {str(budget): float(numpy.mean([
                    next(item["final_top10_overlap"] for item in row["budget"] if item["budget"] == budget)
                    for row in rows])) for budget in budgets},
                "per_query": rows}
        result["controls"][control] = variants
    return result


def synthetic() -> dict[str, numpy.ndarray]:
    rng = numpy.random.default_rng(278)
    documents = rng.normal(size=(32, 256)).astype(numpy.float32)
    queries = documents[:4].copy()
    codes = numpy.packbits(documents >= 0, axis=1, bitorder="little")
    qcodes = numpy.packbits(queries >= 0, axis=1, bitorder="little")
    vectors = documents[::4]
    offsets = numpy.arange(0, 33, 4, dtype=numpy.int64)
    postings = numpy.arange(32, dtype=numpy.int64)
    return {"documents": documents, "queries": queries,
            "document_codes": codes, "query_codes": qcodes,
            "target_documents": numpy.arange(4, dtype=numpy.int64)[:, None],
            "query_projection": numpy.zeros((4, 256), dtype=numpy.float32),
            "adc_centroids": numpy.zeros((256, 2), dtype=numpy.float32),
            "centroid_vectors": vectors, "centroid_codes": codes[::4],
            "centroid_offsets": offsets, "centroid_documents": postings,
            "prototype_vectors": vectors, "prototype_codes": codes[::4],
            "prototype_offsets": offsets, "prototype_documents": postings}


def self_test(contract_path: Path) -> int:
    try:
        contract = load_contract(contract_path)
        value = evaluate(synthetic(), contract)
        require(set(value["controls"]) == set(CONTROLS), "geometry controls differ")
        require(value["controls"]["q_prototype_restricted"]["1"]["mean_target_region_retention"] >= 0.0,
                "prototype restricted control missing")
        require("radius_all_targets" in value["controls"]["median_seeded"]["1"]["per_query"][0],
                "unconditional radius diagnostics missing")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"semantic-anchor geometry runner self-test failed: {error}", file=sys.stderr)
        return 1
    print("Semantic-anchor geometry runner self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "self-test"))
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-semantic-anchor-geometry.example.json")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "self-test":
            return self_test(args.contract)
        require(args.input is not None and args.output is not None,
                "geometry run requires --input and --output")
        result = evaluate(numpy.load(args.input, allow_pickle=False),
                          load_contract(args.contract))
        result["input_sha256"] = sha256(args.input)
        result["contract_sha256"] = sha256(args.contract)
        result["runner_sha256"] = sha256(Path(__file__))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"run-neuroute-semantic-anchor-geometry-ceiling: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
