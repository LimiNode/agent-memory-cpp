#!/usr/bin/env python3
"""Run the predeclared m=16 versus m=19 fixed-radius MIH reference matrix."""

from __future__ import annotations

import argparse
import concurrent.futures
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
THIS = Path(__file__).resolve()
FAMILY = "mih_arbitrary_m_reference_fixed_radius_v1"


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


shared = load("evaluate-projection-quantization.py", "arbitrary_m_shared")
evaluator = load("evaluate-mih-banding.py", "arbitrary_m_evaluator")


def source_files() -> dict[str, str]:
    return {name: sha256(THIS.with_name(name)) for name in (THIS.name, "mih-arbitrary-m-reference.example.json", "evaluate-mih-banding.py", "evaluate-projection-quantization.py")}


def source_bundle(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") == FAMILY, "arbitrary-m contract identity differs")
    require(value.get("seeds") == [52, 53, 54, 55, 56], "arbitrary-m seed set differs")
    pipeline = value.get("pipeline")
    require(pipeline == {"code_bits": 256, "global_radius": 56, "hamming_limit": 768, "candidate_limit": 512, "second_stage": "binary-adc", "second_limit": 256, "oracle_k": 10}, "arbitrary-m pipeline differs")
    treatments = value.get("treatments")
    require(isinstance(treatments, list) and len(treatments) == 2, "arbitrary-m treatments differ")
    expected = {
        "m16-current-radius-sum": ([16] * 16, [4] * 8 + [3] * 8, "sum_local_radii_equals_global_radius_v1"),
        "m19-uniform-radius2": ([14] * 9 + [13] * 10, [2] * 19, "pigeonhole_all_bands_exceed_radius_implies_distance_at_least_57_v1"),
    }
    for treatment in treatments:
        identifier = treatment.get("id")
        require(identifier in expected, "arbitrary-m treatment identifier differs")
        widths, radii, coverage = expected[identifier]
        require(treatment == {"id": identifier, "band_count": len(widths), "widths": widths, "local_radii": radii, "exact_coverage": coverage}, "arbitrary-m treatment differs")
        require(sum(widths) == 256 and all(0 <= radius <= width for radius, width in zip(radii, widths)), "arbitrary-m band dimensions differ")
        require(sum(radius + 1 for radius in radii) > pipeline["global_radius"], "arbitrary-m fixed-radius coverage is not provable")
    require(value.get("decision_rule") == {"primary": "exact_hamming_radius56_candidate_and_work_comparison", "bootstrap_replicates": 10000, "bootstrap_seed": 20260814, "held_out_selection": "none"}, "arbitrary-m decision rule differs")
    return value


def rows(contract: dict[str, Any]) -> list[dict[str, Any]]:
    result = [{"id": f"{treatment['id']}-seed{seed}", "treatment": treatment, "seed": seed} for treatment in contract["treatments"] for seed in contract["seeds"]]
    require(len(result) == 10 and len({row["id"] for row in result}) == 10, "arbitrary-m matrix expansion differs")
    return result


def contribution_fields() -> set[str]:
    return {"hamming_top_k_recall", "coverage_at_candidate_limit", "reranked_ndcg_at_10", "full_e5_ndcg_at_10", "candidate_count", "exact_bucket_floor_candidate_count", "bucket_probe_count", "posting_visit_count", "e5_oracle_raw_union_coverage", "e5_oracle_hamming_top_k_coverage", "e5_oracle_second_stage_coverage", "e5_oracle_mean_full_hamming_distance", "e5_oracle_hamming_within_48", "e5_oracle_hamming_within_56", "e5_oracle_hamming_within_64", "probe_count_by_flip_depth", "posting_visit_count_by_flip_depth", "stop_reason", "query_ids", "identity_json"}


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
        require(set(values) == contribution_fields(), "arbitrary-m contribution fields differ")
        require(all(values[name].shape == (count,) for name in contribution_fields() - {"query_ids", "identity_json", "probe_count_by_flip_depth", "posting_visit_count_by_flip_depth"}), "arbitrary-m contribution shapes differ")
        require(values["query_ids"].shape == (count,) and values["probe_count_by_flip_depth"].shape == (count, 3) and values["posting_visit_count_by_flip_depth"].shape == (count, 3), "arbitrary-m contribution shape differs")
        identity = json.loads(str(values["identity_json"].item()))
        expected_identity = shared.contribution_identity(evaluation, pipeline["candidate_limit"], pipeline["oracle_k"])
        survival = {"raw_union": float(numpy.mean(values["e5_oracle_raw_union_coverage"])), "hamming_top_k": float(numpy.mean(values["e5_oracle_hamming_top_k_coverage"])), "second_stage": float(numpy.mean(values["e5_oracle_second_stage_coverage"])), "mean_full_hamming_distance": float(numpy.mean(values["e5_oracle_mean_full_hamming_distance"])), "hamming_within_radius": {str(radius): float(numpy.mean(values[f"e5_oracle_hamming_within_{radius}"])) for radius in (48, 56, 64)}}
        probes = sum(sum(__import__("math").comb(width, depth) for depth in range(radius + 1)) for width, radius in zip(treatment["widths"], treatment["local_radii"]))
        return bool(
            report.get("schema_version") == 6 and report.get("family") == "mih_banding_reference_v6"
            and report.get("evaluator_source_files_sha256") == {name: source_files()[name] for name in ("evaluate-mih-banding.py", "evaluate-projection-quantization.py")}
            and report.get("evaluator_source_bundle_sha256") == evaluator.source_bundle_sha256(report["evaluator_source_files_sha256"])
            and report.get("code_bits") == 256 and report.get("band_count") == treatment["band_count"] and report.get("band_width_bits") == treatment["widths"]
            and report.get("global_radius") == pipeline["global_radius"] and report.get("band_probe_radii") == treatment["local_radii"] and report.get("fixed_radius_exact_guarantee") is True
            and report.get("probe_policy") == "uniform-radius" and report.get("hamming_policy") == "uniform" and report.get("seed") == row["seed"] and report.get("itq_iterations") == 50
            and report.get("candidate_limit") == pipeline["candidate_limit"] and report.get("hamming_limit") == pipeline["hamming_limit"] and report.get("second_stage") == pipeline["second_stage"] and report.get("second_limit") == pipeline["second_limit"] and report.get("oracle_k") == pipeline["oracle_k"]
            and report.get("query_count") == count and report.get("mean_bucket_probes_per_query") == float(probes)
            and report.get("calibration_materialization_manifest_sha256") == calibration["manifest_sha256"] and report.get("evaluation_materialization_manifest_sha256") == evaluation["manifest_sha256"]
            and report.get("per_query_contributions_sha256") == sha256(contribution_path) and report.get("per_query_contribution_identity") == identity == expected_identity
            and report.get("mean_candidates_per_query") == float(numpy.mean(values["candidate_count"])) and report.get("mean_posting_visits_per_query") == float(numpy.mean(values["posting_visit_count"])) and report.get("e5_oracle_survival") == survival
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def run(args: Any) -> None:
    contract = load_contract(args.contract)
    calibration, evaluation = shared.load_root(args.calibration_root), shared.load_root(args.evaluation_root)
    shared.validate_calibration_evaluation_pair(calibration, evaluation)
    require(calibration["manifest_sha256"] == contract["training_materialization_manifest_sha256"] and evaluation["manifest_sha256"] == contract["held_out_evaluation_manifest_sha256"], "arbitrary-m materialization provenance differs")
    require(len(calibration["train_ids"]) == 25000 and len(evaluation["document_ids"]) == 22607 and len(evaluation["query_ids"]) == 1252, "arbitrary-m materialization cardinality differs")
    matrix = rows(contract); environment = os.environ.copy(); environment.update({name: "1" for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")})
    def execute(number: int, row: dict[str, Any]) -> None:
        if args.resume and complete(args.output_root, row, contract, calibration, evaluation): return
        report = args.output_root / "reports" / f"{row['id']}.json"; contribution = args.output_root / "contributions" / f"{row['id']}.npz"; report.parent.mkdir(parents=True, exist_ok=True); contribution.parent.mkdir(parents=True, exist_ok=True)
        treatment, pipeline = row["treatment"], contract["pipeline"]
        command = [str(args.python), str(THIS.with_name("evaluate-mih-banding.py")), "evaluate", "--calibration-root", str(args.calibration_root), "--evaluation-root", str(args.evaluation_root), "--output", str(report), "--contributions-output", str(contribution), "--code-bits", "256", "--band-count", str(treatment["band_count"]), "--band-widths", ",".join(map(str, treatment["widths"])), "--band-probe-radii", ",".join(map(str, treatment["local_radii"])), "--global-radius", str(pipeline["global_radius"]), "--probe-policy", "uniform-radius", "--hamming-policy", "uniform", "--seed", str(row["seed"]), "--itq-iterations", "50", "--candidate-limit", str(pipeline["candidate_limit"]), "--hamming-limit", str(pipeline["hamming_limit"]), "--second-stage", pipeline["second_stage"], "--second-limit", str(pipeline["second_limit"]), "--oracle-k", str(pipeline["oracle_k"])]
        print(f"[{number}/{len(matrix)}] evaluate {row['id']}", flush=True); subprocess.run(command, check=True, env=environment)
        require(complete(args.output_root, row, contract, calibration, evaluation), f"invalid arbitrary-m evaluator output: {row['id']}")
    require(args.jobs > 0, "arbitrary-m job count is invalid")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for future in concurrent.futures.as_completed([pool.submit(execute, number, row) for number, row in enumerate(matrix, 1)]): future.result()
    entries = [{"id": row["id"], "treatment": row["treatment"]["id"], "seed": row["seed"], "report_sha256": sha256(args.output_root / "reports" / f"{row['id']}.json"), "contribution_sha256": sha256(args.output_root / "contributions" / f"{row['id']}.npz")} for row in matrix]
    manifest = {"schema_version": 1, "family": FAMILY, "contract_sha256": sha256(args.contract), "calibration_materialization_manifest_sha256": calibration["manifest_sha256"], "evaluation_materialization_manifest_sha256": evaluation["manifest_sha256"], "source_files_sha256": source_files(), "source_bundle_sha256": source_bundle(source_files()), "rows": entries}
    (args.output_root / "matrix-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test(contract_path: Path) -> int:
    try:
        contract = load_contract(contract_path); require(len(rows(contract)) == 10, "arbitrary-m matrix count differs")
        m19 = contract["treatments"][1]; require(sum(radius + 1 for radius in m19["local_radii"]) == 57, "m19 pigeonhole lower bound differs")
        require(sum(sum(__import__("math").comb(width, depth) for depth in range(radius + 1)) for width, radius in zip(m19["widths"], m19["local_radii"])) == 1874, "m19 probe count differs")
        changed = json.loads(json.dumps(contract)); changed["treatments"][1]["local_radii"][0] = 1
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "changed.json"; path.write_text(json.dumps(changed), encoding="utf-8")
            try: load_contract(path)
            except ValueError: pass
            else: raise ValueError("changed arbitrary-m contract was accepted")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"run-mih-arbitrary-m-reference self-test failed: {error}", file=sys.stderr); return 1
    print("MIH arbitrary-m reference self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run"); run_parser.add_argument("--contract", type=Path, required=True); run_parser.add_argument("--calibration-root", type=Path, required=True); run_parser.add_argument("--evaluation-root", type=Path, required=True); run_parser.add_argument("--output-root", type=Path, required=True); run_parser.add_argument("--python", type=Path, default=Path(sys.executable)); run_parser.add_argument("--jobs", type=int, default=1); run_parser.add_argument("--resume", action="store_true")
    test = sub.add_parser("self-test"); test.add_argument("--contract", type=Path, required=True); args = parser.parse_args(argv)
    try: return self_test(args.contract) if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error: print(f"run-mih-arbitrary-m-reference: {error}", file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
