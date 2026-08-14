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
    require(value.get("pipeline") == {"code_bits": 256, "band_count": 16, "band_width_bits": 16, "global_radius": 56, "hamming_limit": 768, "itq_iterations": 50}, "progressive Hamming pipeline differs")
    require(value.get("decision_rule") == {"primary": "prove_or_refute_early_exact_top_k_termination_against_full_radius56_union", "held_out_selection": "none"}, "progressive Hamming decision rule differs")
    return value


def schedule(contract: dict[str, Any]) -> list[int]: return mih.global_radius_schedule(contract["pipeline"]["global_radius"], contract["pipeline"]["band_count"])


def progressive_top_k(index: list[dict[int, numpy.ndarray]], codes: numpy.ndarray, query: numpy.ndarray, ranges: list[tuple[int, int]], radii: list[int], document_ids: numpy.ndarray, limit: int, generation: numpy.ndarray, generation_id: int) -> tuple[numpy.ndarray, numpy.ndarray, dict[str, int | bool]]:
    """Stream postings in increasing local-distance order and stop only by a strict lower bound."""
    probes: list[tuple[int, int, int, numpy.ndarray]] = []
    for band, ((start, stop), radius) in enumerate(zip(ranges, radii)):
        key = mih.band_key(query, start, stop)
        for depth in range(radius + 1):
            keys = [probe for probe in mih.probe_keys(key, stop - start, depth)]
            if depth: keys = keys[len(mih.probe_keys(key, stop - start, depth - 1)):]
            for probe in keys: probes.append((depth, band, probe, index[band].get(probe, numpy.empty(0, dtype=numpy.int32))))
    probes.sort(key=lambda item: (item[0], item[1], item[2]))
    discovered: list[int] = []; distances: list[int] = []; visits = 0; proof_probe = len(probes); proof_visits = 0; proven = False
    for number, (depth, _, _, posting) in enumerate(probes, 1):
        visits += int(posting.size)
        for candidate in posting:
            candidate = int(candidate)
            if generation[candidate] == generation_id: continue
            generation[candidate] = generation_id; discovered.append(candidate); distances.append(int(numpy.count_nonzero(codes[candidate] != query)))
        # The unseen-candidate lower bound changes only when the local Hamming
        # depth changes, so checking within a depth group would add sorting work
        # without strengthening the proof.
        if len(discovered) < limit or (number < len(probes) and probes[number][0] == depth): continue
        order = numpy.lexsort((document_ids[numpy.asarray(discovered, dtype=numpy.int32)], numpy.asarray(distances, dtype=numpy.int32)))[:limit]
        worst = distances[int(order[-1])]
        next_lower_bound = probes[number][0] if number < len(probes) else 1 << 30
        if worst < next_lower_bound:
            proof_probe, proof_visits, proven = number, visits, True; break
    candidate_ids = numpy.asarray(discovered, dtype=numpy.int32); candidate_distances = numpy.asarray(distances, dtype=numpy.int32); order = numpy.lexsort((document_ids[candidate_ids], candidate_distances))[:limit]
    return candidate_ids[order], candidate_distances[order], {"total_probes": len(probes), "probes_at_proof": proof_probe, "posting_visits_at_proof": proof_visits if proven else visits, "total_posting_visits": visits, "unique_candidates": len(discovered), "hamming_computations": len(discovered), "early_proof": proven}


