#!/usr/bin/env python3
"""Run the complete nonlinear prototype-binary policy/width frontier."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import platform
import sys
from pathlib import Path
from typing import Any

import numpy as np

THIS = Path(__file__).resolve().parent
POLICIES = ("fixed_teacher_ranks", "global_random", "student_hard",
            "student_hard_x2")


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
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) +
            "\n").encode("utf-8")


def atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical(value))
    temporary.replace(path)


def load_runner() -> Any:
    path = THIS / "run-neuroute-prototype-binary-neural.py"
    spec = importlib.util.spec_from_file_location("neuroute_neural_runner", path)
    require(spec is not None and spec.loader is not None, "neural runner unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(args: argparse.Namespace) -> None:
    runner = load_runner()
    contract = runner.load_contract(args.contract)
    input_sha = sha256(args.input)
    teacher_sha = sha256(args.teacher_cache)
    manifest_path = args.teacher_manifest or args.teacher_cache.with_suffix(".json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("family") == "neuroute_prototype_fp32_teacher_cache" and
            manifest.get("source_npz_sha256") == input_sha and
            manifest.get("output_npz_sha256") == teacher_sha,
            "teacher cache provenance differs")
    with np.load(args.teacher_cache, mmap_mode="r", allow_pickle=False) as cache:
        require("teacher_top_prototypes" in cache.files,
                "teacher cache is missing teacher_top_prototypes")
        teacher = np.asarray(cache["teacher_top_prototypes"], dtype=np.int32).copy()
    contract_sha = sha256(args.contract)
    runner_path = THIS / "run-neuroute-prototype-binary-neural.py"
    runner_sha = sha256(runner_path)
    checkpoint_dir = args.checkpoint_dir or args.output.with_suffix(
        args.output.suffix + ".parts")
    policies = tuple(args.policies) if args.policies else POLICIES
    with np.load(args.input, mmap_mode="r", allow_pickle=False) as source:
        reports = {}
        for policy in policies:
            report = None
            for width_value in contract["widths"]:
                width = int(width_value)
                checkpoint = checkpoint_dir / f"{policy}-{width}.json"
                part = None
                if checkpoint.is_file():
                    candidate = json.loads(checkpoint.read_text(encoding="utf-8"))
                    if (candidate.get("input_sha256") == input_sha and
                            candidate.get("teacher_cache_sha256") == teacher_sha and
                            candidate.get("contract_sha256") == contract_sha and
                            candidate.get("runner_sha256") == runner_sha and
                            candidate.get("negative_policy") == policy and
                            set(candidate.get("widths", {})) == {str(width)}):
                        part = candidate
                if part is None:
                    cell_contract = copy.deepcopy(contract)
                    cell_contract["widths"] = [width]
                    part = runner.evaluate(source, cell_contract,
                                           teacher_override=teacher,
                                           negative_policy=policy)
                    part.update({"input_sha256": input_sha,
                                 "teacher_cache_sha256": teacher_sha,
                                 "contract_sha256": contract_sha,
                                 "runner_sha256": runner_sha})
                    atomic_write(checkpoint, part)
                if report is None:
                    report = copy.deepcopy(part)
                    report["widths"] = {}
                report["widths"][str(width)] = part["widths"][str(width)]
            reports[policy] = report
    best_by_width = {}
    candidates = []
    for width_value in contract["widths"]:
        width = str(int(width_value))
        rows = []
        for policy, report in reports.items():
            value = report["widths"][width]["partitions"]["internal"][
                "budgets"]["4096"]["teacher_prototype_recall_at_k"]
            rows.append((float(value), policy))
            candidates.append((float(value), policy, int(width)))
        best_by_width[width] = max(rows)[0]
    best_recall, best_policy, best_width = max(candidates)
    try:
        import numba
        scan_backend = {"name": "numba_njit_parallel", "version": numba.__version__}
    except ImportError:
        scan_backend = {"name": "numpy_broadcast_lookup", "version": np.__version__}
    late_frontier_promising = (
        best_width >= 96 and best_recall >= 0.9 * best_by_width["32"])
    result = {"schema_version": 1,
              "family": "neuroute_prototype_binary_neural_frontier",
              "input_sha256": input_sha,
              "teacher_cache_sha256": teacher_sha,
              "teacher_manifest_sha256": sha256(manifest_path),
              "contract_sha256": contract_sha,
              "runner_sha256": runner_sha,
              "execution": {"python": platform.python_version(),
                            "numpy": np.__version__,
                            "hamming_scan_backend": scan_backend,
                            "timing_scope": "directional_concurrent_offline_run"},
              "policies": reports,
              "summary": {"metric": "internal_teacher_prototype_recall_at_4096",
                          "best_value": best_recall,
                          "best_policy": best_policy,
                          "best_width": best_width,
                          "best_value_by_width": best_by_width,
                          "monotonic_growth_64_96_128": (
                              best_by_width["64"] < best_by_width["96"] <
                              best_by_width["128"]),
                          "late_frontier_promising": late_frontier_promising},
              "decision": {"full_cascade_required_before_product_claim": True,
                           "full_cascade_licensed_by_this_report": False,
                           "extend_to_192_256_licensed": late_frontier_promising,
                           "native_mih_licensed": False,
                           "production_selection_licensed": False}}
    atomic_write(args.output, result)


def self_test() -> None:
    require(POLICIES == ("fixed_teacher_ranks", "global_random",
                         "student_hard", "student_hard_x2"),
            "frontier policy order differs")
    print("NeuRoute prototype-binary neural frontier self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--teacher-cache", type=Path)
    parser.add_argument("--teacher-manifest", type=Path)
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-prototype-binary-neural.example.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--checkpoint-dir", type=Path)
    parser.add_argument("--policies", nargs="+", choices=POLICIES,
                        help="subset to run; useful for independent resumable jobs")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        require(args.input is not None and args.teacher_cache is not None and
                args.output is not None, "input, teacher-cache and output are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"run-neuroute-prototype-binary-neural-frontier: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
