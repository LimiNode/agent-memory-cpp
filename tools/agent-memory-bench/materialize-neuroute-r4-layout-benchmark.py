#!/usr/bin/env python3
"""Build full-corpus R4 address-major and indirect layout artifacts."""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_r4_layout_planner", "plan-neuroute-r4-layout-benchmark.py")
coverage = load("neuroute_r4_layout_coverage",
                "run-neuroute-r4-coverage-saturation.py")
scale = coverage.scale
task = coverage.task
multi = coverage.multi
base = coverage.base
fine = coverage.fine
prototype = coverage.prototype


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode()


def write_array(path: Path, value: numpy.ndarray, dtype: str,
                role: str) -> dict[str, Any]:
    array = numpy.ascontiguousarray(value, dtype=dtype)
    array.tofile(path)
    return {"file": path.name, "sha256": sha256(path), "bytes": path.stat().st_size,
            "shape": list(array.shape), "dtype": dtype, "role": role}


def physical(path: Path, role: str, record_bytes: int, records: int) -> dict[str, Any]:
    require(path.stat().st_size == record_bytes * records,
            f"R4 layout physical size differs: {role}")
    return {"file": path.name, "sha256": sha256(path), "bytes": path.stat().st_size,
            "record_bytes": record_bytes, "records": records, "role": role}


def write_document_major_int8(path: Path, documents: numpy.ndarray,
                              chunk_rows: int) -> None:
    with path.open("wb") as stream:
        for start in range(0, len(documents), chunk_rows):
            values = numpy.asarray(documents[start:start + chunk_rows],
                                   dtype=numpy.float32)
            scales = numpy.max(numpy.abs(values), axis=1).astype(numpy.float32) / 127
            scales[scales == 0] = 1.0
            codes = numpy.clip(numpy.rint(values / scales[:, None]),
                               -127, 127).astype(numpy.int16)
            records = numpy.empty((len(values), 388), dtype=numpy.uint8)
            records[:, :384] = numpy.asarray(codes + 127, dtype=numpy.uint8)
            records[:, 384:] = scales.astype("<f4", copy=False).view(
                numpy.uint8).reshape(-1, 4)
            stream.write(records.tobytes())


def address_order(addresses: numpy.ndarray, occupied: numpy.ndarray,
                  selected_offsets: numpy.ndarray, selected_counts: numpy.ndarray,
                  selected_documents: numpy.ndarray
                  ) -> tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray]:
    counts = numpy.bincount(addresses.astype(numpy.int64), minlength=65536)
    offsets = numpy.zeros(65536, dtype=numpy.int64)
    offsets[1:] = numpy.cumsum(counts[:-1], dtype=numpy.int64)
    stable = numpy.argsort(addresses, kind="stable").astype(numpy.int32)
    order = numpy.empty(len(addresses), dtype=numpy.int32)
    address_offsets = numpy.empty(len(occupied), dtype=numpy.uint32)
    cursor = 0
    for row, address in enumerate(occupied):
        address = int(address)
        posting = stable[offsets[address]:offsets[address] + counts[address]]
        begin = int(selected_offsets[row])
        selected = selected_documents[begin:begin + int(selected_counts[row])]
        require(len(numpy.unique(selected)) == len(selected)
                and numpy.all(addresses[selected] == address),
                "R4 layout selected prefix differs")
        address_offsets[row] = cursor
        order[cursor:cursor + len(selected)] = selected
        cursor += len(selected)
        remaining = posting[~numpy.isin(posting, selected, assume_unique=True)]
        order[cursor:cursor + len(remaining)] = remaining
        cursor += len(remaining)
    require(cursor == len(addresses) and len(numpy.unique(order)) == len(addresses),
            "R4 layout full-corpus permutation differs")
    inverse = numpy.empty(len(order), dtype=numpy.uint32)
    inverse[order] = numpy.arange(len(order), dtype=numpy.uint32)
    return order, inverse, address_offsets


