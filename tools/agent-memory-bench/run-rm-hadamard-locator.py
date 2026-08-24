#!/usr/bin/env python3
"""Measure an RM(1,8) / Hadamard nearest-codeword locator on frozen ITQ codes."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy


THIS = Path(__file__).resolve().parent
FAMILY = "rm_1_8_hadamard_locator_calibration_v1"
EXPORT_FAMILY = "native_ann_hamming_shortlist_export_v1"


def load_product() -> Any:
    spec = importlib.util.spec_from_file_location("rm_hadamard_product_shared", THIS / "run-binary-product-locator.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load binary-product shared helpers")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    return module


shared = load_product()
require = shared.require
sha256 = shared.sha256
canonical = shared.canonical
percentile = shared.percentile
load_codes = shared.load_codes
load_words = shared.load_words
input_payloads = shared.input_payloads
hamming_shortlist = shared.hamming_shortlist
adc_positions = shared.adc_positions


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value == {
        "schema_version": 1,
        "family": FAMILY,
        "purpose": "external_calibration_only_structured_full_length_locator_not_selection_or_confirmation",
        "amends_measurement_contract_sha256": "b9a518db2ec48b89e957d260feb2badba5b0a1f18298cb124a9f78b7ea7c9579",
        "input": {"document_count": 25000, "query_count": 648, "code_bits": 256, "manifest_sha256": "1d3e210edfca62d9019c2849fdb1494566556efd3e57f264d9ef31d599dee987"},
        "reference_flat_shortlist_sha256": "48da713381f0b7b9c36635f6c286541311c524083be4c7bf56223ca2be840ce5",
        "code": {"family": "RM(1,8)", "length": 256, "dimension": 9, "center_count": 512, "assignment": "fast_walsh_hadamard_nearest_codeword_with_lowest_center_id_ties_v1"},
        "probing_order": "hamming_distance_then_center_id_v1",
        "target_candidate_fractions": [0.05, 0.10, 0.25],
        "cascade": {"hamming_limit": 768, "adc_limit": 256, "exact_limit": 256, "oracle_k": 10},
        "confirmation": "forbidden_french_and_any_external_confirmation_split_are_out_of_scope",
        "library_dependency": "forbidden_external_python_calibration_harness_only",
    }, "RM/Hadamard contract differs")
    return value


def fwht(values: numpy.ndarray) -> numpy.ndarray:
    require(values.ndim == 2 and values.shape[1] == 256, "FWHT input differs")
    result = values.astype(numpy.int16, copy=True)
    stride = 1
    while stride < result.shape[1]:
        paired = result.reshape(result.shape[0], -1, 2 * stride)
        left, right = paired[:, :, :stride].copy(), paired[:, :, stride:].copy()
        paired[:, :, :stride] = left + right
        paired[:, :, stride:] = left - right
        stride *= 2
    return result


def correlations(bits: numpy.ndarray) -> numpy.ndarray:
    require(bits.ndim == 2 and bits.shape[1] == 256, "RM/Hadamard code width differs")
    return fwht(1 - 2 * bits.astype(numpy.int16, copy=False))


def nearest_cells(correlation: numpy.ndarray) -> numpy.ndarray:
    magnitudes = numpy.abs(correlation)
    affine = numpy.argmax(magnitudes, axis=1)
    signs = correlation[numpy.arange(correlation.shape[0]), affine] < 0
    return (2 * affine + signs.astype(numpy.int16)).astype(numpy.int16)


def cell_order(correlation: numpy.ndarray) -> numpy.ndarray:
    require(correlation.shape == (256,), "RM/Hadamard query correlation differs")
    affine = numpy.arange(256, dtype=numpy.int16)
    identifiers = numpy.empty(512, dtype=numpy.int16)
    identifiers[0::2], identifiers[1::2] = 2 * affine, 2 * affine + 1
    values = numpy.empty(512, dtype=numpy.int16)
    values[0::2], values[1::2] = (256 - correlation) // 2, (256 + correlation) // 2
    return identifiers[numpy.lexsort((identifiers, values))]


def direct_centers() -> numpy.ndarray:
    positions = numpy.arange(256, dtype=numpy.uint16)
    centers = numpy.empty((512, 256), dtype=numpy.uint8)
    for affine in range(256):
        row = (numpy.bitwise_count(positions & numpy.uint16(affine)) & 1).astype(numpy.uint8)
        centers[2 * affine], centers[2 * affine + 1] = row, 1 - row
    return centers


def posting_lists(cells: numpy.ndarray) -> dict[int, numpy.ndarray]:
    return {int(cell): numpy.flatnonzero(cells == cell).astype(numpy.int64) for cell in range(512) if numpy.any(cells == cell)}


def choose_candidates(order: numpy.ndarray, postings: dict[int, numpy.ndarray], target: int) -> tuple[numpy.ndarray, int, int]:
    chunks: list[numpy.ndarray] = []; total = 0; nonempty = 0
    for probes, cell in enumerate(order, start=1):
        posting = postings.get(int(cell))
        if posting is None:
            continue
        chunks.append(posting); total += posting.size; nonempty += 1
        if total >= target:
            return numpy.concatenate(chunks), probes, nonempty
    raise ValueError("RM/Hadamard candidate budget is unreachable")


def run(args: argparse.Namespace, contract: dict[str, Any]) -> None:
    manifest_path = args.input_root / "manifest.json"; manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(sha256(manifest_path) == contract["input"]["manifest_sha256"] and manifest["document_count"] == 25000 and manifest["query_count"] == 648 and manifest["code_bits"] == 256, "RM/Hadamard frozen input differs")
    require(sha256(args.reference_shortlist) == contract["reference_flat_shortlist_sha256"], "RM/Hadamard Flat reference differs")
    reference = json.loads(args.reference_shortlist.read_text(encoding="utf-8")); positions = [int(row["query_position"]) for row in reference["rows"]]
    require(reference.get("backend") == "flat" and len(positions) == 648 and len(set(positions)) == 648, "RM/Hadamard reference query order differs")
    input_payloads(args.input_root, manifest)
    document_path = args.input_root / manifest["document_codes_file"]; query_path = args.input_root / manifest["query_codes_file"]
    document_bits, query_bits = load_codes(document_path, 25000), load_codes(query_path, 648); document_words, query_words = load_words(document_path, 25000), load_words(query_path, 648)
    document_correlation, query_correlation = correlations(document_bits), correlations(query_bits)
    cells = nearest_cells(document_correlation); postings = posting_lists(cells); sizes = [int(value.size) for value in postings.values()]
    metadata = {"center_count": 512, "occupied_cell_count": len(postings), "occupied_cell_size_p50": percentile(sizes, .50), "occupied_cell_size_p95": percentile(sizes, .95), "occupied_cell_size_max": max(sizes)}
    projections = numpy.fromfile(args.input_root / manifest["query_itq_projections_file"], dtype="<f4").reshape(648, 256); centroids = numpy.fromfile(args.input_root / manifest["binary_adc_centroids_file"], dtype="<f4").reshape(256, 2)
    args.output_root.mkdir(parents=True, exist_ok=True); rows: list[dict[str, Any]] = []
    for fraction in contract["target_candidate_fractions"]:
        target = max(contract["cascade"]["hamming_limit"], int(numpy.ceil(fraction * 25000))); exports: list[dict[str, Any]] = []; counts: list[float] = []; probes: list[float] = []; nonempty: list[float] = []
        for position in positions:
            candidates, probe_count, nonempty_count = choose_candidates(cell_order(query_correlation[position]), postings, target)
            shortlist = hamming_shortlist(document_words, query_words[position], candidates, contract["cascade"]["hamming_limit"])
            exports.append({"query_position": position, "hamming_shortlist_positions": shortlist.tolist(), "binary_adc_positions": adc_positions(document_bits, projections[position], centroids, shortlist, contract["cascade"]["adc_limit"]).tolist()})
            counts.append(float(candidates.size)); probes.append(float(probe_count)); nonempty.append(float(nonempty_count))
        identifier = f"rm1-8-target{int(fraction * 100)}"; shortlist_path = args.output_root / "shortlists" / f"{identifier}.json"; quality_path = args.output_root / "quality" / f"{identifier}.json"; contribution_path = args.output_root / "contributions" / f"{identifier}.npz"; shortlist_path.parent.mkdir(parents=True, exist_ok=True)
        export = {"schema_version": 1, "family": EXPORT_FAMILY, "backend": "rm_1_8_hadamard_static", "input_manifest_sha256": sha256(manifest_path), "query_seed": reference["query_seed"], "hamming_limit": contract["cascade"]["hamming_limit"], "code": contract["code"], "target_candidate_fraction": fraction, "rows": exports}
        shortlist_path.write_bytes(canonical(export)); subprocess.run([str(args.python), str(THIS / "evaluate-native-ann-shortlists.py"), "evaluate", "--evaluation-root", str(args.evaluation_root), "--shortlist-export", str(shortlist_path), "--output", str(quality_path), "--contributions-output", str(contribution_path), "--oracle-cache", str(args.output_root / "oracle.npz")], check=True)
        quality = json.loads(quality_path.read_text(encoding="utf-8")); rows.append({"id": identifier, "target_candidate_fraction": fraction, "actual_candidate_fraction": float(numpy.mean(counts)) / 25000.0, "candidate_count_p95": percentile(counts, .95), "centroid_probes_p50": percentile(probes, .50), "centroid_probes_p95": percentile(probes, .95), "nonempty_centroid_probes_p50": percentile(nonempty, .50), "nonempty_centroid_probes_p95": percentile(nonempty, .95), "index_metadata": metadata, "shortlist_sha256": sha256(shortlist_path), "quality_sha256": sha256(quality_path), "e5_oracle_survival_after_adc": quality["e5_oracle_survival_after_adc"], "reranked_ndcg_at_10": quality["reranked_ndcg_at_10"]})
    (args.output_root / "summary.json").write_bytes(canonical({"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "input_manifest_sha256": sha256(manifest_path), "reference_shortlist_sha256": sha256(args.reference_shortlist), "rows": rows}))


def self_test() -> None:
    contract = load_contract(THIS / "rm-hadamard-locator.example.json")
    zeros = numpy.zeros((1, 256), dtype=numpy.uint8); ones = numpy.ones((1, 256), dtype=numpy.uint8)
    require(nearest_cells(correlations(zeros)).tolist() == [0] and nearest_cells(correlations(ones)).tolist() == [1], "RM/Hadamard nearest-center decoding differs")
    centers = direct_centers(); sample = centers[[0, 17, 510]]; correlation = correlations(sample)
    require(numpy.array_equal(nearest_cells(correlation), numpy.asarray([0, 17, 510])) and all(cell_order(row)[0] == item for row, item in zip(correlation, [0, 17, 510])), "RM/Hadamard center ordering differs")
    require(contract["code"]["center_count"] == 512, "RM/Hadamard contract self-test differs")
    print("RM(1,8)/Hadamard locator runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--contract", type=Path, default=THIS / "rm-hadamard-locator.example.json"); parser.add_argument("--input-root", type=Path); parser.add_argument("--evaluation-root", type=Path); parser.add_argument("--reference-shortlist", type=Path); parser.add_argument("--output-root", type=Path); parser.add_argument("--python", type=Path, default=Path(sys.executable)); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    try:
        if args.self_test: self_test(); return 0
        if None in (args.input_root, args.evaluation_root, args.reference_shortlist, args.output_root): parser.error("--input-root, --evaluation-root, --reference-shortlist, and --output-root are required")
        run(args, load_contract(args.contract)); return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"run-rm-hadamard-locator: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
