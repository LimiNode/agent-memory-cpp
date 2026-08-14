#!/usr/bin/env python3
"""Paired bootstrap for the exploratory canonical arbitrary-m MIH frontier."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_runner() -> Any:
    path = THIS.with_name("run-mih-canonical-arbitrary-m-frontier.py")
    spec = importlib.util.spec_from_file_location("canonical_arbitrary_m_bootstrap_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load canonical arbitrary-m runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load_runner()


def source_files() -> dict[str, str]:
    names = (THIS.name, "run-mih-canonical-arbitrary-m-frontier.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py")
    return {name: sha256(THIS.with_name(name)) for name in names}


def source_bundle(value: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def paired_summary(control: numpy.ndarray, challenger: numpy.ndarray, seed: int, replicates: int) -> dict[str, Any]:
    require(control.shape == challenger.shape and control.ndim == 1 and control.size > 0, "paired bootstrap inputs differ")
    delta = challenger - control
    generator = numpy.random.default_rng(seed)
    samples = delta[generator.integers(0, delta.size, size=(replicates, delta.size))].mean(axis=1)
    return {"control_mean": float(control.mean()), "challenger_mean": float(challenger.mean()), "mean_delta": float(delta.mean()), "ci95": [float(numpy.quantile(samples, 0.025)), float(numpy.quantile(samples, 0.975))]}


def load_values(path: Path) -> dict[str, numpy.ndarray]:
    with numpy.load(path, allow_pickle=False) as archive:
        return {name: archive[name].copy() for name in archive.files}


def bootstrap_report(contract: dict[str, Any], challenger_m: int, seed: int, control_path: Path, challenger_path: Path) -> dict[str, Any]:
    control, challenger = load_values(control_path), load_values(challenger_path)
    require(control["query_ids"].tobytes() == challenger["query_ids"].tobytes() and control["identity_json"].tobytes() == challenger["identity_json"].tobytes(), "paired bootstrap identities differ")
    metrics = {
        "bucket_probe_count": "bucket_probes_per_query",
        "posting_visit_count": "posting_visits_per_query",
        "candidate_count": "candidates_per_query",
        "e5_oracle_raw_union_coverage": "raw_union_oracle_survival",
        "e5_oracle_second_stage_coverage": "adc_oracle_survival",
        "reranked_ndcg_at_10": "reranked_ndcg_at_10",
    }
    sources = source_files()
    return {
        "schema_version": 1,
        "family": "mih_canonical_arbitrary_m_frontier_paired_bootstrap_v1",
        "control": "m16-minimum-probe-r56",
        "challenger": f"m{challenger_m}-minimum-probe-r56",
        "seed": seed,
        "replicates": contract["decision_rule"]["bootstrap_replicates"],
        "bootstrap_seed": contract["decision_rule"]["bootstrap_seed"] + challenger_m * 100 + seed,
        "control_contribution_sha256": sha256(control_path),
        "challenger_contribution_sha256": sha256(challenger_path),
        "bootstrap_source_files_sha256": sources,
        "bootstrap_source_bundle_sha256": source_bundle(sources),
        "metrics": {name: paired_summary(control[field].astype(numpy.float64), challenger[field].astype(numpy.float64), contract["decision_rule"]["bootstrap_seed"] + challenger_m * 1000 + seed + index * 100000, contract["decision_rule"]["bootstrap_replicates"]) for index, (field, name) in enumerate(metrics.items())},
    }


def run(args: Any) -> None:
    contract = runner.load_contract(args.contract)
    matrix = json.loads((args.matrix_root / "matrix-manifest.json").read_text(encoding="utf-8"))
    expected_rows = [{"id": row["id"], "treatment": row["treatment"]["id"], "seed": row["seed"], "report_sha256": sha256(args.matrix_root / "reports" / f"{row['id']}.json"), "contribution_sha256": sha256(args.matrix_root / "contributions" / f"{row['id']}.npz")} for row in runner.rows(contract)]
    require(matrix.get("rows") == expected_rows, "canonical arbitrary-m matrix rows differ")
    args.output_root.mkdir(parents=True, exist_ok=True)
    for challenger_m in contract["m_values"]:
        if challenger_m == 16:
            continue
        for seed in contract["seeds"]:
            control = args.matrix_root / "contributions" / f"m16-minimum-probe-r56-seed{seed}.npz"
            challenger = args.matrix_root / "contributions" / f"m{challenger_m}-minimum-probe-r56-seed{seed}.npz"
            output = bootstrap_report(contract, challenger_m, seed, control, challenger)
            (args.output_root / f"m{challenger_m}-minus-m16-seed{seed}.json").write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test() -> int:
    try:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            values = {"query_ids": numpy.asarray(["q1", "q2"]), "identity_json": numpy.asarray('{"ok":true}'), "bucket_probe_count": numpy.asarray([1, 1]), "posting_visit_count": numpy.asarray([2, 2]), "candidate_count": numpy.asarray([3, 3]), "e5_oracle_raw_union_coverage": numpy.asarray([0.1, 0.2]), "e5_oracle_second_stage_coverage": numpy.asarray([0.1, 0.2]), "reranked_ndcg_at_10": numpy.asarray([0.2, 0.4])}
            control, challenger = path / "control.npz", path / "challenger.npz"
            numpy.savez(control, **values)
            changed = {name: value.copy() for name, value in values.items()}
            changed["candidate_count"] += 2
            numpy.savez(challenger, **changed)
            contract = runner.load_contract(THIS.with_name("mih-canonical-arbitrary-m-frontier.example.json"))
            report = bootstrap_report(contract, 17, 52, control, challenger)
            require(report["metrics"]["candidates_per_query"]["mean_delta"] == 2.0 and report["challenger"] == "m17-minimum-probe-r56", "paired bootstrap differs")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"bootstrap-mih-canonical-arbitrary-m-frontier self-test failed: {error}", file=sys.stderr)
        return 1
    print("MIH canonical arbitrary-m frontier bootstrap self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--contract", type=Path, required=True)
    run_parser.add_argument("--matrix-root", type=Path, required=True)
    run_parser.add_argument("--output-root", type=Path, required=True)
    sub.add_parser("self-test")
    args = parser.parse_args(argv)
    try:
        return self_test() if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"bootstrap-mih-canonical-arbitrary-m-frontier: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
