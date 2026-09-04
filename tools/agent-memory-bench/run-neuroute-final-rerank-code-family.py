#!/usr/bin/env python3
"""Compare code families only on frozen native actual-R4 ADC64 pools."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
import numpy as np

THIS = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS))
from final_rerank_codecs import build_scorers

DIMENSIONS = 384
DOCUMENTS = 1_000_000
QUERIES = 305
SEEDS = (2026082701, 2026082702, 2026082703)


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


actual = load("neuroute_final_family_actual",
              "run-neuroute-actual-r4-final-codec-frontier.py")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def percentile(values: list[float], value: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=np.float64), value))


def load_cases(report_root: Path, requests: list[dict[str, Any]],
               documents: np.memmap, queries: np.memmap, codes: np.memmap,
               query_codes: np.memmap, projections: np.memmap,
               centroids: np.memmap, ranks: np.ndarray,
               document_ids: list[str], relevance: dict[str, dict[str, float]],
               partition: str) -> list[dict[str, Any]]:
    reports = actual.load_reports(report_root, requests, ["int8"])
    result = []
    for (_mode, seed), value in sorted(reports.items()):
        for current, request in zip(value["report"]["samples"][0]["queries"],
                                    requests):
            native = int(request["native_query"])
            adc = actual.adc_pool(current, native, codes, query_codes,
                                  projections, centroids, ranks)
            source = np.asarray(documents[adc], dtype=np.float32)
            query = np.asarray(queries[native], dtype=np.float32)
            exact_scores = source @ query
            exact_top = actual.stable_top(adc, exact_scores, ranks, 10, True)
            query_id = request["query_id"]
            result.append({"partition": partition, "seed": seed,
                "query_id": query_id, "native_query": native, "adc": adc,
                "vectors": source, "query": query, "exact_top": exact_top,
                "exact_ndcg": actual.ndcg(exact_top, query_id, document_ids,
                                           relevance)})
    return result


def summarize(method: Any, rows: list[dict[str, Any]],
              partitions: list[str]) -> dict[str, Any]:
    summaries = []
    for partition in [*partitions, "all"]:
        current = rows if partition == "all" else [row for row in rows
                                                    if row["partition"] == partition]
        losses = [row["exact_ndcg"] - row["ndcg"] for row in current]
        summaries.append({"partition": partition, "queries": len(current),
            "mean_ndcg_at_10": statistics.fmean(row["ndcg"] for row in current),
            "mean_ndcg_loss_vs_fp32": statistics.fmean(losses),
            "maximum_query_ndcg_loss_vs_fp32": max(losses),
            "mean_top10_overlap_vs_fp32": statistics.fmean(
                row["overlap"] for row in current),
            "top10_overlap_p05": percentile([row["overlap"] for row in current], .05),
            "top10_overlap_worst_query": min(row["overlap"] for row in current),
            "top1_agreement_vs_fp32": statistics.fmean(
                row["top1"] for row in current),
            "score_adc64_ms_p95": percentile(
                [row["score_ms"] for row in current], .95)})
    return {"id": method.id,
        "stage": "frozen_native_adc64_to_top10",
        "payload_bytes_per_document": method.payload_bytes_per_document,
        "payload_bytes_per_million_documents":
            method.payload_bytes_per_document * DOCUMENTS,
        "model_bytes": method.model_bytes,
        "timing_scope": "portable_python_codec_score_and_top10_directional",
        "partitions": summaries}


def run(args: argparse.Namespace) -> None:
    config_protocol = json.loads(args.configuration_protocol.read_text(encoding="utf-8"))
    internal_protocol = json.loads(args.internal_protocol.read_text(encoding="utf-8"))
    config_requests = config_protocol["requests"]
    internal_requests = internal_protocol["requests"]
    require(len(config_requests) == len(internal_requests) == 76,
            "final-rerank partition size differs")
    manifest = json.loads(args.input_manifest.read_text(encoding="utf-8"))
    root = args.input_manifest.parent
    documents = np.memmap(root / manifest["document_vectors_file"], mode="r",
                          dtype="<f4", shape=(DOCUMENTS, DIMENSIONS))
    queries = np.memmap(root / manifest["query_vectors_file"], mode="r",
                        dtype="<f4", shape=(QUERIES, DIMENSIONS))
    codes = np.memmap(root / manifest["document_codes_file"], mode="r",
                      dtype=np.uint8, shape=(DOCUMENTS, 32))
    query_codes = np.memmap(root / manifest["query_codes_file"], mode="r",
                            dtype=np.uint8, shape=(QUERIES, 32))
    projections = np.memmap(root / manifest["query_itq_projections_file"],
                            mode="r", dtype="<f4", shape=(QUERIES, 256))
    centroids = np.memmap(root / manifest["binary_adc_centroids_file"], mode="r",
                          dtype="<f4", shape=(256, 2))
    ranks = np.fromfile(Path(internal_protocol["document_id_rank_file"]), dtype="<u4")
    document_ids = actual.read_ids(args.e5_root / "evaluation-document-ids.jsonl")
    relevance = actual.read_qrels(args.e5_root / "evaluation-qrels.tsv")
    cases = load_cases(args.configuration_report_root, config_requests,
        documents, queries, codes, query_codes, projections, centroids, ranks,
        document_ids, relevance, "configuration")
    cases.extend(load_cases(args.internal_report_root, internal_requests,
        documents, queries, codes, query_codes, projections, centroids, ranks,
        document_ids, relevance, "internal_locked_replay"))

    rng = np.random.default_rng(args.seed)
    training_ids = np.sort(rng.choice(DOCUMENTS, size=args.train_documents,
                                      replace=False))
    training = np.asarray(documents[training_ids], dtype=np.float32)
    methods = {value.strip() for value in args.methods.split(",") if value.strip()}
    requested = None if methods == {"all"} else methods
    started = time.perf_counter()
    scorers = build_scorers(training, args.seed, requested)
    total_fit_ms = (time.perf_counter() - started) * 1000.0
    if requested is not None:
        require(len(scorers) == len(methods), "unknown final-rerank method")
    summaries = []
    for scorer in scorers:
        rows = []
        for case in cases:
            # Database encoding is offline. Only stored-payload scoring and
            # deterministic top-10 selection are timed.
            prepared = scorer.prepare(case["vectors"])
            started = time.perf_counter()
            scores = scorer.scores_prepared(prepared, case["query"])
            top = actual.stable_top(case["adc"], scores, ranks, 10, True)
            elapsed = (time.perf_counter() - started) * 1000.0
            rows.append({"partition": case["partition"],
                "exact_ndcg": case["exact_ndcg"],
                "ndcg": actual.ndcg(top, case["query_id"], document_ids, relevance),
                "overlap": len(set(map(int, top)) & set(map(int, case["exact_top"]))) / 10.0,
                "top1": float(top[0] == case["exact_top"][0]),
                "score_ms": elapsed})
        summaries.append(summarize(scorer, rows,
                                   ["configuration", "internal_locked_replay"]))
    result = {"schema_version": 1,
        "family": "neuroute_final_rerank_code_family_result",
        "claim_scope": "isolated_frozen_native_adc64_to_top10",
        "queries": len(cases), "seeds": list(SEEDS),
        "routing_storage_mode": "int8",
        "training": {"documents": args.train_documents, "seed": args.seed,
            "labels_used": False, "shared_total_fit_ms": total_fit_ms},
        "inputs": {"input_manifest_sha256": sha256(args.input_manifest),
            "configuration_protocol_sha256": sha256(args.configuration_protocol),
            "internal_protocol_sha256": sha256(args.internal_protocol)},
        "methods": summaries,
        "limitations": [
            "configuration and internal queries were opened by earlier studies",
            "Python p95 is directional and is not a native SIMD serving claim",
            "scalar sub-byte and PQ4 p95 excludes physical bit-unpack cost",
            "this isolates final ADC64-to-top10 and does not replace an earlier-stage cascade bake-off"],
        "production_licensed": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--configuration-report-root", type=Path, required=True)
    parser.add_argument("--internal-report-root", type=Path, required=True)
    parser.add_argument("--configuration-protocol", type=Path, required=True)
    parser.add_argument("--internal-protocol", type=Path, required=True)
    parser.add_argument("--input-manifest", type=Path, required=True)
    parser.add_argument("--e5-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--train-documents", type=int, default=16384)
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--methods", default="all")
    args = parser.parse_args()
    try:
        require(1024 <= args.train_documents <= DOCUMENTS,
                "train-documents must be in [1024, 1000000]")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"run-neuroute-final-rerank-code-family: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
