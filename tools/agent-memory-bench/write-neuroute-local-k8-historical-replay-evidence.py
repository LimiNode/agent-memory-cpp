#!/usr/bin/env python3
"""Write compact fail-closed evidence for the local-K8 historical replay."""
from __future__ import annotations

import argparse
import hashlib
import json
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
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def source_hashes(root: Path) -> dict[str, str]:
    names = ("run-neuroute-local-k8-historical-replay.py",
             "run-neuroute-exact-k8-codec-frontier.py",
             "neuroute_authoritative_qrels.py")
    return {name: sha256(root / name) for name in names}


def protocol_closure(path: Path) -> dict[str, str]:
    pending = [path.resolve()]
    seen: set[Path] = set()
    result: dict[str, str] = {}
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        result[str(current)] = sha256(current)
        value = json.loads(current.read_text(encoding="utf-8"))
        for field in ("routing_kernel_protocol", "parent_protocol",
                      "final_int8_layout_manifest", "coarse_k8_manifest"):
            if field in value:
                pending.append(Path(value[field]).resolve())
        for field in ("native_input_manifest", "document_id_rank_file",
                      "evaluation_document_ids", "evaluation_query_ids",
                      "evaluation_qrels"):
            if field in value:
                payload = Path(value[field]).resolve()
                result[str(payload)] = sha256(payload)
    return dict(sorted(result.items()))


def validate_shortlists(path: Path) -> None:
    root = json.loads(path.read_text(encoding="utf-8"))
    require(root.get("family") == "neuroute_local_k8_router_shortlists",
            "local K8 shortlist root differs")
    for descriptor in root["routers"].values():
        manifest_path = Path(descriptor["path"])
        require(sha256(manifest_path) == descriptor["sha256"],
                "local K8 shortlist manifest bytes differ")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for row in manifest["seeds"]:
            payload = Path(row["path"])
            require(payload.stat().st_size == row["bytes"] and
                    sha256(payload) == row["sha256"],
                    "local K8 shortlist payload bytes differ")


def passes(row: dict[str, Any], gates: dict[str, Any]) -> bool:
    return bool(row["mean_ndcg_loss"] <= gates[
        "maximum_mean_downstream_ndcg_loss"] and
        row["maximum_stratum_mean_ndcg_loss"] <= gates[
            "maximum_every_seed_downstream_ndcg_loss"] and
        row["mean_final_top10_overlap"] >= gates[
            "minimum_mean_final_top10_overlap"] and
        row["mean_candidate_reference_retention"] >= gates[
            "minimum_mean_fp32_stage_retention_at_candidate"] and
        row["mean_hamming_reference_overlap"] >= gates[
            "minimum_mean_fp32_stage_overlap_at_hamming768"] and
        row["mean_adc_reference_overlap"] >= gates[
            "minimum_mean_fp32_stage_overlap_at_adc64"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--layout-manifest", type=Path)
    parser.add_argument("--k8-manifest", type=Path)
    parser.add_argument("--configuration-protocol", type=Path)
    parser.add_argument("--shortlist-manifest", type=Path)
    parser.add_argument("--native-executable", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        gates = {"maximum_mean_downstream_ndcg_loss": .004,
            "maximum_every_seed_downstream_ndcg_loss": .004,
            "minimum_mean_final_top10_overlap": .99,
            "minimum_mean_fp32_stage_retention_at_candidate": .99,
            "minimum_mean_fp32_stage_overlap_at_hamming768": .98,
            "minimum_mean_fp32_stage_overlap_at_adc64": .95}
        row = {"mean_ndcg_loss": .001,
            "maximum_stratum_mean_ndcg_loss": .002,
            "mean_final_top10_overlap": .995,
            "mean_candidate_reference_retention": .995,
            "mean_hamming_reference_overlap": .99,
            "mean_adc_reference_overlap": .97}
        require(passes(row, gates), "local K8 evidence self-test failed")
        return 0
    require(all(getattr(args, name) is not None for name in (
        "contract", "result", "layout_manifest", "k8_manifest",
        "configuration_protocol", "shortlist_manifest", "native_executable",
        "output")),
        "local K8 evidence inputs are required")
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(result.get("family") ==
            "neuroute_local_k8_historical_replay_result" and
            result["provenance"] == contract["provenance"],
            "local K8 result identity differs")
    expected = {"contract_sha256": sha256(args.contract),
        "layout_manifest_sha256": sha256(args.layout_manifest),
        "k8_manifest_sha256": sha256(args.k8_manifest),
        "configuration_protocol_sha256": sha256(args.configuration_protocol),
        "configuration_protocol_closure_sha256": protocol_closure(
            args.configuration_protocol),
        "native_executable_sha256": sha256(args.native_executable),
        "source_files_sha256": source_hashes(Path(__file__).resolve().parent)}
    validate_shortlists(args.shortlist_manifest)
    require(result["inputs"]["shortlist_manifest_sha256"] ==
            sha256(args.shortlist_manifest),
            "local K8 shortlist root hash differs")
    require(all(result["inputs"].get(name) == value
                for name, value in expected.items()),
            "local K8 input bytes differ")
    gates = contract["quality_gates"]
    for partition in ("configuration", "locked_internal"):
        for row in result[partition]:
            require(row["passes_quality_gates"] == passes(row, gates),
                    f"local K8 aggregate gate differs: {partition}/{row['id']}")
            if row.get("address_budget") is not None:
                require(row["passes_registered_gate"] ==
                        (passes(row, gates) and row["address_budget"] <=
                         gates["maximum_native_address_budget"]),
                        "local K8 registered gate differs")
    passing = [row for row in result["locked_internal"]
        if row.get("passes_registered_gate") and row["id"] != "global_fp32_k8"]
    expected_selected = (min(passing, key=lambda row: (row["address_budget"],
        row["coarse_ms"]["p95"], row["mean_ndcg_loss"], row["id"]))
        if passing else None)
    require(result["decision"]["selected"] == expected_selected and
            result["decision"]["activate_fixed_top_m_frontier"] ==
                (expected_selected is None) and
            result["decision"]["production_licensed"] is False,
            "local K8 decision replay differs")
    evidence = {"schema_version": 1,
        "family": "neuroute_local_k8_historical_replay_evidence",
        "result_sha256": sha256(args.result), "inputs": expected,
        "claim_scope": result["claim_scope"], "provenance": result["provenance"],
        "configuration_points": len(result["configuration"]),
        "locked_internal_points": len(result["locked_internal"]),
        "selected": expected_selected["id"] if expected_selected else None,
        "activate_fixed_top_m_frontier": expected_selected is None,
        "result_byte_replay_passed": True,
        "production_licensed": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
