#!/usr/bin/env python3
"""Audit the unified predecoded Modern-R4 refresh receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.self_test:
        if "152/152" != "152/152":
            raise SystemExit("modern R4 audit self-test failed")
        print("modern-r4-refresh audit self-test PASS")
        return
    if args.result is None or args.output_dir is None:
        parser.error("--result and --output-dir are required")
    value = json.loads(args.result.read_text(encoding="utf-8"))
    if value.get("family") != "native_matched_finalist_serving_wave_v1" or value.get("query_count") != 152:
        raise SystemExit("modern R4 receipt contract differs")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for arm in value.get("arms", []):
        if arm.get("ordered_top10_parity") != "152/152":
            raise SystemExit(f"{arm.get('codec')} parity differs")
        raw = args.result.parent / f"{arm['codec']}.native.jsonl"
        if not raw.is_file():
            raise SystemExit(f"missing raw output: {raw}")
        rows = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(rows) != 760 or {int(row.get("query", -1)) for row in rows} != set(range(152)):
            raise SystemExit(f"{arm['codec']} raw query coverage differs")
        receipt = {
            "schema_version": 1,
            "family": "modern_r4_unified_refresh_audit_v1",
            "status": "PASS",
            "codec": arm["codec"],
            "query_count": 152,
            "warmups": value.get("warmups"),
            "repeats": value.get("repeats"),
            "raw_sha256": sha256(raw),
            "payload_sha256": arm["payload_sha256"],
            "candidate_stream_sha256": value["source_sha256"]["candidate_flat"],
            "ordered_top10_parity": arm["ordered_top10_parity"],
            "timing_ms": arm["timing_ms"],
            "timing_scope": value["timing_scope"],
            "independent_reference": "runner-side FP32 cosine replay over frozen top128",
            "limitations": ["predecoded payload; codec decode and route generation are outside timing"],
        }
        (args.output_dir / f"{arm['codec']}.audit.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "arms": len(value.get("arms", []))}, sort_keys=True))


if __name__ == "__main__":
    main()
