#!/usr/bin/env python3
"""Run the frozen query-adaptive ADC best-first MIH matrix."""
from __future__ import annotations
import argparse, concurrent.futures, hashlib, importlib.util, json, os, subprocess, sys, tempfile
from pathlib import Path
from typing import Any

VARIANTS = ("budgeted-confidence", "adc-best-first-2", "adc-best-first-3")
BUDGETS = ((8192, 11000), (12288, 19000), (16384, 30000))
SEEDS = (42, 43, 44, 45, 46)

def require(value: bool, message: str) -> None:
    if not value: raise ValueError(message)
def sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def load_shared() -> Any:
    path = Path(__file__).with_name("evaluate-projection-quantization.py")
    spec = importlib.util.spec_from_file_location("mih_best_first_shared", path)
    if spec is None or spec.loader is None: raise RuntimeError("cannot load shared evaluator")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
def evaluator_sources() -> dict[str, str]:
    root = Path(__file__).parent
    return {name: sha256(root / name) for name in ("evaluate-mih-banding.py", "evaluate-projection-quantization.py")}
def source_bundle(files: dict[str, str]) -> str: return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
def load_matrix(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8")); e = value.get("evaluation", {})
    require(set(value) == {"schema_version", "family", "evaluation"} and value["schema_version"] == 1 and value["family"] == "mih_adc_best_first_matrix_v1", "matrix identity is invalid")
    require(e["code_bits"] == 256 and e["band_count"] == 32 and e["probe_radius"] == 1 and tuple(e["variants"]) == VARIANTS and tuple(map(tuple, e["budgets"])) == BUDGETS and e["hamming_limit"] == 768 and e["second_limit"] == 256 and e["second_stage"] == "binary-adc" and e["oracle_k"] == 10 and e["candidate_limit"] == 512 and e["itq_iterations"] == 50 and tuple(e["itq_seeds"]) == SEEDS, "matrix contract is invalid")
    return value
def rows(matrix: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    load = matrix["evaluation"]
    result = [(f"mih256-{variant}-target{candidate}-p{postings}-h768-adc256-seed{seed}", {"variant": variant, "candidate": candidate, "postings": postings, "seed": seed}) for variant in load["variants"] for candidate, postings in load["budgets"] for seed in load["itq_seeds"]]
    require(len(result) == 45 and len({name for name, _ in result}) == 45, "matrix rows are invalid"); return result
def complete(report_path: Path, contribution: Path, row: dict[str, Any], calibration: dict[str, Any], evaluation: dict[str, Any]) -> bool:
    if not report_path.is_file() or not contribution.is_file(): return False
    try: report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return False
    policy = "budgeted-confidence" if row["variant"] == "budgeted-confidence" else "budgeted-adc-best-first"
    flips = None if row["variant"] == "budgeted-confidence" else int(row["variant"][-1])
    sources = evaluator_sources(); shared = load_shared()
    return report.get("schema_version") == 6 and report.get("family") == "mih_banding_reference_v6" and report.get("code_bits") == 256 and report.get("band_count") == 32 and report.get("band_width_bits") == [8] * 32 and report.get("probe_radius") == (flips or 1) and report.get("base_probe_radius") == 1 and report.get("probe_policy") == policy and report.get("soft_candidate_target") == row["candidate"] and report.get("soft_posting_visit_target") == row["postings"] and report.get("max_probe_bit_flips") == flips and report.get("hamming_policy") == "uniform" and report.get("hamming_limit") == 768 and report.get("second_limit") == 256 and report.get("second_stage") == "binary-adc" and report.get("seed") == row["seed"] and report.get("query_count") == len(evaluation["query_ids"]) and report.get("calibration_materialization_manifest_sha256") == calibration["manifest_sha256"] and report.get("evaluation_materialization_manifest_sha256") == evaluation["manifest_sha256"] and report.get("calibration_train_ids_sha256") == shared.ordered_ids_sha256(calibration["train_ids"]) and report.get("calibration_vector_count") == len(calibration["train_ids"]) and report.get("evaluator_source_files_sha256") == sources and report.get("evaluator_source_bundle_sha256") == source_bundle(sources) and report.get("per_query_contribution_identity") == shared.contribution_identity(evaluation, 512, 10) and report.get("per_query_contributions_sha256") == sha256(contribution)
def run(args: Any) -> None:
    matrix_rows = rows(load_matrix(args.matrix)); shared = load_shared(); calibration = shared.load_root(args.calibration_root); evaluation = shared.load_root(args.evaluation_root); evaluator = Path(__file__).with_name("evaluate-mih-banding.py"); env = os.environ.copy()
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"): env[key] = "1"
    def execute(index: int, name: str, row: dict[str, Any]) -> None:
        report = args.output_root / "reports" / f"{name}.json"; contribution = args.output_root / "contributions" / f"{name}.npz"
        if args.resume and complete(report, contribution, row, calibration, evaluation): return
        report.parent.mkdir(parents=True, exist_ok=True); contribution.parent.mkdir(parents=True, exist_ok=True)
        policy = "budgeted-confidence" if row["variant"] == "budgeted-confidence" else "budgeted-adc-best-first"
        command = [str(args.python), str(evaluator), "evaluate", "--calibration-root", str(args.calibration_root), "--evaluation-root", str(args.evaluation_root), "--output", str(report), "--contributions-output", str(contribution), "--code-bits", "256", "--band-count", "32", "--probe-radius", "1", "--probe-policy", policy, "--soft-candidate-target", str(row["candidate"]), "--soft-posting-visit-target", str(row["postings"]), "--hamming-policy", "uniform", "--seed", str(row["seed"]), "--itq-iterations", "50", "--candidate-limit", "512", "--hamming-limit", "768", "--second-limit", "256", "--second-stage", "binary-adc", "--oracle-k", "10"]
        if policy == "budgeted-adc-best-first": command += ["--max-probe-bit-flips", row["variant"][-1]]
        print(f"[{index}/45] {name}", flush=True); subprocess.run(command, check=True, env=env); require(complete(report, contribution, row, calibration, evaluation), f"invalid completed row: {name}")
    require(args.jobs > 0, "job count is invalid")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        for future in concurrent.futures.as_completed([executor.submit(execute, i, n, r) for i, (n, r) in enumerate(matrix_rows, 1)]): future.result()
def self_test(path: Path) -> int:
    try:
        require(len(rows(load_matrix(path))) == 45, "row count is invalid")
        with tempfile.TemporaryDirectory() as directory:
            value = json.loads(path.read_text(encoding="utf-8")); value["evaluation"]["budgets"] = [[12288, 19000]]; invalid = Path(directory) / "invalid.json"; invalid.write_text(json.dumps(value), encoding="utf-8")
            try: rows(load_matrix(invalid))
            except ValueError: pass
            else: raise ValueError("incomplete budget grid was accepted")
    except (OSError, ValueError, json.JSONDecodeError) as error: print(f"run-mih-adc-best-first-matrix self-test failed: {error}", file=sys.stderr); return 1
    print("MIH ADC best-first matrix self-test passed"); return 0
def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True); run_parser = sub.add_parser("run");
    for flag in ("matrix", "calibration-root", "evaluation-root", "output-root"): run_parser.add_argument(f"--{flag}", type=Path, required=True)
    run_parser.add_argument("--python", type=Path, default=Path(sys.executable)); run_parser.add_argument("--jobs", type=int, default=1); run_parser.add_argument("--resume", action="store_true"); test = sub.add_parser("self-test"); test.add_argument("--matrix", type=Path, required=True); args = parser.parse_args(argv)
    try: return self_test(args.matrix) if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error: print(f"run-mih-adc-best-first-matrix: {error}", file=sys.stderr); return 1
if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
