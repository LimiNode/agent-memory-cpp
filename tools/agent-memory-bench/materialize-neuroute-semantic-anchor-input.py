#!/usr/bin/env python3
"""Validate and canonicalize frozen R4 semantic-anchor NPZ inputs.

This tool intentionally refuses to synthesize anchor codes.  Anchor codes must
already be produced by the same frozen ITQ transform as document codes; using a
mean/sign surrogate would invalidate the relocation experiment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy


THIS = Path(__file__).resolve().parent
FAMILY = "neuroute_semantic_anchor_relocation_materialization"
BASE = ("documents", "queries", "document_codes", "query_codes")
ANCHOR = tuple(f"{kind}_{name}" for kind in ("centroid", "prototype")
               for name in ("vectors", "codes", "offsets", "documents"))
OPTIONAL = ("target_documents", "query_projection", "adc_centroids")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY,
            "semantic-anchor materialization contract differs")
    require(value.get("code_bits") == 256 and value.get("anchor_code_provenance")
            == "same_frozen_itq_transform_as_documents",
            "semantic-anchor code provenance differs")
    return value


def validate(source: Path, contract: dict[str, Any]) -> dict[str, numpy.ndarray]:
    with numpy.load(source, allow_pickle=False) as archive:
        files = set(archive.files)
        require(set(BASE).issubset(files), "materialization base arrays are missing")
        require(set(ANCHOR).issubset(files), "materialization anchor arrays are missing")
        values = {name: numpy.asarray(archive[name]) for name in files
                  if name in BASE + ANCHOR + OPTIONAL}
    documents = numpy.asarray(values["documents"], dtype=numpy.float32)
    queries = numpy.asarray(values["queries"], dtype=numpy.float32)
    document_codes = numpy.asarray(values["document_codes"], dtype=numpy.uint8)
    query_codes = numpy.asarray(values["query_codes"], dtype=numpy.uint8)
    require(documents.ndim == 2 and queries.ndim == 2
            and documents.shape[1] == queries.shape[1],
            "materialization vector dimensions differ")
    require(document_codes.shape == (len(documents), 32)
            and query_codes.shape == (len(queries), 32),
            "materialization document/query code dimensions differ")
    for kind in ("centroid", "prototype"):
        vectors = numpy.asarray(values[f"{kind}_vectors"], dtype=numpy.float32)
        codes = numpy.asarray(values[f"{kind}_codes"], dtype=numpy.uint8)
        offsets = numpy.asarray(values[f"{kind}_offsets"], dtype=numpy.int64)
        postings = numpy.asarray(values[f"{kind}_documents"], dtype=numpy.int64)
        require(vectors.ndim == 2 and vectors.shape[1] == documents.shape[1]
                and codes.shape == (len(vectors), 32)
                and offsets.shape == (len(vectors) + 1,)
                and offsets[0] == 0 and offsets[-1] == len(postings)
                and numpy.all(offsets[1:] >= offsets[:-1])
                and numpy.all((postings >= 0) & (postings < len(documents))),
                f"{kind} anchor arrays are inconsistent")
    if "target_documents" in values:
        targets = numpy.asarray(values["target_documents"], dtype=numpy.int64)
        require(targets.ndim == 2 and targets.shape[0] == len(queries)
                and numpy.all((targets >= 0) & (targets < len(documents))),
                "materialization targets are inconsistent")
    if "query_projection" in values or "adc_centroids" in values:
        require("query_projection" in values and "adc_centroids" in values,
                "materialization ADC arrays are incomplete")
    return {name: numpy.asarray(value) for name, value in values.items()}


def materialize(source: Path, output: Path, contract_path: Path) -> None:
    contract = load_contract(contract_path)
    values = validate(source, contract)
    output.parent.mkdir(parents=True, exist_ok=True)
    numpy.savez(output, **values)
    manifest = {
        "schema_version": 1, "family": FAMILY,
        "contract_sha256": digest(contract_path.read_bytes()),
        "source_sha256": digest(source.read_bytes()),
        "array_sha256": {name: digest(numpy.ascontiguousarray(values[name]).tobytes())
                          for name in sorted(values)},
        "array_names": sorted(values),
        "anchor_code_provenance": contract["anchor_code_provenance"],
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _descriptor(rows: list[dict[str, Any]], role: str) -> dict[str, Any]:
    matches = [row for row in rows if row["role"] == role]
    require(len(matches) == 1, f"R4 descriptor differs: {role}")
    return matches[0]


def _fit_frozen_transform(query_vectors: numpy.ndarray,
                          query_projection: numpy.ndarray) -> numpy.ndarray:
    """Recover the frozen affine ITQ projection from its query materialization."""
    design = numpy.column_stack((query_vectors.astype(numpy.float64),
                                 numpy.ones(len(query_vectors), dtype=numpy.float64)))
    return numpy.linalg.lstsq(design, query_projection.astype(numpy.float64),
                              rcond=None)[0]


def _encode(values: numpy.ndarray, transform: numpy.ndarray) -> numpy.ndarray:
    result = numpy.empty((len(values), 32), dtype=numpy.uint8)
    for start in range(0, len(values), 16384):
        stop = min(start + 16384, len(values))
        design = numpy.column_stack((numpy.asarray(values[start:stop], dtype=numpy.float64),
                                     numpy.ones(stop - start, dtype=numpy.float64)))
        projected = design @ transform
        result[start:stop] = numpy.packbits(projected >= 0.0, axis=1,
                                            bitorder="little")
    return result


def materialize_r4(layout_path: Path, k8_path: Path, native_path: Path,
                   oracle_path: Path, seed: int, output: Path,
                   contract_path: Path) -> None:
    """Build one seed-bound NPZ from the frozen R4 materializations."""
    contract = load_contract(contract_path)
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    k8 = json.loads(k8_path.read_text(encoding="utf-8"))
    native = json.loads(native_path.read_text(encoding="utf-8"))
    oracle = numpy.load(oracle_path, allow_pickle=False)
    require(layout.get("family") == "neuroute_r4_layout_materialization",
            "R4 layout manifest differs")
    require(k8.get("family") == "neuroute_current_k8_physical_materialization",
            "R4 K8 manifest differs")
    require(native.get("code_bits") == 256 and native.get("embedding_dimension") == 384,
            "R4 native input manifest differs")
    require("exact_top_positions" in oracle and
            numpy.asarray(oracle["exact_top_positions"]).shape == (native["query_count"], 10),
            "R4 exact oracle differs")
    layout_seed = next(row for row in layout["seeds"] if int(row["seed"]) == seed)
    k8_seed = next(row for row in k8["seeds"] if int(row["seed"]) == seed)
    root = layout_path.parent / f"seed-{seed}"
    mappings = layout_seed["mappings"]
    occupied = numpy.fromfile(root / _descriptor(mappings, "occupied_addresses")["file"], dtype="<u4")
    counts = numpy.fromfile(root / _descriptor(mappings, "address_counts")["file"], dtype="<u4")
    representative_counts = numpy.fromfile(
        root / _descriptor(mappings, "representative_counts")["file"], dtype="u1")
    representative_documents = numpy.fromfile(
        root / _descriptor(mappings, "representative_documents")["file"], dtype="<i4")
    layout_queries = numpy.fromfile(
        root / _descriptor(mappings, "query_vectors")["file"], dtype="<f4").reshape(152, 384)
    require(len(occupied) == len(counts) == len(representative_counts)
            and int(representative_counts.sum()) == len(representative_documents),
            "R4 address mappings differ")
    documents_path = native_path.parent / native["document_vectors_file"]
    document_codes_path = native_path.parent / native["document_codes_file"]
    query_vectors = numpy.fromfile(native_path.parent / native["query_vectors_file"],
                                   dtype="<f4").reshape(native["query_count"], 384)
    query_projection = numpy.fromfile(native_path.parent / native["query_itq_projections_file"],
                                      dtype="<f4").reshape(native["query_count"], 256)
    query_codes = numpy.fromfile(native_path.parent / native["query_codes_file"],
                                 dtype="<u8").reshape(native["query_count"], 4).view(numpy.uint8)
    query_indices = []
    lookup = {numpy.ascontiguousarray(row).tobytes(): index
              for index, row in enumerate(query_vectors)}
    for row in layout_queries:
        key = numpy.ascontiguousarray(row).tobytes()
        require(key in lookup, "R4 layout query is absent from native input")
        query_indices.append(lookup[key])
    query_indices = numpy.asarray(query_indices, dtype=numpy.int64)
    transform = _fit_frozen_transform(query_vectors, query_projection)
    reconstructed_codes = _encode(query_vectors[query_indices], transform)
    require(numpy.array_equal(reconstructed_codes, query_codes[query_indices]),
            "reconstructed frozen ITQ query codes differ")
    records = numpy.memmap(Path(k8_seed["path"]), mode="r", dtype="<f4",
                           shape=(int(k8_seed["active_prototypes"]), 384))
    active = numpy.minimum(counts, 8).astype(numpy.int64)
    offsets = numpy.empty(len(active) + 1, dtype=numpy.int64)
    offsets[0] = 0
    numpy.cumsum(active, out=offsets[1:])
    require(int(offsets[-1]) == len(records), "R4 K8 record count differs")
    centroid_vectors = numpy.asarray(records[offsets[:-1]], dtype=numpy.float32)
    prototype_vectors = numpy.asarray(records, dtype=numpy.float32)
    centroid_codes = _encode(centroid_vectors, transform)
    prototype_codes = _encode(prototype_vectors, transform)
    postings_offsets = numpy.empty(len(occupied) + 1, dtype=numpy.int64)
    postings_offsets[0] = 0
    numpy.cumsum(representative_counts.astype(numpy.int64), out=postings_offsets[1:])
    centroid_postings = representative_documents.astype(numpy.int64, copy=True)
    prototype_postings = numpy.empty(int((representative_counts.astype(numpy.int64) * active).sum()),
                                      dtype=numpy.int64)
    cursor = 0
    for address, count in enumerate(active):
        chunk = representative_documents[postings_offsets[address]:postings_offsets[address + 1]]
        for _ in range(int(count)):
            prototype_postings[cursor:cursor + len(chunk)] = chunk
            cursor += len(chunk)
    prototype_offsets = numpy.empty(len(records) + 1, dtype=numpy.int64)
    prototype_offsets[0] = 0
    numpy.cumsum(numpy.repeat(representative_counts.astype(numpy.int64), active),
                out=prototype_offsets[1:])
    values = {
        "documents": numpy.memmap(documents_path, mode="r", dtype="<f4",
                                   shape=(native["document_count"], 384)),
        "queries": query_vectors[query_indices].astype(numpy.float32, copy=False),
        "document_codes": numpy.memmap(document_codes_path, mode="r", dtype=numpy.uint8,
                                        shape=(native["document_count"], 32)),
        "query_codes": query_codes[query_indices],
        "target_documents": numpy.asarray(oracle["exact_top_positions"])[query_indices],
        "query_projection": query_projection[query_indices],
        "adc_centroids": numpy.fromfile(native_path.parent / native["binary_adc_centroids_file"],
                                         dtype="<f4").reshape(256, 2),
        "centroid_vectors": centroid_vectors, "centroid_codes": centroid_codes,
        "centroid_offsets": postings_offsets,
        "centroid_documents": centroid_postings,
        "prototype_vectors": prototype_vectors, "prototype_codes": prototype_codes,
        "prototype_offsets": prototype_offsets,
        "prototype_documents": prototype_postings,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    numpy.savez(output, **values)
    manifest = {"schema_version": 1, "family": FAMILY,
                "contract_sha256": digest(contract_path.read_bytes()),
                "layout_manifest_sha256": digest(layout_path.read_bytes()),
                "k8_manifest_sha256": digest(k8_path.read_bytes()),
                "native_manifest_sha256": digest(native_path.read_bytes()),
                "oracle_sha256": digest(oracle_path.read_bytes()), "seed": seed,
                "query_indices": query_indices.tolist(),
                "anchor_code_provenance": contract["anchor_code_provenance"]}
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def self_test(contract_path: Path) -> int:
    try:
        from importlib.util import spec_from_file_location, module_from_spec
        runner_path = THIS / "run-neuroute-semantic-anchor-relocation.py"
        spec = spec_from_file_location("anchor_runner", runner_path)
        require(spec is not None and spec.loader is not None, "runner import failed")
        module = module_from_spec(spec); spec.loader.exec_module(module)
        values = module.synthetic()
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.npz"
            output = Path(directory) / "materialized.npz"
            numpy.savez(source, **values)
            materialize(source, output, contract_path)
            loaded = validate(output, load_contract(contract_path))
            require(set(loaded) == set(values), "materialized array set differs")
            require(all(numpy.array_equal(loaded[name], values[name]) for name in values),
                    "materialized array bytes differ")
    except (OSError, ValueError, KeyError, TypeError, ImportError, json.JSONDecodeError) as error:
        print(f"semantic-anchor materializer self-test failed: {error}", file=sys.stderr)
        return 1
    print("Semantic-anchor materializer self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("materialize", "from-r4", "self-test"))
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-semantic-anchor-materialization.example.json")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--layout-manifest", type=Path)
    parser.add_argument("--k8-manifest", type=Path)
    parser.add_argument("--native-input-manifest", type=Path)
    parser.add_argument("--oracle", type=Path)
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    try:
        if args.command == "self-test":
            return self_test(args.contract)
        if args.command == "from-r4":
            require(all(value is not None for value in (
                args.layout_manifest, args.k8_manifest, args.native_input_manifest,
                args.oracle, args.seed, args.output)),
                    "from-r4 requires frozen manifests, oracle, seed, and output")
            materialize_r4(args.layout_manifest, args.k8_manifest,
                           args.native_input_manifest, args.oracle, args.seed,
                           args.output, args.contract)
            return 0
        require(args.input is not None and args.output is not None,
                "materialize requires --input and --output")
        materialize(args.input, args.output, args.contract)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"materialize-neuroute-semantic-anchor-input: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
