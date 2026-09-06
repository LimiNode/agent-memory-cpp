#!/usr/bin/env python3
"""Materialize frozen document-cell routing inputs for the native bake-off.

The materialization is deliberately separate from the native scorer: all
centroids, PCA cells, teacher labels and Direct4096 weights are frozen in one
manifest, while large document/code payloads are referenced by their existing
evidence-bound paths.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import numpy as np

THIS = Path(__file__).resolve().parent


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    require(spec is not None and spec.loader is not None, f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


centroid = load("centroid", "run-pca12-bucket-centroid-bakeoff.py")
multi = load("multi", "run-multimodal-cell-router-bakeoff.py")


def write_array(path: Path, value: np.ndarray) -> dict[str, Any]:
    value.astype(value.dtype.newbyteorder("<"), copy=False).tofile(path)
    return {"path": path.name, "bytes": path.stat().st_size,
            "sha256": sha256(path), "dtype": value.dtype.str,
            "shape": list(value.shape)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--native-input-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", default="13,37,101")
    args = parser.parse_args()
    manifest = json.loads(args.cache.read_text(encoding="utf-8"))
    require(manifest.get("family") == "neuroute_ordinal_lattice_input",
            "ordinal cache family differs")
    root = args.cache.parent
    data = {name: np.load(root / row["path"], allow_pickle=False)
            for name, row in manifest["outputs"].items()}
    source = manifest["source"]
    documents = np.memmap(Path(source["document_vectors"]), mode="r", dtype="<f4",
                          shape=(int(source["document_count"]), 384))
    require(documents.shape[0] == 1_000_000, "document count differs")
    native_manifest = json.loads(args.native_input_manifest.read_text(encoding="utf-8"))
    native_root = args.native_input_manifest.parent
    def native_file(name: str) -> Path:
        path = native_root / native_manifest[name]
        require(path.is_file(), f"native payload missing: {name}")
        return path

    mean, projection, cuts, projected, cells = centroid.frozen_partition(documents)
    postings = centroid.build_postings(cells)
    pca_centers, e5_centers = centroid.make_centroids(
        documents, projected, cells, postings, 8)
    train_queries = np.asarray(data["train_queries"], dtype=np.float32)
    train_ids = np.asarray(data["train_teacher_ids"], dtype=np.int64)
    train_cells = multi.teacher_cells(train_ids, cells)
    normalized_train = train_queries - mean
    artifacts: dict[str, dict[str, np.ndarray]] = {}
    for seed in (int(value) for value in args.seeds.split(",")):
        artifacts[str(seed)] = multi.train_head(normalized_train, train_cells,
                                                4096, seed, 120)
    args.output.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Any] = {}
    outputs["mean"] = write_array(args.output / "pca-mean.f32", mean)
    outputs["projection"] = write_array(args.output / "pca-projection.f32", projection)
    outputs["cuts"] = write_array(args.output / "pca-cuts.f32", cuts)
    outputs["cells"] = write_array(args.output / "document-cells.u16", cells)
    outputs["pca-centroids"] = write_array(args.output / "pca-centroids-k1.f32",
                                            pca_centers[:, 0])
    for count in (1, 2, 4, 8):
        outputs[f"e5-centroids-k{count}"] = write_array(
            args.output / f"e5-centroids-k{count}.f32", e5_centers[:, :count])
    model_outputs = {}
    for seed, artifact in artifacts.items():
        model_outputs[seed] = {}
        for name, value in artifact.items():
            model_outputs[seed][name] = write_array(
                args.output / f"direct4096-{seed}-{name}.f32", value)
    outputs["direct4096"] = model_outputs
    # The ordinal cache's first 152 queries are the same config/internal query
    # order used by the native DE-1M input.  Copy compact labels into the
    # materialization so the native process has no Python dependency.
    for name in ("eval_queries", "eval_teacher_ids", "eval_qrel_ids",
                 "eval_qrel_scores"):
        outputs[name] = write_array(args.output / f"{name}.bin",
                                    np.asarray(data[name]))
    native_names = {
        "document_vectors": "document_vectors_file",
        "document_codes": "document_codes_file",
        "query_codes": "query_codes_file",
        "query_projections": "query_itq_projections_file",
        "adc_centroids": "binary_adc_centroids_file",
    }
    native_payloads = {}
    for logical, key in native_names.items():
        path = native_file(key)
        native_payloads[logical] = {"path": str(path.resolve()),
                                    "bytes": path.stat().st_size,
                                    "sha256": sha256(path)}
    # Native input contains 305 queries in a different order.  Align its
    # compact codes/projections to the 152-query ordinal evaluation set by
    # exact vector identity, rather than assuming positional correspondence.
    native_queries = np.fromfile(native_file("query_vectors_file"), dtype="<f4").reshape(-1, 384)
    native_codes = np.fromfile(native_file("query_codes_file"), dtype="u1").reshape(-1, 32)
    native_projections = np.fromfile(native_file("query_itq_projections_file"), dtype="<f4").reshape(-1, 256)
    mapping = np.asarray([int(np.argmax(native_queries @ query))
                          for query in np.asarray(data["eval_queries"], dtype=np.float32)], dtype=np.int32)
    eval_vectors = np.asarray(data["eval_queries"], dtype=np.float32)
    require(np.all(np.sum(native_queries[mapping] * eval_vectors, axis=1) > 0.999),
            "native query alignment failed")
    outputs["query_codes_mapped"] = write_array(
        args.output / "query-codes-mapped.u8", native_codes[mapping])
    outputs["query_projections_mapped"] = write_array(
        args.output / "query-projections-mapped.f32", native_projections[mapping])
    outputs["query_mapping"] = write_array(args.output / "query-mapping.i32", mapping)
    final = {"schema_version": 1,
             "family": "native_document_routing_bakeoff_materialization_v1",
             "documents": int(documents.shape[0]), "dimension": 384,
             "query_count": int(data["eval_queries"].shape[0]),
             "candidate_budgets": [32000, 64000],
             "routing_methods": ["pca_threshold", "pca_centroid_k1",
                                 "e5_centroid_k1", "e5_centroid_k2",
                                 "e5_centroid_k4", "e5_centroid_k8",
                                 "direct4096_top32"],
             "cache_manifest_sha256": sha256(args.cache),
             "native_input_manifest_sha256": sha256(args.native_input_manifest),
             "outputs": outputs, "native_payloads": native_payloads,
             "protocol": {"partition": "pca12_documents_stride4_svd_median",
                          "centroid_training": "deterministic_5_iter_bucket_kmeans_in_pca12",
                          "direct4096": "supervised_multihot_cell_head_top32_then_pca_fallback",
                          "cascade": "cell postings -> Hamming@768 -> ADC@64 -> exact@10",
                          "warmups": 1, "repeats": 3}}
    (args.output / "manifest.json").write_text(
        json.dumps(final, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(args.output / "manifest.json"),
                      "sha256": sha256(args.output / "manifest.json")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
