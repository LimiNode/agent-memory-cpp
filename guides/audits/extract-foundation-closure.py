"""Reproducible source-bound foundation inventory; preserves historical catalogues.

The string ledger is exhaustive lexical discovery, NOT semantic inference.
The companion Markdown audit owns semantic decisions and known limitations.
Verification preserves the historical SOURCE catalogue; the separate
verify-foundation-amxr-delta.py --current qualifies the integrated source delta.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "77a278bef338074227efa05365e83e967334f429"
OUT = ROOT / "guides/audits/2026-11-foundation-source-inventory.json"
CATALOGUE = ROOT / "guides/audits/2026-11-foundation-public-type-catalogue.json"


def module():
    spec = importlib.util.spec_from_file_location(
        "catalogue", ROOT / "guides/audits/extract-public-type-catalogue.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    result.BASE = SOURCE
    result.LOCAL = ROOT / ".artifacts/foundation-catalogue"
    return result


def inventory():
    files = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", SOURCE,
         "src/agent_memory"], text=True).splitlines()
    rows = []
    for name in files:
        raw = subprocess.check_output(["git", "-C", str(ROOT), "show", f"{SOURCE}:{name}"])
        sites = [{"line": n, "source": line.strip()} for n, line in
                 enumerate(raw.decode("utf-8").splitlines(), 1)
                 if re.search(r"std::(?:string(?:_view)?|basic_string)\b", line)]
        rows.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest(),
                     "string_sites": sites})
    return {"schema": "agent-memory.foundation-discovery.v1", "source_commit": SOURCE,
            "scope": "All tracked production src/agent_memory files; all lexical std string sites, including text/comments. Not a semantic classifier or complete C++ call graph.",
            "files": rows, "counts": {"files": len(rows),
                "string_files": sum(bool(r["string_sites"]) for r in rows),
                "string_sites": sum(len(r["string_sites"]) for r in rows)}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--clang")
    parser.add_argument("--mingw-root", type=Path)
    args = parser.parse_args()
    audit = inventory()
    cat = module()
    if args.verify:
        assert json.loads(OUT.read_text(encoding="utf-8")) == audit
        cat.verify(json.loads(CATALOGUE.read_text(encoding="utf-8")), historical_source=True)
    else:
        if not args.clang:
            parser.error("generation needs --clang")
        headers = cat.included_headers()
        ast, extraction = cat.compiler_ast(headers, args.clang, args.mingw_root)
        catalogue = cat.make_catalogue(ast, headers, extraction)
        cat.verify(catalogue)
        # Match the historical catalogue's compact per-row review format.
        blocks = []
        for key, value in catalogue.items():
            if isinstance(value, list) and key in {"headers", "types", "identity_fields", "identity_decisions"}:
                encoded = "[\n" + ",\n".join(
                    "    " + json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                    for row in value) + "\n  ]"
            else:
                encoded = json.dumps(value, ensure_ascii=False, indent=2)
            blocks.append("  " + json.dumps(key) + ": " + encoded)
        CATALOGUE.write_text("{\n" + ",\n".join(blocks) + "\n}\n", encoding="utf-8")
        OUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verified": args.verify, **audit["counts"]}))
