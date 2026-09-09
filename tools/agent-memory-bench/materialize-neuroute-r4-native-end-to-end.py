#!/usr/bin/env python3
"""Materialize compact bindings for the R4 native end-to-end benchmark."""
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


def load(name: str, filename: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, THIS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_r4_e2e_planner", "plan-neuroute-r4-native-end-to-end.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n").encode("utf-8")


def read_ids(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line)["id"] for line in stream]


def expected_query(row: dict[str, Any]) -> dict[str, Any]:
    budget = next(value for value in row["budgets"]
                  if value["candidate_fraction_budget"] == .005)
    value = budget["last_feasible"]
    return {"selected_address_sha256": value["selected_address_sha256"],
            "candidate_count": value["candidate_count"],
            "exact_ndcg_at_10": value["exact_ndcg_at_10"]}


def materialize(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    paths = {"coverage_result": args.coverage_result,
             "representative_codec_result": args.representative_codec_result,
             "representative_codec_evidence": args.representative_codec_evidence,
             "mapped_access_result": args.mapped_access_result,
             "mapped_access_evidence": args.mapped_access_evidence,
             "int8_compression_result": args.int8_compression_result,
             "int8_compression_evidence": args.int8_compression_evidence,
             "layout_manifest": args.layout_manifest,
             "native_input_manifest": args.native_input_manifest,
             "e5_manifest": args.e5_root / "manifest.json"}
    actual = {f"{name}_sha256": sha256(path) for name, path in paths.items()}
    require(actual == contract["activation"],
            "R4 end-to-end activation differs")
    layout = json.loads(args.layout_manifest.read_text(encoding="utf-8"))
    quality = json.loads(args.coverage_result.read_text(encoding="utf-8"))
    codec = json.loads(args.representative_codec_result.read_text(encoding="utf-8"))
    require(layout.get("family") == "neuroute_r4_layout_materialization" and
            quality.get("family") == "neuroute_r4_coverage_saturation_result",
            "R4 end-to-end parent identity differs")
    document_ids = read_ids(args.e5_root / "evaluation-document-ids.jsonl")
    query_ids = read_ids(args.e5_root / "evaluation-query-ids.jsonl")
    require(len(document_ids) == 1000000 and len(query_ids) == 305,
            "R4 end-to-end ID shape differs")
    order = sorted(range(len(document_ids)), key=document_ids.__getitem__)
    ranks = numpy.empty(len(document_ids), dtype="<u4")
    ranks[numpy.asarray(order, dtype=numpy.int64)] = numpy.arange(
        len(document_ids), dtype=numpy.uint32)
    args.output_root.mkdir(parents=True, exist_ok=True)
    rank_path = args.output_root / "document-id-rank.u32le"
    ranks.tofile(rank_path)
    query_position = {value: index for index, value in enumerate(query_ids)}
    internal_ids = layout["query_ids"][contract["route"]["layout_request_offset"]:]
    require(len(internal_ids) == 76 and len(set(internal_ids)) == 76,
            "R4 end-to-end internal query identity differs")
    quality_rows = {row["seed"]: row for row in quality["internal_rows"]
                    if row["treatment"] == "actual_k32_max"}
    codec_rows = {row["seed"]: row for row in codec["internal_rows"]
                  if row["treatment"] == "int8"}
    require(set(quality_rows) == set(contract["route"]["seeds"]),
            "R4 end-to-end parent seed coverage differs")
    require(set(codec_rows) == set(quality_rows),
            "R4 end-to-end INT8 parent seed coverage differs")
    requests = []
    for local, query_id in enumerate(internal_ids):
        expected = {}
        for seed, row in quality_rows.items():
            query = row["queries"][local]
            codec_query = codec_rows[seed]["queries"][local]
            require(query["query_id"] == query_id and
                    codec_query["query_id"] == query_id,
                    "R4 end-to-end parent query ordering differs")
            expected[str(seed)] = {"fp32": expected_query(query),
                                   "int8": expected_query(codec_query)}
        requests.append({"request": 76 + local,
                         "native_query": query_position[query_id],
                         "query_id": query_id, "expected": expected})
    protocol = {"schema_version": 1,
        "family": "neuroute_r4_native_end_to_end_protocol",
        "contract_sha256": sha256(args.contract), "activation": actual,
        "layout_manifest": str(args.layout_manifest.resolve()),
        "native_input_manifest": str(args.native_input_manifest.resolve()),
        "document_id_rank_file": str(rank_path.resolve()),
        "document_id_rank_sha256": sha256(rank_path),
        "evaluation_document_ids": str((args.e5_root /
            "evaluation-document-ids.jsonl").resolve()),
        "evaluation_query_ids": str((args.e5_root /
            "evaluation-query-ids.jsonl").resolve()),
        "evaluation_qrels": str((args.e5_root / "evaluation-qrels.tsv").resolve()),
        "seeds": contract["route"]["seeds"], "requests": requests,
        "treatments": contract["treatments"],
        "concurrency_treatments": contract["concurrency"]["treatments"],
        "workers": contract["concurrency"]["workers"],
        "warmup_passes": contract["warm_page_cache"]["warmup_passes"],
        "measured_passes": contract["warm_page_cache"]["measured_passes"],
        "concurrency_passes": contract["concurrency"]["measured_passes"],
        **contract["cascade"]}
    protocol_path = args.output_root / "protocol.json"
    protocol_path.write_bytes(canonical(protocol))
    manifest = {"schema_version": 1,
        "family": "neuroute_r4_native_end_to_end_materialization",
        "contract_sha256": sha256(args.contract), "activation": actual,
        "protocol_file": protocol_path.name,
        "protocol_sha256": sha256(protocol_path),
        "document_id_rank_file": rank_path.name,
        "document_id_rank_sha256": sha256(rank_path),
        "document_count": len(document_ids), "query_count": len(requests)}
    (args.output_root / "manifest.json").write_bytes(canonical(manifest))


def self_test() -> None:
    require(expected_query({"budgets": [{"candidate_fraction_budget": .005,
            "last_feasible": {"selected_address_sha256": "a" * 64,
            "candidate_count": 3, "exact_ndcg_at_10": .5}}]})["candidate_count"] == 3,
            "R4 end-to-end materializer boundary selection differs")
    print("NeuRoute R4 native end-to-end materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-r4-native-end-to-end.example.json")
    for name in ("coverage-result", "representative-codec-result",
                 "representative-codec-evidence", "mapped-access-result", "mapped-access-evidence",
                 "int8-compression-result", "int8-compression-evidence",
                 "layout-manifest", "native-input-manifest", "e5-root", "output-root"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if any(value is None for name, value in vars(args).items()
               if name not in {"self_test", "contract"}):
            parser.error("all R4 end-to-end materialization paths are required")
        materialize(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"materialize-neuroute-r4-native-end-to-end: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
