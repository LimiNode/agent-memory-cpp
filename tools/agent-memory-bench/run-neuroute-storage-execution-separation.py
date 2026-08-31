#!/usr/bin/env python3
"""Verify the #261 canonical storage/execution separation contract."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") ==
            "neuroute_storage_execution_separation",
            "storage/execution contract differs")
    require(value["storage_modes"] == ["int8",
            "nonlinear_int5_power_half"] and
            value["execution_kernels"] == ["portable", "sse2", "avx2"],
            "storage/execution axes differ")
    return value


def activation(args: argparse.Namespace) -> dict[str, str]:
    return {"final_rerank_result_sha256": sha256(args.final_rerank_result),
        "final_rerank_evidence_sha256": sha256(args.final_rerank_evidence),
        "physical_storage_manifest_sha256": sha256(args.storage_manifest),
        "safe_executable_sha256": sha256(args.safe_executable),
        "avx2_executable_sha256": sha256(args.avx2_executable)}


def cache_option(path: Path) -> str:
    prefix = "AGENT_MEMORY_NEUROUTE_ENABLE_AVX2:BOOL="
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    raise ValueError("NeuRoute AVX2 CMake cache option is missing")


def invoke(executable: Path, manifest: Path, representation: str,
           output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run([str(executable), "--verify", str(manifest),
        representation, "nonlinear_int5_power_half", str(output)],
        check=False, capture_output=True, text=True)
    require(completed.returncode == 0,
            "storage/execution native verification failed: " +
            completed.stderr.strip())


def run(args: argparse.Namespace) -> None:
    contract = load_contract(args.contract)
    require(activation(args) == contract["activation"],
            "storage/execution activation differs")
    require(cache_option(args.safe_cache) == "OFF" and
            cache_option(args.avx2_cache) == "ON",
            "storage/execution CMake opt-in differs")
    representation = contract["physical_verification"]["representation_id"]
    if not (args.reuse_reports and args.safe_report.is_file()):
        invoke(args.safe_executable, args.storage_manifest, representation,
               args.safe_report)
    if not (args.reuse_reports and args.avx2_report.is_file()):
        invoke(args.avx2_executable, args.storage_manifest, representation,
               args.avx2_report)
    safe = json.loads(args.safe_report.read_text(encoding="utf-8"))
    avx2 = json.loads(args.avx2_report.read_text(encoding="utf-8"))
    expected = contract["physical_verification"]
    for report in (safe, avx2):
        require(report["selected_storage_mode"] ==
                "nonlinear_int5_power_half" and
                report["stores_materialized_by_configuration"] == 1 and
                report["physical_store"]["sha256"] ==
                expected["store_sha256"] and
                report["sample_records"] == expected["sample_records"] and
                report["format"] == contract["canonical_formats"][
                    "nonlinear_int5_power_half"],
                "storage/execution physical report differs")
    safe_ids = [row["id"] for row in safe["kernels"]]
    avx_ids = [row["id"] for row in avx2["kernels"]]
    safe_policy = (safe["build"]["safe_portable_default"] is True and
        safe["build"]["avx2_compiled"] is False and
        safe_ids == ["portable", "sse2"])
    avx_policy = (avx2["build"]["avx2_compiled"] is True and
        avx2["build"]["avx2_runtime_supported"] is True and
        avx_ids == ["portable", "sse2", "avx2"])
    digest = safe["portable_decoded_sha256"]
    cross_build = digest == avx2["portable_decoded_sha256"]
    all_kernels = all(row["matches_portable"] is True and
        row["decoded_sha256"] == digest
        for report in (safe, avx2) for row in report["kernels"])
    one_store = all(report["stores_materialized_by_configuration"] == 1
                    for report in (safe, avx2))
    passed = (safe_policy and avx_policy and cross_build and all_kernels and
              one_store)
    result = {"schema_version": 1,
        "family": "neuroute_storage_execution_separation_result",
        "contract_sha256": sha256(args.contract),
        "activation": contract["activation"],
        "reports": {"safe": {"path": str(args.safe_report.resolve()),
            "sha256": sha256(args.safe_report)},
            "avx2": {"path": str(args.avx2_report.resolve()),
            "sha256": sha256(args.avx2_report)}},
        "compatibility": {"decoded_sha256": digest,
            "cross_build_portable_identity": cross_build,
            "all_available_kernel_identity": all_kernels,
            "physical_store_sha256": expected["store_sha256"],
            "sample_records": expected["sample_records"]},
        "builds": {"safe": safe["build"], "avx2": avx2["build"]},
        "decision": {"gates_passed": passed,
            "safe_portable_build_is_default": safe_policy,
            "avx2_is_explicit_cmake_opt_in": avx_policy,
            "persisted_bytes_are_execution_independent":
                cross_build and all_kernels,
            "storage_mode_is_explicit_user_configuration": True,
            "one_codec_store_per_index": one_store,
            "existing_index_manifest_is_authoritative": True,
            "specialized_avx2_repack_is_production_format": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))
    require(passed, "storage/execution separation gate failed")


def self_test() -> None:
    contract = load_contract(THIS /
        "neuroute-storage-execution-separation.example.json")
    require(contract["build_policy"]["default_avx2"] is False and
            contract["canonical_formats"]["nonlinear_int5_power_half"][
                "record_bytes"] == 244,
            "storage/execution self-test differs")
    print("NeuRoute storage/execution separation self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-storage-execution-separation.example.json")
    for name in ("final-rerank-result", "final-rerank-evidence",
                 "storage-manifest", "safe-executable", "avx2-executable",
                 "safe-cache", "avx2-cache", "safe-report", "avx2-report",
                 "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--reuse-reports", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"contract", "reuse_reports", "self_test"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all storage/execution paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.SubprocessError) as error:
        print(f"run-neuroute-storage-execution-separation: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
