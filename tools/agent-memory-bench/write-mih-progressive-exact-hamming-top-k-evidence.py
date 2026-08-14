#!/usr/bin/env python3
"""Package validated progressive exact-Hamming diagnostic evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
THIS = Path(__file__).resolve(); ROOT = THIS.parents[2]


def require(value: bool, message: str) -> None:
    if not value: raise ValueError(message)
def sha256_bytes(value: bytes) -> str: return hashlib.sha256(value).hexdigest()
def sha256(path: Path) -> str: return sha256_bytes(path.read_bytes())
def digest(value: dict[str, str]) -> str: return sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
def snapshot(ref: str, path: str) -> bytes: return subprocess.run(["git", "show", f"{ref}:{path}"], cwd=ROOT, check=True, capture_output=True).stdout


def load_runner() -> Any:
    path=THIS.with_name("run-mih-progressive-exact-hamming-top-k.py"); spec=importlib.util.spec_from_file_location("progressive_evidence_runner", path)
    if spec is None or spec.loader is None: raise RuntimeError("cannot load progressive runner")
    module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module); return module


runner=load_runner()


def collect(args: Any) -> dict[str, bytes]:
    contract=runner.load_contract(args.contract); calibration=runner.shared.load_root(args.calibration_root); evaluation=runner.shared.load_root(args.evaluation_root); runner.shared.validate_calibration_evaluation_pair(calibration,evaluation)
    matrix_path=args.matrix_root/"matrix-manifest.json"; matrix=json.loads(matrix_path.read_text(encoding="utf-8")); names=("run-mih-progressive-exact-hamming-top-k.py","mih-progressive-exact-hamming-top-k.example.json","evaluate-mih-banding.py","evaluate-projection-quantization.py"); expected={name:sha256_bytes(snapshot(args.measured_source_ref,f"tools/agent-memory-bench/{name}")) for name in names}
    require(matrix.get("schema_version")==1 and matrix.get("family")==runner.FAMILY and matrix.get("contract_sha256")==sha256(args.contract) and matrix.get("source_files_sha256")==expected and matrix.get("source_bundle_sha256")==digest(expected),"progressive matrix provenance differs")
    expected_rows=[]; files={"bundle/contract.json":args.contract.read_bytes(),"bundle/matrix-manifest.json":matrix_path.read_bytes(),"bundle/experiment-note.md":args.note.read_bytes()}
    for seed in contract["seeds"]:
        report_path=args.matrix_root/"reports"/f"m16-r56-seed{seed}.json"; contribution_path=args.matrix_root/"contributions"/f"m16-r56-seed{seed}.npz"; report=json.loads(report_path.read_text(encoding="utf-8"))
        require(report.get("schema_version")==1 and report.get("family")==runner.FAMILY and report.get("source_files_sha256")==expected and report.get("source_bundle_sha256")==digest(expected) and report.get("calibration_materialization_manifest_sha256")==calibration["manifest_sha256"] and report.get("evaluation_materialization_manifest_sha256")==evaluation["manifest_sha256"] and report.get("seed")==seed and report.get("query_count")==len(evaluation["query_ids"]) and report.get("contribution_sha256")==sha256(contribution_path) and report.get("full_union_top_k_match_fraction")==1.0 and report.get("early_proof_fraction")==0.0,"progressive report differs")
        expected_rows.append({"seed":seed,"report_sha256":sha256(report_path),"contribution_sha256":sha256(contribution_path)}); files[f"bundle/reports/{report_path.name}"]=report_path.read_bytes(); files[f"bundle/contributions/{contribution_path.name}"]=contribution_path.read_bytes()
    require(matrix.get("rows")==expected_rows,"progressive matrix rows differ")
    for name in names: files[f"bundle/sources/{name}"]=snapshot(args.measured_source_ref,f"tools/agent-memory-bench/{name}")
    files[f"bundle/sources/{THIS.name}"]=THIS.read_bytes(); return files


def package(args: Any) -> None:
    files=collect(args); members={name:{"sha256":sha256_bytes(value),"size":len(value)} for name,value in sorted(files.items())}; root=digest({name:item["sha256"] for name,item in members.items()}); manifest={"schema_version":1,"family":"mih_progressive_exact_hamming_top_k_evidence_v1","measured_source_ref":args.measured_source_ref,"bundle_root_sha256":root,"members":members}; files["bundle/evidence-manifest.json"]=(json.dumps(manifest,indent=2,sort_keys=True)+"\n").encode(); args.output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.output,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,value in sorted(files.items()): info=zipfile.ZipInfo(name); info.date_time=(1980,1,1,0,0,0); info.external_attr=0o100644<<16; archive.writestr(info,value)
    with zipfile.ZipFile(args.output) as archive:
        require(set(archive.namelist())==set(files),"evidence member set differs")
        for name,value in files.items(): require(archive.read(name)==value,f"evidence member differs: {name}")
    print(json.dumps({"archive_sha256":sha256(args.output),"bundle_root_sha256":root},sort_keys=True))


def self_test() -> int:
    try:
        require(digest({"a":"b","c":"d"})==digest({"c":"d","a":"b"}),"canonical digest differs")
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"archive.zip"
            with zipfile.ZipFile(path,"w") as archive: archive.writestr("bundle/a",b"a")
            with zipfile.ZipFile(path) as archive: require(archive.read("bundle/a")==b"a","archive reopen differs")
    except (OSError,ValueError): print("MIH progressive exact Hamming evidence packager self-test failed",file=sys.stderr); return 1
    print("MIH progressive exact Hamming evidence packager self-test passed"); return 0


def main(argv:list[str])->int:
    parser=argparse.ArgumentParser(); parser.add_argument("--self-test",action="store_true"); parser.add_argument("--contract",type=Path); parser.add_argument("--matrix-root",type=Path); parser.add_argument("--calibration-root",type=Path); parser.add_argument("--evaluation-root",type=Path); parser.add_argument("--note",type=Path); parser.add_argument("--output",type=Path); parser.add_argument("--measured-source-ref"); args=parser.parse_args(argv)
    try:
        if args.self_test:return self_test()
        require(all((args.contract,args.matrix_root,args.calibration_root,args.evaluation_root,args.note,args.output,args.measured_source_ref)),"evidence packager arguments are required"); package(args); return 0
    except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError,subprocess.CalledProcessError) as error: print(f"write-mih-progressive-exact-hamming-top-k-evidence: {error}",file=sys.stderr); return 1


if __name__=="__main__":raise SystemExit(main(sys.argv[1:]))
