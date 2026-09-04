#!/usr/bin/env python3
"""Measure nonlinear INT5 inside frozen ADC256 top-64 final pools."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
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


planner = load("neuroute_final_nonlinear_int5_planner",
               "plan-neuroute-final-nonlinear-int5.py")


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


def source_hashes() -> dict[str, str]:
    names = ("plan-neuroute-final-nonlinear-int5.py",
             "run-neuroute-final-nonlinear-int5.py")
    return {name: sha256(THIS / name) for name in names}


def resolve(root: Path, dataset: str, payload: dict[str, Any],
            relative: str = "") -> Path:
    path = Path(payload["file"])
    if not payload.get("external_frozen_root", False):
        path = root / dataset / relative / path
    require(path.is_file() and sha256(path) == payload["sha256"],
            "final nonlinear INT5 payload differs")
    return path


def read_array(root: Path, dataset: str, payload: dict[str, Any],
               dtype: str, relative: str = "") -> numpy.ndarray:
    return numpy.fromfile(resolve(root, dataset, payload, relative),
                          dtype=dtype).reshape(payload["shape"])


def output_entry(root: Path, manifest: dict[str, Any], name: str) -> Path:
    row = manifest["outputs"][name]
    path = root / row["path"]
    require(path.is_file() and sha256(path) == row["sha256"],
            f"final nonlinear INT5 E5 output differs: {name}")
    return path


def read_ids(path: Path) -> list[str]:
    return [str(json.loads(line)["id"])
            for line in path.read_text(encoding="utf-8").splitlines() if line]


def read_qrels(path: Path) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        query, _, document, grade = line.split()
        result.setdefault(query, {})[document] = int(grade)
    return result


def query_positions(final_queries: numpy.ndarray, e5_queries: numpy.ndarray,
                    query_ids: list[str], dataset_id: str,
                    collision_resolution: list[dict[str, Any]]) -> list[int]:
    by_bytes: dict[bytes, list[int]] = {}
    for position, row in enumerate(e5_queries):
        by_bytes.setdefault(row.tobytes(), []).append(position)
    result = []
    resolutions = {(dataset, int(entry["local_query"])): entry["query_id"]
                   for entry in collision_resolution
                   for dataset in entry["datasets"]}
    for local, row in enumerate(final_queries):
        matches = by_bytes.get(row.tobytes(), [])
        require(matches, "final nonlinear INT5 query-vector mapping differs")
        if len(matches) == 1:
            result.append(matches[0])
            continue
        wanted = resolutions.get((dataset_id, local))
        selected = [position for position in matches
                    if query_ids[position] == wanted]
        require(len(selected) == 1,
                "final nonlinear INT5 query-vector collision differs")
        result.append(selected[0])
    require(len(set(result)) == len(result),
            "final nonlinear INT5 mapped queries are duplicated")
    return result


def ndcg(ranked_ids: list[str], grades: dict[str, int]) -> float:
    value = sum((2.0 ** grades.get(document, 0) - 1.0) /
                math.log2(rank + 2.0)
                for rank, document in enumerate(ranked_ids[:10]))
    ideal = sorted(grades.values(), reverse=True)[:10]
    denominator = sum((2.0 ** grade - 1.0) / math.log2(rank + 2.0)
                      for rank, grade in enumerate(ideal))
    return min(1.0, max(0.0, value / denominator)) if denominator else 0.0


def coefficients(treatment: dict[str, Any]) -> numpy.ndarray:
    signed = numpy.arange(-15, 16, dtype=numpy.float32)
    magnitude = numpy.abs(signed) / 15.0
    kind = treatment["kind"]
    if kind == "uniform":
        values = magnitude
    elif kind == "power":
        values = numpy.power(magnitude, 1.0 / float(treatment["parameter"]))
    else:
        require(kind == "mulaw", "final nonlinear INT5 compander differs")
        parameter = float(treatment["parameter"])
        values = numpy.expm1(magnitude * math.log1p(parameter)) / parameter
    return numpy.asarray(numpy.copysign(values, signed), dtype=numpy.float32)


def quantize(values: numpy.ndarray, treatment: dict[str, Any]
             ) -> tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray]:
    amplitudes = numpy.max(numpy.abs(values), axis=1).astype(numpy.float32)
    amplitudes[amplitudes == 0] = 1.0
    normalized = numpy.asarray(values / amplitudes[:, None], dtype=numpy.float32)
    kind = treatment["kind"]
    if kind == "uniform":
        transformed = normalized
    elif kind == "power":
        transformed = numpy.copysign(numpy.power(numpy.abs(normalized),
            float(treatment["parameter"])), normalized)
    else:
        require(kind == "mulaw", "final nonlinear INT5 compander differs")
        parameter = float(treatment["parameter"])
        transformed = numpy.copysign(numpy.log1p(
            parameter * numpy.abs(normalized)) / math.log1p(parameter), normalized)
    signed = numpy.clip(numpy.rint(transformed * 15.0), -15, 15).astype(numpy.int16)
    table = coefficients(treatment)
    reconstructed = numpy.asarray(
        table[signed + 15] * amplitudes[:, None], dtype=numpy.float32)
    return signed, amplitudes, reconstructed


def sequence(values: numpy.ndarray) -> str:
    return hashlib.sha256(numpy.asarray(values, dtype="<u4").tobytes()).hexdigest()


def inversion_metrics(reference: numpy.ndarray, current: numpy.ndarray,
                      reference_scores: numpy.ndarray,
                      boundaries: list[float]) -> dict[str, Any]:
    current_rank = numpy.empty(len(current), dtype=numpy.int32)
    current_rank[current] = numpy.arange(len(current), dtype=numpy.int32)
    ordered_ranks = current_rank[reference]
    pairs = 0
    inversions = 0
    cross = 0
    for left in range(len(reference)):
        for right in range(left + 1, len(reference)):
            pairs += 1
            if ordered_ranks[left] > ordered_ranks[right]:
                inversions += 1
                if left < 10 <= right:
                    cross += 1
    margin_counts = []
    lower = -numpy.inf
    for upper in [*boundaries, numpy.inf]:
        count = 0
        flipped = 0
        for left in range(len(reference)):
            for right in range(left + 1, len(reference)):
                margin = abs(float(reference_scores[reference[left]] -
                                   reference_scores[reference[right]]))
                if margin > lower and margin <= upper:
                    count += 1
                    flipped += int(ordered_ranks[left] > ordered_ranks[right])
        margin_counts.append({"minimum_exclusive": None if not numpy.isfinite(lower)
                              else lower,
                              "maximum_inclusive": None if not numpy.isfinite(upper)
                              else upper,
                              "pairs": count, "inversions": flipped})
        lower = upper
    tau_a = 1.0 - 2.0 * inversions / pairs
    return {"all_pair_inversions": inversions, "all_pairs": pairs,
            "tau_a": tau_a,
            "top10_vs_rest_inversions": cross,
            "top10_vs_rest_pairs": 10 * (len(reference) - 10),
            "margin_bins": margin_counts}


def evaluate_query(documents: numpy.ndarray, query: numpy.ndarray,
                   pool: numpy.ndarray, ranks: numpy.ndarray,
                   document_ids: list[str], grades: dict[str, int],
                   treatments: list[dict[str, Any]], boundaries: list[float]
                   ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    values = numpy.asarray(documents[pool], dtype=numpy.float32)
    fp32_scores = numpy.asarray(numpy.sum(values * query[None, :], axis=1),
                                dtype=numpy.float32)
    fp32_order = numpy.lexsort((ranks[pool], -fp32_scores)).astype(numpy.int32)
    fp32_ranked = pool[fp32_order]
    baseline = {"ndcg_at_10": ndcg([document_ids[int(value)]
        for value in fp32_ranked[:10]], grades),
        "ranked_sha256": sequence(fp32_ranked[:10])}
    rows = []
    for treatment in treatments:
        _, _, reconstructed = quantize(values, treatment)
        scores = numpy.asarray(numpy.sum(reconstructed * query[None, :], axis=1),
                               dtype=numpy.float32)
        order = numpy.lexsort((ranks[pool], -scores)).astype(numpy.int32)
        ranked = pool[order]
        inversions = inversion_metrics(fp32_order, order, fp32_scores, boundaries)
        top10 = set(int(value) for value in ranked[:10])
        fp32_top10 = set(int(value) for value in fp32_ranked[:10])
        fp32_band = set(int(value) for value in fp32_ranked[8:12])
        current_band = set(int(value) for value in ranked[8:12])
        rows.append({"treatment": treatment["id"],
            "ndcg_at_10": ndcg([document_ids[int(value)]
                for value in ranked[:10]], grades),
            "ranked_sha256": sequence(ranked[:10]),
            "top10_overlap": len(top10 & fp32_top10) / 10.0,
            "top1_agreement": bool(ranked[0] == fp32_ranked[0]),
            "positions_9_to_12_flip_rate": 1.0 -
                len(fp32_band & current_band) / 4.0,
            "mean_absolute_score_error": float(numpy.mean(
                numpy.abs(scores - fp32_scores))),
            "maximum_absolute_score_error": float(numpy.max(
                numpy.abs(scores - fp32_scores))),
            **inversions})
    return baseline, rows


def dataset_rows(dataset: dict[str, Any], e5_root: Path,
                 materialization_root: Path, contract: dict[str, Any]
                 ) -> list[dict[str, Any]]:
    dataset_id = dataset["id"]
    e5_manifest = json.loads((e5_root / "manifest.json").read_text(
        encoding="utf-8"))
    document_ids = read_ids(output_entry(e5_root, e5_manifest,
                                         "evaluation_document_ids"))
    all_query_ids = read_ids(output_entry(e5_root, e5_manifest,
                                          "evaluation_query_ids"))
    qrels = read_qrels(output_entry(e5_root, e5_manifest, "evaluation_qrels"))
    all_query_vectors = numpy.memmap(output_entry(e5_root, e5_manifest,
        "evaluation_query_vectors"), mode="r", dtype="<f4",
        shape=(len(all_query_ids), 384))
    fp32 = next(row for row in dataset["representations"] if row["id"] == "fp32")
    documents = numpy.memmap(resolve(materialization_root, dataset_id,
        fp32["encoded"]), mode="r", dtype="<f4",
        shape=tuple(fp32["encoded"]["shape"]))
    final_queries = read_array(materialization_root, dataset_id,
                               dataset["query_vectors"], "<f4")
    positions = query_positions(final_queries, all_query_vectors, all_query_ids,
        dataset_id, contract["query_vector_collision_resolution"])
    require(len(document_ids) == len(documents) and
            len(positions) == int(dataset["query_count"]),
            "final nonlinear INT5 dataset cardinality differs")
    id_ranks = read_array(materialization_root, dataset_id,
                          dataset["document_id_rank"], "<u4")
    rows = []
    boundaries = contract["diagnostics"]["fp32_pair_margin_bins"]
    for route in dataset["routes"]:
        seed = int(route["seed"])
        pools = read_array(materialization_root, dataset_id, route["pool"],
                           "<u4", str(seed))
        for local, source in enumerate(positions):
            partition = "parameter_selection" if local % 2 == 0 else \
                "heldout_confirmation"
            query_id = all_query_ids[source]
            baseline, treatments = evaluate_query(documents,
                numpy.asarray(final_queries[local], dtype=numpy.float32),
                pools[local], id_ranks, document_ids, qrels.get(query_id, {}),
                contract["treatments"], boundaries)
            for value in treatments:
                rows.append({"dataset": dataset_id, "seed": seed,
                    "partition": partition, "query": local,
                    "query_id": query_id, "fp32": baseline, **value})
    return rows


def combine_margin_bins(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for index in range(len(rows[0]["margin_bins"])):
        template = rows[0]["margin_bins"][index]
        pairs = sum(row["margin_bins"][index]["pairs"] for row in rows)
        inversions = sum(row["margin_bins"][index]["inversions"] for row in rows)
        result.append({"minimum_exclusive": template["minimum_exclusive"],
            "maximum_inclusive": template["maximum_inclusive"],
            "pairs": pairs, "inversions": inversions,
            "inversion_rate": inversions / pairs if pairs else None})
    return result


def summarize(rows: list[dict[str, Any]], partition: str,
              treatments: list[dict[str, Any]], quality: dict[str, float]
              ) -> list[dict[str, Any]]:
    selected_partition = [row for row in rows if row["partition"] == partition]
    uniform = {(row["dataset"], row["seed"], row["query"]): row["ndcg_at_10"]
               for row in selected_partition if row["treatment"] == "int5_uniform"}
    output = []
    for treatment in treatments:
        current = [row for row in selected_partition
                   if row["treatment"] == treatment["id"]]
        dataset_losses = []
        dataset_regressions = []
        for dataset in ("de-25k", "fr-25k", "ja-25k", "de-1m"):
            values = [row for row in current if row["dataset"] == dataset]
            dataset_losses.append(float(numpy.mean([
                row["fp32"]["ndcg_at_10"] - row["ndcg_at_10"]
                for row in values])))
            dataset_regressions.append(float(numpy.mean([
                uniform[(row["dataset"], row["seed"], row["query"])] -
                row["ndcg_at_10"] for row in values])))
        all_pairs = sum(row["all_pairs"] for row in current)
        all_inversions = sum(row["all_pair_inversions"] for row in current)
        cross_pairs = sum(row["top10_vs_rest_pairs"] for row in current)
        cross_inversions = sum(row["top10_vs_rest_inversions"] for row in current)
        mean_loss = float(numpy.mean(dataset_losses))
        output.append({"treatment": treatment["id"],
            "dataset_losses_vs_fp32": dataset_losses,
            "cross_dataset_mean_loss_vs_fp32": mean_loss,
            "dataset_regressions_vs_uniform": dataset_regressions,
            "cross_dataset_mean_regression_vs_uniform": float(
                numpy.mean(dataset_regressions)),
            "absolute_quality_eligible": mean_loss <= quality[
                "maximum_cross_dataset_mean_ndcg_loss_vs_fp32"] and
                max(dataset_losses) <= quality[
                    "maximum_per_dataset_ndcg_loss_vs_fp32"],
            "mean_top10_overlap": float(numpy.mean([
                row["top10_overlap"] for row in current])),
            "top1_agreement": float(numpy.mean([
                row["top1_agreement"] for row in current])),
            "tau_a": 1.0 - 2.0 * all_inversions / all_pairs,
            "top10_vs_rest_inversion_rate": cross_inversions / cross_pairs,
            "mean_positions_9_to_12_flip_rate": float(numpy.mean([
                row["positions_9_to_12_flip_rate"] for row in current])),
            "mean_absolute_score_error": float(numpy.mean([
                row["mean_absolute_score_error"] for row in current])),
            "maximum_absolute_score_error": float(max(
                row["maximum_absolute_score_error"] for row in current)),
            "pairwise_inversions_by_fp32_margin": combine_margin_bins(current),
            "query_seed_rows": len(current)})
    return output


def activation(args: argparse.Namespace) -> dict[str, Any]:
    roots = {row["id"]: getattr(args, row["id"].replace("-", "_") + "_e5_root")
             for row in planner.load_contract(args.contract)["datasets"]}
    return {"legacy_final_codec_quality_sha256": sha256(args.legacy_quality),
        "legacy_final_codec_evidence_sha256": sha256(args.legacy_evidence),
        "final_materialization_sha256": sha256(
            args.final_materialization_root / "manifest.json"),
        "e5_manifest_sha256": {name: sha256(root / "manifest.json")
                               for name, root in roots.items()}}


def run(args: argparse.Namespace) -> None:
    contract = planner.load_contract(args.contract)
    actual = activation(args)
    expected = {key: value for key, value in contract["activation"].items()
                if key != "native_executable_sha256"}
    require(actual == expected, "final nonlinear INT5 activation differs")
    legacy = json.loads(args.legacy_evidence.read_text(encoding="utf-8"))
    require(legacy["decision"]["selected_quantizer"] == "int5_document" and
            legacy["decision"]["selected_layout"] == "simdcomp_bp128",
            "final nonlinear INT5 legacy decision differs")
    manifest = json.loads((args.final_materialization_root /
        "manifest.json").read_text(encoding="utf-8"))
    by_id = {row["id"]: row for row in manifest["datasets"]}
    all_rows = []
    for row in contract["datasets"]:
        dataset_id = row["id"]
        root = getattr(args, dataset_id.replace("-", "_") + "_e5_root")
        all_rows.extend(dataset_rows(by_id[dataset_id], root,
                                     args.final_materialization_root, contract))
    selection = summarize(all_rows, "parameter_selection",
                          contract["treatments"], contract["quality"])
    nonlinear = [row for row in selection
                 if row["treatment"] != "int5_uniform" and
                 row["absolute_quality_eligible"]]
    require(nonlinear, "final nonlinear INT5 has no selection candidate")
    selected = min(nonlinear, key=lambda row: (
        row["cross_dataset_mean_loss_vs_fp32"],
        row["top10_vs_rest_inversion_rate"], row["treatment"]))["treatment"]
    confirmation = summarize(all_rows, "heldout_confirmation",
                             contract["treatments"], contract["quality"])
    selected_confirmation = next(row for row in confirmation
                                 if row["treatment"] == selected)
    quality = contract["quality"]
    confirmation_passes = (
        selected_confirmation["absolute_quality_eligible"] and
        selected_confirmation["cross_dataset_mean_regression_vs_uniform"] <=
            quality["maximum_confirmation_mean_ndcg_regression_vs_uniform"] and
        max(selected_confirmation["dataset_regressions_vs_uniform"]) <=
            quality["maximum_confirmation_per_dataset_regression_vs_uniform"])
    output = {"schema_version": 1,
        "family": "neuroute_final_nonlinear_int5_quality_result",
        "claim_scope": contract["claim_scope"],
        "contract_sha256": sha256(args.contract), "activation": actual,
        "source_files_sha256": source_hashes(), "matrix": planner.plan(contract),
        "rows": all_rows,
        "parameter_selection": {"summary": selection,
            "selected_nonlinear": selected,
            "rule": contract["decision"]["parameter_selection"]},
        "heldout_confirmation": {"summary": confirmation,
            "selected_nonlinear": selected,
            "selected_passes_quality": confirmation_passes,
            "opened_after_parameter_selection": True},
        "decision": {"selected_nonlinear_treatment": selected,
            "selected_nonlinear_passes_heldout_quality": confirmation_passes,
            "native_latency_pending": confirmation_passes,
            "native_latency_skipped_reason": None if confirmation_passes else
                "selected_nonlinear_failed_heldout_quality",
            "production_selection_licensed": False}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(output))


def self_test() -> None:
    contract = planner.load_contract(THIS /
        "neuroute-final-nonlinear-int5.example.json")
    values = numpy.asarray([[1.0, -.25, 0.0]], dtype=numpy.float32)
    power = next(row for row in contract["treatments"]
                 if row["id"] == "int5_power_050")
    signed, amplitudes, reconstructed = quantize(values, power)
    require(signed.tolist() == [[15, -8, 0]] and amplitudes.tolist() == [1.0]
            and reconstructed.shape == values.shape,
            "final nonlinear INT5 quantizer self-test differs")
    reference = numpy.arange(4, dtype=numpy.int32)
    current = numpy.asarray([0, 2, 1, 3], dtype=numpy.int32)
    metrics = inversion_metrics(reference, current,
        numpy.asarray([4.0, 3.0, 2.0, 1.0], dtype=numpy.float32), [.5])
    require(metrics["all_pair_inversions"] == 1,
            "final nonlinear INT5 inversion self-test differs")
    print("NeuRoute final nonlinear INT5 runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=THIS /
                        "neuroute-final-nonlinear-int5.example.json")
    parser.add_argument("--legacy-quality", type=Path)
    parser.add_argument("--legacy-evidence", type=Path)
    parser.add_argument("--final-materialization-root", type=Path)
    for dataset in ("de-25k", "fr-25k", "ja-25k", "de-1m"):
        parser.add_argument(f"--{dataset}-e5-root", type=Path,
                            dest=dataset.replace("-", "_") + "_e5_root")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        ignored = {"self_test", "contract"}
        if any(value is None for name, value in vars(args).items()
               if name not in ignored):
            parser.error("all final nonlinear INT5 paths are required")
        run(args)
        return 0
    except (OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError, numpy.linalg.LinAlgError) as error:
        print(f"run-neuroute-final-nonlinear-int5: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
