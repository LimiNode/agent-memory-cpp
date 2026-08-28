#!/usr/bin/env python3
"""Bind the legacy pool-local INT6 native artifact to passed quality evidence."""

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
    result = json.loads(args.result.read_text(encoding="utf-8"))
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    closure = json.loads(args.conditional_closure.read_text(encoding="utf-8"))
    manifest_path = args.materialization_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    native = json.loads(args.native_result.read_text(encoding="utf-8"))
    require(evidence.get("passed") is True
            and evidence.get("result_sha256") == sha256(args.result),
            "INT6 native closure legacy evidence differs")
    require(closure.get("passed") is True
            and closure.get("result_sha256") == sha256(args.result)
            and closure.get("legacy_evidence_sha256") == sha256(args.evidence),
            "INT6 native closure conditional evidence differs")
    require(result.get("decision", {}).get("selected_codec") == "int6_document",
            "INT6 native closure selected codec differs")
    require(manifest.get("quality_result_sha256") == sha256(args.result),
            "INT6 native closure materialization quality binding differs")
    require(native.get("family") == "neuroute_int6_codec_native_result"
            and native.get("materialization_sha256") == sha256(manifest_path)
            and len(native.get("rows", [])) == 12,
            "INT6 native closure report differs")
    receipt = {
        "schema_version": 1,
        "family": "neuroute_int6_pool_local_additive_closure",
        "passed": True,
        "quality_result_sha256": sha256(args.result),
        "legacy_quality_evidence_sha256": sha256(args.evidence),
        "conditional_closure_sha256": sha256(args.conditional_closure),
        "materialization_sha256": sha256(manifest_path),
        "native_result_sha256": sha256(args.native_result),
        "closure_source_sha256": sha256(Path(__file__)),
        "layout_scope": "post_gather_contiguous_64_document_pools",
        "timing_status": "decode_and_score_lower_bound_superseded_by_full_corpus_random_gather",
        "authoritative_successor": "PR_215_full_corpus_codec_io",
        "deterministic_native_rows": len(native["rows"]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(receipt))


def self_test() -> None:
    require(canonical({"b": 1, "a": 2}).startswith(b'{\n  "a"'),
            "INT6 native closure canonical JSON differs")
    print("NeuRoute INT6 native additive closure self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--conditional-closure", type=Path)
    parser.add_argument("--materialization-root", type=Path)
    parser.add_argument("--native-result", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        require(all(value is not None for name, value in vars(args).items()
                    if name != "self_test"),
                "all INT6 native closure paths are required")
        run(args)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"audit-neuroute-int6-native-closure: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
