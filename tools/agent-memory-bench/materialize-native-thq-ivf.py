#!/usr/bin/env python3
"""Materialize deterministic E5-space IVF postings for the native THQ replay."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import faiss
import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--documents-count", type=int, default=1_000_000)
    parser.add_argument("--nlist", type=int, default=4096)
    parser.add_argument("--train-limit", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=20260908)
    parser.add_argument("--batch", type=int, default=20_000)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    docs = np.memmap(args.documents, mode="r", dtype="<f4",
                     shape=(args.documents_count, 384))
    train = np.asarray(docs[:args.train_limit], dtype=np.float32)
    index = faiss.Kmeans(384, args.nlist, niter=15, nredo=1,
                         seed=args.seed, spherical=True, verbose=False, gpu=False)
    index.train(train)
    centroids = np.asarray(index.centroids, dtype=np.float32)
    centroid_path = args.output / "e5-centroids.f32"
    centroids.tofile(centroid_path)
    flat = faiss.IndexFlatIP(384)
    flat.add(centroids)
    assignments = np.empty(args.documents_count, dtype=np.int32)
    for first in range(0, args.documents_count, args.batch):
        stop = min(first + args.batch, args.documents_count)
        _, ids = flat.search(np.asarray(docs[first:stop], dtype=np.float32), 1)
        assignments[first:stop] = ids[:, 0]
    positions = np.arange(args.documents_count, dtype=np.uint32)
    order = np.lexsort((positions, assignments.astype(np.int64))).astype(np.uint32)
    counts = np.bincount(assignments, minlength=args.nlist).astype(np.uint32)
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.uint64))).astype(np.uint32)
    order_path = args.output / "posting-order.u32"
    offsets_path = args.output / "posting-offsets.u32"
    order.tofile(order_path)
    offsets.tofile(offsets_path)
    manifest = {
        "schema_version": 1,
        "family": "native_thq_ivf_materialization_v1",
        "documents": args.documents_count,
        "dimension": 384,
        "nlist": args.nlist,
        "train_limit": args.train_limit,
        "seed": args.seed,
        "assignment": "spherical_faiss_kmeans_then_stable_cell_order",
        "references": {
            "document_vectors": {"path": str(args.documents.resolve()), "sha256": sha256(args.documents)},
            "centroids": {"path": str(centroid_path.resolve()), "sha256": sha256(centroid_path)},
            "posting_order": {"path": str(order_path.resolve()), "sha256": sha256(order_path)},
            "posting_offsets": {"path": str(offsets_path.resolve()), "sha256": sha256(offsets_path)},
        },
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"family": manifest["family"], "documents": args.documents_count,
                      "nlist": args.nlist, "posting_bytes": int(order.nbytes + offsets.nbytes)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
