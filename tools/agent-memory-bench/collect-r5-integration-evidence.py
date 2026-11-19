"""Archive an actual combined CI run; never infer integration from separate PR runs."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import yaml


PRS = {633, 635, 638, 640, 641, 642, 664, 665, 666}
RESEARCH_JOBS = {
    "exact-vector-numerical-frontier", "r5-exact-vector-index", "r5-emeans-cpu",
    "exact-lexical-topk-decision", "production-reranker-admission",
}
ARTIFACT_GROUPS = (
    "r5-combined-ctest-logs", "exact-vector-numerical-frontier-receipt",
    "r5-vector-ubuntu-latest-", "r5-vector-windows-latest-", "r5-emeans-",
    "exact-lexical-topk-decision-ubuntu-latest", "exact-lexical-topk-decision-windows-latest",
    "production-reranker-admission-receipt-ubuntu-latest",
    "production-reranker-admission-receipt-windows-latest",
)
REVIEWED_SHARED_FILES = {
    ".github/workflows/ci.yml", "CMakeLists.txt", "tools/agent-memory-bench/CMakeLists.txt",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


class UniqueLoader(yaml.SafeLoader):
    """Reject a duplicate mapping before YAML silently replaces a CI job."""


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        require(key not in result, f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def validate_inputs(inputs):
    require(inputs.get("schema") == "r5-integration-inputs-v1", "input schema")
    require(inputs.get("production_activation") is False, "research activation forbidden")
    require(re.fullmatch(r"[0-9a-f]{40}", inputs.get("integration_baseline", "")), "baseline SHA")
    sources = inputs.get("sources", [])
    require(len(sources) == len(PRS) and {s.get("pr") for s in sources} == PRS, "nine unique PRs required")
    for source in sources:
        require(re.fullmatch(r"[0-9a-f]{40}", source.get("head", "")), "source SHA")


def audit_workflow(text):
    jobs = yaml.load(text, Loader=UniqueLoader)["jobs"]
    active, done = set(), set()

    def visit(key):
        require(key in jobs and key not in active, f"missing/cyclic job: {key}")
        if key in done:
            return
        active.add(key)
        needs = jobs[key].get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        require(len(needs) == len(set(needs)), f"duplicate needs: {key}")
        for parent in needs:
            visit(parent)
        active.remove(key)
        done.add(key)

    for key in jobs:
        visit(key)
    gate = jobs["ci-gate"]
    step = gate["steps"][0]
    env_results = {}
    for name, value in step["env"].items():
        if isinstance(value, str):
            match = re.fullmatch(r"\$\{\{ needs\.([\w-]+)\.result \}\}", value)
            if match:
                env_results[match.group(1)] = name
    called = set(re.findall(r'check_required "\$[A-Z0-9_]+" "\$([A-Z0-9_]+)"', step["run"]))
    for key in RESEARCH_JOBS | {"research-full"}:
        require(key in gate["needs"] and env_results.get(key) in called, f"lost required gate: {key}")
    return {"unique_jobs": len(jobs), "required_r5_gates_present": True}


def validate_junit(text):
    tests = list(ET.fromstring(text).iter("testcase"))
    require(tests, "empty combined CTest result")
    names = [test.attrib["name"] for test in tests]
    require(len(names) == len(set(names)), "duplicate CTests")
    require(not any(test.find(tag) is not None for test in tests for tag in ("failure", "error", "skipped")),
            "combined CTest failure, error or skip")
    for fragment in ("exact_vector_index_decision", "exact_lexical_topk_decision",
                     "production_reranker_admission", "exact_vector_numerical_frontier"):
        require(any(fragment in name for name in names), f"combined CTest missing: {fragment}")
    return {"test_count": len(tests), "failed": 0, "skipped": 0}


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_assembly(inputs):
    """Verify unique input blobs exactly; shared build files need structural/runtime checks."""
    owners = {}
    baseline = inputs["integration_baseline"]
    trees = {}
    for source in inputs["sources"]:
        head = source["head"]
        require(git("cat-file", "-t", head) == "commit", "input source is not a commit")
        require(git("merge-base", baseline, head) == baseline, "input is not based on integration baseline")
        require(not git("diff", "--name-only", baseline, head, "--", "src", "external"),
                f"production changes in input PR {source['pr']}")
        trees[head] = {entry.split("\t", 1)[1]: entry for entry in git("ls-tree", "-r", head).splitlines()}
        for path in git("diff", "--name-only", baseline, head).splitlines():
            owners.setdefault(path, []).append(source)
    verified, shared = [], []
    actual_tree = {entry.split("\t", 1)[1]: entry for entry in git("ls-tree", "-r", "HEAD").splitlines()}
    for path, sources in sorted(owners.items()):
        if len(sources) > 1:
            require(path in REVIEWED_SHARED_FILES, f"unreviewed shared path: {path}")
            shared.append(path)
            continue
        expected = trees[sources[0]["head"]].get(path, "")
        actual = actual_tree.get(path, "")
        require(actual == expected, f"input blob not preserved: PR {sources[0]['pr']}: {path}")
        verified.append({"path": path, "pr": sources[0]["pr"], "git_tree_entry": actual})
    return {"exact_unique_input_blobs": verified, "reviewed_shared_build_files": shared,
            "shared_file_validation": "workflow DAG/gates, actual combined configure/build/CTest"}


def self_test(inputs, workflow):
    validate_inputs(inputs)
    audit_workflow(workflow)
    mutations = []
    bad = copy.deepcopy(inputs)
    bad["production_activation"] = True
    mutations.append(lambda: validate_inputs(bad))
    bad_head = copy.deepcopy(inputs)
    bad_head["sources"][0]["head"] = "unknown"
    mutations.append(lambda: validate_inputs(bad_head))
    duplicate = copy.deepcopy(inputs)
    duplicate["sources"][0] = duplicate["sources"][1]
    mutations.append(lambda: validate_inputs(duplicate))
    parsed = yaml.load(workflow, Loader=UniqueLoader)
    for key in sorted(RESEARCH_JOBS):
        changed = copy.deepcopy(parsed)
        changed["jobs"]["ci-gate"]["needs"].remove(key)
        payload = yaml.safe_dump(changed)
        mutations.append(lambda payload=payload: audit_workflow(payload))
    mutations.append(lambda: audit_workflow(workflow + "\njobs: {}\n"))
    names = ("exact_vector_index_decision", "exact_lexical_topk_decision",
             "production_reranker_admission", "exact_vector_numerical_frontier")
    valid_xml = "<testsuite>" + "".join(f'<testcase name="{name}" />' for name in names) + "</testsuite>"
    validate_junit(valid_xml)
    for tag in ("failure", "error", "skipped"):
        bad_xml = valid_xml.replace(" />", f"><{tag}/></testcase>", 1)
        mutations.append(lambda text=bad_xml: validate_junit(text))
    mutations.append(lambda: validate_junit("<testsuite/>"))
    mutations.append(lambda: validate_junit(valid_xml.replace("</testsuite>", '<testcase name="exact_vector_index_decision"/></testsuite>')))
    for mutation in mutations:
        try:
            mutation()
        except (ValueError, KeyError):
            continue
        raise ValueError("negative integration control was accepted")
    return len(mutations)


def collect(args, inputs, workflow):
    output = args.output.resolve()
    artifacts = args.artifacts.resolve()
    output.mkdir(parents=True, exist_ok=True)
    receipt = {"schema": "r5-combined-integration-v1", "status": "FAIL",
               "inputs": inputs, "production_activation": False,
               "scope": "Actual combined workflow only; packaging is a separate workflow.",
               "checkout_sha": git("rev-parse", "HEAD"), "source_tree": git("rev-parse", "HEAD^{tree}"),
               "run_url": os.environ.get("GITHUB_SERVER_URL", "https://github.com") + "/" +
                          os.environ.get("GITHUB_REPOSITORY", "LimiNode/agent-memory-cpp") +
                          "/actions/runs/" + os.environ.get("GITHUB_RUN_ID", "local"),
               "errors": [], "files": []}
    try:
        validate_inputs(inputs)
        require(os.environ.get("R5_CI_GATE_RESULT") == "success", "combined CI gate did not pass")
        require(os.environ.get("R5_FULL_RESULT") == "success", "full research CTest did not pass")
        require(git("status", "--porcelain", "--untracked-files=no") == "", "dirty tracked source")
        require(not git("diff", "--name-only", inputs["integration_baseline"], "HEAD", "--", "src", "external"),
                "production or submodule changes in combined research")
        require(subprocess.run(["git", "diff", "--check", inputs["integration_baseline"], "HEAD"]).returncode == 0,
                "combined whitespace check failed")
        receipt["workflow_audit"] = audit_workflow(workflow)
        receipt["assembly_audit"] = audit_assembly(inputs)
        directories = [path for path in artifacts.iterdir() if path.is_dir()]
        for group in ARTIFACT_GROUPS:
            require(any(path.name.startswith(group) and any(p.is_file() for p in path.rglob("*"))
                        for path in directories), f"missing integration artifact group: {group}")
        results = list(artifacts.rglob("r5-integration-junit.xml"))
        require(len(results) == 1, "expected one full combined CTest XML")
        receipt["ctest"] = validate_junit(results[0].read_text(encoding="utf-8"))
        archive = output / "tested-source.tar.gz"
        subprocess.run(["git", "archive", "--format=tar.gz", f"--output={archive}", "HEAD"], check=True)
        receipt["source_archive_sha256"] = sha256(archive)
        receipt["input_manifest_sha256"] = sha256(args.inputs)
        receipt["workflow_sha256"] = sha256(Path(".github/workflows/ci.yml"))
        receipt["status"] = "PASS"
    except (ValueError, OSError, KeyError, ET.ParseError, subprocess.SubprocessError) as error:
        receipt["errors"].append(str(error))
    if artifacts.exists():
        receipt["files"] = [{"path": str(path.relative_to(artifacts)).replace("\\", "/"),
                             "bytes": path.stat().st_size, "sha256": sha256(path)}
                            for path in sorted(artifacts.rglob("*")) if path.is_file()]
    result = output / "integration.receipt.json"
    result.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    result.with_suffix(".json.sha256").write_text(f"{sha256(result)}  {result.name}\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "errors": receipt["errors"], "receipt": str(result)}))
    return 0 if receipt["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=Path("tools/agent-memory-bench/r5-integration-inputs.json"))
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--audit-assembly", action="store_true", help="Verify committed combined input blobs without asserting CI success")
    parser.add_argument("--artifacts", type=Path, default=Path(".build/r5-integration-downloads"))
    parser.add_argument("--output", type=Path, default=Path(".build/r5-integration-evidence"))
    args = parser.parse_args()
    inputs = json.loads(args.inputs.read_text(encoding="utf-8"))
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    if args.self_test:
        print(json.dumps({"status": "PASS", "negative_controls": self_test(inputs, workflow)}))
        return 0
    if args.audit_assembly:
        validate_inputs(inputs)
        result = audit_assembly(inputs)
        print(json.dumps({"assembly_status": "PASS", "unique_input_files": len(result["exact_unique_input_blobs"]),
                          "shared_build_files": result["reviewed_shared_build_files"], "ci_status": "NOT_CHECKED"}))
        return 0
    return collect(args, inputs, workflow)


if __name__ == "__main__":
    sys.exit(main())
