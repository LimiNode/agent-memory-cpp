#!/usr/bin/env python3
"""Independently check exported FP32 inputs/results using a Decimal oracle."""
from __future__ import annotations

import argparse
import copy
from decimal import Decimal, localcontext
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[2]
RUNNER = Path(__file__).with_name("exact_vector_index_decision.cpp")
INDEX = ROOT / "src/agent_memory/index/ExactVectorIndex.cpp"
SOURCE_FILES = [RUNNER, INDEX, INDEX.with_suffix(".hpp"),
                INDEX.with_name("VectorSimilarityComputer.cpp"), INDEX.with_name("VectorSimilarityComputer.hpp")]


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fp32(value: float) -> float:
    return struct.unpack("f", struct.pack("f", value))[0]


def oracle(a: list[float], b: list[float], metric: str) -> float:
    # from_float is exact for exported FP32 inputs. No production helper or
    # host long-double precision participates; round to the public score type.
    with localcontext() as context:
        context.prec = 100
        left = [Decimal.from_float(float(x)) for x in a]
        right = [Decimal.from_float(float(x)) for x in b]
        value = sum((x * y for x, y in zip(left, right)), Decimal(0))
        if metric == "cosine":
            aa = sum((x * x for x in left), Decimal(0))
            bb = sum((x * x for x in right), Decimal(0))
            value = value / (aa.sqrt() * bb.sqrt()) if aa and bb else Decimal(0)
        elif metric == "negative_squared_l2":
            value = -sum(((x - y) ** 2 for x, y in zip(left, right)), Decimal(0))
        else:
            require(metric == "dot", "unknown metric")
        return fp32(float(value))


def ranking(scores: list[float]) -> list[str]:
    return sorted((f"chunk:{i}" for i in range(len(scores))),
                  key=lambda key: (-scores[int(key.split(":")[1])], key))


def close(actual: float, expected: float, metric: str) -> bool:
    tolerance = max(abs(expected) * (8e-7 if metric == "cosine" else 2e-5),
                    2e-44 if metric == "cosine" else 2e-6)
    return math.isfinite(actual) and abs(actual - expected) <= tolerance


