#!/usr/bin/env python3
"""Describe the tail of document-cascade rerank losses.

This is a diagnostic companion to the code-family runner.  It keeps the
frozen native ADC64 pools and qrels fixed, then reports quantiles, loss gates,
and the individual worst queries for the scalar final-rerank methods.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
import numpy as np

THIS = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS))
from final_rerank_codecs import build_scorers


def load_module(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


family = load_module("neuroute_final_rerank_family_tail",
                    "run-neuroute-final-rerank-code-family.py")
actual = family.actual


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: list[float], q: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=np.float64), q))


def grades(top: np.ndarray, query_id: str, document_ids: list[str],
           relevance: dict[str, dict[str, float]]) -> list[float]:
    qrels = relevance.get(query_id, {})
    return [float(qrels.get(document_ids[int(position)], 0.0)) for position in top]


def summarize(rows: list[dict[str, Any]], partition: str) -> dict[str, Any]:
    current = rows if partition == "all" else [r for r in rows
                                                if r["partition"] == partition]
    losses = [r["loss"] for r in current]
    overlaps = [r["overlap"] for r in current]
    return {
        "partition": partition,
        "queries": len(current),
        "mean_ndcg": statistics.fmean(r["ndcg"] for r in current),
        "mean_ndcg_loss_vs_fp32": statistics.fmean(losses),
        "ndcg_loss_p50": percentile(losses, .50),
        "ndcg_loss_p95": percentile(losses, .95),
        "ndcg_loss_p99": percentile(losses, .99),
        "maximum_ndcg_loss": max(losses),
        "fraction_loss_gt_0_01": statistics.fmean(x > .01 for x in losses),
        "fraction_loss_gt_0_02": statistics.fmean(x > .02 for x in losses),
        "fraction_loss_gt_0_05": statistics.fmean(x > .05 for x in losses),
        "positive_loss_queries": sum(x > 0.0 for x in losses),
        "mean_top10_overlap_vs_fp32": statistics.fmean(overlaps),
        "overlap_p05": percentile(overlaps, .05),
        "overlap_worst_query": min(overlaps),
        "top1_agreement": statistics.fmean(r["top1"] for r in current),
    }


def run(args: argparse.Namespace) -> None:
    config_protocol = json.loads(args.configuration_protocol.read_text(encoding="utf-8"))
    internal_protocol = json.loads(args.internal_protocol.read_text(encoding="utf-8"))
    config_requests = config_protocol["requests"]
    internal_requests = internal_protocol["requests"]
    manifest = json.loads(args.input_manifest.read_text(encoding="utf-8"))
    root = args.input_manifest.parent
    documents = np.memmap(root / manifest["document_vectors_file"], mode="r",
                          dtype="<f4", shape=(family.DOCUMENTS, family.DIMENSIONS))
    queries = np.memmap(root / manifest["query_vectors_file"], mode="r",
                        dtype="<f4", shape=(family.QUERIES, family.DIMENSIONS))
    codes = np.memmap(root / manifest["document_codes_file"], mode="r",
                      dtype=np.uint8, shape=(family.DOCUMENTS, 32))
    query_codes = np.memmap(root / manifest["query_codes_file"], mode="r",
                            dtype=np.uint8, shape=(family.QUERIES, 32))
    projections = np.memmap(root / manifest["query_itq_projections_file"],
                            mode="r", dtype="<f4", shape=(family.QUERIES, 256))
    centroids = np.memmap(root / manifest["binary_adc_centroids_file"], mode="r",
                          dtype="<f4", shape=(256, 2))
    ranks = np.fromfile(Path(internal_protocol["document_id_rank_file"]), dtype="<u4")
    document_ids = actual.read_ids(args.e5_root / "evaluation-document-ids.jsonl")
    relevance = actual.read_qrels(args.e5_root / "evaluation-qrels.tsv")
    cases = family.load_cases(args.configuration_report_root, config_requests,
        documents, queries, codes, query_codes, projections, centroids, ranks,
        document_ids, relevance, "configuration")
    cases.extend(family.load_cases(args.internal_report_root, internal_requests,
        documents, queries, codes, query_codes, projections, centroids, ranks,
        document_ids, relevance, "internal_locked_replay"))
    rng = np.random.default_rng(args.seed)
    training_ids = np.sort(rng.choice(family.DOCUMENTS, size=args.train_documents,
                                      replace=False))
    training = np.asarray(documents[training_ids], dtype=np.float32)
    requested = {x.strip() for x in args.methods.split(",") if x.strip()}
    scorers = build_scorers(training, args.seed, requested)
    result_methods = []
    for scorer in scorers:
        rows = []
        for case in cases:
            prepared = scorer.prepare(case["vectors"])
            scores = scorer.scores_prepared(prepared, case["query"])
            top = actual.stable_top(case["adc"], scores, ranks, 10, True)
            exact = case["exact_top"]
            exact_ndcg = case["exact_ndcg"]
            ndcg = actual.ndcg(top, case["query_id"], document_ids, relevance)
            row = {
                "partition": case["partition"], "seed": case["seed"],
                "query_id": case["query_id"], "native_query": case["native_query"],
                "loss": float(exact_ndcg - ndcg), "ndcg": float(ndcg),
                "overlap": len(set(map(int, top)) & set(map(int, exact))) / 10.0,
                "top1": float(top[0] == exact[0]),
                "fp32_grades": grades(exact, case["query_id"], document_ids, relevance),
                "method_grades": grades(top, case["query_id"], document_ids, relevance),
                "fp32_top10": [int(x) for x in exact],
                "method_top10": [int(x) for x in top],
            }
            row["fp32_relevant_count"] = sum(x > 0 for x in row["fp32_grades"])
            row["method_relevant_count"] = sum(x > 0 for x in row["method_grades"])
            rows.append(row)
        ordered = sorted(rows, key=lambda r: (-r["loss"], r["query_id"], r["seed"]))
        result_methods.append({
            "id": scorer.id,
            "payload_bytes_per_document": scorer.payload_bytes_per_document,
            "model_bytes": scorer.model_bytes,
            "partitions": [summarize(rows, p) for p in
                            ("configuration", "internal_locked_replay", "all")],
            "worst_queries": ordered[:args.worst_queries],
        })
    output = {
        "schema_version": 1,
        "family": "neuroute_document_cascade_tail_anatomy",
        "claim_scope": "frozen_native_adc64_to_top10_qrels_tail_diagnostic",
        "queries": len(cases),
        "training": {"documents": args.train_documents, "seed": args.seed,
                     "labels_used": False},
        "inputs": {"input_manifest_sha256": sha256(args.input_manifest),
                   "configuration_protocol_sha256": sha256(args.configuration_protocol),
                   "internal_protocol_sha256": sha256(args.internal_protocol)},
        "methods": result_methods,
        "limitations": ["native ADC64 pools and query partitions are inherited frozen inputs",
                        "p95/p99 are portable Python scorer diagnostics, not native latency",
                        "qrels anatomy identifies tail queries but does not establish corpus-wide quality"],
        "production_licensed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(output))


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("configuration-report-root", "internal-report-root",
                 "configuration-protocol", "internal-protocol", "input-manifest",
                 "e5-root", "output"):
        parser.add_argument("--" + name, dest=name.replace("-", "_"), type=Path,
                            required=True)
    parser.add_argument("--train-documents", type=int, default=16384)
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--methods", default="int8_linear,int10_linear,int12_linear,int12_power05")
    parser.add_argument("--worst-queries", type=int, default=10)
    args = parser.parse_args()
    try:
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"analyze-neuroute-document-cascade-tail: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
