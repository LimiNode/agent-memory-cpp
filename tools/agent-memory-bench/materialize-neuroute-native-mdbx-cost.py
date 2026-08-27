#!/usr/bin/env python3
"""Materialize frozen NeuRoute/PCA routes for the native MDBX cost study."""

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
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("neuroute_native_mdbx_cost_planner", "plan-neuroute-native-mdbx-cost.py")
sanity = load("neuroute_native_mdbx_cost_sanity", "run-neuroute-training-sanity.py")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sequence_sha256(values: numpy.ndarray) -> str:
    packed = numpy.asarray(values, dtype="<u4")
    return hashlib.sha256(packed.tobytes()).hexdigest()


def update_query_sequence(digest: Any, query: int, values: numpy.ndarray) -> None:
    digest.update(int(query).to_bytes(4, "little"))
    digest.update(int(values.size).to_bytes(4, "little"))
    digest.update(numpy.asarray(values, dtype="<u4").tobytes())


def write_array(path: Path, values: numpy.ndarray, dtype: str) -> dict[str, Any]:
    packed = numpy.ascontiguousarray(values, dtype=dtype)
    path.parent.mkdir(parents=True, exist_ok=True)
    packed.tofile(path)
    return {"file": path.name, "sha256": sha256(path), "shape": list(packed.shape), "dtype": dtype}


def lexicographic_ranks(document_ids: numpy.ndarray) -> numpy.ndarray:
    order = numpy.argsort(document_ids, kind="stable")
    ranks = numpy.empty(order.size, dtype=numpy.uint32)
    ranks[order] = numpy.arange(order.size, dtype=numpy.uint32)
    return ranks


def expected_frontier(data: dict[str, Any], positions: list[int], logits: numpy.ndarray,
                      index: dict[str, Any], learned: bool, bits: int,
                      budgets: list[int], mass_target: float) -> list[dict[str, Any]]:
    rows = []
    for probes in budgets:
        candidate_digest = hashlib.sha256()
        hamming_digest = hashlib.sha256()
        adc_digest = hashlib.sha256()
        query_rows = []
        for local_query, position in enumerate(positions):
            requested = (sanity.alignment.german.diagnostic.addresses(logits[local_query], bits, probes)
                         if learned else sanity.alignment.german.direct.confidence_addresses(
                             logits[local_query], bits, probes))
            posting_entries = sum(len(index["postings"].get(address, ())) for address in requested)
            candidates, accepted = sanity.alignment.german.direct.candidate_union(
                requested, index["postings"], len(data["document_ids"]), mass_target)
            hamming, adc, _ = sanity.alignment.german.direct.cascade(data, position, candidates)
            update_query_sequence(candidate_digest, local_query, candidates)
            update_query_sequence(hamming_digest, local_query, hamming)
            update_query_sequence(adc_digest, local_query, adc)
            addresses = numpy.asarray(requested, dtype=numpy.uint32)
            query_rows.append({
                "query": local_query,
                "source_query_position": position,
                "requested_address_sha256": sequence_sha256(addresses),
                "requested_address_count": len(requested),
                "accepted_probe_count": len(accepted),
                "posting_entries_requested": posting_entries,
                "candidate_count": int(candidates.size),
                "candidate_sha256": sequence_sha256(candidates),
                "hamming_count": int(hamming.size),
                "hamming_sha256": sequence_sha256(hamming),
                "adc_count": int(adc.size),
                "adc_sha256": sequence_sha256(adc),
            })
        rows.append({
            "probes": probes,
            "candidate_sequence_sha256": candidate_digest.hexdigest(),
            "hamming_sequence_sha256": hamming_digest.hexdigest(),
            "adc_sequence_sha256": adc_digest.hexdigest(),
            "queries": query_rows,
        })
    return rows


