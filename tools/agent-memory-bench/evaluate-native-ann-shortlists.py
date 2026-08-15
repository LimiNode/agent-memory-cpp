#!/usr/bin/env python3
"""Evaluate native ANN Hamming/ADC shortlist exports against E5 qrels."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve().parent


def load_shared() -> Any:
    spec = importlib.util.spec_from_file_location(
        "native_ann_quality_shared", THIS / "evaluate-projection-quantization.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load projection evaluation helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


shared = load_shared()
EvaluationError = shared.EvaluationError


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvaluationError(message)


def load_export(path: Path, query_count: int, document_count: int, hamming_limit: int, adc_limit: int) -> tuple[dict[str, Any], dict[int, tuple[numpy.ndarray, numpy.ndarray]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    require(payload.get("schema_version") == 1 and payload.get("family") == "native_ann_hamming_shortlist_export_v1", "native ANN shortlist export identity differs")
    require(payload.get("hamming_limit") == hamming_limit and isinstance(payload.get("backend"), str), "native ANN shortlist export contract differs")
    rows = payload.get("rows")
    require(isinstance(rows, list) and len(rows) == query_count, "native ANN shortlist export row count differs")
    result: dict[int, tuple[numpy.ndarray, numpy.ndarray]] = {}
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get("query_position"), int), "native ANN shortlist export query position differs")
        position = row["query_position"]
        hamming = numpy.asarray(row.get("hamming_shortlist_positions"), dtype=numpy.int64)
        adc = numpy.asarray(row.get("binary_adc_positions"), dtype=numpy.int64)
        require(0 <= position < query_count and position not in result, "native ANN shortlist export query positions differ")
        require(hamming.shape == (hamming_limit,) and adc.shape == (adc_limit,), "native ANN shortlist export shortlist length differs")
        require(numpy.all((0 <= hamming) & (hamming < document_count)) and numpy.unique(hamming).size == hamming.size, "native ANN Hamming shortlist positions differ")
        require(numpy.all((0 <= adc) & (adc < document_count)) and numpy.unique(adc).size == adc.size and numpy.all(numpy.isin(adc, hamming)), "native ANN ADC shortlist positions differ")
        result[position] = (hamming, adc)
    require(set(result) == set(range(query_count)), "native ANN shortlist export is incomplete")
    return payload, result


def oracle_cache_identity(data: dict[str, Any], oracle_k: int) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "evaluation_materialization_manifest_sha256": data["manifest_sha256"],
        "evaluation_qrels_sha256": data["evaluation_qrels_sha256"],
        "ordered_query_ids_sha256": shared.ordered_ids_sha256(data["query_ids"]),
        "oracle_k": oracle_k,
    }


def load_or_create_oracle_cache(data: dict[str, Any], path: Path, oracle_k: int) -> tuple[numpy.ndarray, numpy.ndarray]:
    identity = oracle_cache_identity(data, oracle_k)
    if path.is_file():
        with numpy.load(path, allow_pickle=False) as archive:
            cached_identity = json.loads(str(archive["identity_json"].item()))
            top = archive["exact_top_positions"].copy()
            ndcg = archive["full_e5_ndcg_at_10"].copy()
        require(cached_identity == identity and top.shape == (len(data["query_ids"]), oracle_k) and ndcg.shape == (len(data["query_ids"]),), "native ANN oracle cache provenance differs")
        return top, ndcg
    documents = numpy.asarray(data["documents"], dtype=numpy.float32)
    document_ids = data["document_ids"]
    top = numpy.empty((len(data["query_ids"]), oracle_k), dtype=numpy.int64)
    ndcg = numpy.empty(len(data["query_ids"]), dtype=numpy.float64)
    for position, query_id in enumerate(data["query_ids"]):
        exact_scores = documents @ numpy.asarray(data["queries"][position], dtype=numpy.float32)
        exact_order = numpy.lexsort((document_ids, -exact_scores))
        top[position] = exact_order[:oracle_k]
        ndcg[position] = shared.dcg_at_10(document_ids[exact_order], data["qrels"][query_id])
    path.parent.mkdir(parents=True, exist_ok=True)
    numpy.savez_compressed(path, exact_top_positions=top, full_e5_ndcg_at_10=ndcg, identity_json=numpy.asarray(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
    return top, ndcg


def evaluate(data: dict[str, Any], rows: dict[int, tuple[numpy.ndarray, numpy.ndarray]], hamming_limit: int, adc_limit: int, oracle_k: int, exact_top_positions: numpy.ndarray | None = None, full_e5_ndcg: numpy.ndarray | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    require(0 < oracle_k <= hamming_limit <= len(data["document_ids"]) and 0 < adc_limit <= hamming_limit, "native ANN quality limits differ")
    documents = numpy.asarray(data["documents"], dtype=numpy.float32)
    document_ids = data["document_ids"]
    coverage: list[float] = []
    rerank_ndcg: list[float] = []
    full_ndcg: list[float] = []
    adc_oracle: list[float] = []
    for position, query_id in enumerate(data["query_ids"]):
        query = numpy.asarray(data["queries"][position], dtype=numpy.float32)
        hamming, adc = rows[position]
        candidates = hamming[:hamming_limit]
        if exact_top_positions is None or full_e5_ndcg is None:
            exact_scores = documents @ query
            exact_order = numpy.lexsort((document_ids, -exact_scores))
            exact_top = exact_order[:oracle_k]
            current_full_ndcg = shared.dcg_at_10(document_ids[exact_order], data["qrels"][query_id])
            candidate_scores = exact_scores[candidates]
        else:
            exact_top = exact_top_positions[position]
            current_full_ndcg = float(full_e5_ndcg[position])
            candidate_scores = documents[candidates] @ query
        coverage.append(float(numpy.isin(exact_top, candidates).sum()) / oracle_k)
        rerank_order = candidates[numpy.lexsort((document_ids[candidates], -candidate_scores))]
        grades = data["qrels"][query_id]
        rerank_ndcg.append(shared.dcg_at_10(document_ids[rerank_order], grades))
        full_ndcg.append(current_full_ndcg)
        adc_oracle.append(float(numpy.isin(exact_top, adc[:adc_limit]).sum()) / oracle_k)
    contributions = {
        "coverage_at_hamming_limit": numpy.asarray(coverage, dtype=numpy.float64),
        "reranked_ndcg_at_10": numpy.asarray(rerank_ndcg, dtype=numpy.float64),
        "full_e5_ndcg_at_10": numpy.asarray(full_ndcg, dtype=numpy.float64),
        "e5_oracle_survival_after_adc": numpy.asarray(adc_oracle, dtype=numpy.float64),
    }
    report = {
        "exact_top_k_hamming_coverage": float(numpy.mean(contributions["coverage_at_hamming_limit"])),
        "reranked_ndcg_at_10": float(numpy.mean(contributions["reranked_ndcg_at_10"])),
        "full_e5_ndcg_at_10": float(numpy.mean(contributions["full_e5_ndcg_at_10"])),
        "e5_oracle_survival_after_adc": float(numpy.mean(contributions["e5_oracle_survival_after_adc"])),
        "query_count": len(coverage),
    }
    return report, contributions


def run(args: Any) -> None:
    data = shared.load_root(args.evaluation_root)
    export, rows = load_export(args.shortlist_export, len(data["query_ids"]), len(data["document_ids"]), args.hamming_limit, args.adc_limit)
    exact_top, full_ndcg = load_or_create_oracle_cache(data, args.oracle_cache, args.oracle_k) if args.oracle_cache is not None else (None, None)
    report, contributions = evaluate(data, rows, args.hamming_limit, args.adc_limit, args.oracle_k, exact_top, full_ndcg)
    identity = shared.contribution_identity(data, args.hamming_limit, args.oracle_k)
    args.contributions_output.parent.mkdir(parents=True, exist_ok=True)
    numpy.savez_compressed(
        args.contributions_output,
        **contributions,
        query_ids=numpy.asarray(data["query_ids"], dtype=numpy.str_),
        identity_json=numpy.asarray(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
    )
    result = {
        "schema_version": 1,
        "family": "native_ann_shortlist_quality_v1",
        "evaluation_materialization_manifest_sha256": data["manifest_sha256"],
        "evaluation_qrels_sha256": data["evaluation_qrels_sha256"],
        "shortlist_export_sha256": sha256(args.shortlist_export),
        "shortlist_export_backend": export["backend"],
        "oracle_cache_sha256": sha256(args.oracle_cache) if args.oracle_cache is not None else None,
        "hamming_limit": args.hamming_limit,
        "adc_limit": args.adc_limit,
        "oracle_k": args.oracle_k,
        "per_query_contributions_path": str(args.contributions_output),
        "per_query_contributions_sha256": sha256(args.contributions_output),
        "per_query_contribution_identity": identity,
        "evaluator_source_files_sha256": {Path(__file__).name: sha256(Path(__file__)), "evaluate-projection-quantization.py": sha256(THIS / "evaluate-projection-quantization.py")},
        **report,
    }
    result["evaluator_source_bundle_sha256"] = hashlib.sha256(json.dumps(result["evaluator_source_files_sha256"], sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def self_test() -> int:
    try:
        rows = {0: (numpy.asarray([0, 1]), numpy.asarray([0])), 1: (numpy.asarray([1, 0]), numpy.asarray([1]))}
        data = {"documents": numpy.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=numpy.float32), "document_ids": numpy.asarray(["a", "b"]), "queries": numpy.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=numpy.float32), "query_ids": ["q0", "q1"], "qrels": {"q0": {"a": 1}, "q1": {"b": 1}}}
        report, contributions = evaluate(data, rows, 2, 1, 1)
        require(report["exact_top_k_hamming_coverage"] == 1.0 and report["e5_oracle_survival_after_adc"] == 1.0 and contributions["reranked_ndcg_at_10"].shape == (2,), "native ANN quality evaluation differs")
    except (ValueError, KeyError, TypeError) as error:
        print(f"evaluate-native-ann-shortlists self-test failed: {error}", file=sys.stderr)
        return 1
    print("evaluate-native-ann-shortlists self-test passed")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("self-test")
    command = commands.add_parser("evaluate")
    command.add_argument("--evaluation-root", type=Path, required=True)
    command.add_argument("--shortlist-export", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--contributions-output", type=Path, required=True)
    command.add_argument("--hamming-limit", type=int, default=768)
    command.add_argument("--adc-limit", type=int, default=256)
    command.add_argument("--oracle-k", type=int, default=10)
    command.add_argument("--oracle-cache", type=Path)
    args = parser.parse_args(argv)
    try:
        return self_test() if args.command == "self-test" else (run(args) or 0)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, EvaluationError) as error:
        print(f"evaluate-native-ann-shortlists: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
