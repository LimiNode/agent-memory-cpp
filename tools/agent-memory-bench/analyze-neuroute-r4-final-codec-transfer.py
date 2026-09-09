#!/usr/bin/env python3
"""Replay the final-codec transfer check on the actual full-R4 ADC64 pools."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
import numpy as np


DIMENSIONS = 384
DOCUMENTS = 1_000_000
QUERIES = 305
TOP_K = 10
POPCOUNT = np.asarray([int(value).bit_count() for value in range(256)],
                      dtype=np.uint8)


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


def ids(path: Path) -> list[str]:
    return [json.loads(line)["id"] for line in path.read_text(
        encoding="utf-8").splitlines() if line]


def qrels(path: Path) -> dict[str, dict[str, float]]:
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


def load_reports(root: Path) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for path in sorted(root.glob("*-int8-w1.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        require(report["family"] ==
                "neuroute_external_ann_comparison_r4_samples" and
                report["storage_mode"] == "int8" and
                int(report["workers"]) == 1,
                "R4 final-codec transfer report identity differs")
        result[int(report["seed"])] = {"path": path, "report": report}
    require(set(result) == {2026082701, 2026082702, 2026082703},
            "R4 final-codec transfer seed matrix differs")
    return result


def int8_store(manifest_path: Path) -> tuple[np.memmap, dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("family") == "neuroute_r4_layout_materialization",
            "R4 final INT8 manifest identity differs")
    row = next((value for value in manifest["global_layouts"]
                if value["role"] == "document_major_int8"), None)
    require(row is not None and int(row["records"]) == DOCUMENTS and
            int(row["record_bytes"]) == 388,
            "R4 final INT8 descriptor differs")
    path = manifest_path.parent / row["file"]
    require(path.stat().st_size == int(row["bytes"]) and
            sha256(path) == row["sha256"],
            "R4 final INT8 physical bytes differ")
    return np.memmap(path, mode="r", dtype=np.uint8,
                     shape=(DOCUMENTS, 388)), row


def run(args: argparse.Namespace) -> None:
    selected = load_reports(args.selected_report_root)
    uniform = load_reports(args.uniform_int5_report_root)
    protocol = json.loads(args.r4_protocol.read_text(encoding="utf-8"))
    requests = protocol["requests"]
    require(len(requests) == 76, "R4 final-codec query partition differs")
    native_queries = np.asarray([row["native_query"] for row in requests],
                                dtype=np.int64)
    manifest = json.loads(args.input_manifest.read_text(encoding="utf-8"))
    root = args.input_manifest.parent
    documents = np.memmap(root / manifest["document_vectors_file"],
        mode="r", dtype="<f4", shape=(DOCUMENTS, DIMENSIONS))
    queries = np.memmap(root / manifest["query_vectors_file"],
        mode="r", dtype="<f4", shape=(QUERIES, DIMENSIONS))
    codes = np.memmap(root / manifest["document_codes_file"],
        mode="r", dtype=np.uint8, shape=(DOCUMENTS, 32))
    query_codes = np.memmap(root / manifest["query_codes_file"],
        mode="r", dtype=np.uint8, shape=(QUERIES, 32))
    projections = np.memmap(root / manifest["query_itq_projections_file"],
        mode="r", dtype="<f4", shape=(QUERIES, 256))
    centroids = np.memmap(root / manifest["binary_adc_centroids_file"],
        mode="r", dtype="<f4", shape=(256, 2))
    ranks = np.fromfile(Path(protocol["document_id_rank_file"]), dtype="<u4")
    document_ids = ids(Path(protocol["evaluation_document_ids"]))
    query_ids = ids(Path(protocol["evaluation_query_ids"]))
    relevance = qrels(Path(protocol["evaluation_qrels"]))
    int8, int8_descriptor = int8_store(args.int8_layout_manifest)

    all_rows: list[dict[str, Any]] = []
    per_seed = []
    for seed in sorted(selected):
        selected_report = selected[seed]["report"]
        uniform_report = uniform[seed]["report"]
        require(selected_report.get("final_codec") ==
                    "symmetric_per_document_int8" and
                uniform_report.get("final_codec") is None,
                "R4 final-codec treatment binding differs")
        selected_rows = selected_report["samples"][0]["queries"]
        uniform_rows = uniform_report["samples"][0]["queries"]
        require(len(selected_rows) == len(requests) == len(uniform_rows),
                "R4 final-codec report query count differs")
        seed_rows = []
        for local, (current, old) in enumerate(zip(selected_rows, uniform_rows)):
            require((current["request"], current["native_query"]) ==
                    (old["request"], old["native_query"]) and
                    current["candidate_sha256"] == old["candidate_sha256"] and
                    current["adc_sha256"] == old["adc_sha256"],
                    "R4 final-codec pools differ between treatments")
            native = int(native_queries[local])
            candidates = np.asarray(current["candidate_documents"],
                                    dtype=np.int32)
            distances = POPCOUNT[np.bitwise_xor(codes[candidates],
                                                query_codes[native])].sum(
                                                    axis=1, dtype=np.uint16)
            hamming = stable_top(candidates, distances, ranks,
                                 min(768, len(candidates)), False)
            symbols = np.unpackbits(codes[hamming], axis=1, bitorder="little")
            selected_centroids = centroids[np.arange(256)[None, :], symbols]
            adc_scores = np.square(projections[native][None, :] -
                                   selected_centroids).sum(axis=1,
                                                          dtype=np.float32)
            adc = stable_top(hamming, adc_scores, ranks,
                             min(64, len(hamming)), False)
            require(u32_sha256(adc) == current["adc_sha256"],
                    "R4 final-codec reconstructed ADC64 differs")
            fp32_scores = np.asarray(documents[adc] @ queries[native],
                                     dtype=np.float32)
            fp32 = stable_top(adc, fp32_scores, ranks, TOP_K, True)
            records = np.asarray(int8[adc])
            int8_codes = (records[:, :DIMENSIONS].astype(np.int16) - 127
                          ).astype(np.float32)
            scales = records[:, DIMENSIONS:].copy().view("<f4").reshape(-1)
            int8_scores = (int8_codes @ queries[native]) * scales
            int8_top = stable_top(adc, int8_scores, ranks, TOP_K, True)
            native_int8 = np.asarray(current["exact_documents"], dtype=np.int32)
            uniform_int5 = np.asarray(old["exact_documents"], dtype=np.int32)
            require(np.array_equal(int8_top, native_int8),
                    "R4 native final INT8 ranking differs")
            query_id = query_ids[native]
            values = {"fp32": ndcg(fp32, query_id, document_ids, relevance),
                "int8": ndcg(int8_top, query_id, document_ids, relevance),
                "uniform_int5": ndcg(uniform_int5, query_id, document_ids,
                                     relevance)}
            row = {"seed": seed, "local_query": local,
                "native_query": native, "query_id": query_id,
                "ndcg_at_10": values,
                "top10_overlap_with_fp32": {
                    "int8": len(set(map(int, fp32)) &
                                set(map(int, int8_top))) / TOP_K,
                    "uniform_int5": len(set(map(int, fp32)) &
                                        set(map(int, uniform_int5))) / TOP_K}}
            seed_rows.append(row)
            all_rows.append(row)
        per_seed.append(summarize_rows(seed_rows, seed))

    aggregate = summarize_rows(all_rows, None)
    gates = {"maximum_per_seed_mean_ndcg_loss_int8_vs_fp32": .003,
             "minimum_mean_top10_overlap_int8_vs_fp32": .99}
    int8_passed = (max(row["mean_ndcg_loss_vs_fp32"]["int8"]
                       for row in per_seed) <= gates[
                           "maximum_per_seed_mean_ndcg_loss_int8_vs_fp32"] and
                   aggregate["mean_top10_overlap_with_fp32"]["int8"] >= gates[
                       "minimum_mean_top10_overlap_int8_vs_fp32"])
    uniform_passed = max(row["mean_ndcg_loss_vs_fp32"]["uniform_int5"]
                         for row in per_seed) <= gates[
                             "maximum_per_seed_mean_ndcg_loss_int8_vs_fp32"]
    result = {"schema_version": 1,
        "family": "neuroute_r4_final_codec_transfer_result",
        "inputs": {"r4_protocol_sha256": sha256(args.r4_protocol),
            "input_manifest_sha256": sha256(args.input_manifest),
            "int8_layout_manifest_sha256": sha256(args.int8_layout_manifest),
            "int8_store_sha256": int8_descriptor["sha256"],
            "selected_reports": {str(seed): sha256(value["path"])
                                 for seed, value in selected.items()},
            "uniform_int5_reports": {str(seed): sha256(value["path"])
                                     for seed, value in uniform.items()}},
        "gates": gates, "aggregate": aggregate, "per_seed": per_seed,
        "query_rows": all_rows,
        "decision": {"int8_corrective_gate_passed": int8_passed,
            "uniform_int5_transfer_gate_passed": uniform_passed,
            "selected_full_r4_final_codec":
                "symmetric_per_document_int8" if int8_passed else "fp32",
            "selection_is_post_hoc_corrective_not_preregistered": True,
            "requires_independent_heldout_revalidation": True}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))
    require(int8_passed and not uniform_passed,
            "R4 final-codec corrective decision differs")


def summarize_rows(rows: list[dict[str, Any]], seed: int | None
                   ) -> dict[str, Any]:
    result: dict[str, Any] = {"queries": len(rows)}
    if seed is not None:
        result["seed"] = seed
    result["mean_ndcg_at_10"] = {name: statistics.fmean(
        row["ndcg_at_10"][name] for row in rows)
        for name in ("fp32", "int8", "uniform_int5")}
    result["mean_ndcg_loss_vs_fp32"] = {name: statistics.fmean(
        row["ndcg_at_10"]["fp32"] - row["ndcg_at_10"][name]
        for row in rows) for name in ("int8", "uniform_int5")}
    result["maximum_query_ndcg_loss_vs_fp32"] = {name: max(
        row["ndcg_at_10"]["fp32"] - row["ndcg_at_10"][name]
        for row in rows) for name in ("int8", "uniform_int5")}
    result["mean_top10_overlap_with_fp32"] = {name: statistics.fmean(
        row["top10_overlap_with_fp32"][name] for row in rows)
        for name in ("int8", "uniform_int5")}
    result["changed_top10_queries"] = {name: sum(
        row["top10_overlap_with_fp32"][name] < 1.0 for row in rows)
        for name in ("int8", "uniform_int5")}
    return result


def self_test() -> None:
    candidates = np.asarray([2, 1, 0], dtype=np.int32)
    ranks = np.asarray([2, 1, 0], dtype=np.uint32)
    scores = np.asarray([1.0, 1.0, 1.0], dtype=np.float32)
    require(stable_top(candidates, scores, ranks, 2, True).tolist() == [2, 1]
            and u32_sha256(np.asarray([1, 2], dtype=np.uint32)) ==
            hashlib.sha256(b"\x01\x00\x00\x00\x02\x00\x00\x00").hexdigest(),
            "R4 final-codec transfer self-test differs")
    print("NeuRoute R4 final-codec transfer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("selected-report-root", "uniform-int5-report-root",
                 "input-manifest", "r4-protocol", "int8-layout-manifest",
                 "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        required = ("selected_report_root", "uniform_int5_report_root",
                    "input_manifest", "r4_protocol", "int8_layout_manifest",
                    "output")
        if any(getattr(args, name) is None for name in required):
            parser.error("all R4 final-codec transfer paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"analyze-neuroute-r4-final-codec-transfer: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
