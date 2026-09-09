#!/usr/bin/env python3
"""Materialize nonlinear INT5/6/8 FF32 representative codecs."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, BinaryIO
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


planner = load("neuroute_r4_nonlinear_materializer_planner",
               "plan-neuroute-r4-nonlinear-representative-quantization.py")


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


def descriptor(path: Path, role: str, **extra: Any) -> dict[str, Any]:
    return {"role": role, "file": path.name, "bytes": path.stat().st_size,
            "sha256": sha256(path), **extra}


def quantize(vectors: numpy.ndarray, bits: int, kind: str,
             parameter: float) -> tuple[numpy.ndarray, numpy.ndarray]:
    maximum = (1 << (bits - 1)) - 1
    amplitudes = numpy.max(numpy.abs(vectors), axis=1).astype(numpy.float32)
    amplitudes[amplitudes == 0] = 1.0
    normalized = numpy.asarray(vectors / amplitudes[:, None], dtype=numpy.float32)
    if kind == "power":
        transformed = numpy.copysign(numpy.power(numpy.abs(normalized), parameter),
                                     normalized)
    elif kind == "mulaw":
        transformed = numpy.copysign(
            numpy.log1p(parameter * numpy.abs(normalized)) / numpy.log1p(parameter),
            normalized)
    else:
        raise ValueError("R4 nonlinear materializer compander differs")
    codes = numpy.clip(numpy.rint(transformed * maximum), -maximum, maximum)
    return numpy.asarray(codes + maximum, dtype=numpy.uint8), amplitudes


def write_int8(stream: BinaryIO, codes: numpy.ndarray,
               amplitudes: numpy.ndarray) -> None:
    records = numpy.empty((len(codes), 388), dtype=numpy.uint8)
    records[:, :384] = codes
    records[:, 384:] = amplitudes.astype("<f4", copy=False).view(
        numpy.uint8).reshape(-1, 4)
    stream.write(records.tobytes(order="C"))


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    activation = contract["activation"]
    parent_manifest_path = args.parent_materialization_root / "manifest.json"
    require(sha256(args.parent_result) ==
            activation["representative_codec_result_sha256"] and
            sha256(args.parent_evidence) ==
            activation["representative_codec_evidence_sha256"] and
            sha256(parent_manifest_path) ==
            activation["representative_codec_materialization_sha256"] and
            sha256(args.lossless_result) ==
            activation["lossless_block_result_sha256"] and
            sha256(args.lossless_evidence) ==
            activation["lossless_block_evidence_sha256"],
            "R4 nonlinear materializer activation differs")
    parent = json.loads(parent_manifest_path.read_text(encoding="utf-8"))
    parent_by_seed = {int(row["seed"]): row for row in parent["seeds"]}
    args.output_root.mkdir(parents=True, exist_ok=True)
    seeds = []
    for seed in contract["route"]["seeds"]:
        parent_row = parent_by_seed[int(seed)]
        parent_root = args.parent_materialization_root / f"seed-{seed}"
        root = args.output_root / f"seed-{seed}"
        root.mkdir(parents=True, exist_ok=True)
        mappings = []
        for row in parent_row["mappings"]:
            source = parent_root / row["file"]
            target = root / row["file"]
            shutil.copyfile(source, target)
            require(sha256(target) == row["sha256"],
                    "R4 nonlinear copied mapping differs")
            mappings.append({**row})
        parent_representations = {row["id"]: row
                                  for row in parent_row["representations"]}
        fp32_row = parent_representations["fp32"]
        fp32_path = parent_root / fp32_row["file"]
        rows = int(parent_row["representative_count"])
        vectors = numpy.memmap(fp32_path, mode="r", dtype="<f4",
                               shape=(rows, 384))
        representations = []
        for treatment in contract["representations"]:
            if treatment["storage"] == "parent":
                source = parent_representations[treatment["parent_role"]]
                representations.append({**treatment,
                    "parent_file": source["file"], "bytes": source["bytes"],
                    "sha256": source["sha256"]})
                continue
            bits = int(treatment["bits"])
            kind = treatment["compander"]["kind"]
            parameter = float(treatment["compander"]["parameter"])
            output = root / f"{treatment['id']}.records"
            codes_path = root / f"tmp-{treatment['id']}-codes.u8"
            amplitudes_path = root / f"tmp-{treatment['id']}-amplitudes.f32le"
            if bits == 8:
                output_stream = output.open("wb")
                code_stream = amplitude_stream = None
            else:
                output_stream = None
                code_stream = codes_path.open("wb")
                amplitude_stream = amplitudes_path.open("wb")
            try:
                for start in range(0, rows, args.chunk_rows):
                    current = numpy.asarray(vectors[start:start + args.chunk_rows],
                                            dtype=numpy.float32)
                    codes, amplitudes = quantize(current, bits, kind, parameter)
                    if bits == 8:
                        write_int8(output_stream, codes, amplitudes)
                    else:
                        code_stream.write(codes.tobytes(order="C"))
                        amplitude_stream.write(amplitudes.astype(
                            "<f4", copy=False).tobytes())
            finally:
                for stream in (output_stream, code_stream, amplitude_stream):
                    if stream is not None:
                        stream.close()
            if bits != 8:
                completed = subprocess.run([str(args.native_executable), "--pack",
                    str(bits), str(codes_path), str(amplitudes_path), str(rows),
                    str(output)], check=False, capture_output=True, text=True)
                require(completed.returncode == 0,
                        f"R4 nonlinear packing failed: {completed.stderr}")
                codes_path.unlink()
                amplitudes_path.unlink()
            expected = rows * int(treatment["record_bytes"])
            require(output.stat().st_size == expected,
                    "R4 nonlinear physical store size differs")
            representations.append({**treatment, "file": output.name,
                                    "bytes": expected, "sha256": sha256(output)})
        seeds.append({"seed": seed,
                      "occupied_address_count": parent_row["occupied_address_count"],
                      "representative_count": rows, "mappings": mappings,
                      "representations": representations})
    manifest = {"schema_version": 1,
                "family": "neuroute_r4_nonlinear_quantization_materialization",
                "contract_sha256": sha256(args.contract),
                "parent_materialization_sha256": sha256(parent_manifest_path),
                "source_document_vectors_sha256":
                    parent["source_document_vectors_sha256"],
                "simdcomp_commit": contract["quantization"]["simdcomp_commit"],
                "seeds": seeds}
    (args.output_root / "manifest.json").write_bytes(canonical(manifest))


def self_test() -> None:
    vectors = numpy.asarray([[1.0, -0.25, 0.0]], dtype=numpy.float32)
    codes, amplitudes = quantize(vectors, 5, "power", 0.5)
    require(codes.tolist() == [[30, 7, 15]] and amplitudes.tolist() == [1.0],
            "R4 nonlinear materializer power quantization differs")
    codes, _ = quantize(vectors, 5, "mulaw", 15.0)
    require(codes[0, 0] == 30 and codes[0, 2] == 15,
            "R4 nonlinear materializer mu-law quantization differs")
    print("NeuRoute R4 nonlinear quantization materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
        "neuroute-r4-nonlinear-representative-quantization.example.json")
    for name in ("parent-result", "parent-evidence", "parent-materialization-root",
                 "lossless-result", "lossless-evidence", "native-executable",
                 "output-root"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--chunk-rows", type=int, default=8192)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract", "chunk_rows"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all R4 nonlinear materialization paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"materialize-neuroute-r4-nonlinear-representative-quantization: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
