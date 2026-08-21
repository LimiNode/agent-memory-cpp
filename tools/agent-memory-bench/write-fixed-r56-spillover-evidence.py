#!/usr/bin/env python3
"""Package compact verified evidence for the fixed-r56 spillover diagnostic."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
FAMILY = "fixed_r56_spillover_diagnostic_evidence_v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return digest(path.read_bytes())


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"fixed-r56 evidence JSON object differs: {path}")
    return value


def verify_archive(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(names and len(names) == len(set(names)) and all(name.startswith("bundle/") and "\\" not in name and "/../" not in f"/{name}" for name in names), "fixed-r56 evidence archive paths differ")
        manifest = json.loads(archive.read("bundle/evidence-manifest.json"))
        members = manifest.get("members")
        require(manifest.get("family") == FAMILY and isinstance(members, dict), "fixed-r56 evidence manifest differs")
        expected = set(names) - {"bundle/evidence-manifest.json"}
        observed = {name: {"sha256": digest(archive.read(name)), "size": len(archive.read(name))} for name in sorted(expected)}
        require(set(members) == expected and members == observed and manifest.get("bundle_root_sha256") == digest(canonical(observed)), "fixed-r56 evidence member binding differs")
    return manifest


def run(args: Any) -> None:
    plan, protocol, result = load(args.plan), load(args.protocol), load(args.result)
    require(result.get("plan_sha256") == sha256(args.plan) and result.get("protocol_sha256") == sha256(args.protocol), "fixed-r56 evidence result provenance differs")
    files: dict[str, bytes] = {"bundle/plan.json": args.plan.read_bytes(), "bundle/protocol.json": args.protocol.read_bytes(), "bundle/result.json": args.result.read_bytes(), "bundle/experiment-note.md": args.note.read_bytes()}
    for scale in result.get("scales", []):
        scale_id, identifier = scale.get("scale"), scale.get("id")
        require(isinstance(scale_id, str) and isinstance(identifier, str), "fixed-r56 evidence scale differs")
        root = args.diagnostic_root / scale_id
        for relative, field in (("diagnostic-config.json", "diagnostic_config_sha256"), ("diagnostic-report.json", "diagnostic_report_sha256"), ("candidate-union.json", "candidate_union_sha256")):
            path = root / relative
            require(path.is_file() and sha256(path) == scale.get(field), f"fixed-r56 evidence diagnostic binding differs: {scale_id}/{relative}")
            files[f"bundle/diagnostics/{scale_id}/{relative}"] = path.read_bytes()
    members = {name: {"sha256": digest(value), "size": len(value)} for name, value in sorted(files.items())}
    files["bundle/evidence-manifest.json"] = canonical({"schema_version": 1, "family": FAMILY, "measured_source_ref": args.measured_source_ref, "bundle_root_sha256": digest(canonical(members)), "members": members})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(files.items()):
            info = zipfile.ZipInfo(name); info.date_time = (1980, 1, 1, 0, 0, 0); info.external_attr = 0o100644 << 16
            archive.writestr(info, value)
    manifest = verify_archive(args.output)
    print(json.dumps({"archive_sha256": sha256(args.output), "bundle_root_sha256": manifest["bundle_root_sha256"]}, sort_keys=True))


def self_test() -> int:
    try:
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "archive.zip"; members = {"bundle/value": {"sha256": digest(b"value"), "size": 5}}
            manifest = {"schema_version": 1, "family": FAMILY, "measured_source_ref": "test", "bundle_root_sha256": digest(canonical(members)), "members": members}
            with zipfile.ZipFile(path, "w") as archive: archive.writestr("bundle/value", b"value"); archive.writestr("bundle/evidence-manifest.json", canonical(manifest))
            verify_archive(path)
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"fixed-r56 spillover diagnostic evidence packager self-test failed: {error}", file=sys.stderr); return 1
    print("fixed-r56 spillover diagnostic evidence packager self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--plan", type=Path); parser.add_argument("--protocol", type=Path); parser.add_argument("--result", type=Path); parser.add_argument("--diagnostic-root", type=Path); parser.add_argument("--note", type=Path); parser.add_argument("--measured-source-ref"); parser.add_argument("--output", type=Path); args = parser.parse_args()
    try:
        if args.self_test: return self_test()
        require(all((args.plan, args.protocol, args.result, args.diagnostic_root, args.note, args.measured_source_ref, args.output)), "fixed-r56 evidence packager arguments are required")
        run(args); return 0
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"write-fixed-r56-spillover-evidence: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
