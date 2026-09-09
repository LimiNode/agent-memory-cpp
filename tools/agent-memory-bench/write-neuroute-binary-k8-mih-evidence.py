#!/usr/bin/env python3
"""Fail-closed validator for the bounded prototype-MIH feasibility audit."""
from __future__ import annotations
import argparse, hashlib, json, math
from pathlib import Path
from typing import Any

THIS = Path(__file__).resolve().parent

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024): digest.update(block)
    return digest.hexdigest()

def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) +
            "\n").encode("utf-8")

def require(value: bool, message: str) -> None:
    if not value: raise ValueError(message)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS / "neuroute-binary-k8-mih.example.json")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        require(sum(math.comb(64, i) for i in range(2)) == 65,
                "binary K8 MIH evidence self-test failed")
        return 0
    require(args.result is not None and args.output is not None,
            "binary K8 MIH evidence inputs are required")
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    result = json.loads(args.result.read_text(encoding="utf-8"))
    require(contract["family"] == "neuroute_binary_k8_mih_feasibility" and
            result["family"] == "neuroute_binary_k8_mih_feasibility_result" and
            result["inputs"]["contract_sha256"] == sha256(args.contract),
            "binary K8 MIH evidence binding differs")
    require(set(result["codecs"]) == {row["id"] for row in contract["codecs"]},
            "binary K8 MIH codec matrix differs")
    for codec, rows in result["codecs"].items():
        require(len(rows) == 3, "binary K8 MIH seed matrix differs")
        for row in rows:
            require(row["queries"] == 152 and
                set(row["targets"]) == {str(x) for x in contract["recall_targets"]},
                "binary K8 MIH query matrix differs")
            previous = -1.0
            for target in map(str, contract["recall_targets"]):
                value = row["targets"][target]
                require(value["mean_radius"] >= previous and
                    0.0 <= value["mean_teacher_recall"] <= 1.0 and
                    value["p95_candidate_prototypes"] >= 0.0 and
                    value["p95_unique_addresses"] >= 0.0 and
                    value["mean_mih_probes"] >= 1.0,
                    "binary K8 MIH monotonicity or finiteness differs")
                previous = value["mean_radius"]
    evidence = {"schema_version": 1,
        "family": "neuroute_binary_k8_mih_feasibility_evidence",
        "result_sha256": sha256(args.result),
        "contract_sha256": sha256(args.contract),
        "codecs": sorted(result["codecs"]),
        "result_binding_and_monotonicity_validation_passed": True,
        "physical_backend_licensed": False,
        "learned_hypercube_ceiling_is_independent_followup": True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(evidence))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