def route_record(dataset_root: Path, data: dict[str, Any], positions: list[int],
                 route_id: str, kind: str, seed: int | None, document_logits: numpy.ndarray,
                 query_logits: numpy.ndarray, bits: int, replication: int,
                 budgets: list[int], model_sha256: str | None,
                 mass_target: float) -> dict[str, Any]:
    route_root = dataset_root / route_id
    route_root.mkdir(parents=True, exist_ok=True)
    document_addresses = numpy.asarray([
        sanity.alignment.german.direct.document_addresses(row, bits, replication)
        for row in document_logits
    ], dtype=numpy.uint16)
    compact_logits = numpy.asarray(query_logits, dtype=numpy.float32)
    address_payload = write_array(route_root / "document-addresses.u16le", document_addresses, "<u2")
    logits_payload = write_array(route_root / "query-logits.f32le", compact_logits, "<f4")
    index = sanity.alignment.german.direct.build_index(document_logits, data["documents"], bits, replication)
    return {
        "id": route_id,
        "kind": kind,
        "seed": seed,
        "bits": bits,
        "logit_dimensions": int(compact_logits.shape[1]),
        "document_replication": replication,
        "model_sha256": model_sha256,
        "document_addresses": address_payload,
        "query_logits": logits_payload,
        "occupied_address_count": len(index["postings"]),
        "posting_entry_count": index["posting_ids"],
        "probe_budgets": budgets,
        "expected": expected_frontier(data, positions, compact_logits, index, kind == "learned",
                                      bits, budgets, mass_target),
    }


