#!/usr/bin/env python3
"""Bind the final-codec result to the conditional additive closure."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def run(args: argparse.Namespace) -> None:
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    quality = json.loads(args.quality_result.read_text(encoding="utf-8"))
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    manifest_path = args.native_materialization_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    native = json.loads(args.native_report.read_text(encoding="utf-8"))
    conditional = json.loads(args.conditional_result.read_text(encoding="utf-8"))
    conditional_evidence = json.loads(args.conditional_evidence.read_text(encoding="utf-8"))
    conditional_closure = json.loads(args.conditional_closure.read_text(encoding="utf-8"))
    final_manifest_path = args.final_materialization_root / "manifest.json"

    activation = {
        "conditional_result_sha256": sha256(args.conditional_result),
        "conditional_evidence_sha256": sha256(args.conditional_evidence),
        "final_materialization_sha256": sha256(final_manifest_path),
    }
    require(activation == contract["activation"],
            "final-codec closure activation differs")
    require(conditional.get("family") ==
            "neuroute_conditional_representation_quality_result" and
            conditional_evidence.get("family") ==
            "neuroute_conditional_representation_evidence" and
            conditional_evidence.get("passed") is True and
            conditional_evidence.get("result_sha256") == sha256(args.conditional_result),
            "final-codec closure conditional evidence differs")
    require(conditional_closure.get("family") ==
            "neuroute_conditional_representation_additive_closure" and
            conditional_closure.get("passed") is True and
            conditional_closure.get("result_sha256") == sha256(args.conditional_result) and
            conditional_closure.get("legacy_evidence_sha256") ==
            sha256(args.conditional_evidence) and
            conditional_closure.get("upstream", {}).get(
                "final_materialization_sha256") == sha256(final_manifest_path),
            "final-codec closure parent closure differs")

    require(quality.get("family") == "neuroute_final_codec_quality_result" and
            quality.get("contract_sha256") == sha256(args.contract) and
            quality.get("activation") == activation,
            "final-codec closure quality binding differs")
    require(evidence.get("family") == "neuroute_final_codec_evidence" and
            evidence.get("contract_sha256") == sha256(args.contract) and
            evidence.get("quality_result_sha256") == sha256(args.quality_result) and
            evidence.get("native_materialization_sha256") == sha256(manifest_path) and
            evidence.get("native_report_sha256") == sha256(args.native_report),
            "final-codec closure evidence binding differs")
    require(manifest.get("family") == "neuroute_final_codec_native_materialization" and
            manifest.get("contract_sha256") == sha256(args.contract) and
            manifest.get("quality_result_sha256") == sha256(args.quality_result),
            "final-codec closure native materialization differs")
    require(native.get("family") == "neuroute_final_codec_native_result" and
            native.get("materialization_sha256") == sha256(manifest_path) and
            len(native.get("rows", [])) == 84,
            "final-codec closure native report differs")

    receipt = {
        "schema_version": 1,
        "family": "neuroute_final_codec_additive_closure",
        "passed": True,
        "claim_scope": "downstream_provenance_only_no_quality_or_timing_change",
        "contract_sha256": sha256(args.contract),
        "quality_result_sha256": sha256(args.quality_result),
        "legacy_evidence_sha256": sha256(args.evidence),
        "native_materialization_sha256": sha256(manifest_path),
        "native_report_sha256": sha256(args.native_report),
        "conditional_result_sha256": sha256(args.conditional_result),
        "conditional_evidence_sha256": sha256(args.conditional_evidence),
        "conditional_additive_closure_sha256": sha256(args.conditional_closure),
        "final_materialization_sha256": sha256(final_manifest_path),
        "closure_source_sha256": sha256(Path(__file__)),
        "selected_quantizer": quality["decision"]["selected_quantizer"],
        "selected_layout": evidence["decision"]["selected_layout"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(receipt))


def self_test() -> None:
    require(canonical({"b": 1, "a": 2}).startswith(b'{\n  "a"'),
            "final-codec closure canonical JSON differs")
    print("NeuRoute final-codec additive closure self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--quality-result", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--native-materialization-root", type=Path)
    parser.add_argument("--native-report", type=Path)
    parser.add_argument("--conditional-result", type=Path)
    parser.add_argument("--conditional-evidence", type=Path)
    parser.add_argument("--conditional-closure", type=Path)
    parser.add_argument("--final-materialization-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        require(all(value is not None for name, value in vars(args).items()
                    if name != "self_test"),
                "all final-codec closure paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"audit-neuroute-final-codec-closure: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
