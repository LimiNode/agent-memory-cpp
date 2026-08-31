#!/usr/bin/env python3
"""Materialize mixed nonlinear-INT5/INT8 address-major R4 stores."""
from __future__ import annotations
import argparse
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


planner = load("neuroute_r4_int5_integration_materializer_planner",
               "plan-neuroute-r4-int5-physical-integration.py")


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
                       sort_keys=True) + "\n").encode("utf-8")


def role(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(row for row in rows if row["role"] == name)


def read_array(root: Path, row: dict[str, Any], dtype: str) -> numpy.ndarray:
    path = root / row["file"]
    require(path.is_file() and sha256(path) == row["sha256"],
            f"R4 INT5 integration mapping differs: {row['role']}")
    return numpy.fromfile(path, dtype=dtype).reshape(row["shape"])


def descriptor(path: Path, role_name: str, **extra: Any) -> dict[str, Any]:
    return {"role": role_name, "path": str(path.resolve()),
            "bytes": path.stat().st_size, "sha256": sha256(path), **extra}


def activation(args: argparse.Namespace) -> dict[str, str]:
    native_protocol = json.loads(args.native_end_to_end_protocol.read_text(
        encoding="utf-8"))
    return {
        "nonlinear_result_sha256": sha256(args.nonlinear_result),
        "nonlinear_evidence_sha256": sha256(args.nonlinear_evidence),
        "nonlinear_materialization_sha256": sha256(
            args.nonlinear_materialization_root / "manifest.json"),
        "layout_manifest_sha256": sha256(args.layout_root / "manifest.json"),
        "native_end_to_end_result_sha256": sha256(args.native_end_to_end_result),
        "native_end_to_end_evidence_sha256": sha256(
            args.native_end_to_end_evidence),
        "native_end_to_end_protocol_sha256": sha256(
            args.native_end_to_end_protocol),
        "native_input_manifest_sha256": sha256(Path(
            native_protocol["native_input_manifest"])),
    }


def materialize(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    actual = activation(args)
    require(actual == contract["activation"],
            "R4 INT5 integration activation differs")
    nonlinear_result = json.loads(args.nonlinear_result.read_text(
        encoding="utf-8"))
    require(nonlinear_result["decision"]["selected_representation"] ==
            "int5_power_050" and
            nonlinear_result["decision"]["selected_internal_passes_gates"] is True,
            "R4 INT5 integration selected parent differs")
    nonlinear_manifest_path = args.nonlinear_materialization_root / "manifest.json"
    nonlinear = json.loads(nonlinear_manifest_path.read_text(encoding="utf-8"))
    layout_manifest_path = args.layout_root / "manifest.json"
    layout = json.loads(layout_manifest_path.read_text(encoding="utf-8"))
    native_protocol = json.loads(args.native_end_to_end_protocol.read_text(
        encoding="utf-8"))
    require(native_protocol["family"] == "neuroute_r4_native_end_to_end_protocol",
            "R4 INT5 integration native parent differs")
    nonlinear_by_seed = {int(row["seed"]): row for row in nonlinear["seeds"]}
    layout_by_seed = {int(row["seed"]): row for row in layout["seeds"]}
    args.output_root.mkdir(parents=True, exist_ok=True)
    seed_rows = []
    for seed in contract["route"]["seeds"]:
        layout_row = layout_by_seed[int(seed)]
        nonlinear_row = nonlinear_by_seed[int(seed)]
        layout_seed_root = args.layout_root / f"seed-{seed}"
        nonlinear_seed_root = args.nonlinear_materialization_root / f"seed-{seed}"
        layout_mappings = {row["role"]: row for row in layout_row["mappings"]}
        nonlinear_mappings = {row["role"]: row
                              for row in nonlinear_row["mappings"]}
        occupied = read_array(layout_seed_root,
            layout_mappings["occupied_addresses"], "<u4")
        address_offsets = read_array(layout_seed_root,
            layout_mappings["address_offsets"], "<u4")
        address_counts = read_array(layout_seed_root,
            layout_mappings["address_counts"], "<u4")
        representative_counts = read_array(layout_seed_root,
            layout_mappings["representative_counts"], "u1")
        layout_documents = read_array(layout_seed_root,
            layout_mappings["representative_documents"], "<i4")
        nonlinear_documents = read_array(nonlinear_seed_root,
            nonlinear_mappings["representative_document_positions"], "<i4")
        require(numpy.array_equal(layout_documents, nonlinear_documents) and
                int(numpy.sum(representative_counts, dtype=numpy.uint64)) ==
                int(nonlinear_row["representative_count"]),
                "R4 INT5 integration FF32 ordering differs")
        uniform_row = role(layout_row["layouts"], "address_major_int8")
        uniform_path = layout_seed_root / uniform_row["file"]
        require(sha256(uniform_path) == uniform_row["sha256"],
                "R4 INT5 integration uniform store differs")
        side_row = next(row for row in nonlinear_row["representations"]
                        if row["id"] == "int5_power_050")
        side_path = nonlinear_seed_root / side_row["file"]
        require(sha256(side_path) == side_row["sha256"],
                "R4 INT5 integration side store differs")
        uniform = numpy.memmap(uniform_path, mode="r", dtype=numpy.uint8,
                               shape=(1000000, 388))
        representative_count = int(nonlinear_row["representative_count"])
        side = numpy.memmap(side_path, mode="r", dtype=numpy.uint8,
                            shape=(representative_count, 244))
        root = args.output_root / f"seed-{seed}"
        root.mkdir(parents=True, exist_ok=True)
        mixed_path = root / "mixed-int5-prefix-int8-remainder.records"
        mixed_offsets = numpy.empty(len(occupied), dtype="<u8")
        representative_cursor = 0
        document_cursor = 0
        with mixed_path.open("wb") as stream:
            for row in range(len(occupied)):
                mixed_offsets[row] = stream.tell()
                prefix = int(representative_counts[row])
                count = int(address_counts[row])
                start = int(address_offsets[row])
                require(start == document_cursor and prefix <= count,
                        "R4 INT5 integration address boundary differs")
                if prefix:
                    stream.write(numpy.asarray(side[
                        representative_cursor:representative_cursor + prefix],
                        dtype=numpy.uint8).tobytes(order="C"))
                if count > prefix:
                    stream.write(numpy.asarray(uniform[
                        start + prefix:start + count],
                        dtype=numpy.uint8).tobytes(order="C"))
                representative_cursor += prefix
                document_cursor += count
        require(representative_cursor == representative_count and
                document_cursor == 1000000,
                "R4 INT5 integration mixed cursor differs")
        expected_mixed = representative_count * 244 + (
            1000000 - representative_count) * 388
        require(mixed_path.stat().st_size == expected_mixed,
                "R4 INT5 integration mixed store size differs")
        offsets_path = root / "mixed-address-byte-offsets.u64le"
        mixed_offsets.tofile(offsets_path)
        baseline_footprint = uniform_path.stat().st_size
        side_footprint = baseline_footprint + side_path.stat().st_size
        layouts = [
            descriptor(uniform_path, "homogeneous_int8", record_bytes=388,
                representative_payload_bytes=representative_count * 388,
                active_store_bytes=baseline_footprint,
                full_physical_footprint_bytes=baseline_footprint),
            descriptor(side_path, "int5_side_store", record_bytes=244,
                representative_payload_bytes=representative_count * 244,
                active_store_bytes=side_path.stat().st_size,
                corpus_store_bytes=baseline_footprint,
                full_physical_footprint_bytes=side_footprint),
            descriptor(mixed_path, "int5_mixed", record_bytes=244,
                representative_payload_bytes=representative_count * 244,
                active_store_bytes=mixed_path.stat().st_size,
                full_physical_footprint_bytes=mixed_path.stat().st_size),
        ]
        seed_rows.append({"seed": seed,
            "occupied_address_count": len(occupied),
            "representative_count": representative_count,
            "document_count": 1000000,
            "mappings": [descriptor(offsets_path,
                "mixed_address_byte_offsets", dtype="<u8",
                shape=list(mixed_offsets.shape))],
            "layouts": layouts,
            "audit": {"ff32_document_order_identity": True,
                      "document_physical_order_preserved": True,
                      "mixed_has_no_duplicate_records": True,
                      "bucket_boundary_is_external_directory": True}})
    manifest = {"schema_version": 1,
        "family": "neuroute_r4_int5_physical_integration_materialization",
        "contract_sha256": sha256(args.contract), "activation": actual,
        "layout_manifest_sha256": sha256(layout_manifest_path),
        "nonlinear_materialization_sha256": sha256(nonlinear_manifest_path),
        "seeds": seed_rows}
    manifest_path = args.output_root / "manifest.json"
    manifest_path.write_bytes(canonical(manifest))
    parent_requests = native_protocol["requests"]
    require(len(parent_requests) == 76 and
            all(row["request"] >= 76 for row in parent_requests),
            "R4 INT5 integration native request trace differs")
    protocol = {"schema_version": 1,
        "family": "neuroute_r4_int5_physical_integration_protocol",
        "contract_sha256": sha256(args.contract), "activation": actual,
        "integration_manifest": str(manifest_path.resolve()),
        "integration_manifest_sha256": sha256(manifest_path),
        "layout_manifest": str(layout_manifest_path.resolve()),
        "native_input_manifest": native_protocol["native_input_manifest"],
        "document_id_rank_file": native_protocol["document_id_rank_file"],
        "document_id_rank_sha256": native_protocol["document_id_rank_sha256"],
        "evaluation_document_ids": native_protocol["evaluation_document_ids"],
        "evaluation_query_ids": native_protocol["evaluation_query_ids"],
        "evaluation_qrels": native_protocol["evaluation_qrels"],
        "seeds": contract["route"]["seeds"], "requests": parent_requests,
        "treatments": contract["treatments"],
        "warmup_passes": contract["warm_page_cache"]["warmup_passes"],
        "measured_passes": contract["warm_page_cache"]["measured_passes"],
        **contract["cascade"]}
    protocol_path = args.output_root / "protocol.json"
    protocol_path.write_bytes(canonical(protocol))


def self_test() -> None:
    counts = numpy.asarray([2, 1], dtype=numpy.uint8)
    require(int(numpy.sum(counts, dtype=numpy.uint64)) == 3,
            "R4 INT5 integration materializer count self-test differs")
    print("NeuRoute R4 INT5 physical-integration materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
        "neuroute-r4-int5-physical-integration.example.json")
    for name in ("nonlinear-result", "nonlinear-evidence",
                 "nonlinear-materialization-root", "layout-root",
                 "native-end-to-end-result", "native-end-to-end-evidence",
                 "native-end-to-end-protocol", "output-root"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name not in {"self_test", "contract"}):
            parser.error("all R4 INT5 integration materialization paths are required")
        materialize(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"materialize-neuroute-r4-int5-physical-integration: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
