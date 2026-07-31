#!/usr/bin/env python3
"""Generate the frozen multilingual E5 precomputed embedding benchmark fixture.

The script is intentionally outside the default CI path: it runs a third-party
model. CI validates the committed JSON artifact and its provenance instead.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import multilingual_e5_fixture_generator_common as e5_common
import precomputed_fixture_contract as hash_contract
import precomputed_fixture_multilingual_e5_contract as content_contract


GENERATOR_REVISION = "agent-memory-cpp:multilingual-e5-small-fixture-v1"
GENERATOR_COMMAND = (
    "python tools/agent-memory-bench/generate-precomputed-multilingual-e5-small-fixture.py "
    "--output tests/eval/fixtures/precomputed-embedding-multilingual-e5-small.json"
)
DATASET_REVISION = "agent-memory-multilingual-e5-small-fixture:2026-07-31"
QRELS_REVISION = "agent-memory-multilingual-e5-small-qrels:2026-07-31"


def canonical_contract_script_path() -> Path:
    return Path(__file__).with_name("precomputed_fixture_contract.py")


def content_contract_script_path() -> Path:
    return Path(__file__).with_name("precomputed_fixture_multilingual_e5_contract.py")


def sha256_contract_sources() -> str:
    """Hash the canonical encoding helper and the bilingual content contract."""

    payload = (
        e5_common.sha256_file(canonical_contract_script_path())
        + "\n"
        + e5_common.sha256_file(content_contract_script_path())
        + "\n"
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def build_fixture(*, cache_dir: Path | None, local_files_only: bool) -> dict[str, Any]:
    corpus = content_contract.CORPUS
    queries = content_contract.QUERIES
    judgments = content_contract.JUDGMENTS
    document_texts = [f"passage: {item['title']}\n{item['text']}" for item in corpus]
    query_texts = [f"query: {item['text']}" for item in queries]
    vectors = e5_common.encode_texts(
        document_texts + query_texts,
        cache_dir=cache_dir,
        local_files_only=local_files_only,
    )
    document_vectors = vectors[: len(document_texts)]
    query_vectors = vectors[len(document_texts):]
    dimension = len(document_vectors[0])

    root: dict[str, Any] = {
        "schema_version": 1,
        "name": "precomputed-embedding-multilingual-e5-small",
        "embedding_model": {
            "model_id": e5_common.MODEL_ID,
            "dimension": dimension,
            "similarity_metric": "cosine",
            "pooling_mode": "model_default",
            "normalized": True,
        },
        "embedding_artifact": {
            "generator_id": e5_common.GENERATOR_ID,
            "generator_version": e5_common.GENERATOR_VERSION,
            "dataset_revision": DATASET_REVISION,
            "generator_revision": GENERATOR_REVISION,
            "generator_source_hash": e5_common.generator_source_hash(Path(__file__)),
            "generator_contract_source_hash": sha256_contract_sources(),
            "generator_command": GENERATOR_COMMAND,
            "generator_requirements_lock": e5_common.requirements_lock_identity(),
            "model_revision": e5_common.MODEL_REVISION,
            "tokenizer_revision": e5_common.TOKENIZER_REVISION,
            "qrels_revision": QRELS_REVISION,
            "document_prompt_id": e5_common.DOCUMENT_PROMPT_ID,
            "query_prompt_id": e5_common.QUERY_PROMPT_ID,
            "projection_kind": e5_common.PROJECTION_KIND,
            "normalization": "l2",
            "dtype": "float32",
            "hash_algorithm": "sha256",
            "config_hash": "",
            "dataset_hash": "",
            "qrels_hash": "",
            "artifact_hash": "",
        },
        "corpus": corpus,
        "queries": queries,
        "judgments": judgments,
        "document_embeddings": [
            {"id": item["id"], "vector": vector}
            for item, vector in zip(corpus, document_vectors)
        ],
        "query_embeddings": [
            {"id": query["id"], "vector": vector}
            for query, vector in zip(queries, query_vectors)
        ],
    }
    artifact = root["embedding_artifact"]
    artifact["config_hash"] = hash_contract.sha256_hex(
        hash_contract.canonical_config_text(root)
    )
    artifact["dataset_hash"] = hash_contract.sha256_hex(
        hash_contract.canonical_dataset_payload(root)
    )
    artifact["qrels_hash"] = hash_contract.sha256_hex(
        hash_contract.canonical_qrels_payload(root)
    )
    artifact["artifact_hash"] = hash_contract.sha256_hex(
        hash_contract.canonical_artifact_payload(root)
    )
    return root


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--local-files-only", action="store_true")
    args = parser.parse_args()

    fixture = build_fixture(
        cache_dir=args.cache_dir,
        local_files_only=args.local_files_only,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(fixture, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
