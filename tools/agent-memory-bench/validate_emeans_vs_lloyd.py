#!/usr/bin/env python3
"""Recompute synthetic Lloyd/E-means evidence, not production qualification."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import tempfile
from pathlib import Path

import run_emeans_vs_lloyd as bench

np, faiss = bench.np, bench.faiss


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def numeric(value, label: str) -> float:
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
            f"nonfinite/negative/non-numeric {label}")
    return float(value)


def check_timing(timing: dict, samples: list[float]) -> None:
    require(all(numeric(value, "timing") >= 0 for value in samples), "timing samples differ")
    require(timing == bench.summary(samples), "timing summary differs from samples")


def validate_payload(raw: dict, receipt: dict, expected_head: str | None = None) -> None:
    faiss.omp_set_num_threads(1)
    require(raw.get("schema") == "r5.emeans_vs_lloyd.synthetic_smoke.v2", "schema differs")
    require(raw.get("status") == "PASS" and raw.get("execution_status") == "EXECUTED", "status differs")
    require(raw.get("dataset_kind") == "synthetic_smoke" and raw.get("disposition") == "DEFER", "scope differs")
    require(raw.get("production_activation") is False and receipt.get("production_activation") is False,
            "production activation is forbidden")
    require(receipt.get("schema") == "r5.emeans_vs_lloyd.receipt.v2"
            and receipt.get("execution_status") == "EXECUTED" and receipt.get("disposition") == "DEFER",
            "receipt status differs")
    fixture = bench.make_fixture()
    require(raw.get("dataset") == bench.split_evidence(fixture) == receipt.get("dataset"),
            "regenerated split hashes/disjointness differ")
    config = raw["configuration"]
    expected_config = {"seed": bench.SEED, "dimension": bench.DIMENSION, "centroids": bench.CENTROIDS,
        "passes": bench.PASSES, "assignment_rows_budget": bench.PASSES * bench.TRAIN_ROWS,
        "distance_evaluation_budget": bench.PASSES * bench.TRAIN_ROWS * bench.CENTROIDS,
        "init_centroids_sha256": bench.digest(fixture["train"][:bench.CENTROIDS]),
        "train_repeats": bench.TRAIN_REPEATS, "train_warmups_per_method": 1,
        "search_repeats": bench.SEARCH_REPEATS, "search_warmups_per_query": bench.SEARCH_WARMUPS,
        "top_ks": [10, 100]}
    require(config == expected_config, "benchmark configuration differs")
    environment = raw["environment"]
    require(environment == receipt.get("environment"), "receipt environment differs")
    require(environment["faiss"] == faiss.__version__ == "1.16.0", "Faiss pin differs")
    require(environment["numpy"] == np.__version__ == "2.4.6", "NumPy pin differs")
    require(environment["threads"] == environment["faiss_threads"] == 1, "thread count differs")
    require(environment["blas_thread_env"] == {name: "1" for name in bench.THREAD_VARIABLES},
            "BLAS thread environment differs")
    require(all(isinstance(environment[field], str) and environment[field]
                for field in ("platform", "python", "python_implementation", "cpu")), "runtime details absent")
    require(isinstance(environment["faiss_compile_options"], str)
            and isinstance(environment["numpy_build_configuration"].get("Build Dependencies", {}).get("blas"), dict),
            "native library configuration absent")
    provenance = raw["provenance"]
    require(provenance == receipt.get("provenance"), "receipt provenance differs")
    require(re.fullmatch(r"[0-9a-f]{40}", provenance["actual_checkout_sha"]) is not None,
            "checkout SHA malformed")
    head = provenance["expected_pr_head_sha"]
    require(head is None or re.fullmatch(r"[0-9a-f]{40}", head) is not None, "PR HEAD malformed")
    if expected_head is not None:
        require(head == expected_head, "expected PR HEAD differs")
    if head is not None:
        require(provenance["actual_checkout_sha"] == head, "actual checkout differs from declared PR HEAD")
    require(provenance["source_files_sha256"] == bench.source_evidence(head)["source_files_sha256"],
            "source file hashes differ")
    methods = raw["methods"]
    require(len(methods) == 2 and {m["method"] for m in methods}
            == {"faiss_lloyd", "faiss_numpy_emeans"}, "method set differs")
    base, queries = fixture["base"], fixture["query"]
    expected = np.asarray([bench.exact_top(base, q) for q in queries])
    for method in methods:
        centroids = np.asarray(method["centroids"], dtype=np.float32)
        require(centroids.shape == (bench.CENTROIDS, bench.DIMENSION)
                and np.all(np.isfinite(centroids)), "centroid matrix malformed")
        require(bench.digest(centroids) == method["centroids_sha256"], "centroid digest differs")
        replayed, replay = bench.train_method(method["method"], fixture["train"], fixture["train"][:bench.CENTROIDS])
        require(np.array_equal(replayed, centroids), "training replay differs")
        training = method["training"]
        require(len(training) == bench.TRAIN_REPEATS, "training repeats differ")
        for observation in training:
            for field in ("assignment_rows", "centroid_distance_evaluations", "assignment_count_kind", "recipe",
                          "training_input_rows", "training_input_sha256"):
                require(observation[field] == replay[field], f"training control differs: {field}")
            numeric(observation["elapsed_ms"], "training time")
        check_timing(method["training_timing"], [o["elapsed_ms"] for o in training])
        labels, error = bench.nearest(fixture["train"], centroids)
        _, heldout_error = bench.nearest(fixture["heldout"], centroids)
        for field, expected_error in (("train_mse", error), ("heldout_mse", heldout_error)):
            require(math.isclose(numeric(method[field], field), float(np.mean(expected_error)),
                                 rel_tol=1e-12, abs_tol=1e-12), f"{field} differs from final exact assignment")
        require(method["train_occupancy"] == bench.occupancy(fixture["train"], centroids)
                and method["base_occupancy"] == bench.occupancy(base, centroids), "occupancy/imbalance differs")
        index = bench.make_ivf(centroids, base)
        numeric(method["ivf_build_ms"], "index build time")
        require(method["serialized_index_bytes"] == int(faiss.serialize_index(index).nbytes),
                "serialized index size differs")
        require(method["base_vector_array_bytes"] == base.nbytes
                and method["centroid_array_bytes"] == centroids.nbytes
                and method["peak_rss"] == "NOT_MEASURED", "array memory measurement differs")
        require([r["nprobe"] for r in method["native_ivf"]] == list(bench.NPROBES), "nprobe matrix differs")
        require([r["scan_cap"] for r in method["equal_scan_proxy"]] == list(bench.SCAN_CAPS), "scan-cap matrix differs")
        base_labels, _ = bench.nearest(base, centroids)
        lists = [np.flatnonzero(base_labels == c) for c in range(bench.CENTROIDS)]
        for family in ("native_ivf", "equal_scan_proxy"):
            for row in method[family]:
                actual, scans = [], []
                if family == "native_ivf":
                    require(row["scope"] == "native_IndexIVFFlat_search_top100", "native timing scope differs")
                    index.nprobe = row["nprobe"]
                    for query in queries:
                        faiss.cvar.indexIVF_stats.reset()
                        actual.append(index.search(query.reshape(1, -1), 100)[1][0])
                        distances = np.sum((centroids.astype(np.float64) - query.astype(np.float64)) ** 2, axis=1)
                        cells = np.lexsort((np.arange(bench.CENTROIDS), distances))[:index.nprobe]
                        scans.append(sum(len(lists[c]) for c in cells))
                        require(int(faiss.cvar.indexIVF_stats.ndis) == scans[-1], "native scan replay differs")
                    require(row["per_query_sample_ndis"] == [[n] * bench.SEARCH_REPEATS for n in scans],
                            "native sample ndis differs from list-size sum")
                else:
                    require(row["scope"] == "python_preassigned_list_prefix_cap_exact_fp64_rerank_top100",
                            "capped proxy timing scope differs")
                    for query in queries:
                        candidates = bench.capped_candidates(query, centroids, lists, row["scan_cap"])
                        actual.append(bench.exact_top(base, query, candidates))
                        scans.append(len(candidates))
                    require(scans == [row["scan_cap"]] * bench.QUERY_ROWS, "equal scan cap violated")
                actual = np.asarray(actual)
                require(row["result_ids_sha256"] == bench.ids_digest(actual), "retrieval result digest differs")
                require(row["scanned_documents"] == scans, "scanned-document counts differ")
                for field, expected_values in bench.recalls(actual, expected).items():
                    require(row[field] == expected_values, f"{family} {field} differs from exact oracle")
                if (row.get("nprobe") == bench.CENTROIDS or row.get("scan_cap") == bench.BASE_ROWS):
                    require(row["recall_at_10"] == row["recall_at_100"] == [1.0] * bench.QUERY_ROWS,
                            "full-scan parity differs")
                times = row["per_query_samples_ms"]
                require(len(times) == bench.QUERY_ROWS
                        and all(len(t) == bench.SEARCH_REPEATS for t in times), "per-query timing matrix differs")
                check_timing(row["timing"], [t for q in times for t in q])


def validate(raw_path: Path, receipt_path: Path, sidecar_path: Path,
             expected_head: str | None = None) -> dict:
    raw_hash = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    require(receipt.get("raw_output", {}).get("sha256") == raw_hash, "receipt digest differs")
    require(Path(receipt["raw_output"]["path"]).name == raw_path.name, "receipt filename differs")
    require(sidecar_path.read_text(encoding="utf-8").split() == [raw_hash, raw_path.name], "sidecar differs")
    validate_payload(raw, receipt, expected_head)
    return raw


def mutation_tests(raw: dict) -> int:
    # Re-sign each corrupted payload: rejection must be semantic, not only a stale outer hash.
    mutations = {
        "numeric MSE": lambda x: x["methods"][0].__setitem__("heldout_mse", x["methods"][0]["heldout_mse"] + 1),
        "centroid digest": lambda x: x["methods"][0].__setitem__("centroids_sha256", "0" * 64),
        "corpus digest": lambda x: x["dataset"]["splits"]["train"].__setitem__("sha256", "0" * 64),
        "work budget": lambda x: x["methods"][0]["training"][0].__setitem__("assignment_rows", 20480),
        "recall": lambda x: x["methods"][0]["native_ivf"][-1]["recall_at_100"].__setitem__(0, 0.0),
        "scan cap": lambda x: x["methods"][0]["equal_scan_proxy"][0]["scanned_documents"].__setitem__(0, 1),
        "result digest": lambda x: x["methods"][0]["native_ivf"][0].__setitem__("result_ids_sha256", "0" * 64),
        "nonfinite timing": lambda x: x["methods"][0]["native_ivf"][0]["per_query_samples_ms"][0].__setitem__(0, -1),
        "source binding": lambda x: x["provenance"]["source_files_sha256"].clear(),
        "exact checkout": lambda x: x["provenance"].update({
            "expected_pr_head_sha": x["provenance"]["actual_checkout_sha"], "actual_checkout_sha": "0" * 40}),
        "execution status": lambda x: x.__setitem__("execution_status", "NOT_EXECUTED"),
        "version": lambda x: x["environment"].__setitem__("faiss", "1.15.0"),
    }
    with tempfile.TemporaryDirectory(prefix="emeans-mutations-") as temporary:
        paths = [Path(temporary) / name for name in ("raw.json", "receipt.json", "raw.sha256")]
        for name, change in mutations.items():
            mutated = copy.deepcopy(raw)
            change(mutated)
            bench.write_evidence(mutated, *paths)
            try:
                validate(*paths)
            except (RuntimeError, KeyError, TypeError, ValueError):
                continue
            raise RuntimeError(f"mutation accepted: {name}")
        bench.write_evidence(raw, *paths)
        paths[0].write_bytes(paths[0].read_bytes() + b" ")
        try:
            validate(*paths)
        except RuntimeError:
            pass
        else:
            raise RuntimeError("corrupt raw bytes accepted")
    return len(mutations) + 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--expected-head")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    raw = validate(args.raw, args.receipt, args.sidecar, args.expected_head)
    count = mutation_tests(raw) if args.self_test else 0
    print(json.dumps({"status": "PASS", "methods": len(raw["methods"]), "mutation_controls": count}))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, KeyError, TypeError, ValueError) as error:
        raise SystemExit(f"validate_emeans_vs_lloyd: {error}")
