#!/usr/bin/env python3
"""Fail-closed audit for the route-local physical-ordering research screen."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

N = 1_000_000
Q = 305
WIDTHS = {"route5000": 5000, "top128": 128}
CODEC_WIDTHS = {"lsq32": 36, "tq1": 52, "tq1-pq8": 64, "int8": 392}
MODES = ("prototype_ivf", "modern_r4")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate_permutation(values: np.ndarray, expected_size: int = N) -> None:
    require(values.dtype == np.dtype("<i4"), "permutation dtype must be int32")
    require(values.size == expected_size, "permutation length differs")
    require(np.array_equal(np.sort(values), np.arange(expected_size, dtype="int32")), "permutation is not bijective")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(np.ceil(fraction * len(ordered))) - 1))]


def derive_locality(ids: np.ndarray, physical_slot: np.ndarray, segment_rows: int,
                    corpus_rows: int = N) -> dict:
    touched = []
    fetched_rows = []
    for row in ids:
        blocks = np.unique(physical_slot[row] // segment_rows)
        touched.append(int(blocks.size))
        fetched_rows.append(int(sum(min(corpus_rows, (int(block) + 1) * segment_rows) - int(block) * segment_rows
                                   for block in blocks)))
    codec_bytes = {}
    for codec, width in CODEC_WIDTHS.items():
        useful = ids.shape[1] * width
        fetched = [value * width for value in fetched_rows]
        codec_bytes[codec] = {
            "logical_useful_bytes": useful,
            "fetched_bytes_p50": percentile(fetched, .50),
            "fetched_bytes_p95": percentile(fetched, .95),
            "amplification_p50": percentile([value / useful for value in fetched], .50),
            "amplification_p95": percentile([value / useful for value in fetched], .95),
        }
    return {
        "queries": int(ids.shape[0]),
        "width": int(ids.shape[1]),
        "segment_rows": segment_rows,
        "touched": touched,
        "fetched_rows": fetched_rows,
        "touched_blocks_p50": percentile(touched, .50),
        "touched_blocks_p95": percentile(touched, .95),
        "touched_blocks_p99": percentile(touched, .99),
        "codec_bytes": codec_bytes,
    }


def validate_locality(entry: dict, label: str) -> None:
    require(entry.get("queries") == Q, f"{label}: query count differs")
    require(entry.get("segment_rows", 0) > 0, f"{label}: invalid segment size")
    samples = entry.get("samples_touched_blocks")
    require(isinstance(samples, list) and len(samples) == Q, f"{label}: touched sample count differs")
    require(all(isinstance(value, int) and value > 0 for value in samples), f"{label}: invalid touched sample")
    fetched_rows = entry.get("samples_fetched_rows")
    require(isinstance(fetched_rows, list) and len(fetched_rows) == Q, f"{label}: fetched row sample count differs")
    require(all(isinstance(value, int) and value > 0 for value in fetched_rows), f"{label}: invalid fetched row sample")
    require(entry.get("touched_blocks_p50") <= entry.get("touched_blocks_p95") <= entry.get("touched_blocks_p99"),
            f"{label}: percentile order differs")
    codec_bytes = entry.get("codec_bytes")
    require(set(codec_bytes or {}) == set(CODEC_WIDTHS), f"{label}: codec byte metrics incomplete")
    for codec, width in CODEC_WIDTHS.items():
        metrics = codec_bytes[codec]
        useful = metrics.get("logical_useful_bytes")
        require(useful == entry["width"] * width, f"{label}/{codec}: useful bytes differ")
        for field in ("fetched_bytes_p50", "fetched_bytes_p95", "amplification_p50", "amplification_p95"):
            require(isinstance(metrics.get(field), (int, float)) and np.isfinite(metrics[field]) and metrics[field] >= 0,
                    f"{label}/{codec}: invalid {field}")


def assert_locality_matches(entry: dict, derived: dict, label: str) -> None:
    require(entry["samples_touched_blocks"] == derived["touched"], f"{label}: touched samples differ")
    require(entry["samples_fetched_rows"] == derived["fetched_rows"], f"{label}: fetched-row samples differ")
    for field in ("touched_blocks_p50", "touched_blocks_p95", "touched_blocks_p99"):
        require(entry[field] == derived[field], f"{label}: {field} differs")
    for codec in CODEC_WIDTHS:
        for field in ("logical_useful_bytes", "fetched_bytes_p50", "fetched_bytes_p95",
                      "amplification_p50", "amplification_p95"):
            actual = entry["codec_bytes"][codec][field]
            expected = derived["codec_bytes"][codec][field]
            require(np.isclose(actual, expected, rtol=0.0, atol=1e-12), f"{label}/{codec}: {field} differs")


def validate_route_binding(binding: dict, manifest: dict) -> None:
    require(binding.get("config_matches") is True, "canonical route configuration is not bound")
    require(binding.get("canonical_nlist") == 256 and binding.get("canonical_training_rows") == 25000,
            "canonical route manifest dimensions differ")
    require(binding.get("centroids_hash_equals_manifest_route_model_hash") is True,
            "regenerated centroid/model binding differs")
    require(binding.get("regenerated_centroids_sha256") == binding.get("canonical_route_model_sha256"),
            "regenerated centroid hash differs")
    require(manifest.get("route_model_sha256") == binding.get("canonical_route_model_sha256"),
            "route model hash differs from manifest")


def self_test() -> None:
    validate_permutation(np.arange(N, dtype="<i4"))
    raw = N * 64
    compressed = raw // 2
    saving = 1.0 - compressed / raw
    require(abs(saving - 0.5) < 1e-12, "compression saving formula differs")
    try:
        validate_permutation(np.asarray([0, 2, 2], dtype="<i4"))
    except AssertionError:
        pass
    else:
        raise AssertionError("invalid permutation mutation was accepted")
    ids = np.asarray([[0, 3, 4]], dtype="<i4")
    slots = np.arange(6, dtype="<i4")
    derived = derive_locality(ids, slots, 4, corpus_rows=6)
    require(derived["touched"] == [2] and derived["fetched_rows"] == [6], "partial segment derivation differs")
    entry = {**derived, "samples_touched_blocks": list(derived["touched"]), "samples_fetched_rows": list(derived["fetched_rows"])}
    assert_locality_matches(entry, derived, "mutation baseline")
    entry["samples_touched_blocks"][0] = 1
    try:
        assert_locality_matches(entry, derived, "mutation changed")
    except AssertionError:
        pass
    else:
        raise AssertionError("locality mutation was accepted")
    try:
        validate_route_binding({"config_matches": False}, {"route_model_sha256": "x"})
    except AssertionError:
        pass
    else:
        raise AssertionError("route binding mutation was accepted")
    print("route-local-ordering audit self-test PASS")


def audit(receipt_path: Path) -> None:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    require(receipt.get("schema_version") == 1, "schema version differs")
    require(receipt.get("family") == "route_local_physical_ordering_screen_v1", "family differs")
    require(receipt.get("status") == "EXECUTED", "screen is not executed")
    require(receipt.get("documents") == N and receipt.get("queries") == Q, "corpus dimensions differ")
    require(receipt.get("dimension") == 384, "embedding dimension differs")
    model = receipt.get("route_model") or {}
    require(model.get("nlist") == 256 and model.get("train_rows") == 25000 and model.get("seed") == 20261002,
            "route model configuration differs")
    require(model.get("assignment_contract") == "spherical_faiss_kmeans_argmax_cosine", "assignment contract differs")
    binding = receipt.get("canonical_route_manifest") or {}
    manifest_path = Path(binding.get("path", ""))
    require(manifest_path.is_file(), "canonical route manifest is missing")
    require(sha256(manifest_path) == binding.get("sha256"), "canonical route manifest hash differs")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_route_binding(binding, manifest)
    order = receipt.get("order") or {}
    require(order.get("policy") == "primary_cell_then_numeric_document_id", "ordering policy differs")
    assignment_path = Path(order.get("assignment_path", ""))
    permutation_path = Path(order.get("permutation_path", ""))
    require(assignment_path.is_file() and permutation_path.is_file(), "assignment/permutation evidence is missing")
    assignment = np.fromfile(assignment_path, dtype="<i4")
    permutation = np.fromfile(permutation_path, dtype="<i4")
    require(assignment.size == N and np.all((assignment >= 0) & (assignment < model["nlist"])), "assignment evidence differs")
    validate_permutation(permutation)
    require(hashlib.sha256(assignment.tobytes()).hexdigest() == order.get("assignment_sha256"), "assignment hash differs")
    require(hashlib.sha256(permutation.tobytes()).hexdigest() == order.get("permutation_sha256"), "permutation hash differs")
    fixture_path = Path(receipt.get("fixture_manifest_path", ""))
    require(fixture_path.is_file(), "fixture manifest is missing")
    require(sha256(fixture_path) == receipt.get("fixture_manifest_sha256"), "fixture manifest hash differs")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    require(fixture.get("documents") == N and fixture.get("queries") == Q, "fixture dimensions differ")
    workloads = receipt.get("workloads") or {}
    require(set(workloads) == set(MODES), "workload modes incomplete")
    for mode in MODES:
        require(set(workloads[mode]) == set(WIDTHS), f"{mode}: workload families incomplete")
        for workload, width in WIDTHS.items():
            entry = workloads[mode][workload]
            require(entry.get("source", {}).get("shape") == [Q, width], f"{mode}/{workload}: shape differs")
            source_path = Path(entry["source"]["path"])
            require(source_path.is_file(), f"{mode}/{workload}: source missing")
            require(sha256(source_path) == entry["source"].get("sha256"), f"{mode}/{workload}: source hash differs")
            ids = np.fromfile(source_path, dtype="<i4")
            require(ids.size == Q * width, f"{mode}/{workload}: source size differs")
            ids = ids.reshape(Q, width)
            require(np.all((ids >= 0) & (ids < N)), f"{mode}/{workload}: IDs out of range")
            require(all(np.unique(row).size == width for row in ids), f"{mode}/{workload}: duplicate IDs")
            validate_locality(entry["canonical"], f"{mode}/{workload}/canonical")
            validate_locality(entry["route_local"], f"{mode}/{workload}/route_local")
            canonical_derived = derive_locality(ids, np.arange(N, dtype="<i4"), entry["canonical"]["segment_rows"])
            route_slots = np.empty(N, dtype="<i4")
            route_slots[permutation] = np.arange(N, dtype="<i4")
            route_derived = derive_locality(ids, route_slots, entry["route_local"]["segment_rows"])
            assert_locality_matches(entry["canonical"], canonical_derived, f"{mode}/{workload}/canonical")
            assert_locality_matches(entry["route_local"], route_derived, f"{mode}/{workload}/route_local")
            # This is an observed result, not a theorem: retain both values and report violations.
            entry["observed_touched_block_delta"] = entry["route_local"]["touched_blocks_p50"] - entry["canonical"]["touched_blocks_p50"]
    compression = receipt.get("compression") or {}
    require(set(compression) == set(CODEC_WIDTHS), "compression codec set incomplete")
    for codec, width in CODEC_WIDTHS.items():
        entry = compression[codec]
        path = Path(entry.get("source_path", ""))
        require(path.is_file(), f"{codec}: source payload is missing")
        require(isinstance(entry.get("source_sha256"), str) and len(entry["source_sha256"]) == 64,
                f"{codec}: source hash missing")
        require(sha256(path) == entry["source_sha256"], f"{codec}: source payload hash differs")
        fixture_entry = fixture.get("payloads", {}).get(codec) or {}
        require(int(fixture_entry.get("payload_bytes_doc", -1)) == width, f"{codec}: fixture width differs")
        require(fixture_entry.get("payload_sha256") == entry["source_sha256"], f"{codec}: fixture payload hash differs")
        require(path.stat().st_size == N * width, f"{codec}: payload size differs")
        for layout in ("canonical", "route_local"):
            metrics = entry[layout]
            raw = metrics.get("raw_bytes")
            compressed = metrics.get("compressed_bytes")
            require(raw == N * width and isinstance(compressed, int) and compressed > 0, f"{codec}/{layout}: byte accounting differs")
            require(metrics.get("blocks", 0) > 0, f"{codec}/{layout}: block count differs")
            expected_saving = 1.0 - compressed / raw
            expected_ratio = raw / compressed
            require(abs(metrics.get("saving_fraction", 0.0) - expected_saving) < 1e-12, f"{codec}/{layout}: saving formula differs")
            require(abs(metrics.get("compression_ratio", 0.0) - expected_ratio) < 1e-12, f"{codec}/{layout}: ratio formula differs")
    print(json.dumps({"status": "PASS", "receipt": str(receipt_path)}, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.receipt is None:
        parser.error("--receipt is required")
    audit(args.receipt)


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise SystemExit(f"route-local-ordering audit: {error}")
