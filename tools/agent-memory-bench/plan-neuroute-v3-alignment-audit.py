#!/usr/bin/env python3
"""Validate the frozen post-hoc NeuRoute v3 alignment audit contract."""

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
    require(value.get("schema_version") == 1, "alignment audit schema differs")
    require(value.get("family") == "neuroute_v3_posthoc_alignment_audit", "alignment audit family differs")
    require(value.get("claim_scope") == "posthoc_mechanism_diagnostic_no_selection_no_confirmation",
            "alignment audit claim scope differs")
    datasets = value.get("datasets")
    require(isinstance(datasets, list) and [row.get("id") for row in datasets] == ["de-25k", "fr-25k", "ja-25k"],
            "alignment audit dataset order differs")
    expected = {
        "de-25k": ("de", "neuroute_dynamic_false_positive_result_v3", 305, 76, 76),
        "fr-25k": ("fr", "neuroute_v3_external_confirmation_result", 343, 85, 86),
        "ja-25k": ("ja", "neuroute_v3_ja_external_confirmation_result", 860, 215, 215),
    }
    for row in datasets:
        language, family, queries, configuration, internal = expected[row["id"]]
        require((row.get("language"), row.get("result_family"), row.get("documents"), row.get("queries"),
                 row.get("configuration_queries"), row.get("internal_queries"))
                == (language, family, 25000, queries, configuration, internal),
                f"alignment audit dataset differs: {row['id']}")
        require(all(isinstance(row.get(name), str) and len(row[name]) == 64
                    for name in ("result_sha256", "result_contract_sha256")),
                f"alignment audit digest differs: {row['id']}")
    require(value.get("model") == {"treatment": "dynamic_false_positive", "bits": 12,
                                   "seeds": [2026082701, 2026082702, 2026082703]},
            "alignment audit model differs")
    require(value.get("routing") == {"learned_probes": 512, "pca_bits": 8, "pca_probes": 16,
                                     "pca_replication": 4, "candidate_mass_target": 0.1,
                                     "probe_reachability_budgets": [16, 32, 64, 128, 256, 512]},
            "alignment audit routing differs")
    require(value.get("diagnostics") == {"oracle_k": 10, "document_sample": 1024,
                                         "document_neighbours": 10,
                                         "document_sample_order": "sha256_utf8_id_v1",
                                         "partitions": ["configuration_selection", "internal_evaluation"]},
            "alignment audit diagnostics differ")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "neuroute-v3-alignment-audit.example.json")
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract)
        print(json.dumps({"family": contract["family"], "datasets": [row["id"] for row in contract["datasets"]],
                          "claim_scope": contract["claim_scope"]}, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"plan-neuroute-v3-alignment-audit: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
