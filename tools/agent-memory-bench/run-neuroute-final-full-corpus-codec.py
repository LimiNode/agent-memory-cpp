#!/usr/bin/env python3
"""Bind the failed nonlinear gate to the retained physical final codec."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_final_full_corpus_codec_planner",
               "plan-neuroute-final-full-corpus-codec.py")


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


def source_hashes() -> dict[str, str]:
    names = ("plan-neuroute-final-full-corpus-codec.py",
             "run-neuroute-final-full-corpus-codec.py")
    return {name: sha256(THIS / name) for name in names}


def activation(args: argparse.Namespace) -> dict[str, str]:
    return {"nonlinear_quality_sha256": sha256(args.nonlinear_quality),
        "nonlinear_evidence_sha256": sha256(args.nonlinear_evidence),
        "physical_result_sha256": sha256(args.physical_result),
        "physical_evidence_sha256": sha256(args.physical_evidence),
        "physical_storage_manifest_sha256": sha256(
            args.physical_storage_root / "manifest.json")}


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    actual = activation(args)
    require(actual == contract["activation"],
            "final full-corpus codec activation differs")
    nonlinear = json.loads(args.nonlinear_evidence.read_text(encoding="utf-8"))
    physical = json.loads(args.physical_evidence.read_text(encoding="utf-8"))
    result = json.loads(args.physical_result.read_text(encoding="utf-8"))
    require(nonlinear["family"] ==
            "neuroute_final_nonlinear_int5_evidence" and
            nonlinear["decision"]["nonlinear_replacement_licensed"] is False
            and nonlinear["decision"][
                "full_corpus_nonlinear_materialization_licensed"] is False,
            "final full-corpus nonlinear gate differs")
    require(physical["family"] == "neuroute_full_corpus_codec_io_evidence"
            and physical["passed"] is True and
            result["family"] == "neuroute_full_corpus_codec_io_result",
            "final full-corpus physical parent differs")
    manifest_path = args.physical_storage_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    retained = next(row for row in manifest["representations"]
                    if row["id"] == "int5_simdcomp_bp128")
    expected = contract["retained_codec"]
    physical_path = args.physical_storage_root / retained["file"]
    require((retained["record_bytes"], retained["bytes"],
             retained["sha256"]) ==
            (expected["record_bytes"], expected["physical_bytes"],
             expected["physical_sha256"]) and
            physical_path.stat().st_size == expected["physical_bytes"] and
            sha256(physical_path) == expected["physical_sha256"],
            "final full-corpus retained physical bytes differ")
    output = {"schema_version": 1,
        "family": "neuroute_final_full_corpus_codec_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "activation": actual, "source_files_sha256": source_hashes(),
        "matrix": planner.plan(contract),
        "retained_physical_file": {"id": retained["id"],
            "file": retained["file"], "record_bytes": retained["record_bytes"],
            "bytes": retained["bytes"], "sha256": retained["sha256"]},
        "decision": {**contract["decision"],
            "new_full_corpus_materialization_opened": False,
            "retained_physical_file_rehashed": True,
            "reason": contract["conditional_materialization"]["reason"]}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(output))


def self_test() -> None:
    contract = planner.load_contract(THIS /
        "neuroute-final-full-corpus-codec.example.json")
    require(planner.plan(contract)["new_full_corpus_materializations"] == 0,
            "final full-corpus codec runner self-test differs")
    print("NeuRoute final full-corpus codec runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-final-full-corpus-codec.example.json")
    for name in ("nonlinear-quality", "nonlinear-evidence",
                 "physical-result", "physical-evidence",
                 "physical-storage-root", "output"):
        parser.add_argument(f"--{name}", type=Path,
                            dest=name.replace("-", "_"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all final full-corpus codec paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"run-neuroute-final-full-corpus-codec: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
