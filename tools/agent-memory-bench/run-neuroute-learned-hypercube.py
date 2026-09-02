#!/usr/bin/env python3
"""Learn binary codes for semantic prototypes without a query neural router.

This is intentionally a representation ceiling, not a production index.  Query
codes are the deterministic bitwise majority of teacher-positive prototype
codes.  A later metric-router experiment is licensed only if this prototype
geometry improves the frozen baseline while retaining healthy occupancy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy


THIS = Path(__file__).resolve().parent


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == 1
            and value.get("family") == "neuroute_learned_hypercube_prototype_ceiling",
            "learned-hypercube contract differs")
    return value


def initial_codes(vectors: numpy.ndarray, bits: int, seed: int) -> numpy.ndarray:
    rng = numpy.random.default_rng(seed)
    projection = rng.normal(size=(vectors.shape[1], bits)).astype(numpy.float32)
    values = vectors @ projection
    return numpy.where(values >= 0.0, 1, -1).astype(numpy.int8)


def teacher_order(queries: numpy.ndarray, prototypes: numpy.ndarray,
                  topk: int) -> numpy.ndarray:
    ids = numpy.arange(len(prototypes), dtype=numpy.int64)
    result = numpy.empty((len(queries), min(topk, len(prototypes))), dtype=numpy.int64)
    for index, query in enumerate(queries):
        scores = numpy.asarray(prototypes @ query, dtype=numpy.float32)
        ranking = numpy.lexsort((ids, -scores))
        result[index] = ranking[:result.shape[1]]
    return result


def balanced_codes(scores: numpy.ndarray, decorrelation_penalty: float = 0.0) -> numpy.ndarray:
    """Assign exactly half +1 per bit, with stable prototype-id ties."""
    count, bits = scores.shape
    result = numpy.full((count, bits), -1, dtype=numpy.int8)
    positive = count // 2
    ids = numpy.arange(count, dtype=numpy.int64)
    for bit in range(bits):
        adjusted = scores[:, bit].astype(numpy.float32, copy=True)
        if bit and decorrelation_penalty:
            prior = result[:, :bit].astype(numpy.float32)
            adjusted -= decorrelation_penalty * (
                prior @ (prior.T @ adjusted) / max(float(count), 1.0))
        order = numpy.lexsort((ids, -adjusted))
        result[order[:positive], bit] = 1
    return result


def query_codes(codes: numpy.ndarray, order: numpy.ndarray, positive_count: int) -> numpy.ndarray:
    positives = codes[order[:, :positive_count]]
    sums = positives.sum(axis=1, dtype=numpy.int16)
    return numpy.where(sums >= 0, 1, -1).astype(numpy.int8)


def distances(query: numpy.ndarray, codes: numpy.ndarray) -> numpy.ndarray:
    return ((codes != query[None, :]).sum(axis=1)).astype(numpy.int32)


def metrics(codes: numpy.ndarray, order: numpy.ndarray, positive_count: int,
            budgets: list[int]) -> dict[str, Any]:
    qcodes = query_codes(codes, order, positive_count)
    recalls = {str(budget): [] for budget in budgets}
    radii: list[int] = []
    for query_code, teacher in zip(qcodes, order):
        d = distances(query_code, codes)
        ranked = numpy.lexsort((numpy.arange(len(codes)), d))
        target = set(map(int, teacher[:positive_count].tolist()))
        radii.extend(int(d[int(index)]) for index in target)
        for budget in budgets:
            recalls[str(budget)].append(float(len(set(map(int, ranked[:budget])) & target)
                                               / max(len(target), 1)))
    bits_positive = numpy.mean(codes > 0, axis=0)
    entropy = -(bits_positive * numpy.log2(numpy.maximum(bits_positive, 1e-12))
                + (1.0 - bits_positive) * numpy.log2(numpy.maximum(1.0 - bits_positive, 1e-12)))
    correlation = numpy.corrcoef(codes.astype(numpy.float32), rowvar=False)
    off_diagonal = correlation[~numpy.eye(correlation.shape[0], dtype=bool)]
    return {
        "positive_count": positive_count,
        "mean_recall": {budget: float(numpy.mean(values)) for budget, values in recalls.items()},
        "radius": {key: float(numpy.quantile(radii, quantile))
                    for key, quantile in (("r50", .50), ("r90", .90), ("r95", .95), ("r99", .99))},
        "mean_bit_entropy": float(numpy.mean(entropy)),
        "minimum_bit_entropy": float(numpy.min(entropy)),
        "mean_abs_bit_correlation": float(numpy.mean(numpy.abs(off_diagonal))) if off_diagonal.size else 0.0,
        "prototype_code_bytes": int(codes.shape[0] * ((codes.shape[1] + 7) // 8)),
    }


def evaluate(npz: Any, contract: dict[str, Any]) -> dict[str, Any]:
    files = set(npz.files) if hasattr(npz, "files") else set(npz)
    require({"prototype_vectors", "queries"}.issubset(files),
            "learned-hypercube NPZ is missing vectors")
    prototypes = numpy.asarray(npz["prototype_vectors"], dtype=numpy.float32)
    queries = numpy.asarray(npz["queries"], dtype=numpy.float32)
    require(prototypes.ndim == 2 and queries.ndim == 2
            and prototypes.shape[1] == queries.shape[1],
            "learned-hypercube vector dimensions differ")
    bits = int(contract["code_bits"])
    positive_limit = max(int(value) for value in contract["positive_prototypes"])
    if "teacher_positive_ids" in files:
        order = numpy.asarray(npz["teacher_positive_ids"], dtype=numpy.int64)
    elif "teacher_order" in files:
        order = numpy.asarray(npz["teacher_order"], dtype=numpy.int64)
    else:
        require(len(prototypes) <= 4096 and len(queries) <= 1024,
                "large hypercube runs require precomputed teacher positives")
        order = teacher_order(queries, prototypes, positive_limit)
    require(order.ndim == 2 and order.shape[0] == len(queries)
            and order.shape[1] >= positive_limit
            and numpy.all((order[:, :positive_limit] >= 0)
                          & (order[:, :positive_limit] < len(prototypes))),
            "teacher order/positive shape differs")
    frozen = (numpy.asarray(npz["frozen_prototype_codes"], dtype=numpy.int8)
              if "frozen_prototype_codes" in files else initial_codes(prototypes, bits, 20260902))
    require(frozen.shape == (len(prototypes), bits), "frozen prototype code shape differs")
    budgets = [128, 256, 512, 1024]
    result: dict[str, Any] = {"family": contract["family"], "schema_version": 1,
                              "prototype_count": len(prototypes), "query_count": len(queries),
                              "code_bits": bits, "router": "none_prototype_only", "rows": {}}
    for positive_count in contract["positive_prototypes"]:
        result["rows"][f"frozen-p{positive_count}"] = metrics(frozen, order, positive_count, budgets)
    learned = frozen.copy()
    for iteration in range(1, max(contract["iterations"]) + 1):
        qcodes = query_codes(learned, order, max(contract["positive_prototypes"]))
        # Use teacher-positive votes, not address IDs, to avoid silently turning
        # this into a router.  Median assignment keeps every bit populated.
        votes = numpy.zeros_like(learned, dtype=numpy.float32)
        for query_index, positive in enumerate(order[:, :max(contract["positive_prototypes"])]):
            votes[positive] += qcodes[query_index]
        learned = balanced_codes(votes, float(contract["decorrelation_penalty"]))
        if iteration in contract["iterations"]:
            for positive_count in contract["positive_prototypes"]:
                result["rows"][f"learned-i{iteration}-p{positive_count}"] = metrics(
                    learned, order, positive_count, budgets)
    result["decision"] = {
        "prototype_geometry_positive": any(
            result["rows"][f"learned-i{max(contract['iterations'])}-p{p}"]["mean_recall"]["128"]
            >= result["rows"][f"frozen-p{p}"]["mean_recall"]["128"]
            for p in contract["positive_prototypes"]),
        "metric_router_followup_licensed": False,
        "production_selection_licensed": False,
        "reason": "ceiling_requires_external_validation_and_native_replay",
    }
    return result


def synthetic() -> dict[str, numpy.ndarray]:
    rng = numpy.random.default_rng(19)
    prototypes = rng.normal(size=(64, 24)).astype(numpy.float32)
    queries = prototypes[:12] + .05 * rng.normal(size=(12, 24)).astype(numpy.float32)
    return {"prototype_vectors": prototypes, "queries": queries}


def self_test(contract_path: Path) -> int:
    try:
        result = evaluate(synthetic(), load_contract(contract_path))
        require(len(result["rows"]) == 16,
                "learned-hypercube row count differs")
        require(all(row["minimum_bit_entropy"] >= 0.95
                    for key, row in result["rows"].items() if key.startswith("learned-")),
                "learned-hypercube balance collapsed")
        require(result["decision"]["production_selection_licensed"] is False,
                "learned-hypercube production gate opened")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"learned-hypercube runner self-test failed: {error}", file=sys.stderr)
        return 1
    print("Learned-hypercube runner self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "self-test"))
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-learned-hypercube.example.json")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "self-test":
            return self_test(args.contract)
        require(args.input is not None and args.output is not None,
                "run requires --input and --output")
        contract = load_contract(args.contract)
        result = evaluate(numpy.load(args.input, allow_pickle=False), contract)
        result["input_sha256"] = sha256(args.input)
        result["contract_sha256"] = sha256(args.contract)
        result["runner_sha256"] = sha256(Path(__file__))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"run-neuroute-learned-hypercube: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
