#!/usr/bin/env python3
"""Write physical FF32 representative stores for the frozen codec ladder."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, BinaryIO

import numpy


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


def descriptor(path: Path, shape: list[int], dtype: str, role: str) -> dict[str, Any]:
    return {"file": path.name, "sha256": sha256(path), "bytes": path.stat().st_size,
            "shape": shape, "dtype": dtype, "role": role}


def write_array(path: Path, value: numpy.ndarray, dtype: str,
                role: str) -> dict[str, Any]:
    array = numpy.ascontiguousarray(value, dtype=dtype)
    array.tofile(path)
    return descriptor(path, list(array.shape), dtype, role)


def interleaved_quantized(stream: BinaryIO, vectors: numpy.ndarray,
                          bits: int) -> None:
    maximum = (1 << (bits - 1)) - 1
    scales = numpy.max(numpy.abs(vectors), axis=1).astype(numpy.float32) / maximum
    scales[scales == 0] = 1.0
    codes = numpy.clip(numpy.rint(vectors / scales[:, None]),
                       -maximum, maximum).astype(numpy.int16)
    unsigned = numpy.asarray(codes + maximum, dtype=numpy.uint8)
    records = numpy.empty((len(vectors), 388), dtype=numpy.uint8)
    records[:, :384] = unsigned
    records[:, 384:] = scales.astype("<f4", copy=False).view(numpy.uint8).reshape(-1, 4)
    stream.write(records.tobytes(order="C"))


def run(args: argparse.Namespace) -> None:
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    saturation = json.loads(args.saturation_result.read_text(encoding="utf-8"))
    activation = contract["activation"]
    require(sha256(args.saturation_result) ==
            activation["coverage_saturation_result_sha256"],
            "R4 representative-codec saturation result differs")
    require(sha256(args.saturation_evidence) ==
            activation["coverage_saturation_evidence_sha256"],
            "R4 representative-codec saturation evidence differs")
    e5_manifest_path = args.de_1m_e5_root / "manifest.json"
    input_manifest_path = args.de_1m_input_root / "manifest.json"
    require(sha256(e5_manifest_path) == activation["de_1m_e5_manifest_sha256"]
            and sha256(input_manifest_path) == activation["de_1m_input_manifest_sha256"],
            "R4 representative-codec source manifests differ")
    e5 = json.loads(e5_manifest_path.read_text(encoding="utf-8"))
    source_row = e5["outputs"]["evaluation_document_vectors"]
    source_path = args.de_1m_e5_root / source_row["path"]
    require(source_path.is_file() and sha256(source_path) == source_row["sha256"],
            "R4 representative-codec FP32 corpus differs")
    documents = numpy.memmap(source_path, mode="r", dtype="<f4",
                             shape=(1000000, 384))
    args.output_root.mkdir(parents=True, exist_ok=True)
    rows = []
    by_seed = {int(row["seed"]): row for row in saturation["materializations"]}
    for seed in contract["route"]["seeds"]:
        source = by_seed[int(seed)]
        source_root = args.saturation_materialization_root / f"seed-{seed}"
        artifacts = {row["role"]: row for row in source["artifacts"]}
        def load(role: str) -> numpy.ndarray:
            row = artifacts[role]
            path = source_root / row["path"]
            require(path.is_file() and sha256(path) == row["sha256"],
                    f"R4 representative-codec source artifact differs: {seed}/{role}")
            return numpy.load(path, mmap_mode="r")
        occupied = numpy.asarray(load("occupied_addresses"), dtype=numpy.uint32)
        positions = numpy.asarray(load("actual_document_positions_k64")[:32],
                                  dtype=numpy.int32).T
        effective = numpy.minimum(numpy.asarray(load(
            "actual_document_effective_count_k64"), dtype=numpy.int64), 32)
        mask = numpy.arange(32, dtype=numpy.int64)[None, :] < effective[:, None]
        active_positions = numpy.asarray(positions[mask], dtype=numpy.int32)
        require(numpy.all(active_positions >= 0) and
                len(numpy.unique(active_positions)) == len(active_positions),
                "R4 representative-codec active FF32 positions differ")
        offsets = numpy.zeros(len(occupied), dtype=numpy.uint32)
        if len(offsets) > 1:
            offsets[1:] = numpy.cumsum(effective[:-1], dtype=numpy.uint64).astype(
                numpy.uint32)
        root = args.output_root / f"seed-{seed}"
        root.mkdir(parents=True, exist_ok=True)
        mappings = [
            write_array(root / "occupied.u32le", occupied, "<u4", "occupied_addresses"),
            write_array(root / "offsets.u32le", offsets, "<u4", "address_offsets"),
            write_array(root / "counts.u8", effective, "u1", "address_counts"),
            write_array(root / "document-positions.i32le", active_positions, "<i4",
                        "representative_document_positions"),
        ]
        paths = {name: root / f"{name}.records" for name in
                 ("fp32", "fp16", "int8", "int6", "int5")}
        codes = {bits: root / f"tmp-int{bits}-codes.u8" for bits in (5, 6)}
        scales = {bits: root / f"tmp-int{bits}-scales.f32le" for bits in (5, 6)}
        streams = {name: path.open("wb") for name, path in paths.items()
                   if name in ("fp32", "fp16", "int8")}
        code_streams = {bits: codes[bits].open("wb") for bits in (5, 6)}
        scale_streams = {bits: scales[bits].open("wb") for bits in (5, 6)}
        try:
            for start in range(0, len(active_positions), args.chunk_rows):
                current = numpy.asarray(documents[active_positions[
                    start:start + args.chunk_rows]], dtype=numpy.float32)
                streams["fp32"].write(current.astype("<f4", copy=False).tobytes())
                streams["fp16"].write(current.astype("<f2").tobytes())
                interleaved_quantized(streams["int8"], current, 8)
                for bits in (5, 6):
                    maximum = (1 << (bits - 1)) - 1
                    value = numpy.max(numpy.abs(current), axis=1).astype(
                        numpy.float32) / maximum
                    value[value == 0] = 1.0
                    quantized = numpy.clip(numpy.rint(current / value[:, None]),
                                           -maximum, maximum).astype(numpy.int16)
                    code_streams[bits].write(numpy.asarray(
                        quantized + maximum, dtype=numpy.uint8).tobytes())
                    scale_streams[bits].write(value.astype("<f4", copy=False).tobytes())
        finally:
            for stream in [*streams.values(), *code_streams.values(),
                           *scale_streams.values()]:
                stream.close()
        for bits in (6, 5):
            completed = subprocess.run([
                str(args.native_executable), "--pack", str(bits), str(codes[bits]),
                str(scales[bits]), str(len(active_positions)),
                str(paths[f"int{bits}"])], check=False, capture_output=True, text=True)
            require(completed.returncode == 0,
                    f"R4 representative-codec native packing failed: {completed.stderr}")
            codes[bits].unlink()
            scales[bits].unlink()
        representations = []
        for row in contract["representations"]:
            path = paths[row["id"]]
            expected = len(active_positions) * int(row["record_bytes"])
            require(path.stat().st_size == expected,
                    f"R4 representative-codec store size differs: {seed}/{row['id']}")
            representations.append({**row, "file": path.name,
                                    "sha256": sha256(path), "bytes": expected})
        rows.append({
            "seed": seed,
            "occupied_address_count": len(occupied),
            "representative_count": len(active_positions),
            "mappings": mappings,
            "representations": representations,
        })
    output = {
        "schema_version": 1,
        "family": "neuroute_r4_representative_codec_materialization",
        "contract_sha256": sha256(args.contract),
        "saturation_result_sha256": sha256(args.saturation_result),
        "source_e5_manifest_sha256": sha256(e5_manifest_path),
        "source_document_vectors_sha256": source_row["sha256"],
        "simdcomp_commit": contract["quantization"]["simdcomp_commit"],
        "seeds": rows,
    }
    (args.output_root / "manifest.json").write_bytes(canonical(output))


def self_test() -> None:
    values = numpy.asarray([[1.0, -0.5, 0.0]], dtype=numpy.float32)
    maximum = 15
    scale = numpy.max(numpy.abs(values), axis=1) / maximum
    codes = numpy.rint(values / scale[:, None]).astype(numpy.int16)
    require(codes.tolist() == [[15, -7, 0]],
            "R4 representative-codec materializer rounding differs")
    print("NeuRoute R4 representative-codec materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=False)
    parser.add_argument("--saturation-result", type=Path)
    parser.add_argument("--saturation-evidence", type=Path)
    parser.add_argument("--saturation-materialization-root", type=Path)
    parser.add_argument("--de-1m-e5-root", type=Path)
    parser.add_argument("--de-1m-input-root", type=Path)
    parser.add_argument("--native-executable", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--chunk-rows", type=int, default=8192)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(getattr(args, name) is None for name in (
                "contract", "saturation_result", "saturation_evidence",
                "saturation_materialization_root", "de_1m_e5_root",
                "de_1m_input_root", "native_executable", "output_root")):
            parser.error("all R4 representative-codec materialization paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"materialize-neuroute-r4-representative-codec: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
