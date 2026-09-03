#!/usr/bin/env python3
"""Run the complete nonlinear prototype-binary policy/width frontier."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
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
    with np.load(args.input, mmap_mode="r", allow_pickle=False) as source:
        reports = {}
        for policy in POLICIES:
            reports[policy] = runner.evaluate(source, contract,
                                              teacher_override=teacher,
                                              negative_policy=policy)
    result = {"schema_version": 1,
              "family": "neuroute_prototype_binary_neural_frontier",
              "input_sha256": input_sha,
              "teacher_cache_sha256": teacher_sha,
              "teacher_manifest_sha256": sha256(manifest_path),
              "contract_sha256": sha256(args.contract),
              "runner_sha256": sha256(THIS /
                                      "run-neuroute-prototype-binary-neural.py"),
              "policies": reports,
              "decision": {"full_cascade_required": True,
                           "native_mih_licensed": False,
                           "production_selection_licensed": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))


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
