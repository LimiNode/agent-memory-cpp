#!/usr/bin/env python3
"""Compare codec families on both document-selection stages of actual R4.

The runner reconstructs the authoritative native candidate and Hamming pools,
scores every candidate document once per codec, and evaluates three distinct
contracts:

* candidate documents -> 512/768/1024;
* frozen historical Hamming768 -> 32/64/128;
* configured end-to-end profiles -> top10.

Database encoding is offline.  Python timings are directional and include
portable scoring plus deterministic selection, not native packed/SIMD kernels.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
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
STAGE1_COUNTS = (512, 768, 1024)
STAGE2_COUNTS = (32, 64, 128)
POPCOUNT = np.asarray([int(value).bit_count() for value in range(256)],
                      dtype=np.uint8)
ALL_METHOD_IDS = (
    "fp32", "fp16",
    *(f"int{bits}_{suffix}" for bits in (4, 5, 6, 8, 10, 12)
      for suffix in ("linear", "power05")),
    *(name for bits in (128, 208, 256, 384)
      for name in (f"itq{bits}_hamming", f"itq{bits}_adc",
                   f"rabitq{bits}", f"bbq{bits}_fp16_scales")),
    "itq_ternary128_adc", "itq_quaternary104_adc",
    "pq4", "pq8", "opq4", "opq8")


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


actual = load("neuroute_document_cascade_actual",
              "run-neuroute-actual-r4-final-codec-frontier.py")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def u32_sha256(values: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(values, dtype="<u4").tobytes()).hexdigest()


def percentile(values: list[float], quantile: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=np.float64), quantile))


def positions_top(ids: np.ndarray, scores: np.ndarray, ranks: np.ndarray,
                  count: int) -> np.ndarray:
    order = np.lexsort((ranks[ids], -np.asarray(scores, dtype=np.float32)))
    return order[:min(count, len(order))].astype(np.int32, copy=False)


def subset_prepared(prepared: Any, positions: np.ndarray) -> Any:
    if isinstance(prepared, tuple):
        return tuple(value[positions] for value in prepared)
    return prepared[positions]


def load_cases(report_root: Path, requests: list[dict[str, Any]],
               documents: np.memmap, queries: np.memmap, codes: np.memmap,
               query_codes: np.memmap, ranks: np.ndarray,
               document_ids: list[str], relevance: dict[str, dict[str, float]],
               partition: str) -> list[dict[str, Any]]:
    reports = actual.load_reports(report_root, requests, ["int8"])
    cases: list[dict[str, Any]] = []
    for (_mode, seed), value in sorted(reports.items()):
        rows = value["report"]["samples"][0]["queries"]
        for current, request in zip(rows, requests):
            native = int(request["native_query"])
            candidate = np.asarray(current["candidate_documents"],
                                   dtype=np.int32)
            require(u32_sha256(candidate) == current["candidate_sha256"],
                    "native candidate pool hash differs")
            distances = POPCOUNT[np.bitwise_xor(codes[candidate],
                                                query_codes[native])].sum(
                                                    axis=1, dtype=np.uint16)
            hamming_positions = np.lexsort((ranks[candidate], distances))[
                :min(768, len(candidate))].astype(np.int32, copy=False)
            hamming = candidate[hamming_positions]
            require(u32_sha256(hamming) == current["hamming_sha256"],
                    "native Hamming768 reconstruction differs")
            vectors = np.asarray(documents[candidate], dtype=np.float32)
            query = np.asarray(queries[native], dtype=np.float32)
            exact_scores = np.asarray(vectors @ query, dtype=np.float32)
            oracle_positions = positions_top(candidate, exact_scores, ranks, 10)
            oracle = candidate[oracle_positions]
            query_id = request["query_id"]
            cases.append({"partition": partition, "seed": int(seed),
                "query_id": query_id, "native_query": native,
                "candidate": candidate, "vectors": vectors, "query": query,
                "exact_scores": exact_scores,
                "candidate_oracle": oracle,
                "candidate_oracle_ndcg": actual.ndcg(
                    oracle, query_id, document_ids, relevance),
                "hamming_positions": hamming_positions,
                "historical_final": np.asarray(current["exact_documents"],
                                               dtype=np.int32)})
    return cases


def metric_row(case: dict[str, Any], selected_positions: np.ndarray,
               reference_positions: np.ndarray, input_oracle: np.ndarray,
               input_oracle_ndcg: float, document_ids: list[str],
               relevance: dict[str, dict[str, float]]) -> dict[str, Any]:
    candidate = case["candidate"]
    selected = candidate[selected_positions]
    reference = candidate[reference_positions]
    exact_local = case["exact_scores"][selected_positions]
    final_positions = positions_top(selected, exact_local, RANKS, 10)
    final = selected[final_positions]
    ndcg = actual.ndcg(final, case["query_id"], document_ids, relevance)
    candidate_oracle = case["candidate_oracle"]
    return {"partition": case["partition"], "seed": case["seed"],
        "stage_overlap": len(set(map(int, selected)) &
                             set(map(int, reference))) / len(reference),
        "input_oracle_overlap": len(set(map(int, final)) &
                                    set(map(int, input_oracle))) / 10.0,
        "candidate_oracle_overlap": len(set(map(int, final)) &
            set(map(int, candidate_oracle))) / 10.0,
        "candidate_oracle_top1": float(final[0] == candidate_oracle[0]),
        "ndcg": ndcg,
        "ndcg_loss_vs_input_fp32": input_oracle_ndcg - ndcg,
        "ndcg_loss_vs_candidate_fp32":
            case["candidate_oracle_ndcg"] - ndcg}


def summarize(rows: list[dict[str, Any]], partition: str) -> dict[str, Any]:
    current = rows if partition == "all" else [row for row in rows
                                                if row["partition"] == partition]
    return {"partition": partition, "cases": len(current),
        "mean_stage_overlap_vs_fp32": statistics.fmean(
            row["stage_overlap"] for row in current),
        "stage_overlap_p05": percentile(
            [row["stage_overlap"] for row in current], .05),
        "stage_overlap_worst_query": min(
            row["stage_overlap"] for row in current),
        "mean_final_top10_overlap_vs_input_fp32": statistics.fmean(
            row["input_oracle_overlap"] for row in current),
        "mean_final_top10_overlap_vs_candidate_fp32": statistics.fmean(
            row["candidate_oracle_overlap"] for row in current),
        "final_top10_overlap_p05_vs_candidate_fp32": percentile(
            [row["candidate_oracle_overlap"] for row in current], .05),
        "final_top10_overlap_worst_query_vs_candidate_fp32": min(
            row["candidate_oracle_overlap"] for row in current),
        "top1_agreement_vs_candidate_fp32": statistics.fmean(
            row["candidate_oracle_top1"] for row in current),
        "mean_ndcg_at_10": statistics.fmean(row["ndcg"] for row in current),
        "mean_ndcg_loss_vs_input_fp32": statistics.fmean(
            row["ndcg_loss_vs_input_fp32"] for row in current),
        "mean_ndcg_loss_vs_candidate_fp32": statistics.fmean(
            row["ndcg_loss_vs_candidate_fp32"] for row in current),
        "maximum_query_ndcg_loss_vs_candidate_fp32": max(
            row["ndcg_loss_vs_candidate_fp32"] for row in current)}


def partition_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [summarize(rows, partition) for partition in
            ("configuration", "internal_locked_replay", "all")]


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and
            value.get("family") == "neuroute_document_cascade_code_family",
            "document cascade contract identity differs")
    require(tuple(value["stage1_counts"]) == STAGE1_COUNTS and
            tuple(value["stage2_counts"]) == STAGE2_COUNTS,
            "document cascade stage counts differ")
    return value


def score_cache_signature(cases: list[dict[str, Any]], seed: int,
                          train_documents: int) -> str:
    payload = {"seed": seed, "train_documents": train_documents,
        "cases": [{"partition": case["partition"], "seed": case["seed"],
                   "native_query": case["native_query"],
                   "candidate_sha256": u32_sha256(case["candidate"])}
                  for case in cases]}
    return hashlib.sha256(canonical(payload)).hexdigest()


def materialize_scores(cases: list[dict[str, Any]], training: np.ndarray,
                       methods: set[str] | None, seed: int, cache_dir: Path,
                       signature: str) -> tuple[dict[str, np.ndarray],
                                                dict[str, dict[str, Any]]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = cache_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) \
        if metadata_path.exists() else {"signature": signature, "methods": {}}
    require(metadata.get("signature") == signature,
            "score cache belongs to a different case matrix")
    desired = set(ALL_METHOD_IDS) if methods is None else methods
    require(desired <= set(ALL_METHOD_IDS), "unknown document-cascade method")
    offsets = np.cumsum([0, *[len(case["candidate"]) for case in cases]])
    arrays: dict[str, np.ndarray] = {}
    missing: set[str] = set()
    for method in ALL_METHOD_IDS:
        if method not in desired:
            continue
        path = cache_dir / f"{method}.npy"
        if method in metadata["methods"] and path.exists():
            values = np.load(path, mmap_mode="r")
            require(len(values) == int(offsets[-1]),
                    "cached score vector length differs")
            arrays[method] = values
            print(f"{method}: cache hit", flush=True)
        else:
            missing.add(method)
    if not missing:
        return arrays, {method: metadata["methods"][method]
                        for method in ALL_METHOD_IDS if method in desired}
    started = time.perf_counter()
    scorers = build_scorers(training, seed, missing)
    fit_ms = (time.perf_counter() - started) * 1000.0
    require({scorer.id for scorer in scorers} == missing,
            "document-cascade scorer construction differs")
    for index, scorer in enumerate(scorers, start=1):
        path = cache_dir / f"{scorer.id}.npy"
        values = np.lib.format.open_memmap(path, mode="w+", dtype="<f4",
                                           shape=(int(offsets[-1]),))
        encode_ms: list[float] = []
        stage1_ms: list[float] = []
        stage2_ms: list[float] = []
        for case_index, case in enumerate(cases):
            begin = time.perf_counter()
            prepared = scorer.prepare(case["vectors"])
            encode_ms.append((time.perf_counter() - begin) * 1000.0)
            begin = time.perf_counter()
            scores = scorer.scores_prepared(prepared, case["query"])
            for count in STAGE1_COUNTS:
                positions_top(case["candidate"], scores, RANKS, count)
            stage1_ms.append((time.perf_counter() - begin) * 1000.0)
            hamming_positions = case["hamming_positions"]
            begin = time.perf_counter()
            hamming_scores = scorer.scores_prepared(
                subset_prepared(prepared, hamming_positions), case["query"])
            hamming_ids = case["candidate"][hamming_positions]
            for count in STAGE2_COUNTS:
                positions_top(hamming_ids, hamming_scores, RANKS, count)
            stage2_ms.append((time.perf_counter() - begin) * 1000.0)
            values[offsets[case_index]:offsets[case_index + 1]] = scores
        values.flush()
        method_metadata = {"payload_bytes_per_document":
                scorer.payload_bytes_per_document,
            "payload_bytes_per_million_documents":
                scorer.payload_bytes_per_document * DOCUMENTS,
            "model_bytes": scorer.model_bytes,
            "offline_encode_candidate_pool_ms_p95": percentile(encode_ms, .95),
            "stage1_score_and_three_selections_ms_p95":
                percentile(stage1_ms, .95),
            "stage2_score_and_three_selections_ms_p95":
                percentile(stage2_ms, .95),
            "shared_fit_ms_all_requested_methods": fit_ms,
            "timing_scope": "portable_python_directional"}
        metadata["methods"][scorer.id] = method_metadata
        metadata_path.write_bytes(canonical(metadata))
        arrays[scorer.id] = values
        print(f"[{index}/{len(scorers)}] {scorer.id}: materialized",
              flush=True)
    return arrays, {method: metadata["methods"][method]
                    for method in ALL_METHOD_IDS if method in desired}


def slices(cases: list[dict[str, Any]]) -> list[slice]:
    offsets = np.cumsum([0, *[len(case["candidate"]) for case in cases]])
    return [slice(int(offsets[index]), int(offsets[index + 1]))
            for index in range(len(cases))]


def evaluate_stage1(method: str, scores: np.ndarray,
                    cases: list[dict[str, Any]], score_slices: list[slice],
                    document_ids: list[str],
                    relevance: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    results = []
    for count in STAGE1_COUNTS:
        rows = []
        for case, current_slice in zip(cases, score_slices):
            current = np.asarray(scores[current_slice], dtype=np.float32)
            selected = positions_top(case["candidate"], current, RANKS, count)
            reference = positions_top(case["candidate"], case["exact_scores"],
                                      RANKS, count)
            rows.append(metric_row(case, selected, reference,
                case["candidate_oracle"], case["candidate_oracle_ndcg"],
                document_ids, relevance))
        results.append({"method": method, "output_documents": count,
                        "partitions": partition_summaries(rows)})
    return results


def evaluate_fixed_stage2(method: str, scores: np.ndarray,
                          cases: list[dict[str, Any]], score_slices: list[slice],
                          document_ids: list[str],
                          relevance: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    results = []
    for count in STAGE2_COUNTS:
        rows = []
        for case, current_slice in zip(cases, score_slices):
            hamming_positions = case["hamming_positions"]
            ids = case["candidate"][hamming_positions]
            exact = case["exact_scores"][hamming_positions]
            pool_top = positions_top(ids, exact, RANKS, 10)
            pool_oracle = ids[pool_top]
            pool_ndcg = actual.ndcg(pool_oracle, case["query_id"],
                                    document_ids, relevance)
            current = np.asarray(scores[current_slice],
                                 dtype=np.float32)[hamming_positions]
            selected_local = positions_top(ids, current, RANKS, count)
            reference_local = positions_top(ids, exact, RANKS, count)
            rows.append(metric_row(case, hamming_positions[selected_local],
                hamming_positions[reference_local], pool_oracle, pool_ndcg,
                document_ids, relevance))
        results.append({"method": method, "input": "historical_hamming768",
            "output_documents": count, "partitions": partition_summaries(rows)})
    return results


def evaluate_profile(profile: dict[str, Any], arrays: dict[str, np.ndarray],
                     cases: list[dict[str, Any]], score_slices: list[slice],
                     document_ids: list[str],
                     relevance: dict[str, dict[str, float]],
                     method_metadata: dict[str, dict[str, Any]]) -> dict[str, Any]:
    stage1_method = profile["stage1_method"]
    stage2_method = profile["stage2_method"]
    final_method = profile["final_method"]
    for method in (stage1_method, stage2_method):
        require(method in arrays, f"profile method {method} has no score cache")
    require(final_method == "exact_fp32" or final_method in arrays,
            f"profile final method {final_method} has no score cache")
    stage1_count = int(profile["stage1_documents"])
    stage2_count = int(profile["stage2_documents"])
    require(stage1_count in STAGE1_COUNTS and stage2_count in STAGE2_COUNTS,
            "profile stage count is outside the measured grid")
    rows = []
    for case, current_slice in zip(cases, score_slices):
        candidate = case["candidate"]
        first_scores = np.asarray(arrays[stage1_method][current_slice],
                                  dtype=np.float32)
        first = positions_top(candidate, first_scores, RANKS, stage1_count)
        second_scores = np.asarray(arrays[stage2_method][current_slice],
                                   dtype=np.float32)[first]
        second_local = positions_top(candidate[first], second_scores, RANKS,
                                     stage2_count)
        second = first[second_local]
        if final_method == "exact_fp32":
            final_scores = case["exact_scores"][second]
        else:
            final_scores = np.asarray(arrays[final_method][current_slice],
                                      dtype=np.float32)[second]
        final_local = positions_top(candidate[second], final_scores, RANKS, 10)
        final = candidate[second[final_local]]
        ndcg = actual.ndcg(final, case["query_id"], document_ids, relevance)
        oracle = case["candidate_oracle"]
        rows.append({"partition": case["partition"], "seed": case["seed"],
            "overlap": len(set(map(int, final)) & set(map(int, oracle))) / 10.0,
            "top1": float(final[0] == oracle[0]), "ndcg": ndcg,
            "loss": case["candidate_oracle_ndcg"] - ndcg})
    summaries = []
    for partition in ("configuration", "internal_locked_replay", "all"):
        current = rows if partition == "all" else [row for row in rows
                                                    if row["partition"] == partition]
        summaries.append({"partition": partition, "cases": len(current),
            "mean_final_top10_overlap_vs_candidate_fp32": statistics.fmean(
                row["overlap"] for row in current),
            "final_top10_overlap_p05_vs_candidate_fp32": percentile(
                [row["overlap"] for row in current], .05),
            "final_top10_overlap_worst_query_vs_candidate_fp32": min(
                row["overlap"] for row in current),
            "top1_agreement_vs_candidate_fp32": statistics.fmean(
                row["top1"] for row in current),
            "mean_ndcg_at_10": statistics.fmean(row["ndcg"] for row in current),
            "mean_ndcg_loss_vs_candidate_fp32": statistics.fmean(
                row["loss"] for row in current),
            "maximum_query_ndcg_loss_vs_candidate_fp32": max(
                row["loss"] for row in current)})
    auxiliary_methods = [stage1_method, stage2_method]
    final_payload = 1536 if final_method == "exact_fp32" else \
        method_metadata[final_method]["payload_bytes_per_document"]
    if final_method != "exact_fp32":
        auxiliary_methods.append(final_method)
    unique_auxiliary = list(dict.fromkeys(auxiliary_methods))
    resident_methods = [*unique_auxiliary]
    if final_method == "exact_fp32" and "fp32" not in resident_methods:
        resident_methods.append("fp32")
    mean_candidates = statistics.fmean(len(case["candidate"])
                                       for case in cases)
    logical_bytes = (mean_candidates *
        method_metadata[stage1_method]["payload_bytes_per_document"] +
        stage1_count *
        method_metadata[stage2_method]["payload_bytes_per_document"] +
        stage2_count * final_payload)
    return {**profile,
        "cost": {"mean_logical_payload_bytes_read_per_request": logical_bytes,
            "auxiliary_codec_bytes_per_document": sum(
                method_metadata[method]["payload_bytes_per_document"]
                for method in unique_auxiliary),
            "materialized_payload_bytes_per_document": sum(
                method_metadata[method]["payload_bytes_per_document"]
                for method in resident_methods),
            "materialized_payload_bytes_per_million_documents": sum(
                method_metadata[method]["payload_bytes_per_million_documents"]
                for method in resident_methods),
            "model_bytes": sum(method_metadata[method]["model_bytes"]
                               for method in resident_methods),
            "requires_fp32_source_vectors": final_method == "exact_fp32",
            "stage1_python_p95_ms": method_metadata[stage1_method][
                "stage1_score_and_three_selections_ms_p95"],
            "stage2_python_p95_ms": method_metadata[stage2_method][
                "stage2_score_and_three_selections_ms_p95"],
            "final_python_p95_ms": None,
            "timing_note": "stage p95 values include all three measured K selections; final p95 is supplied by the isolated final-rerank study"},
        "partitions": summaries}


def historical_baseline(cases: list[dict[str, Any]], document_ids: list[str],
                        relevance: dict[str, dict[str, float]]) -> dict[str, Any]:
    rows = []
    for case in cases:
        final = case["historical_final"]
        ndcg = actual.ndcg(final, case["query_id"], document_ids, relevance)
        oracle = case["candidate_oracle"]
        rows.append({"partition": case["partition"],
            "overlap": len(set(map(int, final)) & set(map(int, oracle))) / 10.0,
            "top1": float(final[0] == oracle[0]), "ndcg": ndcg,
            "loss": case["candidate_oracle_ndcg"] - ndcg})
    summaries = []
    for partition in ("configuration", "internal_locked_replay", "all"):
        current = rows if partition == "all" else [row for row in rows
                                                    if row["partition"] == partition]
        summaries.append({"partition": partition, "cases": len(current),
            "mean_final_top10_overlap_vs_candidate_fp32": statistics.fmean(
                row["overlap"] for row in current),
            "final_top10_overlap_p05_vs_candidate_fp32": percentile(
                [row["overlap"] for row in current], .05),
            "final_top10_overlap_worst_query_vs_candidate_fp32": min(
                row["overlap"] for row in current),
            "top1_agreement_vs_candidate_fp32": statistics.fmean(
                row["top1"] for row in current),
            "mean_ndcg_at_10": statistics.fmean(row["ndcg"] for row in current),
            "mean_ndcg_loss_vs_candidate_fp32": statistics.fmean(
                row["loss"] for row in current),
            "maximum_query_ndcg_loss_vs_candidate_fp32": max(
                row["loss"] for row in current)})
    return {"id": "historical_hamming768_adc64_exact10",
            "partitions": summaries}


def run(args: argparse.Namespace) -> None:
    global RANKS
    contract = load_contract(args.contract)
    config_protocol = json.loads(args.configuration_protocol.read_text(
        encoding="utf-8"))
    internal_protocol = json.loads(args.internal_protocol.read_text(
        encoding="utf-8"))
    config_requests = config_protocol["requests"]
    internal_requests = internal_protocol["requests"]
    require(len(config_requests) == len(internal_requests) == 76,
            "document cascade partition size differs")
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
    RANKS = np.fromfile(Path(internal_protocol["document_id_rank_file"]),
                        dtype="<u4")
    document_ids = actual.read_ids(args.e5_root /
                                   "evaluation-document-ids.jsonl")
    relevance = actual.read_qrels(args.e5_root / "evaluation-qrels.tsv")
    cases = load_cases(args.configuration_report_root, config_requests,
        documents, queries, codes, query_codes, RANKS, document_ids, relevance,
        "configuration")
    cases.extend(load_cases(args.internal_report_root, internal_requests,
        documents, queries, codes, query_codes, RANKS, document_ids, relevance,
        "internal_locked_replay"))
    rng = np.random.default_rng(args.seed)
    training_ids = np.sort(rng.choice(DOCUMENTS, size=args.train_documents,
                                      replace=False))
    training = np.asarray(documents[training_ids], dtype=np.float32)
    method_names = {value.strip() for value in args.methods.split(",")
                    if value.strip()}
    requested = None if method_names == {"all"} else method_names
    signature = score_cache_signature(cases, args.seed, args.train_documents)
    arrays, method_metadata = materialize_scores(cases, training, requested,
        args.seed, args.score_cache, signature)
    score_slices = slices(cases)
    stage1 = []
    stage2 = []
    for method, scores in arrays.items():
        stage1.extend(evaluate_stage1(method, scores, cases, score_slices,
                                     document_ids, relevance))
        stage2.extend(evaluate_fixed_stage2(method, scores, cases, score_slices,
                                            document_ids, relevance))
    profiles = [evaluate_profile(profile, arrays, cases, score_slices,
                                 document_ids, relevance, method_metadata)
                for profile in contract.get("profiles", [])]
    result = {"schema_version": 1,
        "family": "neuroute_document_cascade_code_family_result",
        "claim_scope": "actual_r4_document_selection_and_composed_profiles",
        "cases": len(cases), "seeds": list(SEEDS),
        "candidate_documents": {"mean": statistics.fmean(
            len(case["candidate"]) for case in cases),
            "minimum": min(len(case["candidate"]) for case in cases),
            "maximum": max(len(case["candidate"]) for case in cases)},
        "training": {"documents": args.train_documents, "seed": args.seed,
                     "labels_used": False},
        "inputs": {"contract_sha256": sha256(args.contract),
            "score_cache_signature": signature,
            "input_manifest_sha256": sha256(args.input_manifest),
            "configuration_protocol_sha256":
                sha256(args.configuration_protocol),
            "internal_protocol_sha256": sha256(args.internal_protocol)},
        "method_metadata": method_metadata,
        "stage1_candidate_documents_to_k": stage1,
        "stage2_historical_hamming768_to_k": stage2,
        "historical_native_profile": historical_baseline(
            cases, document_ids, relevance),
        "composed_profiles": profiles,
        "limitations": [
            "configuration and internal queries were opened by earlier studies",
            "FP32 candidate-pool top10 is the representation oracle; it is not a corpus-wide exact-search claim",
            "Python p95 is directional and is not a native packed/SIMD serving claim",
            "sub-byte scalar timing excludes physical packed-bit decoding",
            "RaBitQ-RR-1 and BBQ-block-1 are local research specifications, not vendor-compatible implementations"],
        "production_licensed": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    global RANKS
    RANKS = np.asarray([4, 3, 2, 1, 0], dtype=np.uint32)
    ids = np.asarray([0, 1, 2, 3, 4], dtype=np.int32)
    scores = np.asarray([1, 1, 2, 0, 2], dtype=np.float32)
    assert positions_top(ids, scores, RANKS, 3).tolist() == [4, 2, 1]
    prepared = (np.arange(10).reshape(5, 2), np.arange(5))
    sliced = subset_prepared(prepared, np.asarray([3, 1]))
    assert sliced[0].tolist() == [[6, 7], [2, 3]]
    assert sliced[1].tolist() == [3, 1]
    print("document cascade code-family self-test: ok")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
        "neuroute-document-cascade-code-family.example.json")
    parser.add_argument("--configuration-report-root", type=Path)
    parser.add_argument("--internal-report-root", type=Path)
    parser.add_argument("--configuration-protocol", type=Path)
    parser.add_argument("--internal-protocol", type=Path)
    parser.add_argument("--input-manifest", type=Path)
    parser.add_argument("--e5-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--score-cache", type=Path)
    parser.add_argument("--train-documents", type=int, default=16384)
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--methods", default="all")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = ("configuration_report_root", "internal_report_root",
                    "configuration_protocol", "internal_protocol",
                    "input_manifest", "e5_root", "output", "score_cache")
        if any(getattr(args, name) is None for name in required):
            parser.error("all document-cascade paths are required")
        require(1024 <= args.train_documents <= DOCUMENTS,
                "train-documents must be in [1024, 1000000]")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"run-neuroute-document-cascade-code-family: {error}",
              file=sys.stderr)
        return 1


RANKS = np.empty(0, dtype=np.uint32)


if __name__ == "__main__":
    raise SystemExit(main())
