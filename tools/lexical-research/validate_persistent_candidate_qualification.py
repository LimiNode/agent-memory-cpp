#!/usr/bin/env python3
"""Run or validate the bounded, source-bound persistent candidate baseline."""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from unittest.mock import patch


PROTOCOL = "persistent_lexical_candidate_qualification_v1"
SOURCE_LIST = "tools/lexical-research/persistent_candidate_sources.txt"
QUERIES = {"common", "rare", "absent", "repeated", "identifier", "ru_en"}
CONTROLS = {"default_tf", "k_window_invariance", "candidate_budget_truncated",
            "posting_budget_truncated", "update", "erase", "reopen"}
ARRAYS = ("samples_ms", "allocation_counts", "allocated_bytes", "snapshot_ms", "tokenization_ms")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(root: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *arguments], text=True).strip()


def source_manifest(root: Path) -> str:
    records = []
    paths = (root / SOURCE_LIST).read_text(encoding="utf-8").splitlines()
    require(len(paths) == len(set(paths)) and SOURCE_LIST in paths, "invalid source list")
    paths = sorted(set(paths + git(root, "ls-files", "--", "src", "cmake").splitlines()))
    for relative in paths:
        path = root / relative
        require(relative and not Path(relative).is_absolute()
                and ".." not in Path(relative).parts, "unsafe source path")
        path.resolve().relative_to(root.resolve())
        records.append(f"{relative}:{digest(path)}\n")
    return hashlib.sha256("".join(records).encode()).hexdigest()


def finite(value: object, *, positive: bool = False) -> bool:
    return (type(value) in (int, float) and math.isfinite(value)
            and (value > 0 if positive else value >= 0))


def check_hits(row: dict) -> None:
    require(row["policy"] in {"TF", "BM25"}, "unknown scoring policy")
    require(row["completeness"] == "Complete", "non-complete oracle comparison")
    require(re.fullmatch(r"[0-9a-f]{64}", row["corpus_sha256"]) is not None, "missing corpus binding")
    expected, actual = row["expected_ids"], row["actual_ids"]
    require(isinstance(expected, list) and isinstance(actual, list) and expected == actual,
            "candidate membership/order disagrees with independent oracle")
    require(all(type(unit) is int and unit > 0 for unit in actual)
            and len(actual) == len(set(actual)), "invalid or duplicate unit identity")
    scores, goldens = row["actual_scores"], row["expected_scores"]
    require(len(scores) == len(goldens) == len(actual), "score/result pairing mismatch")
    for score, golden in zip(scores, goldens):
        require(finite(score, positive=True) and finite(golden, positive=True), "invalid hit score")
        require(math.isclose(score, golden, rel_tol=1e-5, abs_tol=1e-5), "score oracle mismatch")
    for index in range(1, len(actual)):
        require(scores[index - 1] >= scores[index], "scores are not descending")
        if scores[index - 1] == scores[index]:
            require(actual[index - 1] < actual[index], "unstable unit-ID tie order")


