#!/usr/bin/env python3
"""Conformant progressive exact Hamming top-K over a fixed MIH union."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve(); FAMILY = "mih_progressive_exact_hamming_top_k_v1"


def require(value: bool, message: str) -> None:
    if not value: raise ValueError(message)


def sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name: str, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, THIS.with_name(name))
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module); return module


shared = load("evaluate-projection-quantization.py", "progressive_hamming_shared")
mih = load("evaluate-mih-banding.py", "progressive_hamming_mih")


def sources() -> dict[str, str]: return {name: sha256(THIS.with_name(name)) for name in (THIS.name, "mih-progressive-exact-hamming-top-k.example.json", "evaluate-mih-banding.py", "evaluate-projection-quantization.py")}
def source_bundle(value: dict[str, str]) -> str: return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8")); require(value.get("schema_version") == 1 and value.get("family") == FAMILY, "progressive Hamming contract identity differs")
    require(value.get("seeds") == [52, 53, 54, 55, 56], "progressive Hamming seed set differs")
    require(value.get("pipeline") == {"code_bits": 256, "band_count": 16, "band_width_bits": 16, "global_radius": 56, "top_k_values": [10, 64, 128, 256, 512, 768], "itq_iterations": 50}, "progressive Hamming pipeline differs")
    require(value.get("decision_rule") == {"primary": "measure_global_pigeonhole_bound_exact_top_k_scaling_against_full_radius56_union", "held_out_selection": "none"}, "progressive Hamming decision rule differs")
    return value


def schedule(contract: dict[str, Any]) -> list[int]: return mih.global_radius_schedule(contract["pipeline"]["global_radius"], contract["pipeline"]["band_count"])


def contribution_summary(path: Path, contract: dict[str, Any], query_ids: numpy.ndarray, document_count: int) -> dict[str, Any]:
    """Validate per-query evidence and derive every report aggregate from it."""
    limits = numpy.asarray(contract["pipeline"]["top_k_values"], dtype=numpy.int32); query_count = len(query_ids); maximum = int(limits[-1])
    required = {"query_ids", "top_k_values", "total_probe_count", "total_posting_visits", "total_unique_candidate_count", "total_hamming_computations", "probe_count_at_proof", "posting_visits_at_proof", "unique_candidates_at_proof", "hamming_computations_at_proof", "global_lower_bound_at_proof", "early_proof", "full_union_top_k_match", "progressive_top_768_document_positions", "full_union_top_768_document_positions", "progressive_cutoff_hamming_distance", "full_union_cutoff_hamming_distance"}
    with numpy.load(path, allow_pickle=False) as data:
        require(set(data.files) == required, "progressive contribution fields differ")
        values = {name: data[name] for name in required}
    require(numpy.array_equal(values["query_ids"], query_ids) and numpy.array_equal(values["top_k_values"], limits), "progressive contribution query identity differs")
    one_dimensional = ("total_probe_count", "total_posting_visits", "total_unique_candidate_count", "total_hamming_computations")
    two_dimensional = ("probe_count_at_proof", "posting_visits_at_proof", "unique_candidates_at_proof", "hamming_computations_at_proof", "global_lower_bound_at_proof", "early_proof", "full_union_top_k_match", "progressive_cutoff_hamming_distance", "full_union_cutoff_hamming_distance")
    require(all(values[name].shape == (query_count,) for name in one_dimensional) and all(values[name].shape == (query_count, len(limits)) for name in two_dimensional), "progressive contribution work shape differs")
    progressive = values["progressive_top_768_document_positions"]; full = values["full_union_top_768_document_positions"]
    require(progressive.shape == (query_count, maximum) and full.shape == (query_count, maximum) and numpy.issubdtype(progressive.dtype, numpy.integer) and numpy.issubdtype(full.dtype, numpy.integer), "progressive contribution top-K shape differs")
    require(numpy.all(progressive >= 0) and numpy.all(progressive < document_count) and numpy.array_equal(progressive, full), "progressive contribution top-K identity differs")
    require(all(numpy.unique(row).size == maximum for row in progressive), "progressive contribution top-K contains duplicates")
    cutoff_match = numpy.array_equal(values["progressive_cutoff_hamming_distance"], values["full_union_cutoff_hamming_distance"])
    expected_match = numpy.asarray([[numpy.array_equal(progressive[row, :limit], full[row, :limit]) for limit in limits] for row in range(query_count)], dtype=numpy.bool_)
    require(cutoff_match and numpy.array_equal(values["full_union_top_k_match"], expected_match), "progressive contribution exactness flag differs")
    require(all(numpy.issubdtype(values[name].dtype, numpy.integer) for name in one_dimensional + two_dimensional if name not in ("early_proof", "full_union_top_k_match")) and numpy.all(values["total_probe_count"] > 0) and numpy.all(values["total_posting_visits"] >= 0) and numpy.all(values["total_unique_candidate_count"] >= maximum) and numpy.all(values["total_hamming_computations"] == values["total_unique_candidate_count"]), "progressive contribution totals differ")
    summary = {"mean_total_probes": float(numpy.mean(values["total_probe_count"])), "mean_total_posting_visits": float(numpy.mean(values["total_posting_visits"])), "mean_total_unique_candidates": float(numpy.mean(values["total_unique_candidate_count"])), "mean_total_hamming_computations": float(numpy.mean(values["total_hamming_computations"])), "by_top_k": {}}
    for position, limit in enumerate(limits):
        summary["by_top_k"][str(int(limit))] = {"mean_probes_at_proof": float(numpy.mean(values["probe_count_at_proof"][:, position])), "mean_posting_visits_at_proof": float(numpy.mean(values["posting_visits_at_proof"][:, position])), "mean_unique_candidates_at_proof": float(numpy.mean(values["unique_candidates_at_proof"][:, position])), "mean_hamming_computations_at_proof": float(numpy.mean(values["hamming_computations_at_proof"][:, position])), "mean_global_lower_bound_at_proof": float(numpy.mean(values["global_lower_bound_at_proof"][:, position])), "early_proof_fraction": float(numpy.mean(values["early_proof"][:, position])), "full_union_top_k_match_fraction": float(numpy.mean(expected_match[:, position]))}
    return summary


def validate_report(report: dict[str, Any], contribution_path: Path, contract: dict[str, Any], query_ids: numpy.ndarray, document_count: int, calibration_manifest_sha256: str, evaluation_manifest_sha256: str, expected_sources: dict[str, str], seed: int) -> None:
    summary = contribution_summary(contribution_path, contract, query_ids, document_count)
    require(report.get("schema_version") == 3 and report.get("family") == FAMILY and report.get("source_files_sha256") == expected_sources and report.get("source_bundle_sha256") == source_bundle(expected_sources) and report.get("calibration_materialization_manifest_sha256") == calibration_manifest_sha256 and report.get("evaluation_materialization_manifest_sha256") == evaluation_manifest_sha256 and report.get("seed") == seed and report.get("query_count") == len(query_ids) and report.get("pipeline") == contract["pipeline"] and report.get("band_probe_radii") == schedule(contract) and report.get("contribution_sha256") == sha256(contribution_path), "progressive report identity differs")
    require(all(report.get(name) == value for name, value in summary.items()), "progressive report aggregate differs from contribution")


def progressive_top_k_curve(index: list[dict[int, numpy.ndarray]], codes: numpy.ndarray, query: numpy.ndarray, ranges: list[tuple[int, int]], radii: list[int], document_ids: numpy.ndarray, limits: list[int], generation: numpy.ndarray, generation_id: int) -> tuple[numpy.ndarray, dict[int, dict[str, Any]], dict[str, int]]:
    """Measure a global pigeonhole proof for every predeclared top-K value."""
    probes: list[tuple[int, int, numpy.ndarray, int]] = []
    for band, ((start, stop), radius) in enumerate(zip(ranges, radii)):
        key = mih.band_key(query, start, stop)
        for depth in range(radius + 1):
            keys = [probe for probe in mih.probe_keys(key, stop - start, depth)]
            if depth: keys = keys[len(mih.probe_keys(key, stop - start, depth - 1)):]
            postings = [index[band].get(probe, numpy.empty(0, dtype=numpy.int32)) for probe in keys]
            probes.append((depth, band, numpy.concatenate(postings) if postings else numpy.empty(0, dtype=numpy.int32), len(keys)))
    probes.sort(key=lambda item: (item[0], item[1]))
    discovered: list[int] = []; distances: list[int] = []; visits = 0; bucket_probes = 0; completed = [-1] * len(ranges); records: dict[int, dict[str, Any]] = {}; maximum = max(limits)
    for number, (depth, band, posting, group_probe_count) in enumerate(probes, 1):
        bucket_probes += group_probe_count
        visits += int(posting.size)
        for candidate in posting:
            candidate = int(candidate)
            if generation[candidate] == generation_id: continue
            generation[candidate] = generation_id; discovered.append(candidate); distances.append(int(numpy.count_nonzero(codes[candidate] != query)))
        completed[band] = depth
        lower_bound = sum(value + 1 for value in completed)
        if len(discovered) < min(limits): continue
        candidate_ids = numpy.asarray(discovered, dtype=numpy.int32); candidate_distances = numpy.asarray(distances, dtype=numpy.int32); order = numpy.lexsort((document_ids[candidate_ids], candidate_distances))[:min(maximum, candidate_ids.size)]
        for limit in limits:
            if limit in records or candidate_ids.size < limit: continue
            selected = candidate_ids[order[:limit]]; worst = int(candidate_distances[order[limit - 1]])
            if worst < lower_bound:
                records[limit] = {"ids": selected, "probe_count": bucket_probes, "posting_visits": visits, "unique_candidates": candidate_ids.size, "hamming_computations": candidate_ids.size, "global_lower_bound": lower_bound, "early_proof": number < len(probes)}
    candidate_ids = numpy.asarray(discovered, dtype=numpy.int32); candidate_distances = numpy.asarray(distances, dtype=numpy.int32); order = numpy.lexsort((document_ids[candidate_ids], candidate_distances))[:maximum]
    for limit in limits:
        if limit not in records:
            records[limit] = {"ids": candidate_ids[order[:limit]], "probe_count": bucket_probes, "posting_visits": visits, "unique_candidates": candidate_ids.size, "hamming_computations": candidate_ids.size, "global_lower_bound": sum(value + 1 for value in completed), "early_proof": False}
    return candidate_ids[order], records, {"total_probes": bucket_probes, "total_posting_visits": visits, "unique_candidates": candidate_ids.size, "hamming_computations": candidate_ids.size}


def run(args: Any) -> None:
    contract = load_contract(args.contract); calibration, evaluation = shared.load_root(args.calibration_root), shared.load_root(args.evaluation_root); shared.validate_calibration_evaluation_pair(calibration, evaluation); require(calibration["manifest_sha256"] == contract["calibration_materialization_manifest_sha256"] and evaluation["manifest_sha256"] == contract["evaluation_materialization_manifest_sha256"], "progressive Hamming materialization provenance differs")
    pipe = contract["pipeline"]; require(len(calibration["train_ids"]) == 25000 and len(evaluation["document_ids"]) == 22607 and len(evaluation["query_ids"]) == 1252, "progressive Hamming materialization cardinality differs")
    ranges = mih.band_ranges(pipe["code_bits"], pipe["band_count"]); radii = schedule(contract); args.output_root.mkdir(parents=True, exist_ok=True); rows = []
    for number, seed in enumerate(contract["seeds"], 1):
        report_path = args.output_root / "reports" / f"m16-r56-seed{seed}.json"; contribution_path = args.output_root / "contributions" / f"m16-r56-seed{seed}.npz"
        if args.resume and report_path.is_file() and contribution_path.is_file():
            validate_report(json.loads(report_path.read_text(encoding="utf-8")), contribution_path, contract, numpy.asarray(evaluation["query_ids"], dtype=numpy.str_), len(evaluation["document_ids"]), calibration["manifest_sha256"], evaluation["manifest_sha256"], sources(), seed)
            rows.append({"seed": seed, "report_sha256": sha256(report_path), "contribution_sha256": sha256(contribution_path)}); continue
        print(f"[{number}/5] progressive m16-r56 seed{seed}", flush=True); weights = shared.itq_weights(numpy.asarray(calibration["train"]), 256, seed, 50); thresholds = shared.binary_thresholds(numpy.asarray(calibration["train"]), weights); codes = numpy.asarray(evaluation["documents"]) @ weights.T + thresholds >= 0.; queries = numpy.asarray(evaluation["queries"]) @ weights.T + thresholds >= 0.; index = mih.build_index(codes, ranges); generation = numpy.zeros(codes.shape[0], dtype=numpy.uint32)
        total_probes=[]; total_visits=[]; total_candidates=[]; total_computations=[]; proof_probes=[]; proof_visits=[]; proof_candidates=[]; proof_computations=[]; proof_bounds=[]; early=[]; matched=[]; progressive_ids=[]; full_ids=[]; progressive_cutoffs=[]; full_cutoffs=[]
        for query_number, query in enumerate(queries, 1):
            selected, records, diagnostic = progressive_top_k_curve(index, codes, query, ranges, radii, evaluation["document_ids"], pipe["top_k_values"], generation, query_number)
            full_candidates, expected_probes = mih.candidate_union(index, query, ranges, radii); expected = mih.stable_hamming_order(codes, query, evaluation["document_ids"], full_candidates)[:max(pipe["top_k_values"])]
            require(expected_probes == diagnostic["total_probes"] and numpy.array_equal(selected, expected), "progressive exact top-K differs from full-union Hamming order")
            for limit in pipe["top_k_values"]: require(numpy.array_equal(records[limit]["ids"], expected[:limit]), "progressive proof top-K differs from full-union Hamming order")
            total_probes.append(diagnostic["total_probes"]); total_visits.append(diagnostic["total_posting_visits"]); total_candidates.append(diagnostic["unique_candidates"]); total_computations.append(diagnostic["hamming_computations"])
            progressive_ids.append(selected); full_ids.append(expected); progressive_cutoffs.append([int(numpy.count_nonzero(codes[records[limit]["ids"][limit - 1]] != query)) for limit in pipe["top_k_values"]]); full_cutoffs.append([int(numpy.count_nonzero(codes[expected[limit - 1]] != query)) for limit in pipe["top_k_values"]]); proof_probes.append([records[limit]["probe_count"] for limit in pipe["top_k_values"]]); proof_visits.append([records[limit]["posting_visits"] for limit in pipe["top_k_values"]]); proof_candidates.append([records[limit]["unique_candidates"] for limit in pipe["top_k_values"]]); proof_computations.append([records[limit]["hamming_computations"] for limit in pipe["top_k_values"]]); proof_bounds.append([records[limit]["global_lower_bound"] for limit in pipe["top_k_values"]]); early.append([records[limit]["early_proof"] for limit in pipe["top_k_values"]]); matched.append([True] * len(pipe["top_k_values"]))
        values = {"query_ids": numpy.asarray(evaluation["query_ids"], dtype=numpy.str_), "top_k_values": numpy.asarray(pipe["top_k_values"], dtype=numpy.int32), "total_probe_count": numpy.asarray(total_probes, dtype=numpy.int32), "total_posting_visits": numpy.asarray(total_visits, dtype=numpy.int32), "total_unique_candidate_count": numpy.asarray(total_candidates, dtype=numpy.int32), "total_hamming_computations": numpy.asarray(total_computations, dtype=numpy.int32), "probe_count_at_proof": numpy.asarray(proof_probes, dtype=numpy.int32), "posting_visits_at_proof": numpy.asarray(proof_visits, dtype=numpy.int32), "unique_candidates_at_proof": numpy.asarray(proof_candidates, dtype=numpy.int32), "hamming_computations_at_proof": numpy.asarray(proof_computations, dtype=numpy.int32), "global_lower_bound_at_proof": numpy.asarray(proof_bounds, dtype=numpy.int32), "early_proof": numpy.asarray(early, dtype=numpy.bool_), "full_union_top_k_match": numpy.asarray(matched, dtype=numpy.bool_), "progressive_top_768_document_positions": numpy.asarray(progressive_ids, dtype=numpy.int32), "full_union_top_768_document_positions": numpy.asarray(full_ids, dtype=numpy.int32), "progressive_cutoff_hamming_distance": numpy.asarray(progressive_cutoffs, dtype=numpy.int32), "full_union_cutoff_hamming_distance": numpy.asarray(full_cutoffs, dtype=numpy.int32)}
        contribution_path.parent.mkdir(parents=True, exist_ok=True); numpy.savez_compressed(contribution_path, **values); summary = contribution_summary(contribution_path, contract, numpy.asarray(evaluation["query_ids"], dtype=numpy.str_), len(evaluation["document_ids"])); report = {"schema_version": 3, "family": FAMILY, "source_files_sha256": sources(), "source_bundle_sha256": source_bundle(sources()), "evaluator_runtime": shared.evaluator_runtime(), "calibration_materialization_manifest_sha256": calibration["manifest_sha256"], "evaluation_materialization_manifest_sha256": evaluation["manifest_sha256"], "seed": seed, "query_count": len(evaluation["query_ids"]), "pipeline": pipe, "band_probe_radii": radii, "contribution_sha256": sha256(contribution_path), **summary}; report_path.parent.mkdir(parents=True, exist_ok=True); report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"); validate_report(report, contribution_path, contract, numpy.asarray(evaluation["query_ids"], dtype=numpy.str_), len(evaluation["document_ids"]), calibration["manifest_sha256"], evaluation["manifest_sha256"], sources(), seed); rows.append({"seed": seed, "report_sha256": sha256(report_path), "contribution_sha256": sha256(contribution_path)})
    manifest = {"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "source_files_sha256": sources(), "source_bundle_sha256": source_bundle(sources()), "rows": rows}; (args.output_root / "matrix-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test(contract_path: Path) -> int:
    try:
        contract = load_contract(contract_path); require(schedule(contract) == [3] * 9 + [2] * 7, "radius-56 schedule differs")
        codes = numpy.asarray([[False, False], [True, False], [True, True]], dtype=bool); ranges = [(0, 1), (1, 2)]; index = mih.build_index(codes, ranges); generation = numpy.zeros(3, dtype=numpy.uint32); ids = numpy.asarray(["a", "b", "c"])
        selected, records, diagnostic = progressive_top_k_curve(index, codes, numpy.asarray([False, False]), ranges, [1, 0], ids, [1], generation, 1); require(selected.tolist() == [0] and records[1]["ids"].tolist() == [0] and records[1]["early_proof"] and records[1]["global_lower_bound"] == 1 and diagnostic["total_probes"] == 3, "progressive global-bound proof differs")
        changed = json.loads(json.dumps(contract)); changed["pipeline"]["global_radius"] = 55
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "changed.json"; path.write_text(json.dumps(changed), encoding="utf-8")
            try: load_contract(path)
            except ValueError: pass
            else: raise ValueError("changed progressive Hamming contract was accepted")
    except (OSError, ValueError, json.JSONDecodeError) as error: print(f"run-mih-progressive-exact-hamming-top-k self-test failed: {error}", file=sys.stderr); return 1
    print("MIH progressive exact Hamming top-K self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest="command", required=True); run_parser=sub.add_parser("run"); run_parser.add_argument("--contract", type=Path, required=True); run_parser.add_argument("--calibration-root", type=Path, required=True); run_parser.add_argument("--evaluation-root", type=Path, required=True); run_parser.add_argument("--output-root", type=Path, required=True); run_parser.add_argument("--resume", action="store_true"); test=sub.add_parser("self-test"); test.add_argument("--contract", type=Path, required=True); args=parser.parse_args(argv)
    try: return self_test(args.contract) if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error: print(f"run-mih-progressive-exact-hamming-top-k: {error}", file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
