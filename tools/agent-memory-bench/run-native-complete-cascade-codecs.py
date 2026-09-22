#!/usr/bin/env python3
"""Materialize finalist payloads and run the native THQ complete-cascade control.

The native executable scores the THQ byte-LUT and cosine-reranks predecoded
rows.  It intentionally does not claim compressed-code decode throughput;
codec decode is audited by the source-bound Python runners and the native
measurement isolates the serving hot path after payload decode.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np

D = 384
THQ_BYTES = 96
QUERY_COUNT = 152
CODECS = {
    "joint2": (32, "joint2"),
    "lsq32": (32, "lsq"),
    "lsq48": (48, "lsq"),
    "turboquant1": (52, "turbo"),
    "turboquant2": (100, "turbo"),
    "elastic_bbq": (62, "bbq"),
    "rslm3": (146, "rslm3"),
    "rslm4": (194, "rslm4"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def unpack_thq(codes: np.ndarray) -> np.ndarray:
    value = np.asarray(codes, dtype=np.uint8)
    result = np.empty((len(value), D), dtype=np.uint8)
    for byte in range(THQ_BYTES):
        lane = value[:, byte]
        result[:, 4 * byte:4 * byte + 4] = np.stack(
            ((lane & 3), (lane >> 2) & 3, (lane >> 4) & 3,
             (lane >> 6) & 3), axis=1)
    return result


def candidate_ids(flat: Path, raw_path: Path) -> tuple[np.ndarray, np.ndarray]:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    counts = np.asarray([int(row["candidate_count"]) for row in raw["rows"]],
                        dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    records = np.memmap(flat, mode="r", dtype=np.uint8,
                        shape=(int(offsets[-1]), 148))
    ids = np.frombuffer(np.asarray(records[:, :4]).tobytes(), dtype="<i4")
    ids = ids.reshape(-1).astype(np.int32, copy=False)
    require(len(counts) == QUERY_COUNT and offsets[-1] == len(ids),
            "candidate stream cardinality differs")
    return ids, offsets.astype(np.uint64)


def write_dense(path: Path, values: np.ndarray) -> None:
    values = np.asarray(values, dtype=np.float32)
    require(values.ndim == 2 and values.shape[1] == D and np.isfinite(values).all(),
            f"invalid dense payload: {path}")
    values.astype("<f4", copy=False).tofile(path)


def build_lsq(thq: np.ndarray, models: Path,
              codes: Path) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    with np.load(models, allow_pickle=False) as model, np.load(codes,
                                                               allow_pickle=False) as saved:
        centroids = np.asarray(model["centroids"], dtype=np.float32)
        selected = np.asarray(saved["selected_ids"], dtype=np.int64)
        selected_unique = np.unique(selected).astype(np.int32)
        levels = unpack_thq(thq[selected_unique])
        base = centroids[np.arange(D)[None, :], levels]
        positions: dict[int, np.ndarray] = {}
        conflicts: dict[int, int] = {}
        code_rows: dict[int, np.ndarray] = {}
        for m in (32, 48):
            code_rows = {}
            codes_m = np.asarray(saved[f"codes_{m}"], dtype=np.uint8)
            require(codes_m.shape[:2] == selected.shape,
                    f"LSQ{m} code shape differs")
            for doc, row in zip(selected.reshape(-1), codes_m.reshape(-1, m)):
                key = int(doc)
                previous = code_rows.get(key)
                if previous is not None:
                    if not np.array_equal(previous, row):
                        conflicts[m] = conflicts.get(m, 0) + 1
                else:
                    code_rows[key] = np.asarray(row, dtype=np.uint8).copy()
            cb = np.asarray(model[f"lsq{m}_codebooks"], dtype=np.float32)
            offsets = np.asarray(model[f"lsq{m}_offsets"], dtype=np.int64)
            decoded = np.empty((len(selected_unique), D), dtype=np.float32)
            for out, doc in enumerate(selected_unique):
                row = code_rows.get(int(doc))
                require(row is not None, f"LSQ{m} missing candidate document {doc}")
                decoded[out] = np.sum(
                    cb[offsets[:-1] + row.astype(np.int64)], axis=0,
                    dtype=np.float32)
            positions[m] = decoded + base
    return {"lsq32": (selected_unique, positions[32]),
            "lsq48": (selected_unique, positions[48]),
            "_lsq_conflicts": conflicts}


def unpack_symbols(packed: np.ndarray, bits: int, count: int) -> np.ndarray:
    """Unpack one row of little-endian packed symbols."""
    values = np.asarray(packed, dtype=np.uint8)
    symbols = np.empty((len(values), count), dtype=np.uint8)
    mask = (1 << bits) - 1
    for index in range(count):
        bit = index * bits
        byte = bit // 8
        shift = bit % 8
        symbols[:, index] = (values[:, byte] >> shift) & mask
        if shift + bits > 8:
            symbols[:, index] |= (values[:, byte + 1] << (8 - shift)) & mask
    return symbols


def build_joint2(thq: np.ndarray, models: Path, artifact_dir: Path,
                 selected_unique: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Decode the persisted THQ-joint2 candidate payload independently."""
    ids = np.fromfile(artifact_dir / "candidate.ids.i4", dtype="<i4")
    require(np.array_equal(ids, selected_unique),
            "joint2 candidate IDs differ from the frozen candidate union")
    with np.load(models, allow_pickle=False) as model:
        centroids = np.asarray(model["centroids"], dtype=np.float32)
    levels = unpack_thq(np.asarray(thq[ids]))
    base = centroids[np.arange(D)[None, :], levels]
    packed_path = artifact_dir / "thq-joint2.candidate.packed"
    codebook_path = artifact_dir / "thq-joint2.codebook.f32"
    packed_width = (128 * 2 + 7) // 8
    packed = np.memmap(packed_path, mode="r", dtype=np.uint8,
                       shape=(len(ids), packed_width))
    symbols = unpack_symbols(packed, 2, 128)
    codebook = np.asarray(np.memmap(codebook_path, mode="r", dtype="<f4",
                                    shape=(128, 64, 4, 3)), dtype=np.float32)
    patterns = levels.reshape(len(ids), 128, 3)
    pattern_ids = (patterns[:, :, 0] + 4 * patterns[:, :, 1] +
                   16 * patterns[:, :, 2]).astype(np.int64)
    decoded = np.empty((len(ids), D), dtype=np.float32)
    for block in range(128):
        decoded[:, 3 * block:3 * block + 3] = codebook[
            block, pattern_ids[:, block], symbols[:, block]]
    return ids, base + decoded


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-executable", type=Path, required=True)
    parser.add_argument("--thq", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--candidate-flat", type=Path, required=True)
    parser.add_argument("--candidate-raw", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--lsq-models", type=Path, required=True)
    parser.add_argument("--lsq-codes", type=Path, required=True)
    parser.add_argument("--turbo-payload", type=Path, required=True)
    parser.add_argument("--bbq-payload", type=Path, required=True)
    parser.add_argument("--rslm-dir", type=Path, required=True)
    parser.add_argument("--joint-artifact-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    thq = np.memmap(args.thq, mode="r", dtype=np.uint8,
                    shape=(1_000_000, THQ_BYTES))
    ids, offsets = candidate_ids(args.candidate_flat, args.candidate_raw)
    unique = np.unique(ids).astype(np.int32)
    ids_path = args.output_root / "codec-document-ids.i4"
    flat_ids_path = args.output_root / "candidate-ids.i4"
    offsets_path = args.output_root / "candidate-offsets.u64"
    unique.astype("<i4").tofile(ids_path)
    ids.astype("<i4").tofile(flat_ids_path)
    offsets.astype("<u8").tofile(offsets_path)

    dense = build_lsq(thq, args.lsq_models, args.lsq_codes)
    lsq_conflicts = dense.pop("_lsq_conflicts")
    dense["joint2"] = build_joint2(thq, args.lsq_models,
                                    args.joint_artifact_dir, unique)
    with np.load(args.turbo_payload, allow_pickle=False) as payload:
        turbo_ids = np.asarray(payload["document_ids"], dtype=np.int32)
        require(np.all(np.isin(turbo_ids, unique)), "TurboQuant IDs are outside candidate union")
        dense["turboquant1"] = (turbo_ids, np.asarray(payload["base"], dtype=np.float32) + np.asarray(payload["decoded1"], dtype=np.float32))
        dense["turboquant2"] = (turbo_ids, np.asarray(payload["base"], dtype=np.float32) + np.asarray(payload["decoded2"], dtype=np.float32))
    with np.load(args.bbq_payload, allow_pickle=False) as payload:
        bbq_ids = np.asarray(payload["document_ids"], dtype=np.int32)
        require(np.all(np.isin(bbq_ids, unique)), "BBQ IDs are outside candidate union")
        base = np.asarray(payload["base"], dtype=np.float32)
        lows = np.asarray(payload["lows"], dtype=np.float32)
        highs = np.asarray(payload["highs"], dtype=np.float32)
        quantized = np.asarray(payload["codes"], dtype=np.float32)
        # v4 BBQ artifacts may fit intervals in a global orthogonal
        # preconditioned frame.  Decode there and apply the inverse rotation;
        # the legacy payload has no rotation field and remains identity.
        rotation = np.asarray(payload["rotation"], dtype=np.float32) if "rotation" in payload else np.eye(D, dtype=np.float32)
        centroid = np.asarray(payload["centroid"], dtype=np.float32) if "centroid" in payload else base[0]
        require(rotation.shape == (D, D), "BBQ rotation shape differs")
        transformed_base = centroid @ rotation
        centered = lows[:, None] + (highs - lows)[:, None] * quantized
        decoded = (transformed_base[None, :] + centered) @ rotation.T
        dense["elastic_bbq"] = (bbq_ids, decoded)
    rslm_count = len(unique)
    for name in ("rslm3", "rslm4"):
        path = args.rslm_dir / f"{name}.faithful.f32"
        require(path.stat().st_size == rslm_count * D * 4,
                f"{name} artifact cardinality differs")
        dense[name] = (unique, np.asarray(np.memmap(path, mode="r", dtype="<f4",
                                                     shape=(rslm_count, D)), dtype=np.float32))

    rows = []
    for name, (payload_bytes, _) in CODECS.items():
        payload_path = args.output_root / f"{name}.decoded.f32"
        codec_ids, codec_vectors = dense[name]
        codec_ids_path = args.output_root / f"{name}.document-ids.i4"
        codec_ids.astype("<i4").tofile(codec_ids_path)
        write_dense(payload_path, codec_vectors)
        command = [str(args.native_executable), "--dense-candidate-gate",
                   str(args.thq), str(args.thresholds), str(codec_ids_path),
                   str(payload_path), str(args.candidate_flat), str(offsets_path),
                   str(args.queries), str(QUERY_COUNT), str(payload_bytes)]
        completed = subprocess.run(command, check=True, capture_output=True,
                                   text=True)
        output_path = args.output_root / f"{name}.native.jsonl"
        output_path.write_text(completed.stdout, encoding="utf-8")
        stderr = json.loads(completed.stderr.strip().splitlines()[-1])
        query_rows = [json.loads(line) for line in completed.stdout.splitlines() if line]
        require(len(query_rows) == QUERY_COUNT, f"{name} native row count differs")
        rows.append({"codec": name, "payload_bytes": payload_bytes,
                     "native_summary": stderr,
                     "output_sha256": sha256(output_path),
                     "decoded_payload_sha256": sha256(payload_path),
                     "top10_rows": query_rows})
    result = {
        "schema_version": 2,
        "family": "thq_native_complete_cascade_predecoded_v2",
        "status": "EXECUTED",
        "metric": "cosine",
        "query_count": QUERY_COUNT,
        "candidate_scope": "frozen R4 candidate stream -> native THQ4 byte-LUT top128",
        "decode_scope": "native cosine rerank over persisted predecoded rows; compressed decode excluded",
        "unique_candidate_documents": int(len(unique)),
        "lsq_duplicate_assignment_conflicts": lsq_conflicts,
        "source_hashes": {key: sha256(path) for key, path in {
            "thq": args.thq, "thresholds": args.thresholds,
            "candidate_flat": args.candidate_flat, "candidate_raw": args.candidate_raw,
            "queries": args.queries}.items()},
        "codec_source_hashes": {
            "lsq_models": sha256(args.lsq_models),
            "lsq_codes": sha256(args.lsq_codes),
            "turbo_payload": sha256(args.turbo_payload),
            "bbq_payload": sha256(args.bbq_payload),
            "joint_candidate_ids": sha256(args.joint_artifact_dir / "candidate.ids.i4"),
            "joint2_packed": sha256(args.joint_artifact_dir / "thq-joint2.candidate.packed"),
            "joint2_codebook": sha256(args.joint_artifact_dir / "thq-joint2.codebook.f32"),
            "rslm3": sha256(args.rslm_dir / "rslm3.faithful.f32"),
            "rslm4": sha256(args.rslm_dir / "rslm4.faithful.f32"),
        },
        "rows": rows,
    }
    result_path = args.output_root / "native-complete-cascade.result.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps({"result": str(result_path), "codecs": list(CODECS),
                      "unique_candidate_documents": len(unique)}, sort_keys=True))


if __name__ == "__main__":
    main()
