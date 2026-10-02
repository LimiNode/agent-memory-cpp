#!/usr/bin/env python3
"""Strict completion validator for the mandatory 7x3 serving matrix.

The checkpoint inventory intentionally permits partial evidence.  This tool
does not: it exits non-zero until every mandatory finalist/mode row is bound
to a completed result with raw evidence, audit and independent ordered parity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

FINALISTS = ("INT8", "LSQ32", "LSQ48", "TQ1", "TQ1+PQ8", "PLSQ8x6x8", "RSLM1")
MODES = ("full_flat_1m", "prototype_ivf_balanced", "modern_r4")
EXPECTED_CODEC = {"LSQ32", "LSQ48", "TQ1", "TQ1+PQ8", "INT8", "PLSQ8x6x8", "RSLM1"}
FROZEN_CANDIDATE = {
    "prototype_ivf_balanced": "a983347fa5dd46bad1c34367407f67094450af09b6bfef707b1d898ded411e0b",
    "modern_r4": "d76cabd553bbd1453908a9cd28fe3578895cf2cd3876026a5b1fd5813839bc79",
}


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(inventory: dict, repo: Path | None = None) -> dict:
    require(inventory.get("family") == "three_mode_codec_bakeoff_inventory_v1", "inventory family differs")
    modes = inventory.get("modes", {})
    missing: list[str] = []
    completed = 0
    for mode in MODES:
        rows = modes.get(mode, {})
        for arm in FINALISTS:
            row = rows.get(arm)
            if not isinstance(row, dict):
                missing.append(f"{mode}/{arm}: missing row")
                continue
            if row.get("status") != "PRESENT":
                missing.append(f"{mode}/{arm}: status={row.get('status')}")
                continue
            result_ref = row.get("result") if isinstance(row.get("result"), dict) else row
            result = result_ref
            if repo is not None and isinstance(result_ref, dict) and isinstance(result_ref.get("path"), str):
                result_path = repo / result_ref["path"]
                if result_path.is_file():
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    result_ref = dict(result_ref)
                    actual_result_sha = file_sha256(result_path)
                    if result_ref.get("sha256") and result_ref["sha256"] != actual_result_sha:
                        missing.append(f"{mode}/{arm}: result binding differs")
                    result_ref.setdefault("sha256", actual_result_sha)
                elif result_ref.get("sha256"):
                    missing.append(f"{mode}/{arm}: result path missing")
            if not isinstance(result, dict) or result.get("status") not in ("PRESENT", "PASS", "PASS_STRUCTURAL_ONLY"):
                missing.append(f"{mode}/{arm}: missing result binding")
                continue
            expected_mode = mode
            if result.get("mode") != expected_mode:
                missing.append(f"{mode}/{arm}: mode={result.get('mode')}")
            if result.get("codec") != arm:
                missing.append(f"{mode}/{arm}: codec={result.get('codec')}")
            family = str(result.get("family", ""))
            family_ok = ((mode == "full_flat_1m" and family in ("three_mode_full_flat_replay_v1", "three_mode_full_flat_replay_v2")) or
                         (mode == "prototype_ivf_balanced" and "prototype_ivf_balanced" in family) or
                         (mode == "modern_r4" and "modern_r4" in family))
            if not family_ok:
                missing.append(f"{mode}/{arm}: family={family}")
            if result.get("representation") != "packed_native":
                missing.append(f"{mode}/{arm}: representation={result.get('representation')}")
            if result.get("timed_final_scorer") != "codec_specific":
                missing.append(f"{mode}/{arm}: timed_final_scorer={result.get('timed_final_scorer')}")
            if result.get("predecoded_fp32") is not False:
                missing.append(f"{mode}/{arm}: predecoded_fp32={result.get('predecoded_fp32')}")
            if result.get("percentile_contract") != "nearest_rank_v1":
                missing.append(f"{mode}/{arm}: percentile_contract={result.get('percentile_contract')}")
            if result.get("reference_kind") not in ("independent_packed_replay", "independent_reference"):
                missing.append(f"{mode}/{arm}: reference_kind={result.get('reference_kind')}")
            if mode != "full_flat_1m":
                if repo is not None and result.get("candidate_stream_sha256") != FROZEN_CANDIDATE[mode]:
                    missing.append(f"{mode}/{arm}: candidate identity differs")
                if mode == "modern_r4":
                    identity = result.get("candidate_identity_path")
                    identity_sha = result.get("candidate_identity_sha256")
                    identity_path = repo / identity if repo is not None and isinstance(identity, str) else None
                    if repo is not None and (identity_path is None or not identity_path.is_file() or not identity_sha or file_sha256(identity_path) != identity_sha):
                        missing.append(f"{mode}/{arm}: candidate semantic identity binding missing")
                    elif repo is not None:
                        identity_doc = json.loads(identity_path.read_text(encoding="utf-8"))
                        if identity_doc.get("status") != "PASS" or identity_doc.get("semantic_identity") is not True or identity_doc.get("set_parity") != "152/152":
                            missing.append(f"{mode}/{arm}: candidate semantic identity is not PASS")
            if repo is not None and mode != "full_flat_1m" and arm == "TQ1" and result.get("metric") != "reconstructed_cosine_exact_tq_norm":
                missing.append(f"{mode}/{arm}: TQ1 norm contract is not exact")
            if repo is not None and mode != "full_flat_1m" and arm == "TQ1+PQ8" and result.get("metric") != "reconstructed_cosine_tq1_plus_pq8":
                missing.append(f"{mode}/{arm}: TQ1+PQ8 metric contract differs")
            if arm == "INT8" and result.get("metric") != "reconstructed_cosine_exact":
                missing.append(f"{mode}/{arm}: INT8 metric contract differs")
            if mode != "full_flat_1m" and arm in ("LSQ32", "LSQ48"):
                if result.get("production_scorer") != "sparse_lut":
                    missing.append(f"{mode}/{arm}: selected LSQ production scorer differs")
                stages = result.get("timing_ms", {}).get("stages", {})
                if not all(stage in stages for stage in ("thq4_prefilter", "prepare", "score", "topk")):
                    missing.append(f"{mode}/{arm}: selected LSQ stage timings missing")
            audit_path = result.get("audit_path")
            if audit_path and repo is not None:
                audit_file = repo / audit_path
                if not audit_file.is_file() or result.get("audit_sha256") != file_sha256(audit_file):
                    missing.append(f"{mode}/{arm}: audit binding differs")
                else:
                    audit_doc = json.loads(audit_file.read_text(encoding="utf-8"))
                    if audit_doc.get("status") != "PASS" or audit_doc.get("independent_top10_exact") != "152/152":
                        missing.append(f"{mode}/{arm}: codec audit is not PASS")
                    if audit_doc.get("reference_kind") not in ("independent_packed_replay", "independent_thq_identity_replay"):
                        missing.append(f"{mode}/{arm}: audit is not independent replay")
            elif result.get("audit_status", result.get("audit", {}).get("status")) != "PASS":
                missing.append(f"{mode}/{arm}: audit status={result.get('audit_status')}")
            raw_sha = result.get("raw_sha256") or result.get("raw_jsonl", {}).get("sha256")
            audit_sha = (result.get("audit_sha256")
                         or result.get("audit", {}).get("audit_runner_sha256")
                         or result_ref.get("sha256"))
            parity = (result.get("independent_top10_exact")
                      or result.get("audit", {}).get("top10_exact")
                      or result.get("ordered_parity")
                      or result.get("ordered_top10_parity"))
            for field, value in (("raw_sha256", raw_sha), ("audit_sha256", audit_sha), ("independent_top10_exact", parity)):
                if not value:
                    missing.append(f"{mode}/{arm}: missing {field}")
            if parity != "152/152":
                missing.append(f"{mode}/{arm}: parity={parity}")
            if not any(item.startswith(f"{mode}/{arm}:") for item in missing):
                completed += 1
    return {"schema_version": 1, "family": "three_mode_codec_bakeoff_completion_v1",
            "status": "PASS" if not missing else "INCOMPLETE",
            "required_rows": len(FINALISTS) * len(MODES), "completed_rows": completed,
            "missing": missing}


def self_test() -> None:
    base = {"family": "three_mode_codec_bakeoff_inventory_v1", "modes": {}}
    for mode in MODES:
        base["modes"][mode] = {}
        for arm in FINALISTS:
            base["modes"][mode][arm] = {"status": "PRESENT", "result": {
                "status": "PASS", "raw_sha256": "x", "audit_sha256": "y",
                "independent_top10_exact": "152/152",
                "mode": mode, "codec": arm,
                "family": ("three_mode_full_flat_replay_v1" if mode == "full_flat_1m" else
                            ("packed_prototype_ivf_balanced_v1" if mode == "prototype_ivf_balanced" else "packed_modern_r4_v1")),
                "representation": "packed_native",
                "timed_final_scorer": "codec_specific",
                "predecoded_fp32": False,
                "percentile_contract": "nearest_rank_v1",
                "reference_kind": "independent_packed_replay",
                "metric": "reconstructed_cosine_exact" if arm == "INT8" else "cosine",
                "audit_status": "PASS",
                "production_scorer": "sparse_lut" if arm in ("LSQ32", "LSQ48") and mode != "full_flat_1m" else None,
                "timing_ms": {"stages": {"thq4_prefilter": {}, "prepare": {}, "score": {}, "topk": {}}},
            }}
    require(validate(base)["status"] == "PASS", "completion self-test pass case failed")
    mutated = json.loads(json.dumps(base))
    mutated["modes"]["modern_r4"]["INT8"]["result"]["mode"] = "prototype_ivf_balanced"
    require(validate(mutated)["status"] == "INCOMPLETE", "mode mutation was accepted")
    mutated = json.loads(json.dumps(base))
    mutated["modes"]["prototype_ivf_balanced"]["INT8"]["result"]["family"] = "packed_modern_r4_v1"
    require(validate(mutated)["status"] == "INCOMPLETE", "cross-mode family mutation was accepted")
    mutated = json.loads(json.dumps(base))
    mutated["modes"]["modern_r4"]["INT8"]["result"]["codec"] = "TQ1"
    require(validate(mutated)["status"] == "INCOMPLETE", "codec mutation was accepted")
    mutated = json.loads(json.dumps(base))
    mutated["modes"]["prototype_ivf_balanced"]["INT8"]["result"]["family"] = "three_mode_full_flat_replay_v1"
    require(validate(mutated)["status"] == "INCOMPLETE", "family mutation was accepted")
    mutated = json.loads(json.dumps(base))
    mutated["modes"]["modern_r4"]["LSQ32"]["result"]["production_scorer"] = "gather"
    require(validate(mutated)["status"] == "INCOMPLETE", "LSQ scorer mutation was accepted")
    mutated = json.loads(json.dumps(base))
    mutated["modes"]["full_flat_1m"]["INT8"]["result"]["audit_status"] = "FAIL"
    require(validate(mutated)["status"] == "INCOMPLETE", "audit mutation was accepted")
    base["modes"]["full_flat_1m"]["TQ1"]["status"] = "PENDING_SOURCE_REPLAY"
    result = validate(base)
    require(result["status"] == "INCOMPLETE" and result["completed_rows"] == 20, "completion self-test fail case failed")
    print("validate-three-mode-completion self-test PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(); return
    if not args.inventory:
        parser.error("--inventory is required")
    result = validate(json.loads(args.inventory.read_text(encoding="utf-8")), args.inventory.resolve().parents[2])
    if args.result:
        args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
