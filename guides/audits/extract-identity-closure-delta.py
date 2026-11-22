"""Add a fresh source-bound AST delta without relabelling the historical audit."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "a27e0c0558314d3760b41217ca37b29ab71c3bff"
BASE = "acd5ad5545386b3feeaa79f8a587855b60801b4d"
OUT = ROOT / "guides/audits/2026-11-identity-closure-type-delta.json"
spec = importlib.util.spec_from_file_location("historical_catalogue", Path(__file__).with_name("extract-public-type-catalogue.py"))
catalogue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalogue)


def source_bytes(commit: str, path: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), "show", f"{commit}:{path}"])


def baseline() -> dict:
    path = "guides/audits/2026-11-public-type-catalogue.json"
    original = source_bytes("95262b9d52cd96d88a05b106f5df5e0e498e4340", path)
    assert original == (ROOT / path).read_bytes(), "Historical catalogue changed"
    data = json.loads(original)
    assert data["source_commit"] == BASE
    for header in data["headers"]:
        assert catalogue.sha(source_bytes(BASE, header["path"])) == header["sha256"]
    return data


def verify(delta: dict) -> None:
    old = baseline()
    assert delta["source_commit"] == SOURCE and delta["baseline_commit"] == BASE
    changed = catalogue.git("diff", "--name-only", BASE, SOURCE, "--", "src").splitlines()
    assert [h["path"] for h in delta["changed_source_files"]] == changed
    for header in delta["changed_source_files"]:
        raw = source_bytes(SOURCE, header["path"])
        assert catalogue.sha(raw) == header["sha256"]
        assert raw == (ROOT / header["path"]).read_bytes(), "Source drift requires a new extraction"
    for declaration in delta["new_type_declarations"]:
        raw = source_bytes(SOURCE, declaration["header"])
        assert raw[declaration["offset"]:].decode().startswith(declaration["name"])
        assert catalogue.sha(raw[declaration["range_begin"]:declaration["range_end"]]) == declaration["declaration_sha256"]
    assert delta["baseline_type_count"] == old["counts"]["type_declarations"]
    assert delta["source_type_count"] == delta["baseline_type_count"] + len(delta["new_type_declarations"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--clang")
    parser.add_argument("--mingw-root", type=Path)
    parser.add_argument("--artifacts", type=Path, default=ROOT / ".artifacts/identity-closure-catalogue")
    args = parser.parse_args()
    if args.verify:
        delta = json.loads(OUT.read_text())
        verify(delta)
    else:
        if not args.clang:
            parser.error("--clang is required for a fresh AST extraction")
        old = baseline()
        catalogue.BASE = SOURCE
        catalogue.LOCAL = args.artifacts
        headers = catalogue.included_headers()
        for header in headers:
            assert source_bytes(SOURCE, header) == (ROOT / header).read_bytes()
        ast, profile = catalogue.compiler_ast(headers, args.clang, args.mingw_root)
        current = catalogue.make_catalogue(ast, headers, profile)
        def key(t: dict) -> tuple:
            return t["header"], t["qualified_name"], t["ast_kind"], t["definition"]
        old_types = {key(t): t for t in old["types"]}
        new_types = {key(t): t for t in current["types"]}
        assert len(old_types) == len(old["types"]) and len(new_types) == len(current["types"]), "Ambiguous declaration identity"
        assert old_types.keys() <= new_types.keys(), "Existing declaration disappeared"
        assert all(t["declaration_sha256"] == new_types[k]["declaration_sha256"] for k, t in old_types.items()), "Existing declaration changed"
        added = [t for k, t in new_types.items() if k not in old_types]
        for declaration in added:
            declaration["primary_contract_owner_candidate"] = "guides/identity-policy.md"
            declaration["owner_status"] = "IMPLEMENTED_REVIEW_REQUIRED"
        changed = catalogue.git("diff", "--name-only", BASE, SOURCE, "--", "src").splitlines()
        delta = {"schema": "agent-memory.identity-type-delta.v1", "source_commit": SOURCE,
                 "baseline_commit": BASE, "status": "REVIEW_REQUIRED_NOT_CLOSED",
                 "scope": "Fresh complete-header AST comparison; only added declarations are repeated here. Name-based identity-field counts are heuristics, not complete semantic ID classification.",
                 "extraction": profile, "baseline_type_count": len(old_types), "source_type_count": len(new_types),
                 "source_counts": current["counts"], "unchanged_existing_declaration_hashes": True,
                 "changed_source_files": [{"path": p, "sha256": catalogue.sha(source_bytes(SOURCE, p))} for p in changed],
                 "new_type_declarations": added}
        verify(delta)
        OUT.write_text(json.dumps(delta, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verified": True, "source_commit": SOURCE, "new_types": len(delta["new_type_declarations"])}))
