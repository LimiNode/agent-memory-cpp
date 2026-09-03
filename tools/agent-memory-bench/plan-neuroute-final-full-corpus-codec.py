#!/usr/bin/env python3
"""Validate the conditional full-corpus final-codec closure."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

THIS = Path(__file__).resolve().parent


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1 and value.get("family") ==
            "neuroute_final_full_corpus_codec",
            "final full-corpus codec contract identity differs")
    retained = value["retained_codec"]
    require((retained["id"], retained["documents"],
             retained["dimensions"], retained["record_bytes"],
             retained["physical_bytes"]) ==
            ("int5_uniform_simdcomp_bp128", 1000000, 384, 244, 244000000),
            "final full-corpus retained codec differs")
    require(value["conditional_materialization"]["expected_opened"] is False
            and value["decision"] == {
                "nonlinear_full_corpus_materialization_licensed": False,
                "final_document_codec": "int5_uniform_simdcomp_bp128",
                "production_selection_licensed": True},
            "final full-corpus decision differs")
    require(all(isinstance(item, str) and len(item) == 64
                for item in value["activation"].values()),
            "final full-corpus activation hashes differ")
    return value


def plan(contract: dict[str, Any]) -> dict[str, int]:
    return {"new_full_corpus_materializations": int(
                contract["conditional_materialization"]["expected_opened"]),
            "retained_physical_files_rehashed": 1,
            "retained_documents": contract["retained_codec"]["documents"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-final-full-corpus-codec.example.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        result = plan(load_contract(args.contract))
        if args.self_test:
            require(result == {"new_full_corpus_materializations": 0,
                    "retained_physical_files_rehashed": 1,
                    "retained_documents": 1000000},
                    "final full-corpus plan differs")
            print("NeuRoute final full-corpus codec planner self-test passed")
        else:
            print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as error:
        print(f"plan-neuroute-final-full-corpus-codec: {error}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
