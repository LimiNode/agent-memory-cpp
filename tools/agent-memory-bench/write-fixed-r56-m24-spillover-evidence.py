#!/usr/bin/env python3
"""Package compact, validated fixed-r56 spillover diagnostic evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True
FAMILY = "fixed_r56_spillover_evidence_v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"spillover evidence JSON object differs: {path}")
    return value


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def verify_archive(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(names and len(names) == len(set(names)) and all(name.startswith("bundle/") and "\\" not in name and "/../" not in f"/{name}" for name in names), "spillover evidence archive paths differ")
        manifest = json.loads(archive.read("bundle/evidence-manifest.json"))
        members = manifest.get("members")
        require(manifest.get("family") == FAMILY and isinstance(members, dict), "spillover evidence manifest differs")
        expected = set(names) - {"bundle/evidence-manifest.json"}
        require(set(members) == expected, "spillover evidence member set differs")
        observed = {name: {"sha256": sha256_bytes(archive.read(name)), "size": len(archive.read(name))} for name in sorted(expected)}
        require(members == observed, "spillover evidence member digest differs")
        require(manifest.get("bundle_root_sha256") == sha256_bytes(canonical(observed)), "spillover evidence bundle root differs")
    return manifest


def run(args: Any) -> None:
    plan, source, diagnostic = load(args.plan), load(args.source_result), load(args.diagnostic_result)
    require(diagnostic.get("plan_sha256") == sha256(args.plan) and diagnostic.get("source_result_sha256") == sha256(args.source_result), "spillover evidence result binding differs")
    require(source.get("plan_sha256") == sha256(args.plan), "spillover evidence source plan binding differs")
    files: dict[str, bytes] = {
        "bundle/plan.json": args.plan.read_bytes(),
        "bundle/source-result.json": args.source_result.read_bytes(),
        "bundle/diagnostic-result.json": args.diagnostic_result.read_bytes(),
        "bundle/experiment-note.md": args.note.read_bytes(),
    }
    for scale in diagnostic.get("scales", []):
        scale_id = scale.get("id")
        require(isinstance(scale_id, str), "spillover evidence scale differs")
        for row in scale.get("rows", []):
            identifier = row.get("id")
            require(isinstance(identifier, str), "spillover evidence row differs")
            root = args.diagnostic_root / scale_id
            for category, field in (("configs", "diagnostic_config_sha256"), ("native-reports", "diagnostic_report_sha256"), ("candidate-unions", "candidate_union_sha256")):
                path = root / category / f"{identifier}.json"
                require(path.is_file() and sha256(path) == row.get(field), f"spillover evidence diagnostic member differs: {scale_id}/{identifier}/{category}")
                files[f"bundle/diagnostics/{scale_id}/{category}/{identifier}.json"] = path.read_bytes()
    members = {name: {"sha256": sha256_bytes(value), "size": len(value)} for name, value in sorted(files.items())}
    manifest = {"schema_version": 1, "family": FAMILY, "measured_source_ref": args.measured_source_ref, "bundle_root_sha256": sha256_bytes(canonical(members)), "members": members}
    files["bundle/evidence-manifest.json"] = canonical(manifest)
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
            archive_path = Path(temporary) / "evidence.zip"
            members = {"bundle/value": {"sha256": sha256_bytes(b"value"), "size": 5}}
            manifest = {"schema_version": 1, "family": FAMILY, "measured_source_ref": "test", "bundle_root_sha256": sha256_bytes(canonical(members)), "members": members}
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("bundle/value", b"value")
                archive.writestr("bundle/evidence-manifest.json", canonical(manifest))
            verify_archive(archive_path)
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("bundle/value", b"mutation")
                archive.writestr("bundle/evidence-manifest.json", canonical(manifest))
            try:
                verify_archive(archive_path)
            except ValueError:
                pass
            else:
                raise ValueError("spillover evidence mutation was accepted")
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"fixed-r56 spillover evidence packager self-test failed: {error}", file=sys.stderr)
        return 1
    print("fixed-r56 spillover evidence packager self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--self-test", action="store_true"); parser.add_argument("--plan", type=Path); parser.add_argument("--source-result", type=Path); parser.add_argument("--diagnostic-result", type=Path); parser.add_argument("--diagnostic-root", type=Path); parser.add_argument("--note", type=Path); parser.add_argument("--measured-source-ref", type=str); parser.add_argument("--output", type=Path); args = parser.parse_args()
    try:
        if args.self_test:
            return self_test()
        require(all((args.plan, args.source_result, args.diagnostic_result, args.diagnostic_root, args.note, args.measured_source_ref, args.output)), "spillover evidence packager arguments are required")
        run(args)
        return 0
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"write-fixed-r56-spillover-evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
