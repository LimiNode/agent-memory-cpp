#!/usr/bin/env python3
"""Learn a query-only MIH projection with dynamic danger mining and a Pareto gate."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

THIS = Path(__file__).resolve()
BANDS = 16


class TrainingError(RuntimeError):
    pass


def load(name: str, key: str) -> Any:
    spec = importlib.util.spec_from_file_location(key, THIS.with_name(name))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[key] = module; spec.loader.exec_module(module)
    return module


nlb = load("train-nlb-qrels-supervised.py", "trust_region_nlb")
banding = load("evaluate-mih-banding.py", "trust_region_banding")
shared = load("evaluate-projection-quantization.py", "trust_region_shared")


def require(value: bool, message: str) -> None:
    if not value:
        raise TrainingError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict[str, str]:
    names = (THIS.name, "train-nlb-qrels-supervised.py", "evaluate-mih-banding.py", "evaluate-projection-quantization.py", "requirements-binary-autoencoder-trainer.txt")
    return {name: digest(THIS.with_name(name)) for name in names}


def execution_contract(values: dict[str, Any], train_query_count: int, validation_query_count: int, held_out_exclusion: dict[str, Any]) -> dict[str, Any]:
    """Return the complete frozen invocation identity for either gate outcome."""
    if values["routing_estimator"] == "schedule-aware-stratified-union-posting-v2":
        routing_surrogate = {
            "id": "schedule_aware_stratified_union_posting_proxy_v2",
            "weight": values["routing_work_weight"],
            "strata": values["routing_strata"],
            "pool_per_stratum": values["routing_pool_per_stratum"],
            "temperature": values["routing_temperature"],
            "band_radii": [3] * 9 + [2] * 7,
            "bipolar_agreement_thresholds": [9.0] * 9 + [11.0] * 7,
            "separate_terms": ["expected_posting_visits", "deduplicated_union_probability"],
        }
    else:
        require(values["routing_estimator"] == "sampled-band-radius-soft-collision-v1", "routing estimator differs")
        routing_surrogate = {
            "id": "sampled_band_radius_soft_collision_log_work_proxy_v1",
            "weight": values["routing_work_weight"], "pool_size": values["routing_pool_size"],
            "temperature": values["routing_temperature"], "radius": values["routing_radius"],
        }
    return {
        "epochs": values["epochs"], "batch_size": values["batch_size"],
        "learning_rate": values["learning_rate"], "itq_iterations": values["itq_iterations"],
        "positive_radius": values["positive_radius"], "negative_radius": values["negative_radius"],
        "objective": "dynamic_current_wq_danger_mining_code_trust_region_routing_surrogate_v1",
        "hard_negative_mining": {"id": "dynamic_current_wq_ranked_downstream_danger_v1", "count": values["hard_negative_count"], "ranking": "low_hamming_high_e5_high_posting_mass_then_row_v1"},
        "code_trust_region": {"id": "direct_soft_query_code_drift_to_w0_v1", "weight": values["code_drift_weight"]},
        "routing_work_surrogate": routing_surrogate,
        "train_validation_split": {"id": "sha256_seeded_query_split_v1", "validation_fraction": values["validation_fraction"], "train_query_count": train_query_count, "validation_query_count": validation_query_count},
        "checkpoint_policy": "deterministic_train_validation_pareto_gate_v1",
        "pareto": {"minimum_adc_delta": 0.0, "maximum_work_multiplier": values["maximum_work_multiplier"], "maximum_mean_hamming_drift": values["maximum_mean_hamming_drift"]},
        "held_out_exclusion": held_out_exclusion,
    }


def write_f32(path: Path, value: Any) -> None:
    path.write_bytes(value.astype("<f4", copy=False).tobytes())


def ordered_split(query_ids: list[str], seed: int, validation_fraction: float) -> tuple[list[str], list[str]]:
    ordered = sorted(query_ids, key=lambda value: hashlib.sha256(f"{seed}\0{value}".encode()).digest())
    validation_count = max(1, int(round(len(ordered) * validation_fraction)))
    return ordered[validation_count:], ordered[:validation_count]


def visit_count(index: list[dict[int, Any]], code: Any, ranges: list[tuple[int, int]], radii: list[int]) -> int:
    total = 0
    for band, ((start, stop), radius) in enumerate(zip(ranges, radii)):
        for key in banding.probe_keys(banding.band_key(code, start, stop), stop - start, radius):
            values = index[band].get(key)
            if values is not None:
                total += int(values.size)
    return total


def validation_metrics(data: dict[str, Any], document_codes: Any, document_projection: Any, query_weights: Any, thresholds: Any, index: list[dict[int, Any]], ranges: list[tuple[int, int]], radii: list[int], centers: Any, ids: list[str]) -> dict[str, float]:
    import numpy
    document_ids = numpy.asarray(data["document_ids"])
    query_rows = {value: index for index, value in enumerate(data["query_ids"])}
    candidates: list[float] = []; postings: list[float] = []; survival: list[float] = []; drift: list[float] = []
    base_weights = document_projection["weights"]
    for query_id in ids:
        row = query_rows[query_id]; vector = numpy.clip(numpy.asarray(data["query_vectors"][row], dtype=numpy.float32), -1.0, 1.0)
        base_code = vector @ base_weights.T + thresholds >= 0.0
        query_projection = vector @ query_weights.T + thresholds
        query_code = query_projection >= 0.0
        union, _ = banding.candidate_union(index, query_code, ranges, radii)
        restricted = banding.stable_hamming_order(document_codes, query_code, document_ids, union)[:768]
        second = banding.binary_adc_order(query_projection, centers, document_codes, document_ids, restricted)[:256]
        exact = numpy.asarray(data["document_vectors"]) @ numpy.asarray(data["query_vectors"])[row]
        oracle = numpy.lexsort((document_ids, -exact))[:10]
        candidates.append(float(union.size)); postings.append(float(visit_count(index, query_code, ranges, radii)))
        survival.append(float(numpy.isin(oracle, second).sum()) / 10.0)
        drift.append(float(numpy.count_nonzero(base_code != query_code)))
    return {"adc_survival": float(numpy.mean(survival)), "candidates": float(numpy.mean(candidates)), "postings": float(numpy.mean(postings)), "mean_hamming_drift": float(numpy.mean(drift)), "query_count": len(ids)}


def mine_current_false_positives(data: dict[str, Any], document_codes: Any, index: list[dict[int, Any]], ranges: list[tuple[int, int]], radii: list[int], query_weights: Any, thresholds: Any, query_ids: list[str], count: int) -> dict[str, list[int]]:
    import numpy
    document_ids = numpy.asarray(data["document_ids"]); rows = {value: index for index, value in enumerate(data["query_ids"])}
    posting_mass = numpy.zeros(document_codes.shape[0], dtype=numpy.int64)
    for row, code in enumerate(document_codes):
        posting_mass[row] = sum(int(index[band][banding.band_key(code, start, stop)].size) for band, (start, stop) in enumerate(ranges))
    result: dict[str, list[int]] = {}
    for query_id in query_ids:
        row = rows[query_id]; vector = numpy.clip(numpy.asarray(data["query_vectors"][row], dtype=numpy.float32), -1.0, 1.0)
        projection = vector @ query_weights.T + thresholds; code = projection >= 0.0
        union, _ = banding.candidate_union(index, code, ranges, radii)
        positives = set(data["positive"][query_id])
        eligible = numpy.asarray([value for value in union.tolist() if document_ids[int(value)] not in positives], dtype=numpy.int32)
        require(eligible.size >= count, "insufficient dynamically mined false positives")
        hamming = numpy.count_nonzero(document_codes[eligible] != code, axis=1)
        cosine = numpy.asarray(data["document_vectors"])[eligible] @ numpy.asarray(data["query_vectors"])[row]
        order = numpy.lexsort((eligible, -posting_mass[eligible], -cosine, hamming))
        result[query_id] = [int(value) for value in eligible[order[:count]]]
    return result


def routing_pools(document_codes: Any, index: list[dict[int, Any]], ranges: list[tuple[int, int]], strata: int, per_stratum: int, seed: int, numpy: Any, torch: Any) -> list[tuple[Any, int]]:
    require(strata > 0 and per_stratum > 0, "routing strata differ")
    mass = numpy.zeros(document_codes.shape[0], dtype=numpy.int64)
    for row, code in enumerate(document_codes):
        mass[row] = visit_count(index, code, ranges, [0] * len(ranges))
    ordered = numpy.lexsort((numpy.arange(document_codes.shape[0]), mass))
    result: list[tuple[Any, int]] = []
    for stratum, rows in enumerate(numpy.array_split(ordered, strata)):
        require(rows.size > 0, "routing stratum is empty")
        rng = numpy.random.default_rng(int.from_bytes(hashlib.sha256(f"schedule-aware-routing\0{seed}\0{stratum}".encode()).digest()[:16], "big"))
        selected = rows if rows.size <= per_stratum else numpy.sort(rng.choice(rows, size=per_stratum, replace=False))
        codes = numpy.where(document_codes[selected], 1.0, -1.0).astype(numpy.float32).reshape(-1, BANDS, 16)
        result.append((torch.from_numpy(codes), int(rows.size)))
    return result


def schedule_aware_routing_loss(query_soft: Any, pools: list[tuple[Any, int]], radii: list[int], temperature: float, torch: Any) -> Any:
    code = torch.tanh(4.0 * query_soft).reshape(-1, BANDS, 16)
    thresholds = torch.tensor([16.0 - 2.0 * radius - 1.0 for radius in radii], dtype=code.dtype, device=code.device).reshape(1, BANDS, 1)
    expected_visits = 0.0
    expected_union = 0.0
    for pool, population in pools:
        agreement = torch.einsum("bmk,pmk->bmp", code, pool.to(code.device))
        probability = torch.sigmoid(temperature * (agreement - thresholds))
        expected_visits = expected_visits + probability.sum(1).mean(1) * (float(population) / float(pool.shape[0]))
        expected_union = expected_union + (1.0 - torch.prod(1.0 - probability, dim=1)).mean(1) * (float(population) / float(pool.shape[0]))
    return (torch.log1p(expected_visits) + torch.log1p(expected_union)).mean()


def train(args: Any) -> None:
    require(args.output_root is not None and not args.output_root.exists(), "trainer output root must be new")
    require(args.epochs > 0 and args.hard_negative_count > 0 and args.validation_query_count > 0 and 0.0 < args.validation_fraction < 0.5, "invalid trainer arguments")
    base = nlb.load_base(); base.verify_environment()
    import numpy; import torch; import torch.nn.functional as functional
    data = nlb.load_supervised_materialization(args.materialization_root, base, numpy)
    prepared = json.loads((args.materialization_root / "prepared-study-manifest.json").read_text(encoding="utf-8"))
    document_weights, thresholds = nlb.initialize_itq_median(numpy.asarray(data["train_vectors"]), 256, args.seed, args.itq_iterations, numpy)
    document_codes = numpy.clip(numpy.asarray(data["document_vectors"]), -1.0, 1.0) @ document_weights.T + thresholds >= 0.0
    ranges = banding.band_ranges(256, BANDS); radii = banding.global_radius_schedule(56, BANDS); index = banding.build_index(document_codes, ranges)
    calibration_projection = numpy.clip(numpy.asarray(data["train_vectors"]), -1.0, 1.0) @ document_weights.T + thresholds
    centers = shared.conditional_centers(calibration_projection, calibration_projection >= 0.0, 2)
    train_ids, validation_ids = ordered_split(list(data["query_ids"]), args.seed, args.validation_fraction)
    validation_ids = validation_ids[:args.validation_query_count]
    query_rows = {value: index for index, value in enumerate(data["query_ids"])}; document_rows = {value: index for index, value in enumerate(data["document_ids"])}
    positives = {query: sorted(values, key=lambda document: (-values[document], document))[0] for query, values in data["positive"].items()}
    torch.set_num_threads(1); torch.manual_seed(args.seed); torch.use_deterministic_algorithms(True)
    weight = torch.nn.Parameter(torch.from_numpy(document_weights.copy())); document_tensor = torch.from_numpy(document_weights.copy())
    thresholds_tensor = torch.from_numpy(thresholds.copy()); optimizer = torch.optim.AdamW((weight,), lr=args.learning_rate, weight_decay=0.0)
    pool_rng = numpy.random.default_rng(int.from_bytes(hashlib.sha256(f"routing-pool\0{args.seed}".encode()).digest()[:16], "big"))
    pool_rows = pool_rng.choice(document_codes.shape[0], size=min(args.routing_pool_size, document_codes.shape[0]), replace=False)
    pool_codes = torch.from_numpy(numpy.where(document_codes[pool_rows], 1.0, -1.0).astype(numpy.float32).reshape(-1, BANDS, 16))
    calibrated_pools = routing_pools(document_codes, index, ranges, args.routing_strata, args.routing_pool_per_stratum, args.seed, numpy, torch) if args.routing_estimator == "schedule-aware-stratified-union-posting-v2" else []
    baseline = validation_metrics(data, document_codes, {"weights": document_weights}, document_weights, thresholds, index, ranges, radii, centers, validation_ids)
    print(json.dumps({"seed": args.seed, "baseline_validation": baseline}, sort_keys=True), flush=True)
    history = [{"epoch": -1, "validation": baseline, "pareto_admissible": False, "mining": "w0_baseline_only"}]
    selected: tuple[int, Any, dict[str, float]] | None = None
    for epoch in range(args.epochs):
        mined = mine_current_false_positives(data, document_codes, index, ranges, radii, weight.detach().numpy(), thresholds, train_ids, args.hard_negative_count)
        rng = numpy.random.default_rng(int.from_bytes(hashlib.sha256(f"{args.seed}\0{epoch}".encode()).digest()[:16], "big")); order = numpy.arange(len(train_ids)); rng.shuffle(order)
        for start in range(0, len(order), args.batch_size):
            chosen = [train_ids[int(value)] for value in order[start:start + args.batch_size]]
            query = torch.from_numpy(numpy.asarray(data["query_vectors"][[query_rows[value] for value in chosen]], dtype=numpy.float32).copy())
            positive = torch.from_numpy(numpy.asarray(data["document_vectors"][[document_rows[positives[value]] for value in chosen]], dtype=numpy.float32).copy())
            negative = torch.from_numpy(numpy.asarray(data["document_vectors"][[mined[value][epoch % args.hard_negative_count] for value in chosen]], dtype=numpy.float32).copy())
            query = torch.clamp(query, -1.0, 1.0); positive = torch.clamp(positive, -1.0, 1.0); negative = torch.clamp(negative, -1.0, 1.0)
            def soft_code(vector: Any, matrix: Any) -> Any:
                return torch.tanh(vector @ matrix.T + thresholds_tensor)
            query_soft = soft_code(query, weight); base_soft = soft_code(query, document_tensor)
            positive_soft = soft_code(positive, document_tensor); negative_soft = soft_code(negative, document_tensor)
            query_hard = query_soft + (torch.where(query_soft >= 0.0, torch.ones_like(query_soft), -torch.ones_like(query_soft)) - query_soft).detach()
            positive_hard = positive_soft + (torch.where(positive_soft >= 0.0, torch.ones_like(positive_soft), -torch.ones_like(positive_soft)) - positive_soft).detach()
            negative_hard = negative_soft + (torch.where(negative_soft >= 0.0, torch.ones_like(negative_soft), -torch.ones_like(negative_soft)) - negative_soft).detach()
            positive_distance = 0.5 * (256.0 - (query_hard * positive_hard).sum(1)); negative_distance = 0.5 * (256.0 - (query_hard * negative_hard).sum(1))
            ranking = functional.softplus((positive_distance - args.positive_radius) / 4.0).mean() + functional.softplus((args.negative_radius - negative_distance) / 4.0).mean()
            drift = (query_soft - base_soft).square().mean()
            if args.routing_estimator == "sampled-band-radius-soft-collision-v1":
                routing_code = torch.tanh(4.0 * query_soft).reshape(-1, BANDS, 16)
                agreement = torch.einsum("bmk,pmk->bmp", routing_code, pool_codes)
                expected_bucket_mass = torch.sigmoid(args.routing_temperature * (agreement - (16.0 - args.routing_radius - 0.5))).sum(2)
                routing = torch.log1p(expected_bucket_mass).mean()
            else:
                routing = schedule_aware_routing_loss(query_soft, calibrated_pools, radii, args.routing_temperature, torch)
            loss = ranking + args.code_drift_weight * drift + args.routing_work_weight * routing
            optimizer.zero_grad(set_to_none=True); loss.backward(); optimizer.step()
        metrics = validation_metrics(data, document_codes, {"weights": document_weights}, weight.detach().numpy(), thresholds, index, ranges, radii, centers, validation_ids)
        admissible = metrics["adc_survival"] > baseline["adc_survival"] and metrics["candidates"] <= baseline["candidates"] * args.maximum_work_multiplier and metrics["postings"] <= baseline["postings"] * args.maximum_work_multiplier and metrics["mean_hamming_drift"] <= args.maximum_mean_hamming_drift
        print(json.dumps({"seed": args.seed, "epoch": epoch, "validation": metrics, "pareto_admissible": admissible}, sort_keys=True), flush=True)
        history.append({"epoch": epoch, "validation": metrics, "pareto_admissible": admissible, "mining": "current_wq_ranked_hamming_then_e5_then_posting_mass_v1"})
        if admissible and (selected is None or (metrics["adc_survival"], -metrics["candidates"], -metrics["postings"], -metrics["mean_hamming_drift"], -epoch) > (selected[2]["adc_survival"], -selected[2]["candidates"], -selected[2]["postings"], -selected[2]["mean_hamming_drift"], -selected[0])):
            selected = epoch, weight.detach().numpy().copy(), metrics
    args.output_root.mkdir(parents=True)
    history_path = args.output_root / "training-history.json"; history_path.write_text(json.dumps(history, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    held_out_exclusion = {"id": "external_excluded_document_ids_set_v1", "document_ids_set_sha256": prepared["split"]["external_excluded_document_ids_set_sha256"]}
    values = {"epochs": args.epochs, "batch_size": args.batch_size, "learning_rate": args.learning_rate, "itq_iterations": args.itq_iterations, "hard_negative_count": args.hard_negative_count, "validation_fraction": args.validation_fraction, "positive_radius": args.positive_radius, "negative_radius": args.negative_radius, "code_drift_weight": args.code_drift_weight, "routing_work_weight": args.routing_work_weight, "routing_pool_size": int(pool_rows.size), "routing_temperature": args.routing_temperature, "routing_radius": args.routing_radius, "routing_estimator": args.routing_estimator, "routing_strata": args.routing_strata, "routing_pool_per_stratum": args.routing_pool_per_stratum, "maximum_work_multiplier": args.maximum_work_multiplier, "maximum_mean_hamming_drift": args.maximum_mean_hamming_drift}
    frozen_execution = execution_contract(values, len(train_ids), len(validation_ids), held_out_exclusion)
    if selected is None:
        rejection = {"schema_version": 2, "family": "mih_query_trust_region_gate_rejection_v2", "trainer_source_files_sha256": source_hashes(), "input_materialization_manifest_sha256": data["manifest_sha256"], "seed": args.seed, "history_path": history_path.name, "history_sha256": digest(history_path), "baseline": baseline, "gate": frozen_execution["pareto"], "execution_contract": frozen_execution, "reason": "no_train_validation_pareto_admissible_learned_checkpoint"}
        (args.output_root / "gate-rejection.json").write_text(json.dumps(rejection, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    write_f32(args.output_root / "projection-weights.f32", document_weights); write_f32(args.output_root / "query-projection-weights.f32", selected[1]); write_f32(args.output_root / "thresholds.f32", thresholds)
    artifact = {"schema_version": 1, "trainer": {"id": "agent-memory-cpp:mih-query-trust-region-trainer", "source_files_sha256": source_hashes()}, "input_materialization_manifest_sha256": data["manifest_sha256"], "prepared_study_manifest_sha256": data["manifest"]["prepared_study_manifest_sha256"], "architecture": {"family": "mih_query_trust_region_projection_v1", "input_dimension": 384, "bit_count": 256, "band_count": 16, "band_width_bits": 16, "shared_projection": False, "document_side": "frozen_full_itq_w0_v1", "query_side": "learned_trust_region_projection_v1"}, "training": {"seed": args.seed, "queries_or_qrels_used": True, "execution_contract": frozen_execution, **frozen_execution, "checkpoint": {"policy": "deterministic_train_validation_pareto_gate_v1", "selected_epoch": selected[0], "baseline": baseline, "selected": selected[2], "minimum_adc_delta": 0.0, "maximum_work_multiplier": args.maximum_work_multiplier, "maximum_mean_hamming_drift": args.maximum_mean_hamming_drift}, "training_history": {"path": history_path.name, "sha256": digest(history_path)}}, "weights": {}}
    for key, name, shape in (("projection_weights", "projection-weights.f32", [256, 384]), ("query_projection_weights", "query-projection-weights.f32", [256, 384]), ("thresholds", "thresholds.f32", [256])):
        artifact["weights"][key] = {"path": name, "sha256": digest(args.output_root / name), "shape": shape, "layout": "row_major_out_by_in" if len(shape) == 2 else None, "dtype": "float32_le"}
    (args.output_root / "artifact.json").write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--materialization-root", type=Path); parser.add_argument("--output-root", type=Path); parser.add_argument("--seed", type=int, default=52)
    parser.add_argument("--epochs", type=int, default=6); parser.add_argument("--batch-size", type=int, default=192); parser.add_argument("--learning-rate", type=float, default=5e-6); parser.add_argument("--itq-iterations", type=int, default=50); parser.add_argument("--hard-negative-count", type=int, default=4); parser.add_argument("--validation-fraction", type=float, default=.2); parser.add_argument("--validation-query-count", type=int, default=64); parser.add_argument("--positive-radius", type=int, default=56); parser.add_argument("--negative-radius", type=int, default=80); parser.add_argument("--code-drift-weight", type=float, default=8.0); parser.add_argument("--routing-work-weight", type=float, default=1.0); parser.add_argument("--routing-pool-size", type=int, default=1024); parser.add_argument("--routing-temperature", type=float, default=3.0); parser.add_argument("--routing-radius", type=int, default=3); parser.add_argument("--routing-estimator", choices=("sampled-band-radius-soft-collision-v1", "schedule-aware-stratified-union-posting-v2"), default="sampled-band-radius-soft-collision-v1"); parser.add_argument("--routing-strata", type=int, default=4); parser.add_argument("--routing-pool-per-stratum", type=int, default=512); parser.add_argument("--maximum-work-multiplier", type=float, default=1.02); parser.add_argument("--maximum-mean-hamming-drift", type=float, default=8.0)
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            values = {"epochs": 6, "batch_size": 192, "learning_rate": 5e-6, "itq_iterations": 50, "hard_negative_count": 4, "validation_fraction": .2, "positive_radius": 56, "negative_radius": 80, "code_drift_weight": 8.0, "routing_work_weight": 1.0, "routing_pool_size": 1024, "routing_temperature": 3.0, "routing_radius": 3, "routing_estimator": "sampled-band-radius-soft-collision-v1", "routing_strata": 4, "routing_pool_per_stratum": 512, "maximum_work_multiplier": 1.02, "maximum_mean_hamming_drift": 8.0}
            exclusion = {"id": "external_excluded_document_ids_set_v1", "document_ids_set_sha256": "a" * 64}
            frozen = execution_contract(values, 3461, 64, exclusion); changed = dict(values); changed["learning_rate"] = 1e-5
            require(source_hashes() == source_hashes() and frozen != execution_contract(changed, 3461, 64, exclusion), "trainer execution contract is unstable"); print("MIH query trust-region trainer self-test passed"); return 0
        train(args)
    except (TrainingError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"train-mih-query-trust-region: {error}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
