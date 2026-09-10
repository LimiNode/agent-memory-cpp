#!/usr/bin/env python3
"""Measure absolute THQ distance, cutoff shells, and ordinal deltas."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--codes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--train-documents", type=int, default=100000)
    parser.add_argument("--chunk", type=int, default=20000)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    refs = manifest["references"]
    documents = int(manifest["documents"])
    queries = int(manifest["queries"])
    dimension = int(manifest["dimension"])
    docs = np.memmap(refs["document_vectors"]["path"], mode="r", dtype="<f4",
                     shape=(documents, dimension))
    query_vectors = np.memmap(refs["queries"]["path"], mode="r", dtype="<f4",
                              shape=(queries, dimension))
    teachers = np.memmap(refs["teacher_ids"]["path"], mode="r", dtype="<i8",
                         shape=(queries, 10))
    codes = np.memmap(args.codes, mode="r", dtype=np.uint8,
                      shape=(documents, 144))

    thresholds = np.quantile(np.asarray(docs[:args.train_documents]),
                             [0.25, 0.5, 0.75], axis=0).T.astype(np.float32)
    query_codes = np.packbits(
        (np.asarray(query_vectors)[:, :, None] > thresholds[None, :, :]).reshape(
            queries, -1), axis=1, bitorder="little")
    budgets = (256, 1000, 5000)
    teacher_distances: list[int] = []
    cutoff_rows = {str(k): [] for k in budgets}
    shell_rows = {str(k): [] for k in budgets}
    delta_histogram = np.zeros(4, dtype=np.int64)
    rows = []
    ids = np.arange(documents, dtype=np.int64)
    for query_index, query_code in enumerate(query_codes):
        distances = np.empty(documents, dtype=np.uint16)
        for first in range(0, documents, args.chunk):
            stop = min(documents, first + args.chunk)
            distances[first:stop] = np.unpackbits(
                np.bitwise_xor(codes[first:stop], query_code), axis=1,
                bitorder="little").sum(axis=1, dtype=np.uint16)
        targets = teachers[query_index]
        target_distances = distances[targets]
        teacher_distances.extend(int(value) for value in target_distances)
        query_row = {"query": query_index,
                     "teacher_distance_min": int(target_distances.min()),
                     "teacher_distance_p50": float(np.quantile(target_distances, .5)),
                     "teacher_distance_p95": float(np.quantile(target_distances, .95)),
                     "teacher_distance_max": int(target_distances.max())}
        for budget in budgets:
            cutoff = int(np.partition(distances, budget - 1)[budget - 1])
            less = int(np.count_nonzero(distances < cutoff))
            equal = int(np.count_nonzero(distances == cutoff))
            cutoff_rows[str(budget)].append(cutoff)
            shell_rows[str(budget)].append({"less": less, "equal": equal,
                                             "less_equal": less + equal})
            query_row[f"cutoff_{budget}"] = cutoff
            query_row[f"shell_{budget}"] = equal
        # Valid thermometer codes make this exactly the ordinal-level delta.
        query_levels = np.unpackbits(query_code, bitorder="little")[:dimension * 3]
        query_levels = query_levels.reshape(dimension, 3).sum(axis=1).astype(np.int16)
        target_bits = np.unpackbits(codes[targets], axis=1, bitorder="little")[:, :dimension * 3]
        target_levels = target_bits.reshape(len(targets), dimension, 3).sum(axis=2).astype(np.int16)
        delta_histogram += np.bincount(
            np.abs(target_levels - query_levels[None, :]).reshape(-1),
            minlength=4)[:4]
        rows.append(query_row)

    def summary(values: list[float]) -> dict[str, float]:
        array = np.asarray(values, dtype=np.float64)
        return {"p50": float(np.quantile(array, .5)),
                "p90": float(np.quantile(array, .9)),
                "p95": float(np.quantile(array, .95)),
                "p99": float(np.quantile(array, .99)),
                "max": float(array.max())}

    result = {
        "schema_version": 1,
        "family": "thq_absolute_geometry_v1",
        "representation": {"name": "THQ4-384", "levels": 4,
                            "bits": 1152, "bytes_per_document": 144,
                            "ranking_metric": "plain_hamming",
                            "tie_policy": "distance_ascending_then_document_id_ascending"},
        "documents": documents, "queries": queries,
        "teacher_distance": summary(teacher_distances),
        "cutoff_distance": {key: summary([float(v) for v in values])
                             for key, values in cutoff_rows.items()},
        "tie_shell": {key: {metric: summary([float(row[metric]) for row in rows_])
                             for metric in ("less", "equal", "less_equal")}
                       for key, rows_ in shell_rows.items()},
        "teacher_ordinal_delta_histogram": {str(index): int(value)
                                             for index, value in enumerate(delta_histogram)},
        "per_query": rows,
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
