#!/usr/bin/env python3
"""Materialize source-bound finalist rows and fresh candidate workloads."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
from pathlib import Path

import numpy as np

N = 1_000_000
Q = 305


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def source_file(root: Path, name: str) -> Path:
    """Resolve a canonical source file from either supported source layout."""
    direct = root / name
    nested = root / "payload" / name
    if direct.is_file():
        return direct
    if nested.is_file():
        return nested
    raise FileNotFoundError(f"canonical source file not found: {name} under {root}")


def write_rows(path: Path, rows: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        stream.write(rows.tobytes(order="C"))


def payload_width(rows: np.ndarray, documents: int = N) -> int:
    if rows.nbytes % documents:
        raise ValueError("payload row width is not integral")
    return int(rows.nbytes // documents)


def parse_lsq(path: Path, stages: int) -> np.ndarray:
    data = path.read_bytes()
    if data[:8] != b"AMLSQ01\0":
        raise ValueError("unexpected LSQ payload header")
    encoded_stages, dimension, count = struct.unpack_from("<III", data, 8)
    if (encoded_stages, dimension, count) != (stages, 384, N):
        raise ValueError("unexpected LSQ payload shape")
    offset = 20 + N * 4
    codes = np.frombuffer(data, dtype=np.uint8, count=N * stages, offset=offset).reshape(N, stages)
    offset += codes.nbytes + stages * 256 * 384 * 4 + 4 * 384 * 4
    norms = np.frombuffer(data, dtype="<f4", count=N, offset=offset)
    rows = np.empty(N, dtype=np.dtype([("codes", "u1", (stages,)), ("norm", "<f4")]))
    rows["codes"] = codes
    rows["norm"] = norms
    return rows


def parse_tq(path: Path, pq: bool) -> np.ndarray:
    data = path.read_bytes()
    magic = data[:8]
    dimension, count, width, flags = struct.unpack_from("<4I", data, 8)
    if (dimension, count) != (384, N):
        raise ValueError("unexpected TQ payload shape")
    if not pq:
        if magic != b"AMTQF01\0":
            raise ValueError("unexpected TQ1 payload header")
        return np.frombuffer(data, dtype=np.uint8, count=N * 52, offset=24).reshape(N, 52).copy()
    if magic != b"AMTQP01\0" or width != 8 or flags != 0:
        raise ValueError("unexpected TQ1+PQ8 payload header")
    offset = 24 + N * 4
    signs = np.frombuffer(data, dtype=np.uint8, count=N * 48, offset=offset).reshape(N, 48); offset += signs.nbytes
    scales = np.frombuffer(data, dtype="<f4", count=N, offset=offset); offset += scales.nbytes
    pq_codes = np.frombuffer(data, dtype=np.uint8, count=N * 8, offset=offset).reshape(N, 8); offset += pq_codes.nbytes
    norms = np.frombuffer(data, dtype="<f4", count=N, offset=offset)
    rows = np.empty(N, dtype=np.dtype([("signs", "u1", (48,)), ("scale", "<f4"), ("pq", "u1", (8,)), ("norm", "<f4")]))
    rows["signs"] = signs; rows["scale"] = scales; rows["pq"] = pq_codes; rows["norm"] = norms
    return rows


def parse_int8(codes_path: Path, scales_path: Path, norms_path: Path) -> np.ndarray:
    codes = np.memmap(codes_path, dtype=np.int8, mode="r", shape=(N, 384))
    scales = np.memmap(scales_path, dtype="<f4", mode="r", shape=(N,))
    norms = np.memmap(norms_path, dtype="<f4", mode="r", shape=(N,))
    rows = np.empty(N, dtype=np.dtype([("codes", "i1", (384,)), ("scale", "<f4"), ("inv_norm", "<f4")]))
    rows["codes"] = codes; rows["scale"] = scales; rows["inv_norm"] = norms
    return rows


def prepare_payloads(args: argparse.Namespace) -> dict[str, dict]:
    args.output.mkdir(parents=True, exist_ok=True)
    sources = {
        "tq1-pq8": [args.tq1_pq8],
        "tq1": [args.tq1],
        "lsq32": [args.lsq32],
        "int8": [args.int8, args.int8_scales, args.int8_inv_norm],
    }
    rows_by_codec = {
        "tq1-pq8": parse_tq(args.tq1_pq8, True),
        "tq1": parse_tq(args.tq1, False),
        "lsq32": parse_lsq(args.lsq32, 32),
        "int8": parse_int8(args.int8, args.int8_scales, args.int8_inv_norm),
    }
    payloads = {}
    for codec, rows in rows_by_codec.items():
        path = args.output / codec / "payload.bin"
        write_rows(path, rows)
        payloads[codec] = {
            "path": str(path),
            "payload_sha256": sha256(path),
            # TQ1 is a plain N x 52 byte matrix; structured codecs expose the
            # same width through dtype.itemsize.  Derive from total bytes so
            # both representations remain source-faithful.
            "payload_bytes_doc": payload_width(rows),
            "row_key_bytes": 4,
            "source_sha256": {str(source): sha256(source) for source in sources[codec]},
            "source_paths": [str(source) for source in sources[codec]],
        }
    return payloads


def prepare_workloads(args: argparse.Namespace) -> dict[str, dict]:
    result = json.loads(args.packed_result.read_text(encoding="utf-8"))
    ids_path = source_file(args.source_root, "evaluation-document-ids.jsonl")
    document_ids = [json.loads(line)["id"] for line in ids_path.read_text(encoding="utf-8").splitlines()]
    positions = {value: index for index, value in enumerate(document_ids)}
    workloads = {}
    for mode, source_name in (("prototype_ivf", "prototype-ivf.candidates.i4"), ("modern_r4", "modern-r4-prototype.candidates.i4")):
        mode_dir = args.output / "workloads" / mode
        mode_dir.mkdir(parents=True, exist_ok=True)
        route = args.route_root / source_name
        route_target = mode_dir / "route-5000.i4"
        shutil.copy2(route, route_target)
        ranked = result["stages"][mode]["thq_top128_exact_fp32"]["per_query"]
        top = np.asarray([[positions[item] for item in row["ranked_ids"][:128]] for row in ranked], dtype="<u4")
        top_target = mode_dir / "top128.i4"
        top.tofile(top_target)
        workloads[mode] = {"route": str(route_target), "top128": str(top_target), "route_sha256": sha256(route_target), "top128_sha256": sha256(top_target)}
    return workloads


def self_test() -> None:
    plain = np.zeros((N, 52), dtype=np.uint8)
    structured = np.zeros(N, dtype=np.dtype([("codes", "u1", (32,)), ("norm", "<f4")]))
    if payload_width(plain) != 52 or payload_width(structured) != 36:
        raise ValueError("payload width derivation differs")
    print("prepare-mdbx-finalist-fixture self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--packed-result", type=Path)
    parser.add_argument("--route-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--tq1", type=Path)
    parser.add_argument("--tq1-pq8", type=Path)
    parser.add_argument("--lsq32", type=Path)
    parser.add_argument("--int8", type=Path)
    parser.add_argument("--int8-scales", type=Path)
    parser.add_argument("--int8-inv-norm", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    required = ("source_root", "packed_result", "route_root", "output", "tq1", "tq1_pq8", "lsq32", "int8", "int8_scales", "int8_inv_norm")
    missing = [name for name in required if getattr(args, name) is None]
    if missing:
        parser.error("missing required arguments: " + ", ".join("--" + name.replace("_", "-") for name in missing))
    payloads = prepare_payloads(args)
    workloads = prepare_workloads(args)
    manifest = {"schema_version": 1, "family": "mdbx_finalist_source_fixture_v1", "status": "PREPARED", "documents": N, "queries": Q, "payloads": payloads, "workloads": workloads}
    (args.output / "fixture.manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PREPARED", "manifest": str(args.output / "fixture.manifest.json"), "payloads": list(payloads), "workloads": list(workloads)}, sort_keys=True))


if __name__ == "__main__":
    main()
