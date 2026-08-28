#!/usr/bin/env python3
"""Freeze the multilingual MIRACL train-query pool for nonlinear routing."""

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
SOURCES = (
    ("es", "miracl-v1.0-es/topics/topics.miracl-v1.0-es-train.tsv", 2162,
     "d19226d18d198d740623deefd2f618e9bc5ed589c48b31e6e717e986a856c4d3"),
    ("fr", "miracl-v1.0-fr/topics/topics.miracl-v1.0-fr-train.tsv", 1143,
     "26ddc6d1a7872cd5d0432bce1d138fff02dc7aea64c335a0e95f272930eb9e85"),
    ("ru", "miracl-v1.0-ru/topics/topics.miracl-v1.0-ru-train.tsv", 4683,
     "19c64bbd6cc0a689dbece3a684e8ab87e42e20aa2b0897fdc552235090d95628"),
)
MODEL_ID = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
ORDER_SEED = "neuroute-multilingual-training-query-order-v1"


class MaterializationError(RuntimeError):
    """Raised when frozen multilingual query bytes differ."""


def require(value: bool, message: str) -> None:
    if not value:
        raise MaterializationError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8")


def load_e5_materializer() -> Any:
    path = THIS / "materialize-prepared-e5.py"
    spec = importlib.util.spec_from_file_location("neuroute_multilingual_e5", path)
    if spec is None or spec.loader is None:
        raise MaterializationError("cannot load E5 materializer")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_sources(root: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for language, relative, expected_count, expected_hash in SOURCES:
        path = root / Path(relative).name
        require(path.is_file() and sha256(path) == expected_hash,
                f"multilingual training source differs: {language}")
        current = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            parts = line.split("\t", 1)
            require(len(parts) == 2 and parts[0] and parts[1],
                    f"multilingual training source row differs: {language}/{line_number}")
            current.append((f"{language}:{parts[0]}", parts[1]))
        require(len(current) == expected_count, f"multilingual source count differs: {language}")
        rows.extend(current)
    require(len(rows) == 7988 and len({row[0] for row in rows}) == len(rows),
            "multilingual training pool cardinality differs")
    rows.sort(key=lambda row: (hashlib.sha256(
        f"{ORDER_SEED}\0{row[0]}".encode("utf-8")).digest(), row[0]))
    return rows


def materialize(source_root: Path, output: Path, batch_size: int,
                thread_count: int) -> None:
    require(not output.exists(), "multilingual training output already exists")
    rows = read_sources(source_root)
    e5 = load_e5_materializer()
    encoder = e5.E5Encoder(model_id=MODEL_ID, revision=MODEL_REVISION,
                           cache_dir=None, local_files_only=False,
                           thread_count=thread_count)
    output.mkdir(parents=True)
    ids_path = output / "query-ids.jsonl"
    vectors_path = output / "query-vectors.f32"
    count, dimensions = e5.write_vectors(
        records=rows, output_records_path=ids_path, output_vectors_path=vectors_path,
        prefix="query: ", batch_size=batch_size, encode=encoder.encode,
        progress_label="neuroute_multilingual_training_queries", progress_every=1000)
    require(count == 7988 and dimensions == 384,
            "multilingual training E5 dimensions differ")
    manifest = {
        "schema_version": 1,
        "family": "neuroute_multilingual_training_queries_v1",
        "sources": [{"language": language, "dataset": "miracl/miracl",
                     "revision": SOURCE_REVISION, "path": relative,
                     "count": count_value, "sha256": digest}
                    for language, relative, count_value, digest in SOURCES],
        "ordering": {"algorithm": "sha256_seeded_identifier_v1", "seed": ORDER_SEED},
        "embedding": {"model_id": MODEL_ID, "model_revision": MODEL_REVISION,
                      "query_prefix": "query: ", "normalized": True},
        "execution": encoder.execution_metadata(batch_size),
        "materializer_source_files_sha256": {
            "materialize-neuroute-multilingual-training-queries.py": sha256(Path(__file__)),
            "materialize-prepared-e5.py": sha256(THIS / "materialize-prepared-e5.py"),
        },
        "outputs": {
            "query_ids": {"path": ids_path.name, "sha256": sha256(ids_path),
                          "count": count},
            "query_vectors": {"path": vectors_path.name, "sha256": sha256(vectors_path),
                              "count": count, "dimension": dimensions,
                              "dtype": "float32_le"},
        },
    }
    (output / "manifest.json").write_bytes(canonical(manifest))


def self_test() -> None:
    require(sum(row[2] for row in SOURCES) == 7988
            and all(len(row[3]) == 64 for row in SOURCES),
            "multilingual source contract differs")
    require(MODEL_ID == "intfloat/multilingual-e5-small"
            and MODEL_REVISION == "614241f622f53c4eeff9890bdc4f31cfecc418b3",
            "multilingual model contract differs")
    print("NeuRoute multilingual training-query materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--batch-size", type=int, default=96)
    parser.add_argument("--thread-count", type=int, default=36)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if args.source_root is None or args.output_root is None:
            parser.error("--source-root and --output-root are required")
        materialize(args.source_root, args.output_root, args.batch_size, args.thread_count)
        return 0
    except (OSError, ValueError, MaterializationError) as error:
        print(f"materialize-neuroute-multilingual-training-queries: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
