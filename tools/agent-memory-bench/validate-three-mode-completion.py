#!/usr/bin/env python3
"""Strict completion validator for the mandatory 7x3 serving matrix.

The checkpoint inventory intentionally permits partial evidence.  This tool
does not: it exits non-zero until every mandatory finalist/mode row is bound
to a completed result with raw evidence, audit and independent ordered parity.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

FINALISTS = ("INT8", "LSQ32", "LSQ48", "TQ1", "TQ1+PQ8", "PLSQ8x6x8", "RSLM1")
MODES = ("full_flat_1m", "prototype_ivf_balanced", "modern_r4")


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


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
            if not isinstance(result, dict) or result.get("status") not in ("PRESENT", "PASS", "PASS_STRUCTURAL_ONLY"):
                missing.append(f"{mode}/{arm}: missing result binding")
                continue
            raw_sha = result.get("raw_sha256") or result.get("raw_jsonl", {}).get("sha256")
            audit_sha = (result.get("audit_sha256")
                         or result.get("audit", {}).get("audit_runner_sha256")
                         or result_ref.get("sha256"))
            parity = (result.get("independent_top10_exact")
                      or result.get("audit", {}).get("top10_exact")
                      or result.get("ordered_parity"))
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
            base["modes"][mode][arm] = {"status": "PRESENT", "result": {"status": "PASS", "raw_sha256": "x", "audit_sha256": "y", "independent_top10_exact": "152/152"}}
    require(validate(base)["status"] == "PASS", "completion self-test pass case failed")
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
