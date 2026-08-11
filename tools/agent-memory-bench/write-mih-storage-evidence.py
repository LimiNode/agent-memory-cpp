#!/usr/bin/env python3
"""Write and validate the compact evidence bundle for the MIH storage benchmark."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{path} must contain a JSON object")
    return value


REQUIRED_ROW_FIELDS = {
    "csr_warm_lookup_decode_dedup_ms_median",
    "mdbx_warm_lookup_decode_dedup_ms_median",
    "mdbx_empty_read_transaction_ms_median",
    "bucket_metadata_lookups_per_query",
    "mdbx_posting_page_reads_per_query",
    "unique_candidates_per_query",
}


def validate_reports(paths: list[Path]) -> dict[str, Any]:
    reference: dict[str, Any] | None = None
    expected_pages = {64, 256, 1024}
    seen_pages: set[int] = set()
    for path in paths:
        report = read_json(path)
        require(report.get("schema_version") == 4, f"{path} schema is not v4")
        require(report.get("family") == "mih_mdbx_csr_storage_benchmark_v1", f"{path} family is invalid")
        require(report.get("candidate_union_preflight") == "all selected query/radius CSR and MDBX seen vectors compared exactly before timing", f"{path} has no exact-union preflight")
        require(report.get("query_count") == 128 and report.get("repeat_count") == 5, f"{path} query contract is invalid")
        page_entries = report.get("page_entries")
        require(page_entries in expected_pages and page_entries not in seen_pages, f"{path} page-size matrix is invalid")
        seen_pages.add(page_entries)
        require(len(report.get("selected_query_positions", [])) == 128, f"{path} query selection is invalid")
        rows = report.get("rows", [])
        require(isinstance(rows, list) and [row.get("global_radius") for row in rows] == [48, 56, 64], f"{path} radius matrix is invalid")
        for row in rows:
            require(REQUIRED_ROW_FIELDS <= row.keys(), f"{path} timing row is incomplete")
        stack = report.get("storage_stack", {})
        require(stack.get("provenance_authoritative") is True, f"{path} is not authoritative")
        require(stack.get("provenance_reason") == "repository_pinned_external_submodules", f"{path} dependency provenance is invalid")
        if reference is None:
            reference = report
        else:
            for field in ("input_manifest", "evaluator_source_manifest_sha256", "evaluator_build_environment", "storage_stack", "query_seed", "query_selection_algorithm", "selected_query_positions"):
                require(report.get(field) == reference.get(field), f"{path} differs in {field}")
    require(seen_pages == expected_pages, "evidence requires page sizes 64, 256, and 1024")
    require(reference is not None, "evidence reports are empty")
    return reference


def validate_configs(paths: list[Path], input_root: Path, report: dict[str, Any]) -> None:
    expected_pages = {64, 256, 1024}
    seen_pages: set[int] = set()
    for path in paths:
        config = read_json(path)
        page_entries = config.get("page_entries")
        require(page_entries in expected_pages and page_entries not in seen_pages, f"{path} page-size config is invalid")
        seen_pages.add(page_entries)
        require(config.get("query_count") == report["query_count"], f"{path} query count differs from report")
        require(config.get("repeat_count") == report["repeat_count"], f"{path} repeat count differs from report")
        require(config.get("query_seed") == report["query_seed"], f"{path} query seed differs from report")
        require(config.get("global_radii") == [48, 56, 64], f"{path} radius config is invalid")
        require(Path(config.get("input_directory", "")).resolve() == input_root.resolve(), f"{path} input directory differs from evidence input")
    require(seen_pages == expected_pages, "evidence requires page-size configs 64, 256, and 1024")


def validate_input_binding(report: dict[str, Any], input_manifest: dict[str, Any]) -> None:
    require(report.get("input_manifest") == input_manifest, "packaged input manifest differs from report provenance")


def validate_source_provenance(
    report: dict[str, Any], input_manifest: dict[str, Any], sources: dict[str, str]
) -> None:
    benchmark_source = "tools/agent-memory-bench/mih_storage_benchmark.cpp"
    benchmark_sha256 = sources.get(benchmark_source)
    require(benchmark_sha256 is not None, "benchmark source snapshot is missing")
    expected_evaluator_manifest = hashlib.sha256(
        f"{benchmark_source}:{benchmark_sha256}\n".encode("utf-8")
    ).hexdigest()
    require(report["evaluator_source_manifest_sha256"] == expected_evaluator_manifest, "benchmark snapshot does not match report provenance")
    input_sources = input_manifest.get("source_files_sha256", {})
    for name, expected_sha256 in input_sources.items():
        matches = [actual for path, actual in sources.items() if Path(path).name == name]
        require(len(matches) == 1 and matches[0] == expected_sha256, f"input source snapshot is invalid: {name}")
    writer_source = "tools/agent-memory-bench/write-mih-storage-evidence.py"
    require(writer_source in sources, "evidence writer source snapshot is missing")


def archive(args: argparse.Namespace) -> None:
    report_paths = [Path(value) for value in args.report]
    report = validate_reports(report_paths)
    input_root = Path(args.input)
    input_manifest = read_json(input_root / "manifest.json")
    validate_input_binding(report, input_manifest)
    config_paths = [Path(value) for value in args.config]
    validate_configs(config_paths, input_root, report)
    paths: list[tuple[Path, str]] = [(input_root / "manifest.json", "bundle/input/manifest.json")]
    for field in ("document_codes_file", "query_codes_file"):
        path = input_root / input_manifest[field]
        require(sha256_file(path) == input_manifest[field.replace("_file", "_sha256")], f"{path} SHA-256 is invalid")
        paths.append((path, f"bundle/input/{path.name}"))
    for path in config_paths:
        paths.append((path, f"bundle/configs/{path.name}"))
    for path in report_paths:
        paths.append((path, f"bundle/reports/{path.name}"))
    for path in [Path(value) for value in args.source]:
        require(not path.is_absolute() and ".." not in path.parts, "source path must be repository-relative")
        paths.append((path, f"bundle/sources/{path.as_posix()}"))
    require(all(path.is_file() for path, _ in paths), "an evidence input is missing")
    names = [name for _, name in paths]
    require(len(names) == len(set(names)), "evidence archive names are not unique")
    entries = {name: {"sha256": sha256_file(path), "size": path.stat().st_size} for path, name in paths}
    sources = {name.removeprefix("bundle/sources/"): entry["sha256"] for name, entry in entries.items() if name.startswith("bundle/sources/")}
    validate_source_provenance(report, input_manifest, sources)
    writer_source = "tools/agent-memory-bench/write-mih-storage-evidence.py"
    bundle_root = hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    manifest = {"schema_version": 2, "family": "mih_storage_benchmark_evidence_v1", "bundle_root_sha256": bundle_root, "writer_source_sha256": sources[writer_source], "entries": entries}
    output = Path(args.output)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive_file:
        for path, name in paths:
            archive_file.write(path, name)
        archive_file.writestr("bundle/evidence-bundle-manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    expected_names = names + ["bundle/evidence-bundle-manifest.json"]
    with zipfile.ZipFile(output) as archive_file:
        require(archive_file.namelist() == expected_names, "evidence ZIP names are not deterministic POSIX paths")
    print(json.dumps({"archive": str(output), "archive_sha256": sha256_file(output), "bundle_root_sha256": bundle_root}, indent=2))


def self_test() -> int:
    report = {
        "schema_version": 4,
        "family": "mih_mdbx_csr_storage_benchmark_v1",
        "candidate_union_preflight": "all selected query/radius CSR and MDBX seen vectors compared exactly before timing",
        "query_count": 128,
        "repeat_count": 5,
        "query_seed": 20260810,
        "query_selection_algorithm": "std_mt19937_64_shuffle_v1",
        "selected_query_positions": list(range(128)),
        "storage_stack": {"provenance_authoritative": True, "provenance_reason": "repository_pinned_external_submodules"},
        "input_manifest": {"source_files_sha256": {}},
        "rows": [{"global_radius": radius, **{field: 1 for field in REQUIRED_ROW_FIELDS}} for radius in (48, 56, 64)],
    }
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths: list[Path] = []
            for page_entries in (64, 256, 1024):
                value = copy.deepcopy(report)
                value["page_entries"] = page_entries
                path = root / f"report-{page_entries}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                paths.append(path)
            validate_reports(paths)
            invalid = copy.deepcopy(report)
            invalid["page_entries"] = 64
            invalid["rows"][1]["global_radius"] = 48
            invalid_path = root / "invalid-radius.json"
            invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
            try:
                validate_reports([invalid_path, paths[1], paths[2]])
                raise ValueError("duplicate radius was accepted")
            except ValueError as error:
                require(str(error) != "duplicate radius was accepted", str(error))
            invalid["rows"] = copy.deepcopy(report["rows"])
            invalid["storage_stack"]["provenance_authoritative"] = False
            invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
            try:
                validate_reports([invalid_path, paths[1], paths[2]])
                raise ValueError("non-authoritative report was accepted")
            except ValueError as error:
                require(str(error) != "non-authoritative report was accepted", str(error))
            try:
                validate_input_binding(report, {"source_files_sha256": {"other": "x"}})
                raise ValueError("report/input manifest mismatch was accepted")
            except ValueError as error:
                require(str(error) != "report/input manifest mismatch was accepted", str(error))
            configs: list[Path] = []
            for page_entries in (64, 256, 1024):
                config = {
                    "input_directory": str(root),
                    "page_entries": page_entries,
                    "query_count": 128,
                    "repeat_count": 5,
                    "query_seed": 20260810,
                    "global_radii": [48, 56, 64],
                }
                path = root / f"config-{page_entries}.json"
                path.write_text(json.dumps(config), encoding="utf-8")
                configs.append(path)
            validate_configs(configs, root, report)
            wrong_config = json.loads(configs[0].read_text(encoding="utf-8"))
            wrong_config["page_entries"] = 256
            configs[0].write_text(json.dumps(wrong_config), encoding="utf-8")
            try:
                validate_configs(configs, root, report)
                raise ValueError("wrong page-size matrix was accepted")
            except ValueError as error:
                require(str(error) != "wrong page-size matrix was accepted", str(error))
            benchmark_source = "tools/agent-memory-bench/mih_storage_benchmark.cpp"
            source_sha256 = "a" * 64
            source_report = copy.deepcopy(report)
            source_report["evaluator_source_manifest_sha256"] = hashlib.sha256(
                f"{benchmark_source}:{source_sha256}\n".encode("utf-8")
            ).hexdigest()
            validate_source_provenance(source_report, {"source_files_sha256": {}}, {benchmark_source: source_sha256, "tools/agent-memory-bench/write-mih-storage-evidence.py": "b" * 64})
            source_report["evaluator_source_manifest_sha256"] = "0" * 64
            try:
                validate_source_provenance(source_report, {"source_files_sha256": {}}, {benchmark_source: source_sha256, "tools/agent-memory-bench/write-mih-storage-evidence.py": "b" * 64})
                raise ValueError("wrong source snapshot was accepted")
            except ValueError as error:
                require(str(error) != "wrong source snapshot was accepted", str(error))
        print("MIH storage evidence writer self-test passed")
        return 0
    except (OSError, ValueError) as error:
        print(f"MIH storage evidence writer self-test failed: {error}", file=sys.stderr)
        return 1


def main(argv: list[str]) -> int:
    if argv == ["self-test"]:
        return self_test()
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--report", action="append", required=True)
    parser.add_argument("--config", action="append", required=True)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--output", required=True)
    try:
        archive(parser.parse_args(argv))
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"write-mih-storage-evidence: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
