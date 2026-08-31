#!/usr/bin/env python3
"""Evaluate scalar codecs on actual full-R4 ADC64 pools."""
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
DIMENSIONS = 384
DOCUMENTS = 1_000_000
QUERIES = 305
TOP_K = 10
POPCOUNT = np.asarray([int(value).bit_count() for value in range(256)],
                      dtype=np.uint8)


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_actual_r4_final_planner",
               "plan-neuroute-actual-r4-codec-frontier.py")


def require(value: bool, message: str) -> None:
    if not value:
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


def read_ids(path: Path) -> list[str]:
    return [json.loads(line)["id"] for line in path.read_text(
        encoding="utf-8").splitlines() if line]


def read_qrels(path: Path) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        query, _, document, relevance = line.split()
        result.setdefault(query, {})[document] = float(relevance)
    return result


def ndcg(top: np.ndarray, query_id: str, document_ids: list[str],
         relevance: dict[str, dict[str, float]]) -> float:
    gains = relevance.get(query_id, {})
    actual = sum((2.0 ** gains.get(document_ids[int(position)], 0.0) - 1.0) /
                 math.log2(rank + 2.0) for rank, position in enumerate(top))
    ideal_values = sorted(gains.values(), reverse=True)[:TOP_K]
    ideal = sum((2.0 ** value - 1.0) / math.log2(rank + 2.0)
                for rank, value in enumerate(ideal_values))
    return actual / ideal if ideal else 0.0


def stable_top(candidates: np.ndarray, scores: np.ndarray, ranks: np.ndarray,
               count: int, higher: bool) -> np.ndarray:
    order = np.lexsort((ranks[candidates], -scores if higher else scores))
    return candidates[order[:count]].astype(np.int32, copy=False)


def reconstruct(values: np.ndarray, treatment: dict[str, Any]) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    if treatment["kind"] == "fp32":
        return source
    if treatment["kind"] == "fp16":
        return source.astype(np.float16).astype(np.float32)
    bits = int(treatment["bits"])
    levels = (1 << (bits - 1)) - 1
    amplitudes = np.max(np.abs(source), axis=1).astype(np.float32)
    safe = np.where(amplitudes == 0.0, 1.0, amplitudes)
    normalized = np.asarray(source / safe[:, None], dtype=np.float32)
    compander = treatment["compander"]
    kind = compander["kind"]
    parameter = float(compander["parameter"])
    if kind == "uniform":
        transformed = normalized
    elif kind == "power":
        transformed = np.copysign(np.power(np.abs(normalized), parameter),
                                  normalized)
    else:
        require(kind == "mulaw", "actual-R4 final compander differs")
        transformed = np.copysign(
            np.log1p(parameter * np.abs(normalized)) / np.log1p(parameter),
            normalized)
    codes = np.clip(np.rint(transformed * levels), -levels, levels)
    quantized = np.asarray(codes / levels, dtype=np.float32)
    if kind == "power":
        quantized = np.copysign(
            np.power(np.abs(quantized), 1.0 / parameter), quantized)
    elif kind == "mulaw":
        quantized = np.copysign(
            np.expm1(np.abs(quantized) * np.log1p(parameter)) / parameter,
            quantized)
    return np.asarray(quantized * safe[:, None], dtype=np.float32)


