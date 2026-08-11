#!/usr/bin/env python3
"""Paired-query bootstrap for predeclared MIH ADC-ceiling cells."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import numpy


HAMMING_LIMITS = (512, 768, 1024, 1536)
SECOND_LIMITS = (64, 128, 256, 512)
SECOND_STAGES = ("hamming", "binary-adc", "continuous-itq-projection-l2", "exact-e5-within-hamming")
REPLICATES = 10000
SEED = 20260811


def load_shared() -> Any:
    path = Path(__file__).with_name("evaluate-projection-quantization.py")
    spec = importlib.util.spec_from_file_location("mih_adc_ceiling_bootstrap_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load projection helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shared = load_shared()
EvaluationError = shared.EvaluationError


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources() -> dict[str, str]:
    root = Path(__file__).parent
    return {name: sha256(root / name) for name in (Path(__file__).name, "evaluate-projection-quantization.py")}


def bundle(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_values(path: Path) -> tuple[dict[str, numpy.ndarray], dict[str, Any]]:
    required = {
        "raw_union_oracle_survival", "hamming_oracle_survival", "second_oracle_survival",
        "candidate_count", "posting_visit_count", "bucket_probe_count",
        "exact_bucket_floor_candidate_count", "probe_count_by_flip_depth",
        "posting_visit_count_by_flip_depth", "stop_reason", "query_ids", "identity_json",
    }
    with numpy.load(path, allow_pickle=False) as archive:
        if set(archive.files) != required:
            raise EvaluationError("ceiling contribution fields are invalid")
        values = {name: archive[name].copy() for name in archive.files}
    count = values["query_ids"].shape[0]
    if count != 1252 or values["second_oracle_survival"].shape != (4, 4, 4, count) or values["hamming_oracle_survival"].shape != (4, count) or any(values[name].shape != (count,) for name in ("raw_union_oracle_survival", "candidate_count", "posting_visit_count", "bucket_probe_count", "exact_bucket_floor_candidate_count", "stop_reason")) or values["probe_count_by_flip_depth"].shape != (count, 3) or values["posting_visit_count_by_flip_depth"].shape != (count, 3):
        raise EvaluationError("ceiling contribution shape is invalid")
    identity = json.loads(str(values["identity_json"].item()))
    shared.validate_contribution_identity(identity, values["query_ids"], count)
    return values, identity


def cell(values: dict[str, numpy.ndarray], hamming_limit: int, second_limit: int, stage: str) -> numpy.ndarray:
    try:
        return values["second_oracle_survival"][HAMMING_LIMITS.index(hamming_limit), SECOND_STAGES.index(stage), SECOND_LIMITS.index(second_limit)]
    except ValueError as error:
        raise EvaluationError("bootstrap cell selector is invalid") from error


def bootstrap(args: Any) -> None:
    values, identity = load_values(args.contributions)
    left = cell(values, args.hamming_limit, args.second_limit, args.left_stage)
    right = cell(values, args.hamming_limit, args.second_limit, args.right_stage)
    metrics = shared.paired_bootstrap_metrics({"survival": left}, {"survival": right}, ("survival",), REPLICATES, SEED)
    files = sources()
    report = {
        "schema_version": 1,
        "family": "mih_adc_ceiling_paired_bootstrap_v1",
        "id": args.comparison_id,
        "contributions_file": args.contributions.name,
        "contributions_sha256": sha256(args.contributions),
        "identity": identity,
        "query_count": 1252,
        "hamming_limit": args.hamming_limit,
        "second_limit": args.second_limit,
        "left_stage": args.left_stage,
        "right_stage": args.right_stage,
        "replicates": REPLICATES,
        "seed": SEED,
        "bootstrap_source_files_sha256": files,
        "bootstrap_source_bundle_sha256": bundle(files),
        "bootstrap_runtime": shared.evaluator_runtime(),
        "metrics": metrics,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test() -> int:
    try:
        with __import__("tempfile").TemporaryDirectory() as directory:
            root = Path(directory)
            values = {"raw_union_oracle_survival": numpy.zeros(1252), "hamming_oracle_survival": numpy.zeros((4, 1252)), "second_oracle_survival": numpy.zeros((4, 4, 4, 1252)), "candidate_count": numpy.zeros(1252, dtype=numpy.int32), "posting_visit_count": numpy.zeros(1252, dtype=numpy.int32), "bucket_probe_count": numpy.zeros(1252, dtype=numpy.int32), "exact_bucket_floor_candidate_count": numpy.zeros(1252, dtype=numpy.int32), "probe_count_by_flip_depth": numpy.zeros((1252, 3), dtype=numpy.int32), "posting_visit_count_by_flip_depth": numpy.zeros((1252, 3), dtype=numpy.int32), "stop_reason": numpy.asarray(["candidate"] * 1252), "query_ids": numpy.asarray([str(index) for index in range(1252)]), "identity_json": numpy.asarray(json.dumps({"schema_version": 1, "query_count": 1252, "ordered_query_ids_sha256": "0" * 64, "evaluation_materialization_manifest_sha256": "1" * 64, "evaluation_qrels_sha256": "2" * 64, "candidate_limit": 512, "oracle_k": 10}))}
            path = root / "invalid.npz"; numpy.savez_compressed(path, **values)
            try:
                load_values(path)
            except EvaluationError:
                pass
            else:
                raise EvaluationError("invalid contribution identity was accepted")
    except (EvaluationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"bootstrap-mih-adc-ceiling self-test failed: {error}", file=sys.stderr); return 1
    print("MIH ADC ceiling bootstrap self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("bootstrap")
    run.add_argument("--contributions", type=Path, required=True); run.add_argument("--output", type=Path, required=True); run.add_argument("--comparison-id", required=True); run.add_argument("--hamming-limit", type=int, required=True); run.add_argument("--second-limit", type=int, required=True); run.add_argument("--left-stage", choices=SECOND_STAGES, required=True); run.add_argument("--right-stage", choices=SECOND_STAGES, required=True)
    subparsers.add_parser("self-test"); args = parser.parse_args(argv)
    try:
        if args.command == "self-test": return self_test()
        bootstrap(args)
    except (EvaluationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"bootstrap-mih-adc-ceiling: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
