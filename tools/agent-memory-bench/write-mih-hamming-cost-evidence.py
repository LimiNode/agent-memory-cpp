#!/usr/bin/env python3
"""Write provenance-bound evidence for native MIH Hamming cost decomposition."""

from __future__ import annotations

import argparse
import importlib.util
import math
import sys
from pathlib import Path
from typing import Any


THIS_PATH = Path(__file__).resolve()
BASE_PATH = THIS_PATH.with_name("write-mih-rerank-cost-evidence.py")
COMPONENTS = (
    "direct_indirect_score_buffer",
    "candidate_code_gather",
    "contiguous_hamming_distance_loop",
    "score_buffer_materialization",
    "gather_contiguous_hamming_score_buffer",
    "top_k_selection_on_prepared_scores",
)


def load_base() -> Any:
    spec = importlib.util.spec_from_file_location("mih_rerank_cost_evidence", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load native MIH evidence helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load_base()


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)


def validate_decomposition(report: dict[str, Any]) -> None:
    samples = report.get("hamming_candidate_cost_decomposition_ms_per_query_repeat_means")
    medians = report.get("hamming_candidate_cost_decomposition_ms_per_query_median")
    require(isinstance(samples, dict) and isinstance(medians, dict), "Hamming cost decomposition is absent")
    require(set(samples) == set(COMPONENTS) and set(medians) == set(COMPONENTS), "Hamming cost decomposition components differ")
    for component in COMPONENTS:
        values = base.numeric_samples(samples[component], f"Hamming cost {component}", 7)
        require(close(float(medians[component]), base.median(values)), f"Hamming cost {component} median differs from samples")
    require(
        medians["contiguous_hamming_distance_loop"] < medians["direct_indirect_score_buffer"],
        "contiguous Hamming loop is not below the direct candidate score path",
    )
    require(
        medians["candidate_code_gather"] > medians["contiguous_hamming_distance_loop"],
        "candidate code gather does not expose the expected access cost",
    )


def self_test() -> int:
    try:
        if base.self_test() != 0:
            return 1
        samples = {component: [0.1] * 7 for component in COMPONENTS}
        samples["direct_indirect_score_buffer"] = [0.3] * 7
        samples["candidate_code_gather"] = [0.2] * 7
        report = {
            "hamming_candidate_cost_decomposition_ms_per_query_repeat_means": samples,
            "hamming_candidate_cost_decomposition_ms_per_query_median": {
                component: base.median(values) for component, values in samples.items()
            },
        }
        validate_decomposition(report)
        report["hamming_candidate_cost_decomposition_ms_per_query_median"]["candidate_code_gather"] = 0.0
        try:
            validate_decomposition(report)
        except ValueError:
            pass
        else:
            raise ValueError("Hamming cost mutation self-test did not reject a stale median")
        print("MIH Hamming cost evidence packager self-test passed")
        return 0
    except (OSError, ValueError) as error:
        print(f"write-mih-hamming-cost-evidence self-test: {error}", file=sys.stderr)
        return 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    parser.add_argument("--input-manifest", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not all((args.report, args.input_manifest, args.config, args.output)):
        parser.error("--report, --input-manifest, --config, and --output are required unless --self-test is used")
    try:
        report = base.read_json(args.report)
        config = base.read_json(args.config)
        input_manifest = base.read_json(args.input_manifest)
        base.validate_report(report, config, input_manifest, args.config, args.input_manifest)
        validate_decomposition(report)
        files = base.evidence_files(args.report, args.input_manifest, args.config)
        files.append((THIS_PATH, "bundle/sources/write-mih-hamming-cost-evidence.py"))
        manifest = base.archive_manifest(files)
        manifest["family"] = "mih_hamming_cost_decomposition_evidence_v1"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        base.write_archive(args.output, files, manifest)
        print(base.json.dumps({"sha256": base.digest(args.output), "bundle_root_sha256": manifest["bundle_root_sha256"]}, sort_keys=True))
    except (OSError, ValueError, base.json.JSONDecodeError, base.zipfile.BadZipFile) as error:
        print(f"write-mih-hamming-cost-evidence: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
