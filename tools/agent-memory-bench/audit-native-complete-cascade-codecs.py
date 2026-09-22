#!/usr/bin/env python3
"""Audit native predecoded complete-cascade rows against source-bound results."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np

QUERY_COUNT = 152


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def ndcg(ids: list[int], qids: np.ndarray, grades: np.ndarray) -> float:
    rel = {int(doc): float(score) for doc, score in zip(qids, grades)
           if int(doc) >= 0 and float(score) > 0}
    gains = np.asarray([2.0 ** rel.get(int(doc), 0.0) - 1.0
                        for doc in ids[:10]], dtype=np.float64)
    ideal = np.sort(np.asarray(list(rel.values()), dtype=np.float64))[::-1][:10]
    denominator = float(np.sum((2.0 ** ideal - 1.0) /
                               np.log2(np.arange(2, 2 + len(ideal)))))
    return float(np.sum(gains / np.log2(np.arange(2, 2 + len(gains))) /
                       denominator)) if denominator else 0.0


def quantiles(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {"mean_ms": float(np.mean(array)),
            "p50_ms": float(np.quantile(array, .50)),
            "p95_ms": float(np.quantile(array, .95)),
            "p99_ms": float(np.quantile(array, .99)),
            "minimum_ms": float(np.min(array)),
            "maximum_ms": float(np.max(array))}


def source_rows(path: Path, arm: str, ids_field: str) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = [row for row in raw["rows"] if row.get("arm") == arm]
    require(len(rows) == QUERY_COUNT, f"source row cardinality differs: {arm}")
    return [{"top10": [int(x) for x in row[ids_field]],
             "thq": ([int(x) for x in row["thq4_top128_ids"]]
                     if "thq4_top128_ids" in row else None)}
            for row in sorted(rows, key=lambda row: int(row["query"]))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--lsq-result", type=Path, required=True)
    parser.add_argument("--turbo-result", type=Path, required=True)
    parser.add_argument("--bbq-result", type=Path, required=True)
    parser.add_argument("--rslm-result", type=Path, required=True)
    parser.add_argument("--joint-result", type=Path, required=True)
    parser.add_argument("--qrel-ids", type=Path, required=True)
    parser.add_argument("--qrel-scores", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("family") == "thq_native_complete_cascade_predecoded_v2" and
            result.get("status") == "EXECUTED", "native result identity differs")
    qrel_ids = np.asarray(np.memmap(args.qrel_ids, mode="r", dtype="<i8",
                                    shape=(QUERY_COUNT, 20)))
    qrel_scores = np.asarray(np.memmap(args.qrel_scores, mode="r", dtype="<f4",
                                       shape=(QUERY_COUNT, 20)))
    source = {
        "lsq32": source_rows(args.lsq_result, "faiss_lsq32", "top10_ids"),
        "lsq48": source_rows(args.lsq_result, "faiss_lsq48", "top10_ids"),
        "turboquant1": source_rows(args.turbo_result, "turboquant1", "top10_ids"),
        "turboquant2": source_rows(args.turbo_result, "turboquant2", "top10_ids"),
        "elastic_bbq": source_rows(args.bbq_result, "elastic_bbq_1bit_osq", "top10_ids"),
        "rslm3": source_rows(args.rslm_result, "rslm3-faithful", "cosine_top10_ids"),
        "rslm4": source_rows(args.rslm_result, "rslm4-faithful", "cosine_top10_ids"),
        "joint2": source_rows(args.joint_result, "thq-joint2", "top10_ids"),
    }
    # The faithful RSLM result schema intentionally omits the repeated THQ
    # shell.  Bind its shell comparison to the independently persisted LSQ
    # source rows, which use the same canonical filter.
    for name in ("rslm3", "rslm4"):
        for row, reference in zip(source[name], source["lsq32"]):
            row["thq"] = reference["thq"]
    summaries = {}
    checks = []
    for arm in result["rows"]:
        name = str(arm["codec"])
        rows = sorted(arm["top10_rows"], key=lambda row: int(row["query"]))
        require(len(rows) == QUERY_COUNT, f"native row cardinality differs: {name}")
        src = source[name]
        ordered = set_equal = thq_equal = thq_set_equal = 0
        values = []
        pages = []
        thq_pages = []
        for qi, row in enumerate(rows):
            native_top10 = [int(x) for x in row["top10_ids"]]
            native_thq = [int(x) for x in row["thq4_top128_ids"]]
            ordered += int(native_top10 == src[qi]["top10"])
            set_equal += int(set(native_top10) == set(src[qi]["top10"]))
            if src[qi]["thq"] is not None:
                thq_equal += int(native_thq == src[qi]["thq"])
                thq_set_equal += int(set(native_thq) == set(src[qi]["thq"]))
            values.append(ndcg(native_top10, qrel_ids[qi], qrel_scores[qi]))
            pages.append(int(row["codec_pages"]))
            thq_pages.append(int(row["thq_pages"]))
            require(int(row["thq_pages"]) > 0, f"THQ page count differs: {name}")
            require(int(row["logical_payload_bytes"]) > 0, f"payload byte count differs: {name}")
        timing = quantiles([float(row["timing_ms"]["total"]) for row in rows])
        summaries[name] = {
            "mean_qrels_ndcg10": float(np.mean(values)),
            "p05_qrels_ndcg10": float(np.percentile(values, 5)),
            "worst_qrels_ndcg10": float(np.min(values)),
            "ordered_top10_parity": ordered / QUERY_COUNT,
            "set_top10_parity": set_equal / QUERY_COUNT,
            "thq_top128_ordered_parity": (thq_equal / QUERY_COUNT
                                           if src[0]["thq"] is not None else None),
            "thq_top128_set_parity": (thq_set_equal / QUERY_COUNT
                                       if src[0]["thq"] is not None else None),
            "timing_ms": timing,
            "codec_pages_mean": float(np.mean(pages)),
            "codec_pages_max": int(max(pages)),
            "thq_pages_mean": float(np.mean(thq_pages)),
            "thq_pages_max": int(max(thq_pages)),
        }
        checks.extend([f"{name}: 152 native rows", f"{name}: candidate-local THQ page accounting",
                       f"{name}: source top128 and top10 comparison"])
    audit = {
        "schema_version": 2,
        "family": "thq_native_complete_cascade_predecoded_audit_v2",
        "status": "PASS",
        "source_binding": True,
        "native_result_sha256": sha256(args.result),
        "source_result_sha256": {name: sha256(path) for name, path in {
            "lsq": args.lsq_result, "turboquant": args.turbo_result,
            "elastic_bbq": args.bbq_result, "rslm": args.rslm_result}.items()},
        "joint_result_sha256": sha256(args.joint_result),
        "qrel_ids_sha256": sha256(args.qrel_ids),
        "qrel_scores_sha256": sha256(args.qrel_scores),
        "summaries": summaries,
        "checks": checks,
        "limitations": [
            "native THQ scan and cosine rerank over predecoded rows",
            "compressed decode and query-side codec kernels are excluded",
            "candidate-local frozen R4 stream, not full-corpus ANN serving",
            "LSQ source stores query-occurrence codes; native payload uses first deterministic code per document",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print("native complete cascade audit PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"audit-native-complete-cascade-codecs: {error}")