def run(args: Any) -> None:
    contract = load_contract(args.contract); calibration, evaluation = shared.load_root(args.calibration_root), shared.load_root(args.evaluation_root); shared.validate_calibration_evaluation_pair(calibration, evaluation); require(calibration["manifest_sha256"] == contract["calibration_materialization_manifest_sha256"] and evaluation["manifest_sha256"] == contract["evaluation_materialization_manifest_sha256"], "progressive Hamming materialization provenance differs")
    pipe = contract["pipeline"]; require(len(calibration["train_ids"]) == 25000 and len(evaluation["document_ids"]) == 22607 and len(evaluation["query_ids"]) == 1252, "progressive Hamming materialization cardinality differs")
    ranges = mih.band_ranges(pipe["code_bits"], pipe["band_count"]); radii = schedule(contract); args.output_root.mkdir(parents=True, exist_ok=True); rows = []
    for number, seed in enumerate(contract["seeds"], 1):
        report_path = args.output_root / "reports" / f"m16-r56-seed{seed}.json"; contribution_path = args.output_root / "contributions" / f"m16-r56-seed{seed}.npz"
        if args.resume and report_path.is_file() and contribution_path.is_file(): rows.append({"seed": seed, "report_sha256": sha256(report_path), "contribution_sha256": sha256(contribution_path)}); continue
        print(f"[{number}/5] progressive m16-r56 seed{seed}", flush=True); weights = shared.itq_weights(numpy.asarray(calibration["train"]), 256, seed, 50); thresholds = shared.binary_thresholds(numpy.asarray(calibration["train"]), weights); codes = numpy.asarray(evaluation["documents"]) @ weights.T + thresholds >= 0.; queries = numpy.asarray(evaluation["queries"]) @ weights.T + thresholds >= 0.; index = mih.build_index(codes, ranges); generation = numpy.zeros(codes.shape[0], dtype=numpy.uint32)
        total_probes=[]; proof_probes=[]; visits=[]; proof_visits=[]; candidates=[]; computations=[]; early=[]; matched=[]
        for query_number, query in enumerate(queries, 1):
            selected, selected_distances, diagnostic = progressive_top_k(index, codes, query, ranges, radii, evaluation["document_ids"], pipe["hamming_limit"], generation, query_number)
            full_candidates, expected_probes = mih.candidate_union(index, query, ranges, radii); expected = mih.stable_hamming_order(codes, query, evaluation["document_ids"], full_candidates)[:pipe["hamming_limit"]]
            require(expected_probes == diagnostic["total_probes"] and numpy.array_equal(selected, expected), "progressive exact top-K differs from full-union Hamming order")
            total_probes.append(diagnostic["total_probes"]); proof_probes.append(diagnostic["probes_at_proof"]); visits.append(diagnostic["total_posting_visits"]); proof_visits.append(diagnostic["posting_visits_at_proof"]); candidates.append(diagnostic["unique_candidates"]); computations.append(diagnostic["hamming_computations"]); early.append(diagnostic["early_proof"]); matched.append(True)
        values = {"query_ids": numpy.asarray(evaluation["query_ids"], dtype=numpy.str_), "total_probe_count": numpy.asarray(total_probes, dtype=numpy.int32), "probe_count_at_proof": numpy.asarray(proof_probes, dtype=numpy.int32), "total_posting_visits": numpy.asarray(visits, dtype=numpy.int32), "posting_visits_at_proof": numpy.asarray(proof_visits, dtype=numpy.int32), "unique_candidate_count": numpy.asarray(candidates, dtype=numpy.int32), "full_hamming_computations": numpy.asarray(computations, dtype=numpy.int32), "early_proof": numpy.asarray(early, dtype=numpy.bool_), "full_union_top_k_match": numpy.asarray(matched, dtype=numpy.bool_)}
        numpy.savez_compressed(contribution_path, **values); report = {"schema_version": 1, "family": FAMILY, "source_files_sha256": sources(), "source_bundle_sha256": source_bundle(sources()), "evaluator_runtime": shared.evaluator_runtime(), "calibration_materialization_manifest_sha256": calibration["manifest_sha256"], "evaluation_materialization_manifest_sha256": evaluation["manifest_sha256"], "seed": seed, "query_count": len(evaluation["query_ids"]), "pipeline": pipe, "band_probe_radii": radii, "contribution_sha256": sha256(contribution_path), "mean_total_probes": float(numpy.mean(total_probes)), "mean_probes_at_proof": float(numpy.mean(proof_probes)), "mean_total_posting_visits": float(numpy.mean(visits)), "mean_posting_visits_at_proof": float(numpy.mean(proof_visits)), "mean_unique_candidates": float(numpy.mean(candidates)), "mean_full_hamming_computations": float(numpy.mean(computations)), "early_proof_fraction": float(numpy.mean(early)), "full_union_top_k_match_fraction": float(numpy.mean(matched))}; report_path.parent.mkdir(parents=True, exist_ok=True); report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"); rows.append({"seed": seed, "report_sha256": sha256(report_path), "contribution_sha256": sha256(contribution_path)})
    manifest = {"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "source_files_sha256": sources(), "source_bundle_sha256": source_bundle(sources()), "rows": rows}; (args.output_root / "matrix-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test(contract_path: Path) -> int:
    try:
        contract = load_contract(contract_path); require(schedule(contract) == [3] * 9 + [2] * 7, "radius-56 schedule differs")
        codes = numpy.asarray([[False, False], [True, False], [True, True]], dtype=bool); ranges = [(0, 1), (1, 2)]; index = mih.build_index(codes, ranges); generation = numpy.zeros(3, dtype=numpy.uint32); ids = numpy.asarray(["a", "b", "c"])
        selected, distances, diagnostic = progressive_top_k(index, codes, numpy.asarray([False, False]), ranges, [1, 0], ids, 1, generation, 1); require(selected.tolist() == [0] and distances.tolist() == [0] and diagnostic["early_proof"], "progressive strict-bound proof differs")
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
