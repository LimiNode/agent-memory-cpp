#!/usr/bin/env python3
"""Measure deterministic binary-product locators on frozen ITQ-256 codes."""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy


THIS = Path(__file__).resolve().parent
FAMILY = "binary_product_locator_static_calibration_v1"
EXPORT_FAMILY = "native_ann_hamming_shortlist_export_v1"


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY, "binary-product contract identity differs")
    require(value.get("input") == {"document_count": 25000, "query_count": 648, "code_bits": 256, "manifest_sha256": "1d3e210edfca62d9019c2849fdb1494566556efd3e57f264d9ef31d599dee987"}, "binary-product input contract differs")
    require(value.get("reference_flat_shortlist_sha256") == "48da713381f0b7b9c36635f6c286541311c524083be4c7bf56223ca2be840ce5", "binary-product Flat reference differs")
    require(value.get("target_candidate_fractions") == [0.05, 0.10, 0.25], "binary-product candidate budgets differ")
    require(value.get("cascade") == {"hamming_limit": 768, "adc_limit": 256, "exact_limit": 256, "oracle_k": 10}, "binary-product cascade differs")
    schemes = value.get("schemes")
    require(schemes == [
        {"id": "repetition-b12-c2", "block_count": 12, "centers_per_block": 2, "prototype_family": "repetition_complement_v1"},
        {"id": "repetition-b16-c2", "block_count": 16, "centers_per_block": 2, "prototype_family": "repetition_complement_v1"},
        {"id": "walsh-b6-c4", "block_count": 6, "centers_per_block": 4, "prototype_family": "walsh_complement_prefix_v1"},
        {"id": "walsh-b4-c8", "block_count": 4, "centers_per_block": 8, "prototype_family": "walsh_complement_prefix_v1"},
    ], "binary-product scheme matrix differs")
    return value


def load_codes(path: Path, count: int) -> numpy.ndarray:
    words = numpy.fromfile(path, dtype="<u8")
    require(words.size == count * 4, "binary-product code payload differs")
    return numpy.unpackbits(words.reshape(count, 4).view(numpy.uint8), bitorder="little", axis=1)


def load_words(path: Path, count: int) -> numpy.ndarray:
    words = numpy.fromfile(path, dtype="<u8")
    require(words.size == count * 4, "binary-product word payload differs")
    return words.reshape(count, 4)