def model_payloads(root: Path, model_path: Path) -> list[dict[str, Any]]:
    archive = numpy.load(model_path, allow_pickle=False)
    names = ("feature_mean", "feature_deviation", "query_weight", "query_bias",
             "local_weight", "local_bias", "score_weight1", "score_bias1",
             "score_weight2", "score_bias2", "r4_aggregate_mean",
             "r4_aggregate_deviation")
    result = []
    for name in names:
        result.append(write_array(root / f"model-{name}.f32le",
                                  numpy.asarray(archive[name], dtype=numpy.float32),
                                  "<f4", f"model_{name}"))
    return result


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    activation = contract["activation"]
    require(sha256(args.codec_result) == activation[
        "representative_codec_result_sha256"]
        and sha256(args.codec_evidence) == activation[
            "representative_codec_evidence_sha256"],
            "R4 layout codec activation differs")
    codec = json.loads(args.codec_result.read_text(encoding="utf-8"))
    require(codec["decision"]["selected_representation"] == "int8"
            and codec["decision"]["physical_layout_benchmark_licensed"] is True,
            "R4 layout selected codec is not licensed")
    codec_manifest_path = args.codec_materialization_root / "manifest.json"
    require(sha256(codec_manifest_path) == activation[
        "representative_codec_materialization_sha256"],
            "R4 layout codec materialization differs")
    width_path = args.width_materialization_root / "manifest.json"
    require(sha256(width_path) == activation["width_materialization_sha256"],
            "R4 layout width materialization differs")
    e5_path = args.de_1m_e5_root / "manifest.json"
    input_path = args.de_1m_input_root / "manifest.json"
    require(sha256(e5_path) == activation["de_1m_e5_manifest_sha256"]
            and sha256(input_path) == activation["de_1m_input_manifest_sha256"],
            "R4 layout source manifests differ")
    scale_config = next(row for row in prototype.planner.load_contract(
        THIS / "neuroute-prototype-gain-density-reranker.example.json")["scales"]
                        if row["id"] == "de-1m")
    data = scale.load_scale(scale_config, args.de_1m_e5_root,
                            args.de_1m_input_root)
    saturation = json.loads(args.saturation_result.read_text(encoding="utf-8"))
    def query_ids(partition: str) -> list[str]:
        row = next(value for value in saturation[f"{partition}_rows"]
                   if value["seed"] == contract["route"]["seeds"][0]
                   and value["treatment"] == "actual_k32_max")
        return [value["query_id"] for value in row["queries"]]
    ids = query_ids("configuration") + query_ids("internal")
    require(len(ids) == 152 and len(set(ids)) == 152,
            "R4 layout paired query trace differs")
    by_id = {value: index for index, value in enumerate(data["query_ids"])}
    query_positions = [by_id[value] for value in ids]
    queries = numpy.asarray(data["queries"][query_positions], dtype=numpy.float32)
    args.output_root.mkdir(parents=True, exist_ok=True)
    document_int8_path = args.output_root / "document-major-int8.records"
    write_document_major_int8(document_int8_path, data["documents"], args.chunk_rows)
    document_int8 = numpy.memmap(document_int8_path, mode="r", dtype=numpy.uint8,
                                 shape=(1000000, 388))
    global_rows = [physical(document_int8_path, "document_major_int8", 388, 1000000)]
    width = json.loads(width_path.read_text(encoding="utf-8"))
    width_dataset = next(row for row in width["datasets"] if row["id"] == "de-1m")
    codec_manifest = json.loads(codec_manifest_path.read_text(encoding="utf-8"))
    codec_by_seed = {int(row["seed"]): row for row in codec_manifest["seeds"]}
    model_by_seed = {int(row["seed"]): row for row in saturation[
        "frozen_k32_parent_models"]}
    parent_contract = base.planner.load_contract(
        THIS / "neuroute-nonlinear-listwise-reranker.example.json")
    seed_rows = []
    for seed in contract["route"]["seeds"]:
        root = args.output_root / f"seed-{seed}"
        root.mkdir(parents=True, exist_ok=True)
        route = task.route_entry(width_dataset, 16, seed)
        route_root = args.width_materialization_root / "de-1m" / route["id"]
        addresses = numpy.asarray(task.read_descriptor(
            route_root, route["document_addresses"]), dtype=numpy.uint32)
        index = scale.build_index(addresses, 16)
        occupied, prototypes, effective, _ = multi.build_nested_prototypes(
            data["documents"], addresses, index, 8)
        shortlists, scalar_features = base.prepare_query_features(
            queries, occupied, prototypes, effective, index["counts"], 1000000,
            1024, parent_contract["training"]["feature_query_batch_size"])
        lookup = fine.address_lookup(occupied)
        shortlist_rows = lookup[numpy.asarray(shortlists, dtype=numpy.uint32)]
        require(numpy.all(shortlist_rows >= 0), "R4 layout shortlist rows differ")
        codec_seed = codec_by_seed[int(seed)]
        codec_root = args.codec_materialization_root / f"seed-{seed}"
        mapping = {row["role"]: row for row in codec_seed["mappings"]}
        def read_mapping(role: str, dtype: str) -> numpy.ndarray:
            row = mapping[role]
            path = codec_root / row["file"]
            require(sha256(path) == row["sha256"],
                    f"R4 layout codec mapping differs: {seed}/{role}")
            return numpy.fromfile(path, dtype=dtype).reshape(row["shape"])
        selected_offsets = read_mapping("address_offsets", "<u4")
        selected_counts = read_mapping("address_counts", "u1")
        selected_documents = read_mapping("representative_document_positions", "<i4")
        order, inverse, address_offsets = address_order(
            addresses, occupied, selected_offsets, selected_counts,
            selected_documents)
        fp32_path = root / "address-major-fp32.records"
        int8_path = root / "address-major-int8.records"
        with fp32_path.open("wb") as fp32_stream, int8_path.open("wb") as int8_stream:
            for start in range(0, len(order), args.chunk_rows):
                current = order[start:start + args.chunk_rows]
                fp32_stream.write(numpy.asarray(data["documents"][current],
                                                dtype="<f4").tobytes())
                int8_stream.write(numpy.asarray(document_int8[current],
                                                dtype=numpy.uint8).tobytes())
        mappings = [
            write_array(root / "occupied.u32le", occupied, "<u4", "occupied_addresses"),
            write_array(root / "address-offsets.u32le", address_offsets, "<u4",
                        "address_offsets"),
            write_array(root / "address-counts.u32le", index["counts"][occupied],
                        "<u4", "address_counts"),
            write_array(root / "representative-counts.u8", selected_counts, "u1",
                        "representative_counts"),
            write_array(root / "representative-documents.i32le", selected_documents,
                        "<i4", "representative_documents"),
            write_array(root / "document-to-physical.u32le", inverse, "<u4",
                        "document_to_physical"),
            write_array(root / "physical-to-document.i32le", order, "<i4",
                        "physical_to_document"),
            write_array(root / "queries.f32le", queries, "<f4", "query_vectors"),
            write_array(root / "shortlist-rows.u32le", shortlist_rows, "<u4",
                        "shortlist_rows"),
            write_array(root / "scalar-features.f32le", scalar_features, "<f4",
                        "scalar_features"),
        ]
        model = model_by_seed[int(seed)]
        model_path = args.model_root / model["file"]
        require(sha256(model_path) == model["sha256"], "R4 layout model differs")
        models = model_payloads(root, model_path)
        seed_rows.append({
            "seed": seed,
            "representative_count": int(codec_seed["representative_count"]),
            "mappings": mappings,
            "model": models,
            "layouts": [
                physical(fp32_path, "address_major_fp32", 1536, 1000000),
                physical(int8_path, "address_major_int8", 388, 1000000),
                {**global_rows[0], "role": "document_major_int8_indirect",
                 "external_root": ".."},
            ],
            "audit": {
                "every_document_stored_once": True,
                "ff32_prefix_replayed": True,
                "compact_records_identical_across_layouts": True,
                "addresses_scored_per_query": 1024,
            },
        })
        del addresses, index, occupied, prototypes, effective, shortlists
        del scalar_features, shortlist_rows, order, inverse, address_offsets
        gc.collect()
    manifest = {
        "schema_version": 1,
        "family": "neuroute_r4_layout_materialization",
        "contract_sha256": sha256(args.contract),
        "codec_result_sha256": sha256(args.codec_result),
        "codec_evidence_sha256": sha256(args.codec_evidence),
        "codec_materialization_sha256": sha256(codec_manifest_path),
        "source_document_vectors_sha256": json.loads(e5_path.read_text(
            encoding="utf-8"))["outputs"]["evaluation_document_vectors"]["sha256"],
        "query_ids": ids,
        "global_layouts": global_rows,
        "seeds": seed_rows,
    }
    (args.output_root / "manifest.json").write_bytes(canonical(manifest))


def self_test() -> None:
    addresses = numpy.asarray([1, 0, 1, 0], dtype=numpy.uint32)
    occupied = numpy.asarray([0, 1], dtype=numpy.uint32)
    order, inverse, offsets = address_order(
        addresses, occupied, numpy.asarray([0, 1], dtype=numpy.uint32),
        numpy.asarray([1, 1], dtype=numpy.uint8),
        numpy.asarray([3, 2], dtype=numpy.int32))
    require(order.tolist() == [3, 1, 2, 0]
            and inverse[order].tolist() == [0, 1, 2, 3]
            and offsets.tolist() == [0, 2], "R4 layout ordering self-test differs")
    print("NeuRoute R4 layout materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path)
    for name in ("codec-result", "codec-evidence", "codec-materialization-root",
                 "saturation-result", "model-root", "width-materialization-root",
                 "de-1m-e5-root", "de-1m-input-root", "output-root"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--chunk-rows", type=int, default=8192)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "chunk_rows"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all R4 layout materialization paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            numpy.linalg.LinAlgError) as error:
        print(f"materialize-neuroute-r4-layout-benchmark: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
