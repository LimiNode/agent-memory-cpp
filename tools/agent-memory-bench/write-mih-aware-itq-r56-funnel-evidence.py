#!/usr/bin/env python3
"""Validate and package the r56 funnel diagnosis evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve()


def load(name: str, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, THIS.with_name(name))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module); return module


diagnosis = load("diagnose-mih-aware-itq-r56-funnel.py", "mih_aware_r56_funnel_evidence_diagnosis")
archive = load("write-mih-rerank-cost-evidence.py", "mih_aware_r56_funnel_evidence_archive")
FIELDS = {"threshold_delta", "raw_union_delta", "hamming_k1_delta", "adc_k2_delta", "candidate_delta", "posting_visits_delta", "query_ids", "seeds", "identity_json"}


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_summary(values: dict[str, numpy.ndarray]) -> dict[str, Any]:
    per_seed = []
    for index, seed in enumerate(values["seeds"].tolist()):
        per_seed.append({"seed": int(seed), **diagnosis.summaries({name: values[name][index] for name in diagnosis.DIAGNOSTIC_FIELDS})})
    pooled = diagnosis.summaries({name: values[name].reshape(-1) for name in diagnosis.DIAGNOSTIC_FIELDS})
    return {"per_seed": per_seed, "pooled": pooled}


def make_bundle(args: Any) -> dict[str, Any]:
    contract = diagnosis.load_contract(args.contract); source_members = diagnosis.archive_members(args.source_archive, contract); report = json.loads(args.report.read_text(encoding="utf-8"))
    with numpy.load(args.contribution, allow_pickle=False) as loaded:
        values = {name: loaded[name].copy() for name in loaded.files}
    count = contract["study"]["query_count"]; seeds = contract["study"]["seeds"]
    require(set(values) == FIELDS and values["seeds"].tolist() == seeds and values["query_ids"].shape == (len(seeds), count) and all(values[name].shape == (len(seeds), count) for name in diagnosis.DIAGNOSTIC_FIELDS), "diagnostic contribution fields differ")
    identity = json.loads(str(values.pop("identity_json").item())); require(identity == {"contract_sha256": sha256(args.contract), "source_evidence_sha256": sha256(args.source_archive), "regime": contract["study"]["regime"]}, "diagnostic contribution identity differs")
    expected = expected_summary(values)
    require(report == {"schema_version": 1, "family": diagnosis.FAMILY, "contract_sha256": sha256(args.contract), "source_evidence_archive_sha256": sha256(args.source_archive), "source_evidence_bundle_root_sha256": contract["source_evidence"]["bundle_root_sha256"], "contribution_sha256": sha256(args.contribution), "study": contract["study"], "limitation": "The source evidence stores per-query oracle fractions, not oracle document identities; this diagnostic therefore traces aggregate per-query funnel deltas and cannot attribute a threshold crosser to an individual document.", "per_seed": expected["per_seed"], "pooled": expected["pooled"]}, "diagnostic report differs")
    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory); compact = {"schema_version": 1, "family": "mih_aware_itq_r56_funnel_diagnosis_evidence_v1", "contract_sha256": sha256(args.contract), "report_sha256": sha256(args.report), "contribution_sha256": sha256(args.contribution), "source_evidence_archive_sha256": sha256(args.source_archive), "source_evidence_bundle_root_sha256": contract["source_evidence"]["bundle_root_sha256"]}; compact_path = stage / "compact-manifest.json"; compact_path.write_text(json.dumps(compact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        files = [(args.contract, "bundle/contract.json"), (args.report, "bundle/reports/r56-funnel-report.json"), (args.contribution, "bundle/contributions/r56-funnel.npz"), (compact_path, "bundle/compact-manifest.json"), (args.source_archive, "bundle/source-evidence/mih-aware-itq-repaired-heldout-evidence-v2.zip")]
        for name in (Path(__file__).name, "diagnose-mih-aware-itq-r56-funnel.py", "mih-aware-itq-r56-funnel.example.json", "write-mih-rerank-cost-evidence.py"):
            files.append((THIS.with_name(name), f"bundle/sources/{name}"))
        manifest = archive.archive_manifest(files); manifest["family"] = "mih_aware_itq_r56_funnel_diagnosis_evidence_v1"; args.output.parent.mkdir(parents=True, exist_ok=True); archive.write_archive(args.output, files, manifest)
    return {"archive": str(args.output), "sha256": sha256(args.output), "bundle_root_sha256": manifest["bundle_root_sha256"]}


def self_test() -> int:
    try:
        require(diagnosis.load_contract(THIS.with_name("mih-aware-itq-r56-funnel.example.json")) == diagnosis.CONTRACT, "contract differs")
        if archive.self_test() != 0:
            return 1
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"write-mih-aware-itq-r56-funnel-evidence self-test failed: {error}", file=sys.stderr); return 1
    print("MIH-aware ITQ r56 funnel evidence packager self-test passed"); return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--contract", type=Path); parser.add_argument("--source-archive", type=Path); parser.add_argument("--report", type=Path); parser.add_argument("--contribution", type=Path); parser.add_argument("--output", type=Path); args = parser.parse_args(argv)
    try:
        if args.self_test:
            return self_test()
        require(all((args.contract, args.source_archive, args.report, args.contribution, args.output)), "evidence paths are required"); print(json.dumps(make_bundle(args), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"write-mih-aware-itq-r56-funnel-evidence: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