def block_bounds(block_count: int) -> list[tuple[int, int]]:
    return [(256 * index // block_count, 256 * (index + 1) // block_count) for index in range(block_count)]


def prototypes(width: int, centers: int, family: str) -> numpy.ndarray:
    require(centers in (2, 4, 8), "binary-product local center count differs")
    positions = numpy.arange(width, dtype=numpy.uint32)
    if family == "repetition_complement_v1":
        require(centers == 2, "repetition product center count differs")
        return numpy.stack((numpy.zeros(width, dtype=numpy.uint8), numpy.ones(width, dtype=numpy.uint8)))
    require(family == "walsh_complement_prefix_v1" and centers in (4, 8), "Walsh product prototype family differs")
    patterns: list[numpy.ndarray] = [numpy.zeros(width, dtype=numpy.uint8), numpy.ones(width, dtype=numpy.uint8)]
    for bit in range((centers - 2) // 2):
        row = (numpy.bitwise_count(positions & numpy.uint32(1 << bit)) & 1).astype(numpy.uint8)
        patterns.extend((row, 1 - row))
    return numpy.stack(patterns)


def assignments(bits: numpy.ndarray, scheme: dict[str, Any]) -> tuple[numpy.ndarray, list[numpy.ndarray], list[tuple[int, int]]]:
    bounds = block_bounds(int(scheme["block_count"])); centers = int(scheme["centers_per_block"])
    values = numpy.empty((bits.shape[0], len(bounds)), dtype=numpy.uint8)
    local_prototypes: list[numpy.ndarray] = []
    for index, (begin, end) in enumerate(bounds):
        local = prototypes(end - begin, centers, str(scheme["prototype_family"]))
        distances = numpy.count_nonzero(bits[:, None, begin:end] != local[None, :, :], axis=2)
        values[:, index] = numpy.argmin(distances, axis=1).astype(numpy.uint8)
        local_prototypes.append(local)
    return values, local_prototypes, bounds


def ids(values: numpy.ndarray, centers: int) -> numpy.ndarray:
    result = numpy.zeros(values.shape[0], dtype=numpy.uint64)
    for index in range(values.shape[1]):
        result = result * numpy.uint64(centers) + values[:, index].astype(numpy.uint64)
    return result


def posting_lists(document_ids: numpy.ndarray) -> dict[int, numpy.ndarray]:
    order = numpy.argsort(document_ids, kind="stable")
    sorted_ids = document_ids[order]
    starts = numpy.r_[0, numpy.flatnonzero(sorted_ids[1:] != sorted_ids[:-1]) + 1]
    ends = numpy.r_[starts[1:], sorted_ids.size]
    return {int(sorted_ids[start]): order[start:end] for start, end in zip(starts, ends)}


def local_cost_orders(query: numpy.ndarray, local_prototypes: list[numpy.ndarray], bounds: list[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    result: list[list[tuple[int, int]]] = []
    for prototype, (begin, end) in zip(local_prototypes, bounds):
        distance = numpy.count_nonzero(prototype != query[None, begin:end], axis=1)
        result.append(sorted(((int(item), int(index)) for index, item in enumerate(distance)), key=lambda item: (item[0], item[1])))
    return result


def enumerate_cells(orders: list[list[tuple[int, int]]], centers: int):
    origin = tuple(0 for _ in orders)
    heap: list[tuple[int, tuple[int, ...]]] = [(sum(order[0][0] for order in orders), origin)]
    emitted = {origin}
    while heap:
        cost, ranks = heapq.heappop(heap)
        digits = tuple(orders[index][rank][1] for index, rank in enumerate(ranks))
        cell = 0
        for digit in digits:
            cell = cell * centers + digit
        yield cost, cell
        for dimension in range(len(ranks)):
            next_rank = ranks[dimension] + 1
            if next_rank == len(orders[dimension]):
                continue
            next_ranks = list(ranks); next_ranks[dimension] = next_rank; key = tuple(next_ranks)
            if key not in emitted:
                emitted.add(key)
                heapq.heappush(heap, (cost - orders[dimension][ranks[dimension]][0] + orders[dimension][next_rank][0], key))


def choose_candidates(query: numpy.ndarray, local_prototypes: list[numpy.ndarray], bounds: list[tuple[int, int]], centers: int, postings: dict[int, numpy.ndarray], target: int) -> tuple[numpy.ndarray, int, int]:
    collected: list[numpy.ndarray] = []; probes = 0; occupied = 0; total = 0
    for _, cell in enumerate_cells(local_cost_orders(query, local_prototypes, bounds), centers):
        probes += 1
        posting = postings.get(cell)
        if posting is None:
            continue
        occupied += 1; collected.append(posting); total += posting.size
        if total >= target:
            break
    require(total >= target, "binary-product candidate budget is unreachable")
    return numpy.concatenate(collected), probes, occupied


def hamming_shortlist(document_words: numpy.ndarray, query_words: numpy.ndarray, candidates: numpy.ndarray, limit: int) -> numpy.ndarray:
    values = numpy.bitwise_count(numpy.bitwise_xor(document_words[candidates], query_words)).sum(axis=1)
    return candidates[numpy.lexsort((candidates, values))[:limit]]


def adc_positions(document_bits: numpy.ndarray, projection: numpy.ndarray, centroids: numpy.ndarray, candidates: numpy.ndarray, limit: int) -> numpy.ndarray:
    table = (projection[:, None] - centroids) ** 2
    distance = table[numpy.arange(256)[None, :], document_bits[candidates]].sum(axis=1)
    return candidates[numpy.lexsort((candidates, distance))[:limit]]


def percentile(values: list[float], fraction: float) -> float:
    return float(numpy.quantile(numpy.asarray(values, dtype=numpy.float64), fraction, method="linear"))


def run(args: argparse.Namespace, contract: dict[str, Any]) -> None:
    manifest_path = args.input_root / "manifest.json"; manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(sha256(manifest_path) == contract["input"]["manifest_sha256"] and manifest["document_count"] == 25000 and manifest["query_count"] == 648 and manifest["code_bits"] == 256, "binary-product frozen input differs")
    require(sha256(args.reference_shortlist) == contract["reference_flat_shortlist_sha256"], "binary-product Flat reference differs")
    reference = json.loads(args.reference_shortlist.read_text(encoding="utf-8")); query_positions = [int(row["query_position"]) for row in reference["rows"]]
    require(reference.get("backend") == "flat" and len(query_positions) == 648 and len(set(query_positions)) == 648, "binary-product reference query order differs")
    document_code_path = args.input_root / manifest["document_codes_file"]; query_code_path = args.input_root / manifest["query_codes_file"]
    document_bits = load_codes(document_code_path, 25000); query_bits = load_codes(query_code_path, 648)
    document_words = load_words(document_code_path, 25000); query_words = load_words(query_code_path, 648)
    projections = numpy.fromfile(args.input_root / manifest["query_itq_projections_file"], dtype="<f4").reshape(648, 256)
    centroids = numpy.fromfile(args.input_root / manifest["binary_adc_centroids_file"], dtype="<f4").reshape(256, 2)
    args.output_root.mkdir(parents=True, exist_ok=True); rows: list[dict[str, Any]] = []
    for scheme in contract["schemes"]:
        center_count = int(scheme["centers_per_block"]); document_assignment, local_prototypes, bounds = assignments(document_bits, scheme)
        postings = posting_lists(ids(document_assignment, center_count)); occupied_sizes = [int(value.size) for value in postings.values()]
        scheme_meta = {"id": scheme["id"], "block_bounds": bounds, "occupied_cell_count": len(postings), "occupied_cell_size_p50": percentile(occupied_sizes, .50), "occupied_cell_size_p95": percentile(occupied_sizes, .95), "maximum_global_cell_count": center_count ** int(scheme["block_count"])}
        for fraction in contract["target_candidate_fractions"]:
            target = max(contract["cascade"]["hamming_limit"], int(numpy.ceil(fraction * 25000)))
            exports: list[dict[str, Any]] = []; counts: list[float] = []; probes: list[float] = []; occupied: list[float] = []
            for position in query_positions:
                candidates, probe_count, occupied_count = choose_candidates(query_bits[position], local_prototypes, bounds, center_count, postings, target)
                shortlist = hamming_shortlist(document_words, query_words[position], candidates, contract["cascade"]["hamming_limit"])
                exports.append({"query_position": position, "hamming_shortlist_positions": shortlist.tolist(), "binary_adc_positions": adc_positions(document_bits, projections[position], centroids, shortlist, contract["cascade"]["adc_limit"]).tolist()})
                counts.append(float(candidates.size)); probes.append(float(probe_count)); occupied.append(float(occupied_count))
            identifier = f"{scheme['id']}-target{int(fraction * 100)}"
            shortlist_path = args.output_root / "shortlists" / f"{identifier}.json"; quality_path = args.output_root / "quality" / f"{identifier}.json"; contribution_path = args.output_root / "contributions" / f"{identifier}.npz"
            shortlist_path.parent.mkdir(parents=True, exist_ok=True)
            export = {"schema_version": 1, "family": EXPORT_FAMILY, "backend": "binary_product_static", "input_manifest_sha256": sha256(manifest_path), "query_seed": reference["query_seed"], "hamming_limit": contract["cascade"]["hamming_limit"], "scheme": scheme, "target_candidate_fraction": fraction, "rows": exports}
            shortlist_path.write_bytes(canonical(export))
            subprocess.run([str(args.python), str(THIS / "evaluate-native-ann-shortlists.py"), "evaluate", "--evaluation-root", str(args.evaluation_root), "--shortlist-export", str(shortlist_path), "--output", str(quality_path), "--contributions-output", str(contribution_path), "--oracle-cache", str(args.output_root / "oracle.npz")], check=True)
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
            rows.append({"id": identifier, "scheme": scheme, "target_candidate_fraction": fraction, "actual_candidate_fraction": float(numpy.mean(counts)) / 25000.0, "candidate_count_p95": percentile(counts, .95), "global_cell_probes_p50": percentile(probes, .50), "global_cell_probes_p95": percentile(probes, .95), "occupied_cell_probes_p50": percentile(occupied, .50), "occupied_cell_probes_p95": percentile(occupied, .95), "scheme_metadata": scheme_meta, "shortlist_sha256": sha256(shortlist_path), "quality_sha256": sha256(quality_path), "e5_oracle_survival_after_adc": quality["e5_oracle_survival_after_adc"], "reranked_ndcg_at_10": quality["reranked_ndcg_at_10"]})
    (args.output_root / "summary.json").write_bytes(canonical({"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "input_manifest_sha256": sha256(manifest_path), "reference_shortlist_sha256": sha256(args.reference_shortlist), "rows": rows}))


def self_test() -> None:
    contract = load_contract(THIS / "binary-product-locator.example.json")
    require(block_bounds(12)[0] == (0, 21) and block_bounds(12)[-1] == (234, 256), "binary-product block partition differs")
    prototype = prototypes(16, 4, "walsh_complement_prefix_v1"); require(prototype.shape == (4, 16) and numpy.array_equal(prototype[0] ^ prototype[1], numpy.ones(16, dtype=numpy.uint8)), "binary-product prototypes differ")
    posting = {0: numpy.asarray([1], dtype=numpy.int64), 1: numpy.asarray([2], dtype=numpy.int64), 2: numpy.asarray([3], dtype=numpy.int64), 3: numpy.asarray([4], dtype=numpy.int64)}
    chosen, probes, occupied = choose_candidates(numpy.zeros(2, dtype=numpy.uint8), [numpy.asarray([[0], [1]], dtype=numpy.uint8), numpy.asarray([[0], [1]], dtype=numpy.uint8)], [(0, 1), (1, 2)], 2, posting, 3)
    require(chosen.tolist() == [1, 2, 3] and probes == 3 and occupied == 3, "binary-product best-first enumeration differs")
    require(contract["schemes"][0]["id"] == "repetition-b12-c2", "binary-product contract self-test differs")
    print("binary product locator runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "binary-product-locator.example.json"); parser.add_argument("--input-root", type=Path); parser.add_argument("--evaluation-root", type=Path); parser.add_argument("--reference-shortlist", type=Path); parser.add_argument("--output-root", type=Path); parser.add_argument("--python", type=Path, default=Path(sys.executable)); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    try:
        if args.self_test: self_test(); return 0
        if None in (args.input_root, args.evaluation_root, args.reference_shortlist, args.output_root): parser.error("--input-root, --evaluation-root, --reference-shortlist, and --output-root are required")
        run(args, load_contract(args.contract)); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"run-binary-product-locator: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
