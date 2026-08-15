#!/usr/bin/env python3
"""Run the exploratory canonical minimum-probe arbitrary-m MIH frontier."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()
FAMILY = "mih_canonical_arbitrary_m_frontier_v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name: str, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, THIS.with_name(name))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


shared = load("evaluate-projection-quantization.py", "canonical_arbitrary_m_shared")
evaluator = load("evaluate-mih-banding.py", "canonical_arbitrary_m_evaluator")


def source_files() -> dict[str, str]:
    names = (THIS.name, "mih-canonical-arbitrary-m-frontier.example.json", "evaluate-mih-banding.py", "evaluate-projection-quantization.py")
    return {name: sha256(THIS.with_name(name)) for name in names}


def source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def near_equal_widths(code_bits: int, band_count: int) -> list[int]:
    narrow, remainder = divmod(code_bits, band_count)
    return [narrow + 1] * remainder + [narrow] * (band_count - remainder)


def local_key_count(width: int, radius: int) -> int:
    return sum(math.comb(width, depth) for depth in range(radius + 1))


def minimum_probe_radii(widths: list[int], global_radius: int) -> list[int]:
    """Return a deterministic exact-coverage schedule with the fewest keys."""
    target = global_radius + 1 - len(widths)
    require(target >= 0, "minimum-probe target is invalid")
    states: dict[int, tuple[int, tuple[int, ...]]] = {0: (0, ())}
    for width in widths:
        next_states: dict[int, tuple[int, tuple[int, ...]]] = {}
        for accumulated, value in states.items():
            for radius in range(width + 1):
                total = accumulated + radius
                if total > target:
                    break
                candidate = (value[0] + local_key_count(width, radius), value[1] + (radius,))
                current = next_states.get(total)
                if current is None or candidate[0] < current[0] or (candidate[0] == current[0] and candidate[1] > current[1]):
                    next_states[total] = candidate
        states = next_states
    require(target in states, "minimum-probe schedule cannot meet exact coverage")
    radii = list(states[target][1])
    require(sum(radius + 1 for radius in radii) == global_radius + 1, "minimum-probe coverage differs")
    return radii


def treatments(contract: dict[str, Any]) -> list[dict[str, Any]]:
    pipeline = contract["pipeline"]
    result = []
    for band_count in contract["m_values"]:
        widths = near_equal_widths(pipeline["code_bits"], band_count)
        radii = minimum_probe_radii(widths, pipeline["global_radius"])
        result.append({
            "id": f"m{band_count}-minimum-probe-r56",
            "band_count": band_count,
            "widths": widths,
            "local_radii": radii,
            "exact_coverage": "pigeonhole_all_bands_exceed_schedule_implies_distance_at_least_57_v1",
            "local_key_count": sum(local_key_count(width, radius) for width, radius in zip(widths, radii)),
        })
    return result


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY, "canonical arbitrary-m contract identity differs")
    require(value.get("seeds") == [52, 53, 54, 55, 56], "canonical arbitrary-m seed set differs")
    require(value.get("m_values") == list(range(15, 22)), "canonical arbitrary-m m values differ")
    require(value.get("pipeline") == {"code_bits": 256, "global_radius": 56, "hamming_limit": 768, "candidate_limit": 512, "second_stage": "binary-adc", "second_limit": 256, "oracle_k": 10}, "canonical arbitrary-m pipeline differs")
    require(value.get("schedule_rule") == {"name": "near_equal_width_minimum_enumerated_keys", "coverage": "sum_local_radius_plus_one_equals_57", "tie_break": "reverse_lexicographic_radius_vector_descending_widths"}, "canonical arbitrary-m schedule rule differs")
    require(value.get("decision_rule") == {"scope": "exploratory_observed_evaluation_frontier", "bootstrap_replicates": 10000, "bootstrap_seed": 20260815, "production_selection": "forbidden", "confirmatory_held_out_claim": "forbidden"}, "canonical arbitrary-m decision rule differs")
    return value


def rows(contract: dict[str, Any]) -> list[dict[str, Any]]:
    result = [{"id": f"{treatment['id']}-seed{seed}", "treatment": treatment, "seed": seed} for treatment in treatments(contract) for seed in contract["seeds"]]
    require(len(result) == 35 and len({row["id"] for row in result}) == 35, "canonical arbitrary-m matrix expansion differs")
    return result


def contribution_fields() -> set[str]:
    return {"hamming_top_k_recall", "coverage_at_candidate_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10", "candidate_count", "exact_bucket_floor_candidate_count", "bucket_probe_count", "posting_visit_count", "e5_oracle_raw_union_coverage", "e5_oracle_hamming_top_k_coverage", "e5_oracle_second_stage_coverage", "e5_oracle_mean_full_hamming_distance", "e5_oracle_hamming_within_48", "e5_oracle_hamming_within_56", "e5_oracle_hamming_within_64", "probe_count_by_flip_depth", "posting_visit_count_by_flip_depth", "stop_reason", "query_ids", "identity_json"}


def contribution_summary(values: dict[str, Any]) -> dict[str, Any]:
    mean = lambda name: float(numpy.mean(values[name]))
    return {
        "hamming_top_k_recall": mean("hamming_top_k_recall"),
        "exact_top_k_candidate_coverage": mean("coverage_at_candidate_limit"),
        "reranked_ndcg_at_10": mean("reranked_ndcg_at_10"),
        "full_e5_ndcg_at_10": mean("full_e5_ndcg_at_10"),
        "mean_candidates_per_query": mean("candidate_count"),
        "mean_exact_bucket_floor_candidates_per_query": mean("exact_bucket_floor_candidate_count"),
        "mean_bucket_probes_per_query": mean("bucket_probe_count"),
        "mean_posting_visits_per_query": mean("posting_visit_count"),
        "mean_full_hamming_scores_per_query": mean("candidate_count"),
        "e5_oracle_survival": {
            "raw_union": mean("e5_oracle_raw_union_coverage"),
            "hamming_top_k": mean("e5_oracle_hamming_top_k_coverage"),
            "second_stage": mean("e5_oracle_second_stage_coverage"),
            "mean_full_hamming_distance": mean("e5_oracle_mean_full_hamming_distance"),
            "hamming_within_radius": {str(radius): mean(f"e5_oracle_hamming_within_{radius}") for radius in (48, 56, 64)},
        },
        "mean_probe_count_by_flip_depth": [float(numpy.mean(values["probe_count_by_flip_depth"][:, depth])) for depth in range(3)],
        "mean_posting_visits_by_flip_depth": [float(numpy.mean(values["posting_visit_count_by_flip_depth"][:, depth])) for depth in range(3)],
        "stop_reason_fractions": {reason: float(numpy.mean(values["stop_reason"] == reason)) for reason in ("candidate", "posting", "exhausted", "fixed-radius")},
    }


def report_matches_contribution_summary(report: dict[str, Any], summary: dict[str, Any]) -> bool:
    return all(report.get(name) == value for name, value in summary.items())


def complete(root: Path, row: dict[str, Any], contract: dict[str, Any], calibration: dict[str, Any], evaluation: dict[str, Any]) -> bool:
    report_path = root / "reports" / f"{row['id']}.json"
    contribution_path = root / "contributions" / f"{row['id']}.npz"
    if not report_path.is_file() or not contribution_path.is_file():
        return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        with numpy.load(contribution_path, allow_pickle=False) as archive:
            values = {name: archive[name].copy() for name in archive.files}
        treatment, pipeline, count = row["treatment"], contract["pipeline"], len(evaluation["query_ids"])
        require(set(values) == contribution_fields(), "canonical arbitrary-m contribution fields differ")
        scalar_fields = contribution_fields() - {"query_ids", "identity_json", "probe_count_by_flip_depth", "posting_visit_count_by_flip_depth"}
        require(all(values[name].shape == (count,) for name in scalar_fields), "canonical arbitrary-m contribution shapes differ")
        require(values["query_ids"].shape == (count,) and values["probe_count_by_flip_depth"].shape == (count, 3) and values["posting_visit_count_by_flip_depth"].shape == (count, 3), "canonical arbitrary-m contribution shape differs")
        identity = json.loads(str(values["identity_json"].item()))
        expected_identity = shared.contribution_identity(evaluation, pipeline["candidate_limit"], pipeline["oracle_k"])
        summary = contribution_summary(values)
        return bool(
            report.get("schema_version") == 6 and report.get("family") == "mih_banding_reference_v6"
            and report.get("evaluator_source_files_sha256") == {name: source_files()[name] for name in ("evaluate-mih-banding.py", "evaluate-projection-quantization.py")}
            and report.get("evaluator_source_bundle_sha256") == evaluator.source_bundle_sha256(report["evaluator_source_files_sha256"])
            and report.get("code_bits") == 256 and report.get("band_count") == treatment["band_count"] and report.get("band_width_bits") == treatment["widths"]
            and report.get("global_radius") == pipeline["global_radius"] and report.get("band_probe_radii") == treatment["local_radii"] and report.get("fixed_radius_exact_guarantee") is True
            and report.get("probe_policy") == "uniform-radius" and report.get("hamming_policy") == "uniform" and report.get("seed") == row["seed"] and report.get("itq_iterations") == 50
            and report.get("candidate_limit") == pipeline["candidate_limit"] and report.get("hamming_limit") == pipeline["hamming_limit"] and report.get("second_stage") == pipeline["second_stage"] and report.get("second_limit") == pipeline["second_limit"] and report.get("oracle_k") == pipeline["oracle_k"]
            and report.get("query_count") == count and summary["mean_bucket_probes_per_query"] == float(treatment["local_key_count"])
            and report.get("calibration_materialization_manifest_sha256") == calibration["manifest_sha256"] and report.get("evaluation_materialization_manifest_sha256") == evaluation["manifest_sha256"]
            and report.get("per_query_contributions_sha256") == sha256(contribution_path) and report.get("per_query_contribution_identity") == identity == expected_identity
            and numpy.array_equal(values["query_ids"], numpy.asarray(evaluation["query_ids"]))
            and numpy.all(values["probe_count_by_flip_depth"] == 0) and numpy.all(values["posting_visit_count_by_flip_depth"] == 0) and numpy.all(values["stop_reason"] == "fixed-radius")
            and report_matches_contribution_summary(report, summary)
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def run(args: Any) -> None:
    contract = load_contract(args.contract)
    calibration, evaluation = shared.load_root(args.calibration_root), shared.load_root(args.evaluation_root)
    shared.validate_calibration_evaluation_pair(calibration, evaluation)
    require(calibration["manifest_sha256"] == contract["training_materialization_manifest_sha256"] and evaluation["manifest_sha256"] == contract["held_out_evaluation_manifest_sha256"], "canonical arbitrary-m materialization provenance differs")
    require(len(calibration["train_ids"]) == 25000 and len(evaluation["document_ids"]) == 22607 and len(evaluation["query_ids"]) == 1252, "canonical arbitrary-m materialization cardinality differs")
    matrix = rows(contract)
    environment = os.environ.copy()
    environment.update({name: "1" for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")})

    def execute(number: int, row: dict[str, Any]) -> None:
        if args.resume and complete(args.output_root, row, contract, calibration, evaluation):
            return
        report = args.output_root / "reports" / f"{row['id']}.json"
        contribution = args.output_root / "contributions" / f"{row['id']}.npz"
        report.parent.mkdir(parents=True, exist_ok=True)
        contribution.parent.mkdir(parents=True, exist_ok=True)
        treatment, pipeline = row["treatment"], contract["pipeline"]
        command = [str(args.python), str(THIS.with_name("evaluate-mih-banding.py")), "evaluate", "--calibration-root", str(args.calibration_root), "--evaluation-root", str(args.evaluation_root), "--output", str(report), "--contributions-output", str(contribution), "--code-bits", "256", "--band-count", str(treatment["band_count"]), "--band-widths", ",".join(map(str, treatment["widths"])), "--band-probe-radii", ",".join(map(str, treatment["local_radii"])), "--global-radius", str(pipeline["global_radius"]), "--probe-policy", "uniform-radius", "--hamming-policy", "uniform", "--seed", str(row["seed"]), "--itq-iterations", "50", "--candidate-limit", str(pipeline["candidate_limit"]), "--hamming-limit", str(pipeline["hamming_limit"]), "--second-stage", pipeline["second_stage"], "--second-limit", str(pipeline["second_limit"]), "--oracle-k", str(pipeline["oracle_k"])]
        print(f"[{number}/{len(matrix)}] evaluate {row['id']}", flush=True)
        subprocess.run(command, check=True, env=environment)
        require(complete(args.output_root, row, contract, calibration, evaluation), f"invalid canonical arbitrary-m evaluator output: {row['id']}")

    require(args.jobs > 0, "canonical arbitrary-m job count is invalid")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for future in concurrent.futures.as_completed([pool.submit(execute, number, row) for number, row in enumerate(matrix, 1)]):
            future.result()
    entries = [{"id": row["id"], "treatment": row["treatment"]["id"], "seed": row["seed"], "report_sha256": sha256(args.output_root / "reports" / f"{row['id']}.json"), "contribution_sha256": sha256(args.output_root / "contributions" / f"{row['id']}.npz")} for row in matrix]
    manifest_path = args.output_root / "matrix-manifest.json"
    if args.resume and manifest_path.is_file():
        historical = json.loads(manifest_path.read_text(encoding="utf-8"))
        require(historical.get("schema_version") == 1 and historical.get("family") == FAMILY and historical.get("contract_sha256") == sha256(args.contract), "canonical arbitrary-m historical matrix manifest differs")
        require(historical.get("calibration_materialization_manifest_sha256") == calibration["manifest_sha256"] and historical.get("evaluation_materialization_manifest_sha256") == evaluation["manifest_sha256"] and historical.get("rows") == entries, "canonical arbitrary-m historical matrix rows differ")
        return
    manifest = {"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "calibration_materialization_manifest_sha256": calibration["manifest_sha256"], "evaluation_materialization_manifest_sha256": evaluation["manifest_sha256"], "source_files_sha256": source_files(), "source_bundle_sha256": source_bundle(source_files()), "rows": entries}
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test(contract_path: Path) -> int:
    try:
        contract = load_contract(contract_path)
        values = treatments(contract)
        require(len(rows(contract)) == 35 and [value["band_count"] for value in values] == list(range(15, 22)), "canonical arbitrary-m matrix differs")
        m16 = values[1]
        m19 = values[4]
        require(m16["widths"] == [16] * 16 and m16["local_radii"] == [3] * 9 + [2] * 7 and m16["local_key_count"] == 7232, "m16 minimum-probe schedule differs")
        require(m19["widths"] == [14] * 9 + [13] * 10 and m19["local_radii"] == [2] * 19 and m19["local_key_count"] == 1874, "m19 minimum-probe schedule differs")
        changed = json.loads(json.dumps(contract))
        changed["m_values"] = list(range(16, 22))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "changed.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            try:
                load_contract(path)
            except ValueError:
                pass
            else:
                raise ValueError("changed canonical arbitrary-m contract was accepted")
        values = {name: numpy.asarray([0.0, 1.0]) for name in contribution_fields() - {"query_ids", "identity_json", "probe_count_by_flip_depth", "posting_visit_count_by_flip_depth", "stop_reason"}}
        values.update({"query_ids": numpy.asarray(["q0", "q1"]), "identity_json": numpy.asarray("{}"), "probe_count_by_flip_depth": numpy.zeros((2, 3), dtype=numpy.int32), "posting_visit_count_by_flip_depth": numpy.zeros((2, 3), dtype=numpy.int32), "stop_reason": numpy.asarray(["fixed-radius", "fixed-radius"])})
        summary = contribution_summary(values)
        require(report_matches_contribution_summary(summary, summary), "canonical arbitrary-m contribution summary differs")
        mutated = dict(summary); mutated["reranked_ndcg_at_10"] = 0.0
        require(not report_matches_contribution_summary(mutated, summary), "canonical arbitrary-m nDCG mutation was accepted")
        mutated = dict(summary); mutated["mean_bucket_probes_per_query"] = 0.0
        require(not report_matches_contribution_summary(mutated, summary), "canonical arbitrary-m probe mutation was accepted")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"run-mih-canonical-arbitrary-m-frontier self-test failed: {error}", file=sys.stderr)
        return 1
    print("MIH canonical arbitrary-m frontier self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--contract", type=Path, required=True)
    run_parser.add_argument("--calibration-root", type=Path, required=True)
    run_parser.add_argument("--evaluation-root", type=Path, required=True)
    run_parser.add_argument("--output-root", type=Path, required=True)
    run_parser.add_argument("--python", type=Path, default=Path(sys.executable))
    run_parser.add_argument("--jobs", type=int, default=1)
    run_parser.add_argument("--resume", action="store_true")
    test = sub.add_parser("self-test")
    test.add_argument("--contract", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return self_test(args.contract) if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"run-mih-canonical-arbitrary-m-frontier: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