def validate(receipt: dict) -> None:
    require(receipt["schema_version"] == 1 and receipt["protocol"] == PROTOCOL, "unsupported protocol")
    require(receipt["correctness_status"] == "PASS", "mandatory correctness gate failed")
    require(receipt["recipe_version"] == "technical_fixed_tokens_v1", "unknown corpus recipe")
    require(receipt["quality_status"] == "SYNTHETIC_MECHANICS_NOT_APPLIED_QUALITY"
            and receipt["qualification_status"] == "DIRECTIONAL_BASELINE_NOT_RELEASE_QUALIFIED",
            "inflated qualification claim")
    require(receipt["bm25_options"] == {"k1": 1.5, "b": 0.75}, "unmatched BM25 policy")
    source = receipt["source"]
    for name in ("compiled_commit", "expected_head", "libmdbx_revision", "mdbx_containers_revision"):
        require(re.fullmatch(r"[0-9a-f]{40}", source[name]) is not None, f"invalid {name}")
    require(re.fullmatch(r"[0-9a-f]{64}", source["source_manifest_sha256"]) is not None,
            "invalid source manifest")
    require(all(isinstance(source[name], str) and source[name]
                for name in ("compiler_id", "compiler_version", "build_configuration")), "missing build identity")
    require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", source["json_version"]) is not None,
            "missing actual JSON dependency version")
    require(source["cxx_standard"] == 17 and source["pointer_bits"] in {32, 64}, "invalid build ABI")
    for name in ("base_flags_sha256", "configuration_flags_sha256"):
        require(re.fullmatch(r"[0-9a-f]{64}", source[name]) is not None, "missing compiler flags binding")
    measurement = receipt["measurement"]
    require(measurement["warmups"] == 2 and measurement["repeats"] == 7, "changed repetition contract")
    require(measurement["scope"] == "inclusive_generate_window_response_lifetime", "incomplete timing scope")
    require(measurement["observation_unit"] == "repeated_fixed_query_in_one_process", "inflated sample independence")
    require(measurement["allocation_scope"] == "cxx_new_only_not_mdbx_malloc_rss_or_peak", "incorrect allocation claim")
    require(measurement["diagnostic_semantics"] == "independent_operations_not_subtractable", "invalid stage attribution")
    require(measurement["allocations_in_latency_samples"] is False
            and measurement["arm_order"] == "alternate_tf_bm25_by_sample", "timing instrumentation/order drift")
    require(receipt["score_tolerance"] == {"absolute": 1e-5, "relative": 1e-5}, "changed oracle tolerance")
    require(type(receipt["smoke"]) is bool, "invalid profile selector")
    sizes = {32, 128} if receipt["smoke"] else {32, 128, 512}
    expected_grid = set(itertools.product(sizes, {"short", "mixed"}, QUERIES, {1, 10}, {"TF", "BM25"}))
    actual_grid = set()
    ids = set()
    windows = {}
    corpora = {}
    queries = {}
    for row in receipt["cases"]:
        check_hits(row)
        require(row["id"] not in ids, "duplicate case ID")
        ids.add(row["id"])
        require(type(row["N"]) is int and type(row["K"]) is int, "invalid corpus size or K")
        key = (row["N"], row["length_profile"], row["query_id"], row["K"], row["policy"])
        require(key not in actual_grid, "duplicate matrix arm")
        actual_grid.add(key)
        require(len(row["actual_ids"]) <= row["N"], "candidate window exceeds corpus")
        require(type(row["examined_postings"]) is int
                and row["examined_postings"] >= len(row["actual_ids"]), "invalid posting accounting")
        require(row["query_text"] and isinstance(row["query_terms"], list) and row["query_terms"], "empty query fixture")
        if row["query_id"] == "absent":
            require(not row["actual_ids"] and row["examined_postings"] == 0, "absent term produced candidates")
        for name in ARRAYS:
            values = row[name]
            require(isinstance(values, list) and len(values) == 7 and all(finite(x) for x in values),
                    f"invalid {name} samples")
            if name in {"allocation_counts", "allocated_bytes"}:
                require(all(type(x) is int and x > 0 for x in values), "missing allocation diagnostic")
        profile = (row["N"], row["length_profile"])
        query_key = (*profile, row["query_id"])
        query = (row["query_text"], row["query_terms"])
        require(queries.setdefault(query_key, query) == query, "matched policies use different queries")
        previous = corpora.setdefault(profile, row["corpus_sha256"])
        require(previous == row["corpus_sha256"], "matched arms use different corpora")
        window_key = (*profile, row["query_id"], row["policy"])
        window = (row["query_text"], row["query_terms"], row["actual_ids"], row["actual_scores"])
        require(windows.setdefault(window_key, window) == window, "query.limit truncated the full window")
    require(actual_grid == expected_grid, "incomplete case matrix")
    require(CONTROLS.issubset(receipt["controls"])
            and all(receipt["controls"][name] is True for name in CONTROLS), "mandatory lifecycle/budget control failed")
    lifecycle = set()
    for row in receipt["lifecycle"]:
        check_hits(row)
        key = (row["phase"], row["policy"])
        require(key not in lifecycle, "duplicate lifecycle arm")
        lifecycle.add(key)
    phases = {"update", "erase"} | {f"reopen-{n}-{profile}" for n, profile in itertools.product(sizes, {"short", "mixed"})}
    require(lifecycle == set(itertools.product(phases, {"TF", "BM25"})),
            "incomplete lifecycle matrix")