def materialize_dataset(dataset: dict[str, Any], sanity_dataset: dict[str, Any],
                        roots: dict[str, Path], model_root: Path,
                        sanity_result: dict[str, Any], contract: dict[str, Any],
                        output_root: Path) -> dict[str, Any]:
    data, _, split = sanity.load_dataset(sanity_dataset, roots)
    id_to_position = {value: index for index, value in enumerate(data["query_ids"])}
    positions = [id_to_position[value] for value in split["configuration_selection_query_ids"]]
    require(len(positions) == dataset["configuration_queries"],
            f"native MDBX query partition differs: {dataset['id']}")
    report_dataset = next(row for row in sanity_result["datasets"] if row["id"] == dataset["id"])
    dataset_root = output_root / dataset["id"]
    dataset_root.mkdir(parents=True, exist_ok=True)
    common = {
        "document_codes": write_array(dataset_root / "document-codes.u8", data["document_codes"], "u1"),
        "query_codes": write_array(dataset_root / "query-codes.u8", data["query_codes"][positions], "u1"),
        "query_projection": write_array(dataset_root / "query-projection.f32le",
                                        data["query_projection"][positions], "<f4"),
        "adc_centroids": write_array(dataset_root / "adc-centroids.f32le", data["adc_centroids"], "<f4"),
        "document_id_rank": write_array(dataset_root / "document-id-rank.u32le",
                                        lexicographic_ranks(data["document_ids"]), "<u4"),
    }
    routes = []
    learned = contract["routes"]["learned"]
    for seed in learned["seeds"]:
        model_path = model_root / dataset["id"] / f"model-raw_euclidean_mined_pairs-{seed}.npz"
        model_row = next(row for row in report_dataset["models"]
                         if row["treatment"] == learned["treatment"] and row["seed"] == seed)
        require(model_path.is_file() and sha256(model_path) == model_row["model_sha256"],
                f"native MDBX learned model bytes differ: {dataset['id']} {seed}")
        arrays, metadata = sanity.read_model(model_path)
        require(metadata.get("dataset") == dataset["id"]
                and metadata.get("treatment") == learned["treatment"]
                and metadata.get("seed") == seed,
                f"native MDBX learned model metadata differs: {dataset['id']} {seed}")
        document_raw = sanity.infer(data["documents"], arrays, False)
        threshold = numpy.median(document_raw, axis=0).astype(numpy.float32)
        query_raw = sanity.infer(data["queries"], arrays, False)
        routes.append(route_record(
            dataset_root, data, positions, f"learned-{seed}", "learned", seed,
            document_raw - threshold, query_raw[positions] - threshold,
            learned["bits"], learned["document_replication"], learned["probe_budgets"],
            model_row["model_sha256"], contract["candidate_pipeline"]["candidate_mass_target"]))
    pca = contract["routes"]["pca"]
    pca_document, artifact = sanity.alignment.german.direct.document_head(data["documents"])
    pca_query = ((data["queries"] - artifact["document_mean"]) @ artifact["document_projection"]
                 - artifact["document_threshold"]).astype(numpy.float32)
    routes.append(route_record(
        dataset_root, data, positions, "pca", "pca", None, pca_document, pca_query[positions],
        pca["bits"], pca["document_replication"], pca["probe_budgets"], None,
        contract["candidate_pipeline"]["candidate_mass_target"]))
    return {
        "id": dataset["id"],
        "language": dataset["language"],
        "document_count": len(data["document_ids"]),
        "query_count": len(positions),
        "source_query_positions": positions,
        "e5_manifest_sha256": data["manifest_sha256"],
        "input_manifest_sha256": data["input_manifest_sha256"],
        "common": common,
        "routes": routes,
    }


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    require(sha256(args.sanity_result) == contract["activation"]["training_sanity_result_sha256"],
            "native MDBX training sanity result bytes differ")
    require(sha256(args.sanity_evidence) == contract["activation"]["training_sanity_evidence_sha256"],
            "native MDBX training sanity evidence bytes differ")
    sanity_result = json.loads(args.sanity_result.read_text(encoding="utf-8"))
    sanity_evidence = json.loads(args.sanity_evidence.read_text(encoding="utf-8"))
    require(sanity_evidence.get("integrity_replay_passed") is True
            and sanity_evidence.get("model_set_sha256")
                == contract["activation"]["training_sanity_model_set_sha256"],
            "native MDBX training sanity evidence receipt differs")
    sanity_contract = sanity.planner.load_contract(THIS / "neuroute-training-sanity.example.json")
    sanity_by_id = {row["id"]: row for row in sanity_contract["datasets"]}
    roots = {
        language: {name: getattr(args, f"{language}_{name}_root")
                   for name in ("result", "e5", "input")}
        for language in ("de", "fr", "ja")
    }
    require(all(path is not None for values in roots.values() for path in values.values()),
            "native MDBX dataset roots are required")
    datasets = [materialize_dataset(
        dataset, sanity_by_id[dataset["id"]], roots[dataset["language"]], args.model_root,
        sanity_result, contract, args.output_root) for dataset in contract["datasets"]]
    manifest = {
        "schema_version": 1,
        "family": "neuroute_native_mdbx_cost_materialization",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract),
        "training_sanity_result_sha256": sha256(args.sanity_result),
        "training_sanity_evidence_sha256": sha256(args.sanity_evidence),
        "materializer_source_sha256": sha256(Path(__file__)),
        "candidate_pipeline": contract["candidate_pipeline"],
        "storage": contract["storage"],
        "timing": contract["timing"],
        "datasets": datasets,
    }
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "manifest.json").write_bytes(canonical(manifest))


def self_test() -> None:
    values = numpy.asarray([7, 2, 99], dtype=numpy.uint32)
    require(sequence_sha256(values) == hashlib.sha256(values.astype("<u4").tobytes()).hexdigest(),
            "native MDBX sequence digest self-test differs")
    ranks = lexicographic_ranks(numpy.asarray(["z", "a", "m"]))
    require(ranks.tolist() == [2, 0, 1], "native MDBX ID rank self-test differs")
    print("NeuRoute native MDBX cost materializer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path,
                        default=THIS / "neuroute-native-mdbx-cost.example.json")
    parser.add_argument("--sanity-result", type=Path)
    parser.add_argument("--sanity-evidence", type=Path)
    parser.add_argument("--model-root", type=Path)
    for language in ("de", "fr", "ja"):
        parser.add_argument(f"--{language}-result-root", type=Path)
        parser.add_argument(f"--{language}-e5-root", type=Path)
        parser.add_argument(f"--{language}-input-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        require(all(value is not None for value in (
            args.sanity_result, args.sanity_evidence, args.model_root, args.output_root)),
            "native MDBX materialization paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"materialize-neuroute-native-mdbx-cost: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