def validate(raw: dict) -> dict:
    require(raw.get("schema") == "r5.exact_vector_index.decision.v2", "schema differs")
    require(raw.get("production_path") == "ExactVectorIndex::search", "production path differs")
    require(raw.get("build_configuration") == "Release", "not Release")
    require(raw.get("cxx_standard") == 17, "not C++17")
    require(len(raw.get("compiled_checkout_sha", "")) == 40, "missing compiled checkout")
    require(raw.get("runner_source_sha256") == digest(RUNNER), "compiled runner differs")
    require(raw.get("index_source_sha256") == digest(INDEX), "compiled index differs")
    manifest = "".join(f"{path.relative_to(ROOT).as_posix()}:{digest(path)}\n" for path in SOURCE_FILES)
    require(raw.get("source_manifest_sha256") == hashlib.sha256(manifest.encode()).hexdigest(), "compiled source manifest differs")
    controls = {"nan_query", "infinite_record", "dimension", "empty_query", "overflow_auto", "overflow_scalar", "empty_index"}
    require(set(raw.get("controls", {})) == controls and all(x is True for x in raw["controls"].values()), "exception controls failed")
    require([x["name"] for x in raw["correctness"]] == ["cancellation", "near_tie_and_exact_tie", "tiny_cosine", "random_cosine", "random_dot", "random_negative_squared_l2"], "suite differs")
    failures = {name: [] for name in ("fp32", "fp64_sequential", "neumaier")}
    max_error = {"cosine": 0.0, "dot": 0.0, "negative_squared_l2": 0.0}
    for case in raw["correctness"]:
        metric, corpus, queries = case["metric"], case["corpus"], case["queries"]
        require(corpus and queries and all(len(v) == len(corpus[0]) for v in corpus + queries), "input dimensions differ")
        require(all(math.isfinite(v) and fp32(v) == v for row in corpus + queries for v in row), "inputs are not FP32")
        require([r["simd_requested"] for r in case["runs"]] == [False, True], "missing scalar/auto")
        for run in case["runs"]:
            require(len(run["queries"]) == len(queries), "missing query output")
            for query, observed in zip(queries, run["queries"]):
                scores = [oracle(query, record, metric) for record in corpus]
                order = ranking(scores)
                require([x["k"] for x in observed["limits"]] == [0, 1, 10, len(corpus)], "K coverage differs")
                for limited in observed["limits"]:
                    require([x["id"] for x in limited["hits"]] == order[:limited["k"]], f"production ranking differs: {case['name']}")
                    for hit in limited["hits"]:
                        expected = scores[int(hit["id"].split(":")[1])]
                        require(close(hit["score"], expected, metric), f"production score differs: {case['name']}")
                        max_error[metric] = max(max_error[metric], abs(hit["score"] - expected))
                if metric == "cosine":
                    require(set(observed["arithmetic"]) == set(failures), "arithmetic controls differ")
                    for method, candidate in observed["arithmetic"].items():
                        require(len(candidate) == len(scores), "candidate cardinality differs")
                        if ranking(candidate) != order or any(not close(a, b, metric) for a, b in zip(candidate, scores)):
                            failures[method].append(case["name"])
    require(not failures["neumaier"], "compensated arithmetic differs")
    require("cancellation" in failures["fp64_sequential"], "FP64 cancellation counterexample missing")
    require("tiny_cosine" in failures["fp32"], "FP32 underflow counterexample missing")
    shapes = [(128, 8), (1024, 128), (8192, 32), (65536, 32), (128, 768)]
    require([(x["rows"], x["dimension"], x["metric"]) for x in raw["performance"]] == [(n, d, m) for n, d in shapes for m in ("cosine", "dot")], "timing matrix differs")
    for case in raw["performance"]:
        require(case["query_count"] == 4, "query count differs")
        require([(r["simd_requested"], r["k"]) for r in case["runs"]] == [(s, k) for s in (False, True) for k in (1, 10, case["rows"])], "timing K/dispatch differs")
        for run in case["runs"]:
            require(run["backend"] in {"scalar", "sse2", "avx2"} and run["construction_ms"] > 0, "backend/construction differs")
            samples = run["search_samples_ms"]
            require(len(samples) == 7 and all(math.isfinite(v) and v > 0 for v in samples), "invalid search samples")
            require(len(run["output"]) == 4, "missing timing outputs")
            require(run.get("result_counts") == [min(run["k"], case["rows"])] * 4, "search cardinality differs")
            for found in run["output"]:
                require(len(found) == min(run["k"], 10), "timing output length differs")
                require(found == sorted(found, key=lambda h: (-h["score"], h["id"])), "timing output unsorted")
        for scalar, automatic in zip(case["runs"][:3], case["runs"][3:]):
            for left, right in zip(scalar["output"], automatic["output"]):
                require([h["id"] for h in left] == [h["id"] for h in right], "scalar/auto ranking differs")
                require(all(close(a["score"], b["score"], case["metric"]) for a, b in zip(left, right)), "scalar/auto score differs")
        expected_methods = {"fp32", "fp64_sequential", "neumaier"} if case["metric"] == "cosine" else {"scalar_primitive", "auto_primitive"}
        require(set(case["kernel_samples_ms"]) == expected_methods, "missing kernel control")
        for samples in case["kernel_samples_ms"].values():
            require(len(samples) == 7 and all(math.isfinite(v) and v > 0 for v in samples), "invalid kernel samples")
    return {"correctness_cases": len(raw["correctness"]), "performance_cases": len(raw["performance"]), "production_max_absolute_score_error": max_error, "rejected_arithmetic_controls": {k: sorted(set(v)) for k, v in failures.items()}}


