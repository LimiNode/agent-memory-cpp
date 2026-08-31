#!/usr/bin/env python3
"""Materialize the previously frozen current-K8 coarse router as real bytes."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
import numpy as np

DIMENSIONS = 384
SLOTS = 8


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


def descriptor(rows: list[dict[str, Any]], role: str) -> dict[str, Any]:
    matches = [row for row in rows if row["role"] == role]
    require(len(matches) == 1, f"K8 descriptor differs: {role}")
    return matches[0]


def payload(root: Path, row: dict[str, Any]) -> Path:
    current = root
    if "external_root" in row:
        current /= row["external_root"]
    return current / row["file"]


def normalized(values: np.ndarray) -> np.ndarray:
    result = np.asarray(values, dtype=np.float32).copy()
    norms = np.linalg.norm(result, axis=1).astype(np.float32)
    nonzero = norms > 0.0
    result[nonzero] /= norms[nonzero, None]
    return result


def materialize_seed(documents: np.memmap, layout_path: Path,
                     layout_seed: dict[str, Any], representative_seed: dict[str, Any],
                     representative_root: Path, output_root: Path
                     ) -> dict[str, Any]:
    seed = int(layout_seed["seed"])
    seed_root = layout_path.parent / f"seed-{seed}"
    mappings = layout_seed["mappings"]
    occupied_row = descriptor(mappings, "occupied_addresses")
    counts_row = descriptor(mappings, "address_counts")
    physical_row = descriptor(mappings, "physical_to_document")
    occupied = np.fromfile(payload(seed_root, occupied_row), dtype="<u4")
    counts = np.fromfile(payload(seed_root, counts_row), dtype="<u4")
    physical = np.fromfile(payload(seed_root, physical_row), dtype="<i4")
    require(occupied.size == counts.size and physical.size == 1_000_000 and
            np.all(counts > 0) and int(counts.sum(dtype=np.uint64)) == 1_000_000,
            "K8 layout mappings differ")

    artifact = descriptor(representative_seed["artifacts"],
                          "current_k8_document_members")
    members_path = representative_root / f"seed-{seed}" / artifact["path"]
    require(sha256(members_path) == artifact["sha256"],
            "K8 member artifact differs")
    members = np.load(members_path, mmap_mode="r")
    require(members.shape == (SLOTS - 1, occupied.size) and
            members.dtype == np.int32, "K8 member shape differs")

    effective = np.minimum(counts, SLOTS).astype(np.uint8)
    require(hashlib.sha256(effective.tobytes()).hexdigest() ==
            representative_seed["current_k8_effective_sha256"],
            "K8 effective-count identity differs")
    offsets = np.empty(occupied.size + 1, dtype=np.uint64)
    offsets[0] = 0
    np.cumsum(effective, dtype=np.uint64, out=offsets[1:])
    active = int(offsets[-1])

    output_root.mkdir(parents=True, exist_ok=True)
    records_path = output_root / "current-k8-fp32.records"
    records = np.memmap(records_path, mode="w+", dtype="<f4",
                        shape=(active, DIMENSIONS))
    centroids = np.empty((occupied.size, DIMENSIONS), dtype=np.float32)
    physical_offsets = np.empty(counts.size + 1, dtype=np.uint64)
    physical_offsets[0] = 0
    np.cumsum(counts, dtype=np.uint64, out=physical_offsets[1:])
    physical_rows = np.repeat(np.arange(occupied.size, dtype=np.int32), counts)
    document_rows = np.empty(physical.size, dtype=np.int32)
    require(np.all(physical >= 0), "K8 physical document differs")
    document_rows[physical] = physical_rows
    sums = np.zeros((occupied.size, DIMENSIONS), dtype=np.float32)
    document_batch = 32768
    for first in range(0, documents.shape[0], document_batch):
        stop = min(first + document_batch, documents.shape[0])
        local_rows = document_rows[first:stop]
        order = np.argsort(local_rows, kind="stable")
        sorted_rows = local_rows[order]
        starts = np.r_[0, np.flatnonzero(sorted_rows[1:] !=
                                         sorted_rows[:-1]) + 1]
        unique = sorted_rows[starts]
        vectors = np.asarray(documents[first:stop], dtype=np.float32)[order]
        reduced = np.add.reduceat(vectors, starts, axis=0, dtype=np.float32)
        sums[unique] += reduced
    centroids[:] = normalized(
        sums / counts[:, None].astype(np.float32))
    records[offsets[:-1]] = centroids

    prototype_digest = hashlib.sha256()
    prototype_digest.update(np.ascontiguousarray(centroids).tobytes())
    for slot in range(SLOTS - 1):
        dense = np.zeros((occupied.size, DIMENSIONS), dtype=np.float32)
        active_rows = np.flatnonzero(effective > slot + 1)
        positions = np.asarray(members[slot, active_rows], dtype=np.int64)
        require(np.all(positions >= 0), "K8 active member differs")
        values = normalized(documents[positions])
        dense[active_rows] = values
        records[offsets[active_rows] + slot + 1] = values
        prototype_digest.update(dense.tobytes())
    require(prototype_digest.hexdigest() ==
            representative_seed["current_k8_prototypes_sha256"],
            "K8 prototype identity differs")
    records.flush()
    del records
    return {"seed": seed, "occupied_addresses": int(occupied.size),
        "active_prototypes": active, "slots": SLOTS,
        "dimensions": DIMENSIONS, "record_bytes": DIMENSIONS * 4,
        "layout": "address_major_effective_prefix_fp32le",
        "path": str(records_path.resolve()),
        "bytes": records_path.stat().st_size,
        "sha256": sha256(records_path),
        "effective_sha256": hashlib.sha256(effective.tobytes()).hexdigest(),
        "dense_prototypes_sha256": prototype_digest.hexdigest()}


def run(args: argparse.Namespace) -> None:
    input_manifest = json.loads(args.input_manifest.read_text(encoding="utf-8"))
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    representatives = json.loads(args.representative_result.read_text(
        encoding="utf-8"))
    require(input_manifest["document_count"] == 1_000_000 and
            input_manifest["embedding_dimension"] == DIMENSIONS and
            layout["family"] == "neuroute_r4_layout_materialization" and
            representatives["family"] ==
                "neuroute_r4_document_representatives_result",
            "K8 source contract differs")
    input_root = args.input_manifest.parent
    documents_path = input_root / input_manifest["document_vectors_file"]
    documents = np.memmap(documents_path, mode="r", dtype="<f4",
                          shape=(1_000_000, DIMENSIONS))
    by_seed = {int(row["seed"]): row for row in representatives["seeds"]}
    rows = []
    for layout_seed in layout["seeds"]:
        seed = int(layout_seed["seed"])
        rows.append(materialize_seed(documents, args.layout_manifest,
            layout_seed, by_seed[seed], args.representative_root,
            args.output_root / f"seed-{seed}"))
    result = {"schema_version": 1,
        "family": "neuroute_current_k8_physical_materialization",
        "input_manifest_sha256": sha256(args.input_manifest),
        "layout_manifest_sha256": sha256(args.layout_manifest),
        "representative_result_sha256": sha256(args.representative_result),
        "document_vectors_sha256": sha256(documents_path), "seeds": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    values = np.asarray([[3.0, 4.0], [0.0, 0.0]], dtype=np.float32)
    result = normalized(values)
    require(np.allclose(result[0], [.6, .8]) and
            np.array_equal(result[1], [0.0, 0.0]),
            "K8 materialization self-test differs")
    print("NeuRoute K8 physical materialization self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("input-manifest", "layout-manifest", "representative-result",
                 "representative-root", "output-root", "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = ("input_manifest", "layout_manifest",
                    "representative_result", "representative_root",
                    "output_root", "output")
        if any(getattr(args, name) is None for name in required):
            parser.error("all K8 materialization paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"materialize-neuroute-k8-coarse: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
