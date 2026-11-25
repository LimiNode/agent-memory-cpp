"""Verify the AMXR integration delta without rewriting historical inventories."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASE = "77a278bef338074227efa05365e83e967334f429"
INTEGRATED = "7a9be667b4f5657c1c50590dde6b0a4888acdd4b"
OUT = ROOT / "guides/audits/2026-11-foundation-amxr-integration-delta.json"
EXPECTED = {"src/agent_memory/domain/ExternalObjectRef.cpp",
            "src/agent_memory/domain/ExternalObjectRef.hpp",
            "src/agent_memory/domain/conversation_codec.cpp",
            "src/agent_memory/ingestion/ConversationSourceImporter.cpp"}


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def sha(source, path):
    return hashlib.sha256(git("show", f"{source}:{path}")).hexdigest()


def receipt():
    git("merge-base", "--is-ancestor", BASE, INTEGRATED)
    changed = set(git("diff", "--name-only", BASE, INTEGRATED, "--", "src/agent_memory").decode().splitlines())
    if changed != EXPECTED:
        raise ValueError(f"Unexpected production delta: {sorted(changed)}")
    header = git("show", f"{INTEGRATED}:src/agent_memory/domain/ExternalObjectRef.hpp").decode()
    for signature in ("ByteBuffer serialize_external_object_ref_bytes(",
                      "ExternalObjectRef deserialize_external_object_ref_bytes(ByteView",
                      "std::string serialize_external_object_ref(",
                      "ExternalObjectRef deserialize_external_object_ref(std::string_view"):
        if signature not in header:
            raise ValueError(f"Missing signature: {signature}")
    for path in ("src/agent_memory/domain/conversation_codec.cpp",
                 "src/agent_memory/ingestion/ConversationSourceImporter.cpp"):
        source = git("show", f"{INTEGRATED}:{path}").decode()
        if "serialize_external_object_ref(" in source or "serialize_external_object_ref_bytes(" not in source:
            raise ValueError(f"Direct byte consumer mismatch: {path}")
    return {"schema": "agent-memory.foundation-amxr-delta.v1",
            "historical_inventory_source": BASE, "integrated_source": INTEGRATED,
            "integrated_tree": git("rev-parse", f"{INTEGRATED}^{{tree}}").decode().strip(),
            "scope": "Four production files; original inventory/catalogue unchanged. Static controls are not runtime equivalence proof.",
            "files": [{"path": p, "before_sha256": sha(BASE, p), "after_sha256": sha(INTEGRATED, p)}
                      for p in sorted(changed)]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--current", action="store_true")
    args = parser.parse_args()
    actual = receipt()
    if args.write:
        OUT.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    elif json.loads(OUT.read_text(encoding="utf-8")) != actual:
        raise ValueError("Integration delta is stale")
    if args.current and git("diff", "--name-only", INTEGRATED, "HEAD", "--", "src/agent_memory").strip():
        raise ValueError("Current production tree differs from the integration source")
    print(json.dumps({"verified": True, "source": INTEGRATED, "files": len(actual["files"]),
                      "current_checked": args.current}))