def check_binding(receipt: dict, root: Path, binary: Path, expected_head: str) -> None:
    validate(receipt)
    source, provenance = receipt["source"], receipt["provenance"]
    checkout = git(root, "rev-parse", "HEAD")
    require(source["compiled_commit"] == checkout, "receipt requires the exact measured checkout")
    require(not git(root, "status", "--porcelain", "--untracked-files=no"), "dirty source checkout")
    parents = [line.removeprefix("parent ") for line in git(root, "cat-file", "-p", "HEAD").splitlines()
               if line.startswith("parent ")]
    require(expected_head == checkout or (len(parents) == 2 and expected_head == parents[1]),
            "expected HEAD is not checkout or the PR merge's second parent")
    require(source["expected_head"] == expected_head, "wrong expected PR HEAD")
    require(source["source_manifest_sha256"] == source_manifest(root), "source bytes changed")
    require(source["compiled_commit"] == provenance["actual_checkout"]
            and provenance["worktree_clean"] is True, "dirty or stale compiled checkout")
    require(provenance["binary_sha256"] == digest(binary), "executable bytes changed")
    require(provenance["binary_size"] == binary.stat().st_size, "executable size changed")
    for directory, field in (("libmdbx", "libmdbx_revision"), ("mdbx-containers", "mdbx_containers_revision")):
        dependency = root / "external" / directory
        require(git(dependency, "rev-parse", "HEAD") == source[field], "dependency revision changed")
        require(not git(dependency, "status", "--porcelain"), "dirty dependency sources")


def reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON field")
        result[key] = value
    return result


def load(path: Path) -> dict:
    require(path.stat().st_size <= 64 * 1024 * 1024, "receipt exceeds bounded input size")
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant,
                      object_pairs_hook=unique_object)


def load_bound_receipt(path: Path) -> dict:
    sidecar = path.with_suffix(path.suffix + ".sha256")
    require(sidecar.read_text(encoding="ascii").split() == [digest(path), path.name],
            "receipt sidecar mismatch")
    return load(path)


