#!/usr/bin/env python3
"""Independent packed replay for the full-corpus TQ1+PQ8 gate."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import struct
from pathlib import Path

import numpy as np

D, N, THQ_BYTES, TQ_BYTES, SUBSPACES = 384, 1_000_000, 96, 48, 8


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def reference_module():
    path = Path(__file__).with_name("run-thq-turboquant-reference.py")
    spec = importlib.util.spec_from_file_location("tq_reference_full", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load TQ reference")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


def read_raw(path: Path, query_count: int, repeats: int = 5) -> dict[int, list[int]]:
    rows: dict[int, list[int]] = {}; seen: set[tuple[int, int]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        row = json.loads(line); query, repeat = row.get("query"), row.get("repeat")
        ids = row.get("top10_ids")
        if not isinstance(query, int) or not isinstance(repeat, int) or not isinstance(ids, list) or len(ids) != 10:
            raise ValueError("raw shape differs")
        if not (0 <= query < query_count and 0 <= repeat < repeats) or (query, repeat) in seen:
            raise ValueError("raw query/repeat contract differs")
        seen.add((query, repeat))
        if repeat == 0: rows[query] = ids
    if len(seen) != query_count * repeats or len(rows) != query_count: raise ValueError("raw coverage differs")
    return rows


def parse_payload(path: Path):
    data = path.read_bytes(); dim, count, subs, flags = struct.unpack_from("<4I", data, 8)
    if data[:8] != b"AMTQP01\0" or (dim, count, subs, flags) != (D, N, SUBSPACES, 0): raise ValueError("payload header differs")
    offset = 24; ids = np.frombuffer(data, dtype="<i4", count=N, offset=offset); offset += N * 4
    signs = np.frombuffer(data, dtype=np.uint8, count=N * TQ_BYTES, offset=offset).reshape(N, TQ_BYTES); offset += N * TQ_BYTES
    scales = np.frombuffer(data, dtype="<f4", count=N, offset=offset); offset += N * 4
    codes = np.frombuffer(data, dtype=np.uint8, count=N * SUBSPACES, offset=offset).reshape(N, SUBSPACES); offset += N * SUBSPACES
    norms = np.frombuffer(data, dtype="<f4", count=N, offset=offset); offset += N * 4
    centroids = np.frombuffer(data, dtype="<f4", count=D * 4, offset=offset).reshape(D, 4); offset += D * 4 * 4
    pq = np.frombuffer(data, dtype="<f4", count=SUBSPACES * 256 * 48, offset=offset).reshape(SUBSPACES, 256, 48)
    return ids, signs, scales, codes, norms, centroids, pq


def top10(values, query, chunk_size=100_000):
    ids, signs, scales, codes, norms, centroids, pq = values; tq = reference_module()
    base_lut = np.empty((THQ_BYTES, 256), dtype=np.float64)
    for byte in range(THQ_BYTES):
        for packed in range(256):
            levels = [(packed >> (2 * lane)) & 3 for lane in range(4)]
            base_lut[byte, packed] = sum(float(centroids[4 * byte + lane, levels[lane]]) * float(query[4 * byte + lane]) for lane in range(4))
    rotated = tq.rotate(query[None, :])[0].astype(np.float64); tq_lut = np.empty((TQ_BYTES, 256), dtype=np.float64)
    for byte in range(TQ_BYTES):
        for packed in range(256): tq_lut[byte, packed] = sum((1.0 if (packed >> bit) & 1 else -1.0) * 0.7978846 * rotated[8 * byte + bit] for bit in range(8))
    pq_lut = np.einsum("scw,sw->sc", pq, query.reshape(SUBSPACES, 48))
    qnorm = float(np.linalg.norm(query.astype(np.float64))); ranked: list[tuple[float, int]] = []
    thq_path = Path("__THQ__")
    raise RuntimeError("top10 requires THQ codes passed by main")


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--payload", type=Path, required=True); parser.add_argument("--thq", type=Path, required=True); parser.add_argument("--queries", type=Path, required=True); parser.add_argument("--raw", type=Path, required=True); parser.add_argument("--result", type=Path, required=True); parser.add_argument("--chunk-size", type=int, default=100_000)
    args = parser.parse_args(); native = read_raw(args.raw, 152); values = parse_payload(args.payload); thq = np.memmap(args.thq, mode="r", dtype=np.uint8, shape=(N, THQ_BYTES)); queries = np.memmap(args.queries, mode="r", dtype="<f4", shape=(152, D)); tq = reference_module(); ids, signs, scales, codes, norms, centroids, pq = values; mismatches = []
    for qi, query in enumerate(queries):
        base_lut = np.empty((THQ_BYTES, 256), dtype=np.float64)
        for byte in range(THQ_BYTES):
            for packed in range(256):
                levels = [(packed >> (2 * lane)) & 3 for lane in range(4)]; base_lut[byte, packed] = sum(float(centroids[4 * byte + lane, levels[lane]]) * float(query[4 * byte + lane]) for lane in range(4))
        rotated = tq.rotate(query[None, :])[0].astype(np.float64); tq_lut = np.empty((TQ_BYTES, 256), dtype=np.float64)
        for byte in range(TQ_BYTES):
            for packed in range(256): tq_lut[byte, packed] = sum((1.0 if (packed >> bit) & 1 else -1.0) * 0.7978846 * rotated[8 * byte + bit] for bit in range(8))
        pq_lut = np.einsum("scw,sw->sc", pq, query.reshape(SUBSPACES, 48)); qnorm = float(np.linalg.norm(query.astype(np.float64))); candidates=[]
        for begin in range(0, N, args.chunk_size):
            end=min(N, begin+args.chunk_size); scores=base_lut[np.arange(THQ_BYTES), np.asarray(thq[begin:end])].sum(axis=1) + tq_lut[np.arange(TQ_BYTES), signs[begin:end]].sum(axis=1) * scales[begin:end] + pq_lut[np.arange(SUBSPACES), codes[begin:end]].sum(axis=1); scores=scores/(np.maximum(norms[begin:end].astype(np.float64)*qnorm, np.finfo(np.float64).tiny)); take=min(32,end-begin); sel=np.argpartition(-scores,take-1)[:take]; candidates.extend((float(scores[i]), int(ids[begin+i])) for i in sel)
        candidates.sort(key=lambda item:(-item[0],item[1])); expected=[item[1] for item in candidates[:10]]
        if expected != native[qi]: mismatches.append(qi)
    result={"status":"PASS" if not mismatches else "FAIL","metric":"reconstructed_cosine","query_count":152,"independent_top10_exact":f"{152-len(mismatches)}/152","mismatches":mismatches[:16],"payload_sha256":sha256(args.payload),"thq_sha256":sha256(args.thq),"queries_sha256":sha256(args.queries),"raw_sha256":sha256(args.raw),"reference_runner_sha256":sha256(Path(__file__))}; args.result.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8"); print(json.dumps(result,sort_keys=True));
    if mismatches: raise SystemExit(1)


if __name__ == "__main__": main()
