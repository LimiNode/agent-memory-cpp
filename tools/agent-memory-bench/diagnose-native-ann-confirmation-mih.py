#!/usr/bin/env python3
"""Summarize fixed MIH native work from already measured confirmation reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summary(contract: dict[str, Any], output_root: Path) -> dict[str, Any]:
    mih = contract["frozen_backends"]["mih"]
    rows: list[dict[str, Any]] = []
    for scale in contract["scales"]:
        report_path = output_root / scale["id"] / "comparison" / "native-reports" / f"{mih['id']}.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        counters = report.get("counters_per_query", {})
        timing = report.get("latency_ms_per_query", {})
        expected = ("bucket_probes", "non_empty_probes", "empty_probes", "posting_visits", "unique_candidates", "unique_candidates_per_posting_visit", "mean_posting_length_touched", "p95_posting_length_touched")
        stages = ("bucket_lookup", "posting_traversal", "generation_dedup", "full_hamming_scoring", "top_k_selection", "candidate_generator_total")
        require(report.get("backend", {}).get("name") == "mih" and report.get("band_widths") == mih["band_widths"] and report.get("local_radii") == mih["local_radii"] and report.get("fixed_radius") == 56, f"fixed MIH report differs: {scale['id']}")
        require(all(isinstance(counters.get(name), (int, float)) and counters[name] >= 0 for name in expected), f"MIH counters differ: {scale['id']}")
        require(all(isinstance(timing.get(name), dict) and all(isinstance(timing[name].get(percentile), (int, float)) and timing[name][percentile] >= 0 for percentile in ("p50", "p95", "p99")) for name in stages), f"MIH timings differ: {scale['id']}")
        require(counters["non_empty_probes"] + counters["empty_probes"] == counters["bucket_probes"] and counters["posting_visits"] >= counters["unique_candidates"], f"MIH counter invariants differ: {scale['id']}")
        rows.append({
            "scale": scale["id"],
            "document_count": scale["expected_evaluation_documents"],
            "native_report_sha256": sha256(report_path),
            "candidate_fraction": counters["unique_candidates"] / scale["expected_evaluation_documents"],
            "counters_per_query": {name: counters[name] for name in expected},
            "latency_ms_per_query": {name: timing[name] for name in stages},
        })
    return {
        "schema_version": 1,
        "family": "native_ann_confirmation_fixed_mih_work_diagnostic_v1",
        "purpose": "post_hoc_descriptive_diagnostic_not_selection",
        "frozen_mih": {"id": mih["id"], "band_widths": mih["band_widths"], "local_radii": mih["local_radii"], "fixed_radius": 56},
        "rows": rows,
        "source_files_sha256": {THIS.name: sha256(THIS)},
    }


def self_test() -> int:
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = {"family": "native_ann_confirmation_scale_v1", "frozen_backends": {"mih": {"id": "mih", "band_widths": [256], "local_radii": [2]}}, "scales": [{"id": "test", "expected_evaluation_documents": 10}]}
            report = {"backend": {"name": "mih"}, "band_widths": [256], "local_radii": [2], "fixed_radius": 56, "counters_per_query": {"bucket_probes": 2.0, "non_empty_probes": 1.0, "empty_probes": 1.0, "posting_visits": 3.0, "unique_candidates": 2.0, "unique_candidates_per_posting_visit": 2.0 / 3.0, "mean_posting_length_touched": 3.0, "p95_posting_length_touched": 3.0}, "latency_ms_per_query": {name: {percentile: 1.0 for percentile in ("p50", "p95", "p99")} for name in ("bucket_lookup", "posting_traversal", "generation_dedup", "full_hamming_scoring", "top_k_selection", "candidate_generator_total")}}
            path = root / "test" / "comparison" / "native-reports" / "mih.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(report), encoding="utf-8")
            value = summary(contract, root)
            require(value["rows"][0]["candidate_fraction"] == 0.2, "MIH diagnostic candidate fraction differs")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"diagnose-native-ann-confirmation-mih self-test failed: {error}", file=sys.stderr)
        return 1
    print("diagnose-native-ann-confirmation-mih self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        require(all((args.contract, args.output_root, args.output)), "diagnostic arguments are required")
        contract = json.loads(args.contract.read_text(encoding="utf-8"))
        require(contract.get("family") == "native_ann_confirmation_scale_v1", "confirmation contract identity differs")
        value = summary(contract, args.output_root)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"diagnose-native-ann-confirmation-mih: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
