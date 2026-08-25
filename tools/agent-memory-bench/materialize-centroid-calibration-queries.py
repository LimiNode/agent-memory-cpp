#!/usr/bin/env python3
"""Freeze a train-split MIRACL query bundle for centroid-router calibration."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent
SOURCE_REVISION = "5be20db9509754dadad47689368639fcec739c00"
SOURCE_PATH = "miracl-v1.0-es/topics/topics.miracl-v1.0-es-train.tsv"
SOURCE_SHA256 = "d19226d18d198d740623deefd2f618e9bc5ed589c48b31e6e717e986a856c4d3"
MODEL_ID = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"


class CalibrationMaterializationError(RuntimeError):
    """Raised when the frozen calibration-query contract is not met."""


def require(value: bool, message: str) -> None:
    if not value:
        raise CalibrationMaterializationError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_e5_materializer() -> Any:
    path = THIS / "materialize-prepared-e5.py"
    spec = importlib.util.spec_from_file_location("centroid_calibration_e5_materializer", path)
    if spec is None or spec.loader is None:
        raise CalibrationMaterializationError("cannot load E5 materializer")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def topics(path: Path) -> list[tuple[str, str]]:
    require(path.is_file() and sha256(path) == SOURCE_SHA256, "calibration query source differs")
    rows: list[tuple[str, str]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        parts = line.split("\t", 1)
        require(len(parts) == 2 and parts[0] and parts[1], f"calibration query source line {line_number} differs")
        rows.append((f"es:{parts[0]}", parts[1]))
    require(len(rows) == 2162 and len({identifier for identifier, _ in rows}) == len(rows), "calibration query source cardinality differs")
    return rows


def materialize(source: Path, output: Path, batch_size: int, thread_count: int) -> dict[str, Any]:
    require(not output.exists(), "calibration query output already exists")
    rows = topics(source)
    e5 = load_e5_materializer()
    encoder = e5.E5Encoder(model_id=MODEL_ID, revision=MODEL_REVISION, cache_dir=None, local_files_only=False, thread_count=thread_count)
    output.mkdir(parents=True)
    ids_path, vectors_path = output / "query-ids.jsonl", output / "query-vectors.f32"
    count, dimensions = e5.write_vectors(records=rows, output_records_path=ids_path, output_vectors_path=vectors_path, prefix="query: ", batch_size=batch_size, encode=encoder.encode, progress_label="centroid_calibration_queries", progress_every=1000)
    require(count == len(rows) and dimensions == 384, "calibration query E5 dimensions differ")
    manifest = {
        "schema_version": 1,
        "family": "centroid_router_calibration_queries_v1",
        "source": {"dataset": "miracl/miracl", "revision": SOURCE_REVISION, "path": SOURCE_PATH, "sha256": SOURCE_SHA256, "count": count},
        "embedding": {"model_id": MODEL_ID, "model_revision": MODEL_REVISION, "query_prefix": "query: ", "normalized": True},
        "execution": encoder.execution_metadata(batch_size),
        "materializer_source_files_sha256": {"materialize-centroid-calibration-queries.py": sha256(Path(__file__)), "materialize-prepared-e5.py": sha256(THIS / "materialize-prepared-e5.py")},
        "outputs": {"query_ids": {"path": ids_path.name, "sha256": sha256(ids_path), "count": count}, "query_vectors": {"path": vectors_path.name, "sha256": sha256(vectors_path), "count": count, "dimension": dimensions, "dtype": "float32_le"}},
    }
    (output / "manifest.json").write_bytes(canonical(manifest))
    return manifest


def self_test() -> None:
    require(SOURCE_PATH == "miracl-v1.0-es/topics/topics.miracl-v1.0-es-train.tsv" and len(SOURCE_SHA256) == 64, "calibration source contract differs")
    require(MODEL_ID == "intfloat/multilingual-e5-small" and MODEL_REVISION == "614241f622f53c4eeff9890bdc4f31cfecc418b3", "calibration model contract differs")
    print("centroid calibration-query materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--batch-size", type=int, default=96)
    parser.add_argument("--thread-count", type=int, default=36)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test(); return 0
        if args.source is None or args.output_root is None:
            parser.error("--source and --output-root are required")
        materialize(args.source, args.output_root, args.batch_size, args.thread_count)
        return 0
    except (OSError, ValueError, CalibrationMaterializationError) as error:
        print(f"materialize-centroid-calibration-queries: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