def negative_controls(raw: dict) -> int:
    mutations = [
        lambda x: x["correctness"][0]["runs"][0]["queries"][0]["limits"][1]["hits"][0].update(id="chunk:0"),
        lambda x: x["correctness"][0]["runs"][0]["queries"][0]["limits"][1]["hits"][0].update(score=0),
        lambda x: x["correctness"][2]["corpus"][0].__setitem__(0, -1e-30),
        lambda x: x["controls"].update(overflow_auto=False),
        lambda x: x["correctness"][0]["runs"].pop(),
        lambda x: x["performance"][0]["runs"][0]["search_samples_ms"].clear(),
        lambda x: x.update(runner_source_sha256="0" * 64),
        lambda x: x.update(index_source_sha256="0" * 64),
        lambda x: x.update(source_manifest_sha256="0" * 64),
    ]
    for mutate in mutations:
        changed = copy.deepcopy(raw)
        mutate(changed)
        try:
            validate(changed)
        except (ValueError, KeyError):
            continue
        raise ValueError("mutated evidence accepted")
    return len(mutations)


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("executable", "raw", "receipt", "sidecar"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    subprocess.run([str(args.executable.resolve()), str(args.raw.resolve())], check=True)
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    result = validate(raw)
    result["negative_controls"] = negative_controls(raw) if args.self_test else 0
    checkout = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    expected = os.environ.get("R5_EXPECTED_HEAD", "NOT_SPECIFIED_LOCAL_RUN")
    if expected != "NOT_SPECIFIED_LOCAL_RUN":
        require(raw["compiled_checkout_sha"] == checkout == expected, "expected PR / compiled checkout / tested head differ")
    receipt = {"schema": "r5.exact_vector_index.receipt.v2", "execution_status": "EXECUTED", "status": "PASS", "production_activation": False, "decision": "KEEP CURRENT",
        "oracle": "Decimal precision=100 on exact exported FP32 values; FP32 rounding before ranking",
        "oracle_scope": "six correctness cases, not large timing matrix", "tested_source_head": checkout, "expected_pr_head": expected,
        "compiled_checkout_sha": raw["compiled_checkout_sha"], "runner_source_sha256": raw["runner_source_sha256"], "index_source_sha256": raw["index_source_sha256"],
        "compiled_executable_sha256": digest(args.executable), "raw_output_sha256": digest(args.raw),
        "raw_output_path": str(args.raw.resolve()),
        "correctness_corpus_sha256": hashlib.sha256(json.dumps([{"corpus": c["corpus"], "queries": c["queries"]} for c in raw["correctness"]], sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "environment": {"platform": platform.platform(), "python": platform.python_version(), "compiler": raw["compiler"], "build": raw["build_configuration"], "cxx_standard": raw["cxx_standard"], "base_flags_sha256": raw["base_flags_sha256"], "configuration_flags_sha256": raw["configuration_flags_sha256"]},
        "source_worktree_status": subprocess.check_output(["git", "status", "--short", "--untracked-files=no"], cwd=ROOT, text=True).splitlines(),
        "timing_scopes": {"search_samples_ms": "four fixed independent query vectors through full prebuilt search, including filtering/selection/materialization", "kernel_samples_ms": "four queries/corpus; arithmetic controls only; precomputed record norms for cosine; actual scalar/auto primitive for dot", "construction_ms": "one separate observation; not search latency"},
        "sample_semantics": "7 repeated batches of 4 fixed queries; not 28 independent requests; no stable p99 claim",
        "limitations": ["candidate kernels are arithmetic controls, not replacement indexes", "synthetic data; no production activation", "kernel timings are not end-to-end candidate speedups", "host load is not isolated; simultaneous research builds may affect directional timings"], "validation": result}
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    args.sidecar.write_text(f"{digest(args.raw)}  {args.raw.name}\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", **result}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"validate_exact_vector_index_decision: {error}")
