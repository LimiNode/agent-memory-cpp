#!/usr/bin/env python3
"""Fail-closed audit for post-backlog experiment receipts."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).parents[2] / "guides" / "experiments"
RECEIPTS = [
    "2026-09-11-thq-adc-oracle-result.json",
    "2026-09-11-packed-ordinal-multisequence-result.json",
    "2026-09-11-weighted-collision-voting-result.json",
    "2026-09-11-packed-thq-flat-benchmark-result.json",
    "2026-09-11-progressive-thq-scan-result.json",
    "2026-09-11-cosine-lsh-baseline-result.json",
]

def main() -> int:
    errors = []
    rows = []
    for name in RECEIPTS:
        path = ROOT / name
        if not path.is_file():
            errors.append(f"missing receipt: {name}")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"invalid JSON {name}: {exc}")
            continue
        for key in ("schema_version", "family", "production_activation"):
            if key not in data:
                errors.append(f"{name}: missing {key}")
        if data.get("production_activation") is not False:
            errors.append(f"{name}: production_activation must be false")
        rows.append({"receipt": name, "family": data.get("family"), "production_activation": data.get("production_activation")})
    result = {"schema_version": 1, "family": "postbacklog_research_audit_v1", "receipts": rows, "errors": errors, "status": "pass" if not errors else "fail"}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not errors else 1

if __name__ == "__main__":
    raise SystemExit(main())
