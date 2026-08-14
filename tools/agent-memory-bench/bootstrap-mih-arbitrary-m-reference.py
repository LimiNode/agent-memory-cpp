#!/usr/bin/env python3
"""Paired bootstrap for the predeclared m=19 minus m=16 comparison."""

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


def load_runner() -> Any:
    path = THIS.with_name("run-mih-arbitrary-m-reference.py"); spec = importlib.util.spec_from_file_location("arbitrary_m_runner", path)
    if spec is None or spec.loader is None: raise RuntimeError("cannot load arbitrary-m runner")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module); return module


runner = load_runner()


def require(condition: bool, message: str) -> None:
    if not condition: raise ValueError(message)


def sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def source_files() -> dict[str, str]: return {name: sha256(THIS.with_name(name)) for name in (THIS.name, "run-mih-arbitrary-m-reference.py", "evaluate-projection-quantization.py")}
def source_bundle(value: dict[str, str]) -> str: return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def paired_summary(control: numpy.ndarray, challenger: numpy.ndarray, seed: int, replicates: int) -> dict[str, Any]:
    require(control.shape == challenger.shape and control.ndim == 1 and control.size > 0, "paired bootstrap inputs differ")
    difference = challenger - control; generator = numpy.random.default_rng(seed); indices = generator.integers(0, difference.size, size=(replicates, difference.size), dtype=numpy.int32)
    samples = difference[indices].mean(axis=1)
    return {"mean_delta": float(difference.mean()), "ci95": [float(numpy.quantile(samples, .025)), float(numpy.quantile(samples, .975))], "control_mean": float(control.mean()), "challenger_mean": float(challenger.mean())}


def load_values(path: Path) -> dict[str, numpy.ndarray]:
    with numpy.load(path, allow_pickle=False) as archive: return {name: archive[name].copy() for name in archive.files}


def bootstrap_report(contract: dict[str, Any], seed: int, control_path: Path, challenger_path: Path) -> dict[str, Any]:
    control, challenger = load_values(control_path), load_values(challenger_path)
    require(numpy.array_equal(control["query_ids"], challenger["query_ids"]), "paired query identities differ")
    metrics = {"candidate_count": "unique_candidates", "posting_visit_count": "posting_visits", "bucket_probe_count": "bucket_probes", "e5_oracle_raw_union_coverage": "e5_oracle_raw_union", "e5_oracle_hamming_top_k_coverage": "e5_oracle_hamming_top_k", "e5_oracle_second_stage_coverage": "e5_oracle_second_stage", "reranked_ndcg_at_10": "reranked_ndcg_at_10"}
    sources = source_files()
    return {"schema_version": 2, "family": "mih_arbitrary_m_reference_paired_bootstrap_v2", "seed": seed, "replicates": contract["decision_rule"]["bootstrap_replicates"], "bootstrap_seed": contract["decision_rule"]["bootstrap_seed"] + seed, "control_contribution_sha256": sha256(control_path), "challenger_contribution_sha256": sha256(challenger_path), "bootstrap_source_files_sha256": sources, "bootstrap_source_bundle_sha256": source_bundle(sources), "metrics": {name: paired_summary(control[field].astype(numpy.float64), challenger[field].astype(numpy.float64), contract["decision_rule"]["bootstrap_seed"] + seed + number * 1000, contract["decision_rule"]["bootstrap_replicates"]) for number, (field, name) in enumerate(metrics.items())}}


def run(args: Any) -> None:
    contract = runner.load_contract(args.contract); matrix = json.loads((args.matrix_root / "matrix-manifest.json").read_text(encoding="utf-8")); require(matrix.get("family") == runner.FAMILY and matrix.get("contract_sha256") == sha256(args.contract), "arbitrary-m matrix manifest differs")
    expected_rows = runner.rows(contract); require(matrix.get("rows") == [{"id": row["id"], "treatment": row["treatment"]["id"], "seed": row["seed"], "report_sha256": sha256(args.matrix_root / "reports" / f"{row['id']}.json"), "contribution_sha256": sha256(args.matrix_root / "contributions" / f"{row['id']}.npz")} for row in expected_rows], "arbitrary-m matrix rows differ")
    args.output_root.mkdir(parents=True, exist_ok=True)
    for seed in contract["seeds"]:
        control_path = args.matrix_root / "contributions" / f"m16-canonical-r56-seed{seed}.npz"; challenger_path = args.matrix_root / "contributions" / f"m19-uniform-radius2-seed{seed}.npz"; output = bootstrap_report(contract, seed, control_path, challenger_path)
        path = args.output_root / f"m19-minus-m16-seed{seed}.json"; path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test() -> int:
    try:
        summary = paired_summary(numpy.asarray([1., 2., 3.]), numpy.asarray([2., 4., 6.]), 7, 100)
        require(summary["mean_delta"] == 2.0 and len(summary["ci95"]) == 2 and summary["control_mean"] == 2.0 and summary["challenger_mean"] == 4.0, "paired bootstrap summary differs")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.npz"; numpy.savez_compressed(path, value=numpy.asarray([1], dtype=numpy.int32)); require(load_values(path)["value"].tolist() == [1], "NPZ load differs")
    except (OSError, ValueError):
        print("MIH arbitrary-m bootstrap self-test failed", file=sys.stderr); return 1
    print("MIH arbitrary-m bootstrap self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True); run_parser = sub.add_parser("run"); run_parser.add_argument("--contract", type=Path, required=True); run_parser.add_argument("--matrix-root", type=Path, required=True); run_parser.add_argument("--output-root", type=Path, required=True); sub.add_parser("self-test"); args = parser.parse_args(argv)
    try: return self_test() if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error: print(f"bootstrap-mih-arbitrary-m-reference: {error}", file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main(sys.argv[1:]))