def load_reports(root: Path, requests: list[dict[str, Any]],
                 modes: list[str]) -> dict[tuple[str, int], dict[str, Any]]:
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for path in sorted(root.glob("*-w1.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("storage_mode") not in modes or report.get("workers") != 1:
            continue
        require(report.get("family") ==
                    "neuroute_external_ann_comparison_r4_samples" and
                len(report["samples"]) == 1 and
                len(report["samples"][0]["queries"]) == len(requests),
                "actual-R4 final report identity differs")
        rows = report["samples"][0]["queries"]
        require(all((row["request"], row["native_query"]) ==
                    (request["request"], request["native_query"])
                    for row, request in zip(rows, requests)),
                "actual-R4 final report request order differs")
        key = (report["storage_mode"], int(report["seed"]))
        require(key not in result, "actual-R4 final report duplicated")
        result[key] = {"path": path, "report": report}
    expected = {(mode, seed) for mode in modes
                for seed in (2026082701, 2026082702, 2026082703)}
    require(set(result) == expected, "actual-R4 final report matrix differs")
    return result


def adc_pool(current: dict[str, Any], native: int, codes: np.memmap,
             query_codes: np.memmap, projections: np.memmap,
             centroids: np.memmap, ranks: np.ndarray) -> np.ndarray:
    candidates = np.asarray(current["candidate_documents"], dtype=np.int32)
    distances = POPCOUNT[np.bitwise_xor(codes[candidates],
                                        query_codes[native])].sum(
                                            axis=1, dtype=np.uint16)
    hamming = stable_top(candidates, distances, ranks,
                         min(768, len(candidates)), False)
    symbols = np.unpackbits(codes[hamming], axis=1, bitorder="little")
    selected = centroids[np.arange(256)[None, :], symbols]
    adc_scores = np.square(projections[native][None, :] - selected).sum(
        axis=1, dtype=np.float32)
    result = stable_top(hamming, adc_scores, ranks, min(64, len(hamming)), False)
    require(u32_sha256(result) == current["adc_sha256"],
            "actual-R4 reconstructed ADC64 differs")
    return result


def summarize(rows: list[dict[str, Any]], treatment: dict[str, Any],
              partition: str, gates: dict[str, Any]) -> dict[str, Any]:
    losses = [row["fp32_ndcg"] - row["ndcg"] for row in rows]
    strata = []
    for mode in sorted({row["routing_mode"] for row in rows}):
        for seed in sorted({row["seed"] for row in rows}):
            current = [row for row in rows
                       if row["routing_mode"] == mode and row["seed"] == seed]
            strata.append({"routing_mode": mode, "seed": seed,
                "queries": len(current),
                "mean_ndcg_loss_vs_fp32": statistics.fmean(
                    row["fp32_ndcg"] - row["ndcg"] for row in current),
                "mean_top10_overlap_vs_fp32": statistics.fmean(
                    row["top10_overlap"] for row in current)})
    value = {**treatment, "partition": partition, "queries": len(rows),
        "mean_ndcg_at_10": statistics.fmean(row["ndcg"] for row in rows),
        "mean_ndcg_loss_vs_fp32": statistics.fmean(losses),
        "maximum_query_ndcg_loss_vs_fp32": max(losses),
        "mean_top10_overlap_vs_fp32": statistics.fmean(
            row["top10_overlap"] for row in rows),
        "minimum_query_top10_overlap_vs_fp32": min(
            row["top10_overlap"] for row in rows),
        "top1_agreement_vs_fp32": statistics.fmean(
            row["top1_agreement"] for row in rows),
        "changed_top10_queries": sum(row["top10_overlap"] < 1.0 for row in rows),
        "maximum_stratum_mean_ndcg_loss_vs_fp32": max(
            row["mean_ndcg_loss_vs_fp32"] for row in strata),
        "strata": strata}
    prefix = "configuration" if partition == "configuration" else "internal"
    value["passes_gates"] = bool(
        value["mean_ndcg_loss_vs_fp32"] <= gates[
            f"maximum_{prefix}_mean_ndcg_loss_vs_fp32"] and
        value["maximum_stratum_mean_ndcg_loss_vs_fp32"] <= gates[
            f"maximum_{prefix}_every_seed_and_routing_mode_mean_ndcg_loss_vs_fp32"] and
        value["mean_top10_overlap_vs_fp32"] >= gates[
            f"minimum_{prefix}_mean_top10_overlap_vs_fp32"])
    return value


def evaluate_partition(partition: str, reports: dict[tuple[str, int], dict[str, Any]],
                       requests: list[dict[str, Any]], treatments: list[dict[str, Any]],
                       documents: np.memmap, queries: np.memmap, codes: np.memmap,
                       query_codes: np.memmap, projections: np.memmap,
                       centroids: np.memmap, ranks: np.ndarray,
                       document_ids: list[str], relevance: dict[str, dict[str, float]],
                       gates: dict[str, Any]) -> tuple[list[dict[str, Any]],
                                                       dict[str, list[dict[str, Any]]]]:
    by_treatment = {row["id"]: [] for row in treatments}
    for (mode, seed), value in sorted(reports.items()):
        report_rows = value["report"]["samples"][0]["queries"]
        for local, (current, request) in enumerate(zip(report_rows, requests)):
            native = int(request["native_query"])
            query_id = request["query_id"]
            adc = adc_pool(current, native, codes, query_codes, projections,
                           centroids, ranks)
            source = np.asarray(documents[adc], dtype=np.float32)
            query = np.asarray(queries[native], dtype=np.float32)
            reference_scores = source @ query
            reference_top = stable_top(adc, reference_scores, ranks, TOP_K, True)
            reference_ndcg = ndcg(reference_top, query_id, document_ids, relevance)
            for treatment in treatments:
                values = reconstruct(source, treatment)
                scores = np.asarray(values @ query, dtype=np.float32)
                top = stable_top(adc, scores, ranks, TOP_K, True)
                by_treatment[treatment["id"]].append({
                    "routing_mode": mode, "seed": seed, "local_query": local,
                    "native_query": native, "query_id": query_id,
                    "fp32_ndcg": reference_ndcg,
                    "ndcg": ndcg(top, query_id, document_ids, relevance),
                    "top10_overlap": len(set(map(int, reference_top)) &
                                         set(map(int, top))) / TOP_K,
                    "top1_agreement": float(top[0] == reference_top[0])})
    summaries = [summarize(by_treatment[row["id"]], row, partition, gates)
                 for row in treatments]
    return summaries, by_treatment


def select_per_width(summaries: list[dict[str, Any]],
                     bits: list[int]) -> dict[str, str]:
    result = {}
    for width in bits:
        current = [row for row in summaries if row.get("bits") == width]
        selected = min(current, key=lambda row: (
            row["mean_ndcg_loss_vs_fp32"],
            -row["mean_top10_overlap_vs_fp32"], row["id"]))
        result[str(width)] = selected["id"]
    return result


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    treatments = planner.treatments(contract)
    config_protocol = json.loads(args.configuration_protocol.read_text(
        encoding="utf-8"))
    internal_protocol = json.loads(args.internal_request_protocol.read_text(
        encoding="utf-8"))
    config_requests = config_protocol["requests"]
    internal_requests = internal_protocol["requests"]
    require([row["request"] for row in config_requests] == list(range(76)) and
            [row["request"] for row in internal_requests] == list(range(76, 152)),
            "actual-R4 final partition binding differs")
    modes = contract["routing_storage_modes"]
    config_reports = load_reports(args.configuration_report_root,
                                  config_requests, modes)
    internal_reports = load_reports(args.internal_report_root,
                                    internal_requests, modes)
    manifest = json.loads(args.input_manifest.read_text(encoding="utf-8"))
    require(sha256(args.input_manifest) == contract["activation"][
                "native_input_manifest_sha256"],
            "actual-R4 final input manifest differs")
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
    ranks = np.fromfile(Path(internal_protocol["document_id_rank_file"]),
                        dtype="<u4")
    document_ids = read_ids(args.e5_root / "evaluation-document-ids.jsonl")
    query_ids = read_ids(args.e5_root / "evaluation-query-ids.jsonl")
    relevance = read_qrels(args.e5_root / "evaluation-qrels.tsv")
    require(len(document_ids) == DOCUMENTS and len(query_ids) == QUERIES and
            all(query_ids[row["native_query"]] == row["query_id"]
                for row in [*config_requests, *internal_requests]),
            "actual-R4 final query identity differs")

    config_summaries, config_rows = evaluate_partition(
        "configuration", config_reports, config_requests, treatments,
        documents, queries, codes, query_codes, projections, centroids, ranks,
        document_ids, relevance, contract["final_gates"])
    per_width = select_per_width(config_summaries,
                                 contract["scalar_grid"]["integer_bits"])
    passing = [row for row in config_summaries
               if row["id"] != "fp32" and row["passes_gates"]]
    require(passing, "actual-R4 final configuration has no eligible codec")
    candidate = min(passing, key=lambda row: (
        row["record_bytes"], row["mean_ndcg_loss_vs_fp32"], row["id"]))
    internal_ids = {"fp32", "fp16", candidate["id"], *per_width.values(),
                    *(f"int{bits}_uniform"
                      for bits in contract["scalar_grid"]["integer_bits"])}
    internal_treatments = [row for row in treatments if row["id"] in internal_ids]
    internal_summaries, internal_rows = evaluate_partition(
        "internal_locked_replay", internal_reports, internal_requests,
        internal_treatments, documents, queries, codes, query_codes,
        projections, centroids, ranks, document_ids, relevance,
        contract["final_gates"])
    internal_by_id = {row["id"]: row for row in internal_summaries}
    selected_internal = internal_by_id[candidate["id"]]
    detailed_ids = {candidate["id"], "fp32", "fp16", "int8_uniform",
                    "int5_uniform", per_width["5"], per_width["6"]}
    detailed_rows = {"configuration": {name: config_rows[name]
                                       for name in sorted(detailed_ids)},
                     "internal_locked_replay": {name: internal_rows[name]
                                       for name in sorted(detailed_ids)
                                       if name in internal_rows}}
    result = {"schema_version": 1,
        "family": "neuroute_actual_r4_final_codec_frontier_result",
        "contract_sha256": sha256(args.contract),
        "inputs": {
            "configuration_protocol_sha256": sha256(args.configuration_protocol),
            "internal_request_protocol_sha256": sha256(
                args.internal_request_protocol),
            "input_manifest_sha256": sha256(args.input_manifest),
            "configuration_reports": {f"{mode}/{seed}": sha256(value["path"])
                for (mode, seed), value in config_reports.items()},
            "internal_reports": {f"{mode}/{seed}": sha256(value["path"])
                for (mode, seed), value in internal_reports.items()}},
        "configuration": {"summaries": config_summaries,
            "selected_per_width": per_width,
            "selected_stage_candidate": candidate["id"]},
        "internal_locked_replay": {"summaries": internal_summaries,
            "selected_candidate_passes_gates": selected_internal["passes_gates"]},
        "selected_candidate": selected_internal,
        "detailed_query_rows": detailed_rows,
        "decision": {
            "candidate_for_new_external_confirmation": candidate["id"],
            "replaces_post_hoc_int8_on_locked_replay":
                candidate["id"] != "int8_uniform" and
                selected_internal["passes_gates"],
            "production_licensed": False,
            "reason": "internal_partition_was_previously_opened"}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


def self_test() -> None:
    contract = planner.load_contract(THIS /
        "neuroute-actual-r4-codec-frontier.example.json")
    rows = planner.treatments(contract)
    values = np.asarray([[0.0, .25, -.5, 1.0]], dtype=np.float32)
    uniform = next(row for row in rows if row["id"] == "int8_uniform")
    power = next(row for row in rows if row["id"] == "int5_power_500")
    require(reconstruct(values, uniform).shape == values.shape and
            np.isclose(reconstruct(values, power)[0, -1], 1.0) and
            stable_top(np.asarray([2, 1, 0], dtype=np.int32),
                       np.ones(3, dtype=np.float32),
                       np.asarray([2, 1, 0], dtype=np.uint32), 2, True
                       ).tolist() == [2, 1],
            "actual-R4 final codec self-test differs")
    print("NeuRoute actual-R4 final codec frontier self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-actual-r4-codec-frontier.example.json")
    for name in ("configuration-report-root", "internal-report-root",
                 "configuration-protocol", "internal-request-protocol",
                 "input-manifest", "e5-root", "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = [name for name in vars(args)
                    if name not in {"self_test", "contract"}]
        if any(getattr(args, name) is None for name in required):
            parser.error("all actual-R4 final codec paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"run-neuroute-actual-r4-final-codec-frontier: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