def self_test() -> None:
    row = {"id": "", "N": 32, "length_profile": "short", "query_id": "common", "K": 1,
           "policy": "TF", "completeness": "Complete", "corpus_sha256": "a" * 64,
           "expected_ids": [1, 2], "actual_ids": [1, 2], "expected_scores": [2.0, 2.0],
           "actual_scores": [2.0, 2.0], "examined_postings": 2, "query_text": "common",
           "query_terms": ["common"], **{name: [1] * 7 for name in ARRAYS}}
    cases = []
    for n, length, query, k, policy in itertools.product({32, 128}, {"short", "mixed"}, QUERIES, {1, 10}, {"TF", "BM25"}):
        item = copy.deepcopy(row)
        item.update(id=f"{n}-{length}-{query}-{k}-{policy}", N=n, length_profile=length, query_id=query, K=k, policy=policy)
        if query == "absent":
            item.update(expected_ids=[], actual_ids=[], expected_scores=[], actual_scores=[], examined_postings=0)
        cases.append(item)
    receipt = {"schema_version": 1, "protocol": PROTOCOL, "correctness_status": "PASS", "smoke": True,
               "recipe_version": "technical_fixed_tokens_v1", "bm25_options": {"k1": 1.5, "b": 0.75},
               "quality_status": "SYNTHETIC_MECHANICS_NOT_APPLIED_QUALITY",
               "qualification_status": "DIRECTIONAL_BASELINE_NOT_RELEASE_QUALIFIED",
               "source": {name: "a" * 40 for name in ("compiled_commit", "expected_head", "libmdbx_revision", "mdbx_containers_revision")},
               "measurement": {"warmups": 2, "repeats": 7, "scope": "inclusive_generate_window_response_lifetime",
                   "observation_unit": "repeated_fixed_query_in_one_process", "allocation_scope": "cxx_new_only_not_mdbx_malloc_rss_or_peak",
                   "diagnostic_semantics": "independent_operations_not_subtractable", "allocations_in_latency_samples": False,
                   "arm_order": "alternate_tf_bm25_by_sample"},
               "score_tolerance": {"absolute": 1e-5, "relative": 1e-5}, "cases": cases,
               "controls": dict.fromkeys(CONTROLS, True), "lifecycle": []}
    receipt["source"].update(source_manifest_sha256="b" * 64, compiler_id="GNU", compiler_version="15", build_configuration="Release",
                             cxx_standard=17, pointer_bits=64, base_flags_sha256="c" * 64, configuration_flags_sha256="d" * 64,
                             json_version="3.12.0")
    phases = {"update", "erase"} | {f"reopen-{n}-{profile}" for n, profile in itertools.product({32, 128}, {"short", "mixed"})}
    for phase, policy in itertools.product(phases, {"TF", "BM25"}):
        receipt["lifecycle"].append({**copy.deepcopy(row), "phase": phase, "policy": policy})
    validate(receipt)
    mutations = [lambda r: r.update(correctness_status="FAIL"),
                 lambda r: r["cases"].pop(),
                 lambda r: r["cases"][0].update(completeness="Truncated"),
                 lambda r: r["cases"][0].update(corpus_sha256="wrong"),
                 lambda r: r["cases"][0].update(actual_ids=[2, 1]),
                 lambda r: r["cases"][0].update(actual_scores=[9.0, 2.0]),
                 lambda r: r["cases"][0].update(samples_ms=[1]),
                 lambda r: r["cases"][0].update(samples_ms=[float("nan")] * 7),
                 lambda r: r["cases"][0].update(allocation_counts=[0] * 7),
                 lambda r: r["controls"].update(candidate_budget_truncated=False),
                 lambda r: r["controls"].pop("reopen"),
                 lambda r: r["lifecycle"].pop(),
                 lambda r: r["measurement"].update(scope="scorer_only"),
                 lambda r: r["score_tolerance"].update(relative=0.1),
                 lambda r: r["source"].update(compiled_commit="UNPINNED")]
    mutations.append(lambda r: r["cases"][0].update(query_text="different workload"))
    for mutate in mutations:
        broken = copy.deepcopy(receipt)
        mutate(broken)
        try:
            validate(broken)
        except (ValueError, KeyError, TypeError):
            continue
        raise AssertionError("negative control was accepted")
    binding_controls = 0
    with tempfile.TemporaryDirectory(prefix="candidate-validator-", dir=Path.cwd()) as temporary:
        root = Path(temporary)
        source_list = root / SOURCE_LIST
        source_list.parent.mkdir(parents=True)
        source_list.write_text(SOURCE_LIST + "\n", encoding="utf-8")
        binary = root / "candidate.bin"
        binary.write_bytes(b"fixed-test-executable")
        receipt["provenance"] = {"actual_checkout": "a" * 40, "worktree_clean": True,
            "binary_sha256": digest(binary), "binary_size": binary.stat().st_size}
        # Isolate dependency Git I/O; source, executable and sidecar hashes use
        # real files. These controls test rejection, not execution attestation.
        with patch(__name__ + ".git", side_effect=lambda _root, *args: "a" * 40 if args[0] == "rev-parse" else ""):
            receipt["source"]["source_manifest_sha256"] = source_manifest(root)
            check_binding(receipt, root, binary, "a" * 40)
            for section, field, value in (
                ("source", "expected_head", "e" * 40),
                ("source", "source_manifest_sha256", "e" * 64),
                ("source", "compiled_commit", "e" * 40),
                ("source", "libmdbx_revision", "e" * 40),
                ("source", "mdbx_containers_revision", "e" * 40),
                ("provenance", "worktree_clean", False),
                ("provenance", "binary_sha256", "e" * 64),
                ("provenance", "binary_size", 0),
            ):
                broken = copy.deepcopy(receipt)
                broken[section][field] = value
                try:
                    check_binding(broken, root, binary, "a" * 40)
                except ValueError:
                    binding_controls += 1
                    continue
                raise AssertionError("binding mutation was accepted")
        for wrong_checkout, dirty in ((True, False), (False, True)):
            def mutated_git(_root: Path, *args: str) -> str:
                if args[0] == "rev-parse":
                    return ("e" if wrong_checkout else "a") * 40
                return " M source.cpp" if dirty and args[0] == "status" else ""
            with patch(__name__ + ".git", side_effect=mutated_git):
                try:
                    check_binding(receipt, root, binary, "a" * 40)
                except ValueError:
                    binding_controls += 1
                    continue
                raise AssertionError("checkout mutation was accepted")
        merge_receipt = copy.deepcopy(receipt)
        merge_receipt["source"]["expected_head"] = "e" * 40
        for parents, accepted in ((["e" * 40], False),
                                  (["e" * 40, "f" * 40], False),
                                  (["f" * 40, "e" * 40], True)):
            def merge_git(_root: Path, *args: str) -> str:
                if args[0] == "rev-parse":
                    return "a" * 40
                return "\n".join("parent " + parent for parent in parents) if args[0] == "cat-file" else ""
            with patch(__name__ + ".git", side_effect=merge_git):
                try:
                    check_binding(merge_receipt, root, binary, "e" * 40)
                except ValueError:
                    require(not accepted, "valid PR merge parent was rejected")
                    binding_controls += 1
                    continue
                require(accepted, "ordinary/first-parent stale HEAD was accepted")
        path = root / "receipt.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        sidecar = path.with_suffix(".json.sha256")
        sidecar.write_text(f"{digest(path)}  {path.name}\n", encoding="ascii")
        load_bound_receipt(path)
        for corrupted in ("0" * 64 + "  receipt.json\n", digest(path) + "  renamed.json\n"):
            sidecar.write_text(corrupted, encoding="ascii")
            try:
                load_bound_receipt(path)
            except ValueError:
                binding_controls += 1
                continue
            raise AssertionError("sidecar mutation was accepted")
    print(f"persistent candidate validator: PASS ({len(mutations)} schema + {binding_controls} binding rejection controls)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--expected-head")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        require(args.source_root is not None and args.binary is not None and args.expected_head is not None,
                "--source-root, --binary and --expected-head are required")
        root, binary = args.source_root.resolve(), args.binary.resolve()
        require(bool(args.output) != bool(args.receipt), "choose --output (run) or --receipt (validate)")
        path = args.output or args.receipt
        if args.output:
            checkout = git(root, "rev-parse", "HEAD")
            require(not git(root, "status", "--porcelain", "--untracked-files=no"), "measurement requires a clean worktree")
            before_binary = digest(binary)
            path.parent.mkdir(parents=True, exist_ok=True)
            command = [str(binary), "--output", str(path.resolve())]
            if args.smoke:
                command.append("--smoke")
            subprocess.run(command, check=True, timeout=600)
            receipt = load(path)
            require(checkout == git(root, "rev-parse", "HEAD") and before_binary == digest(binary), "inputs changed during measurement")
            require(not git(root, "status", "--porcelain", "--untracked-files=no"), "sources changed during measurement")
            receipt["provenance"] = {"actual_checkout": checkout, "worktree_clean": True,
                "binary_sha256": before_binary, "binary_size": binary.stat().st_size}
            check_binding(receipt, root, binary, args.expected_head)
            path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            path.with_suffix(path.suffix + ".sha256").write_text(f"{digest(path)}  {path.name}\n", encoding="ascii")
        else:
            receipt = load_bound_receipt(path)
            check_binding(receipt, root, binary, args.expected_head)
        print(f"persistent candidate qualification: PASS ({len(receipt['cases'])} matched cases)")
        return 0
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        print(f"persistent candidate qualification: FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
