#!/usr/bin/env python3
"""Replay deterministic native MDBX evidence and write a compact receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any


THIS = Path(__file__).resolve().parent
ROOT = THIS.parent.parent
CPP = THIS / "neuroute_native_mdbx_cost.cpp"


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def source_manifest_sha256() -> str:
    relative = CPP.relative_to(ROOT).as_posix()
    return hashlib.sha256(f"{relative}:{sha256(CPP)}\n".encode("utf-8")).hexdigest()


def gitlink(path: str) -> str:
    result = subprocess.run(["git", "rev-parse", f"HEAD:{path}"], cwd=ROOT,
                            check=True, capture_output=True, text=True)
    value = result.stdout.strip()
    require(len(value) == 40, f"native MDBX gitlink differs: {path}")
    return value


def validate_report(report: dict[str, Any], contract: dict[str, Any],
                    manifest: dict[str, Any], contract_path: Path,
                    manifest_path: Path) -> None:
    require(report.get("schema_version") == 1
            and report.get("family") == "neuroute_native_mdbx_cost_result",
            "native MDBX evidence report identity differs")
    require(report.get("claim_scope") == contract["claim_scope"]
            and report.get("contract_sha256") == sha256(contract_path)
            and report.get("materialization_sha256") == sha256(manifest_path)
            and report.get("evaluator_source_manifest_sha256") == source_manifest_sha256(),
            "native MDBX evidence report binding differs")
    stack = report.get("storage_stack", {})
    require(stack.get("provenance_authoritative") is True
            and stack.get("provenance_reason") == "repository_pinned_external_submodules"
            and stack.get("libmdbx_commit") == gitlink("external/libmdbx")
            and stack.get("mdbx_containers_commit") == gitlink("external/mdbx-containers"),
            "native MDBX evidence storage provenance differs")
    expected = {(dataset["id"], f"learned-{seed}", seed, probes)
                for dataset in contract["datasets"]
                for seed in contract["routes"]["learned"]["seeds"]
                for probes in contract["routes"]["learned"]["probe_budgets"]}
    expected |= {(dataset["id"], "pca", None, 16) for dataset in contract["datasets"]}
    rows = report.get("rows", [])
    actual = {(row.get("dataset"), row.get("route"), row.get("seed"), row.get("probes"))
              for row in rows}
    require(actual == expected and len(rows) == len(expected) == 57,
            "native MDBX evidence timing matrix differs")
    stages = contract["timing"]["stages"]
    for row in rows:
        require(row.get("query_count")
                == next(dataset["configuration_queries"] for dataset in contract["datasets"]
                        if dataset["id"] == row["dataset"]),
                "native MDBX evidence query count differs")
        require(all(isinstance(row.get(name), str) and len(row[name]) == 64
                    for name in ("candidate_sequence_sha256", "hamming_sequence_sha256",
                                 "adc_sequence_sha256")),
                "native MDBX evidence sequence digest differs")
        timing = row.get("timing_ms", {})
        require(set(timing) == set(stages), "native MDBX evidence timing stages differ")
        for stage in stages:
            value = timing[stage]
            require(all(isinstance(value.get(name), (int, float))
                        and math.isfinite(value[name]) and value[name] >= 0.0
                        for name in ("p50", "p95", "p99"))
                    and value["p50"] <= value["p95"] <= value["p99"]
                    and len(value.get("per_query_median", [])) == row["query_count"]
                    and all(isinstance(sample, (int, float)) and math.isfinite(sample) and sample >= 0.0
                            for sample in value["per_query_median"]),
                    "native MDBX evidence timing sample differs")
    require(manifest.get("contract_sha256") == sha256(contract_path)
            and manifest.get("training_sanity_result_sha256")
                == contract["activation"]["training_sanity_result_sha256"]
            and manifest.get("training_sanity_evidence_sha256")
                == contract["activation"]["training_sanity_evidence_sha256"],
            "native MDBX evidence materialization activation differs")


def self_test() -> None:
    contract = json.loads((THIS / "neuroute-native-mdbx-cost.example.json").read_text(encoding="utf-8"))
    expected = 3 * (3 * 6 + 1)
    require(expected == 57 and contract["timing"]["measured_full_query_passes"] == 9,
            "native MDBX evidence self-test matrix differs")
    print("NeuRoute native MDBX cost evidence self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-native-mdbx-cost.example.json")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--replay-mdbx-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        require(all(value is not None for value in (
            args.manifest, args.report, args.executable, args.replay_mdbx_root, args.output)),
            "native MDBX evidence paths are required")
        contract = json.loads(args.contract.read_text(encoding="utf-8"))
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        report = json.loads(args.report.read_text(encoding="utf-8"))
        validate_report(report, contract, manifest, args.contract, args.manifest)
        subprocess.run([
            str(args.executable), "--validate", str(args.contract), str(args.manifest),
            str(args.replay_mdbx_root), str(args.report),
        ], cwd=ROOT, check=True)
        receipt = {
            "schema_version": 1,
            "family": "neuroute_native_mdbx_cost_evidence",
            "claim_scope": contract["claim_scope"],
            "contract_sha256": sha256(args.contract),
            "materialization_sha256": sha256(args.manifest),
            "report_sha256": sha256(args.report),
            "evaluator_source_manifest_sha256": source_manifest_sha256(),
            "materializer_source_sha256": manifest["materializer_source_sha256"],
            "evidence_writer_sha256": sha256(Path(__file__)),
            "libmdbx_commit": gitlink("external/libmdbx"),
            "mdbx_containers_commit": gitlink("external/mdbx-containers"),
            "timing_row_count": 57,
            "integrity_replay_passed": True,
            "timings_replayed": False,
            "confirmation_claims_permitted": False,
            "scale_transfer_permitted": False,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical(receipt))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.CalledProcessError) as error:
        print(f"write-neuroute-native-mdbx-cost-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
