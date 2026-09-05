#!/usr/bin/env python3
"""Materialize the frozen DE-1M ordinal-lattice teacher cache.

The 1M document matrix remains an immutable referenced memmap.  Only the
train-vector sample, split query vectors, exact E5 top-10 teacher rows, and
qrels positions are written into the cache directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def read_protocol(path: Path) -> tuple[list[int], list[str]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    requests = value["requests"]
    return ([int(row["native_query"]) for row in requests],
            [str(row["query_id"]) for row in requests])


def exact_top(documents: np.ndarray, queries: np.ndarray, count: int,
              block: int) -> tuple[np.ndarray, np.ndarray]:
    best_ids = np.empty((len(queries), 0), dtype=np.int64)
    best_scores = np.empty((len(queries), 0), dtype=np.float32)
    for start in range(0, len(documents), block):
        stop = min(start + block, len(documents))
        scores = np.asarray(documents[start:stop] @ queries.T,
                            dtype=np.float32).T
        ids = np.arange(start, stop, dtype=np.int64)
        for qi in range(len(queries)):
            merged_scores = np.concatenate((best_scores[qi], scores[qi]))
            merged_ids = np.concatenate((best_ids[qi], ids))
            keep = min(count, len(merged_ids))
            positions = np.argpartition(-merged_scores, keep - 1)[:keep]
            order = np.lexsort((merged_ids[positions],
                                -merged_scores[positions]))
            chosen = positions[order]
            if qi == 0:
                next_ids = np.empty((len(queries), keep), dtype=np.int64)
                next_scores = np.empty((len(queries), keep), dtype=np.float32)
            next_ids[qi] = merged_ids[chosen]
            next_scores[qi] = merged_scores[chosen]
        best_ids, best_scores = next_ids, next_scores
        print(f"exact teacher {stop}/{len(documents)}", flush=True)
    return best_ids, best_scores


def read_document_ids(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    with path.open("r", encoding="utf-8") as stream:
        for position, line in enumerate(stream):
            value = json.loads(line)
            identifier = value if isinstance(value, str) else value.get("id")
            require(isinstance(identifier, str), "document id row differs")
            result[identifier] = position
    return result


def read_qrels(path: Path, query_ids: list[str], document_positions: dict[str, int]
               ) -> tuple[np.ndarray, np.ndarray]:
    wanted = {query: index for index, query in enumerate(query_ids)}
    rows: list[list[tuple[int, float]]] = [[] for _ in query_ids]
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            fields = line.split()
            if len(fields) < 4 or fields[0] not in wanted:
                continue
            document = document_positions.get(fields[2])
            if document is not None:
                rows[wanted[fields[0]]].append((document, float(fields[3])))
    width = max(1, max(map(len, rows)))
    ids = np.full((len(rows), width), -1, dtype=np.int64)
    scores = np.zeros((len(rows), width), dtype=np.float32)
    for index, values in enumerate(rows):
        values.sort(key=lambda row: (-row[1], row[0]))
        for column, (document, score) in enumerate(values):
            ids[index, column] = document
            scores[index, column] = score
    return ids, scores


def save(path: Path, value: np.ndarray) -> dict[str, Any]:
    np.save(path, value, allow_pickle=False)
    return {"path": path.name, "sha256": sha256(path),
            "shape": list(value.shape), "dtype": str(value.dtype),
            "bytes": path.stat().st_size}


def run(args: argparse.Namespace) -> None:
    source = json.loads(args.input_manifest.read_text(encoding="utf-8"))
    dimension = int(source["embedding_dimension"])
    document_count = int(source["document_count"])
    query_count = int(source["query_count"])
    root = args.input_manifest.parent
    document_path = root / source["document_vectors_file"]
    query_path = root / source["query_vectors_file"]
    require(sha256(document_path) == source["document_vectors_sha256"],
            "document vector source hash differs")
    require(sha256(query_path) == source["query_vectors_sha256"],
            "query vector source hash differs")
    documents = np.memmap(document_path, mode="r", dtype="<f4",
                          shape=(document_count, dimension))
    queries = np.memmap(query_path, mode="r", dtype="<f4",
                        shape=(query_count, dimension))
    config_positions, config_ids = read_protocol(args.configuration_protocol)
    internal_positions, internal_ids = read_protocol(args.internal_protocol)
    require(len(config_positions) == len(internal_positions) == 76,
            "ordinal lattice query split differs")
    require(not set(config_positions) & set(internal_positions),
            "ordinal lattice config/internal overlap")
    train_query_positions = sorted(set(range(query_count))
                                   - set(config_positions)
                                   - set(internal_positions))
    require(len(train_query_positions) == 153,
            "ordinal lattice train query split differs")
    eval_positions = config_positions + internal_positions
    eval_ids = config_ids + internal_ids
    eval_queries = np.asarray(queries[eval_positions], dtype=np.float32)
    teacher_ids, teacher_scores = exact_top(documents, eval_queries,
                                            args.teacher_k, args.block_documents)
    rng = np.random.default_rng(args.seed)
    sample_positions = np.sort(rng.choice(document_count,
                                          size=args.train_documents,
                                          replace=False))
    train_vectors = np.asarray(documents[sample_positions], dtype=np.float32)
    train_query_vectors = np.asarray(queries[train_query_positions], dtype=np.float32)
    train_teacher_ids, train_teacher_scores = exact_top(
        documents, train_query_vectors, args.teacher_k, args.block_documents)
    document_positions = read_document_ids(args.e5_root /
                                           "evaluation-document-ids.jsonl")
    qrel_ids, qrel_scores = read_qrels(args.e5_root / "evaluation-qrels.tsv",
                                       eval_ids, document_positions)
    args.output.mkdir(parents=True, exist_ok=True)
    outputs = {
        "train_vectors": save(args.output / "train-vectors.npy", train_vectors),
        "train_queries": save(args.output / "train-queries.npy",
                              train_query_vectors),
        "train_teacher_ids": save(args.output / "train-teacher-ids.npy", train_teacher_ids),
        "train_teacher_scores": save(args.output / "train-teacher-scores.npy", train_teacher_scores),
        "eval_queries": save(args.output / "eval-queries.npy", eval_queries),
        "eval_teacher_ids": save(args.output / "eval-teacher-ids.npy", teacher_ids),
        "eval_teacher_scores": save(args.output / "eval-teacher-scores.npy", teacher_scores),
        "eval_partition": save(args.output / "eval-partition.npy",
                               np.asarray(["config"] * 76 + ["internal"] * 76)),
        "eval_qrel_ids": save(args.output / "eval-qrel-ids.npy", qrel_ids),
        "eval_qrel_scores": save(args.output / "eval-qrel-scores.npy", qrel_scores),
        "train_document_positions": save(args.output / "train-document-positions.npy",
                                         sample_positions),
    }
    manifest = {"schema_version": 1,
        "family": "neuroute_ordinal_lattice_input",
        "source": {"input_manifest": str(args.input_manifest.resolve()),
                   "input_manifest_sha256": sha256(args.input_manifest),
                   "document_vectors": str(document_path.resolve()),
                   "document_vectors_sha256": source["document_vectors_sha256"],
                   "document_count": document_count, "dimension": dimension},
        "split": {"train_queries": len(train_query_positions),
                  "config_queries": len(config_positions),
                  "internal_queries": len(internal_positions),
                  "configuration_protocol_sha256": sha256(args.configuration_protocol),
                  "internal_protocol_sha256": sha256(args.internal_protocol)},
        "teacher": {"kind": "exact_global_e5_inner_product",
                    "top_k": args.teacher_k},
        "training": {"document_count": args.train_documents,
                     "seed": args.seed, "labels_used": False},
        "outputs": outputs}
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-manifest", type=Path, required=True)
    parser.add_argument("--configuration-protocol", type=Path, required=True)
    parser.add_argument("--internal-protocol", type=Path, required=True)
    parser.add_argument("--e5-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--train-documents", type=int, default=32768)
    parser.add_argument("--teacher-k", type=int, default=10)
    parser.add_argument("--block-documents", type=int, default=8192)
    parser.add_argument("--seed", type=int, default=20260905)
    args = parser.parse_args()
    try:
        run(args)
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"materialize-neuroute-ordinal-lattice-input: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
