#!/usr/bin/env python3
"""Write a portable fail-closed evidence bundle for the MIH ADC-ceiling study."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, sys, tempfile, zipfile
from pathlib import Path
from typing import Any
sys.dont_write_bytecode = True
import numpy

HAMMING_LIMITS=(512,768,1024,1536); SECOND_LIMITS=(64,128,256,512)
STAGES=("hamming","binary-adc","continuous-itq-projection-l2","exact-e5-within-hamming")
BUDGETS=((8192,11000),(12288,19000),(16384,30000)); SEEDS=(42,43,44,45,46)

def require(value: bool, message: str) -> None:
    if not value: raise ValueError(message)
def sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def digest(value: Any) -> str: return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()
def source_hashes(names: tuple[str,...]) -> dict[str,str]:
    return {name:sha256(Path(__file__).with_name(name)) for name in names}
def load(filename: str, name: str) -> Any:
    path=Path(__file__).with_name(filename); spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {filename}")
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

shared=load("evaluate-projection-quantization.py","mih_adc_ceiling_evidence_shared")
runner=load("run-mih-adc-ceiling-matrix.py","mih_adc_ceiling_evidence_runner")
bootstrap=load("bootstrap-mih-adc-ceiling.py","mih_adc_ceiling_evidence_bootstrap")

def expected_rows(matrix: Path) -> dict[str,dict[str,int]]:
    return dict(runner.rows(runner.load_matrix(matrix)))

def load_npz(path: Path) -> tuple[dict[str,numpy.ndarray],dict[str,Any]]:
    values,identity=bootstrap.load_values(path)
    return values,identity

def validate_report(report: dict[str,Any], row: dict[str,int], values: dict[str,numpy.ndarray], identity: dict[str,Any], contribution: Path) -> None:
    require(report.get("schema_version")==1 and report.get("family")=="mih_adc_ceiling_stage_loss_v1", "report identity is invalid")
    require(report.get("soft_candidate_target")==row["candidate"] and report.get("soft_posting_visit_target")==row["postings"] and report.get("seed")==row["seed"] and report.get("query_count")==1252 and report.get("hamming_limits")==list(HAMMING_LIMITS) and report.get("second_limits")==list(SECOND_LIMITS) and report.get("second_stages")==list(STAGES), "report matrix contract is invalid")
    require(report.get("per_query_contributions_sha256")==sha256(contribution) and report.get("per_query_contribution_identity")==identity, "report contribution provenance is invalid")
    require(report.get("e5_oracle_raw_union_survival")==float(numpy.mean(values["raw_union_oracle_survival"])), "raw summary differs")
    require(report.get("e5_oracle_hamming_survival")==[float(numpy.mean(values["hamming_oracle_survival"][index])) for index in range(4)], "Hamming summary differs")
    cells=report.get("cells"); require(isinstance(cells,list) and len(cells)==64, "cell grid is invalid")
    expected=[{"hamming_limit":h,"second_stage":stage,"second_limit":k,"e5_oracle_second_stage_survival":float(numpy.mean(values["second_oracle_survival"][hi,si,ki]))} for hi,h in enumerate(HAMMING_LIMITS) for si,stage in enumerate(STAGES) for ki,k in enumerate(SECOND_LIMITS)]
    require(cells==expected,"second-stage summaries differ")
    require(report.get("mean_probe_count_by_flip_depth")==[float(numpy.mean(values["probe_count_by_flip_depth"][:,i])) for i in range(3)],"probe depth summary differs")
    require(report.get("mean_posting_visits_by_flip_depth")==[float(numpy.mean(values["posting_visit_count_by_flip_depth"][:,i])) for i in range(3)],"posting depth summary differs")
    expected_stops={reason:float(numpy.count_nonzero(values["stop_reason"]==reason))/1252 for reason in ("candidate","posting","exhausted")}
    require(report.get("stop_reason_fractions")==expected_stops,"stop summary differs")

def bootstrap_id(candidate:int, seed:int, hamming:int, second:int, right:str)->str:
    return f"mih256-adc-vs-{right}-target{candidate}-h{hamming}-k{second}-seed{seed}"

def validate_bootstrap(path: Path, contribution: Path, values: dict[str,numpy.ndarray], identity: dict[str,Any], candidate:int, seed:int, hamming:int, second:int, right:str) -> dict[str,Any]:
    item=json.loads(path.read_text(encoding="utf-8")); identifier=bootstrap_id(candidate,seed,hamming,second,right)
    left=bootstrap.cell(values,hamming,second,"binary-adc"); right_values=bootstrap.cell(values,hamming,second,right)
    expected=shared.paired_bootstrap_metrics({"survival":left},{"survival":right_values},("survival",),10000,20260811)
    expected_sources=source_hashes(("bootstrap-mih-adc-ceiling.py","evaluate-projection-quantization.py"))
    require(item.get("schema_version")==1 and item.get("family")=="mih_adc_ceiling_paired_bootstrap_v1" and item.get("id")==identifier and item.get("contributions_file")==contribution.name and item.get("contributions_sha256")==sha256(contribution) and item.get("identity")==identity and item.get("query_count")==1252 and item.get("hamming_limit")==hamming and item.get("second_limit")==second and item.get("left_stage")=="binary-adc" and item.get("right_stage")==right and item.get("replicates")==10000 and item.get("seed")==20260811 and item.get("metrics")==expected and item.get("bootstrap_source_files_sha256")==expected_sources and item.get("bootstrap_source_bundle_sha256")==digest(expected_sources), f"bootstrap contract is invalid: {path.name}")
    return {"id":identifier,"file":path.name,"sha256":sha256(path),"hamming_limit":hamming,"second_limit":second,"right_stage":right,"metrics":item["metrics"]}

def write(args: Any) -> dict[str,Any]:
    rows=expected_rows(args.matrix); reports=args.input_root/"reports"; contributions=args.input_root/"contributions"; boot=args.bootstrap_root
    require({p.stem for p in reports.glob("*.json")}==set(rows) and {p.stem for p in contributions.glob("*.npz")}==set(rows),"row grid is incomplete")
    compact_rows=[]; files=[(args.matrix,"bundle/matrix.json")]; comparisons=[]; identity=None; contract=None
    for name,row in sorted(rows.items()):
        report_path=reports/f"{name}.json"; contribution=contributions/f"{name}.npz"; report=json.loads(report_path.read_text(encoding="utf-8")); values,item_identity=load_npz(contribution); validate_report(report,row,values,item_identity,contribution)
        current=(report["evaluator_source_files_sha256"],report["evaluator_source_bundle_sha256"],report["evaluator_runtime"],report["calibration_materialization_manifest_sha256"],report["evaluation_materialization_manifest_sha256"],report["calibration_train_ids_sha256"])
        if identity is None: identity,contract=item_identity,current
        else: require(identity==item_identity and contract==current,f"row provenance differs: {name}")
        compact_rows.append({"id":name,"report_file":report_path.name,"report_sha256":sha256(report_path),"contributions_file":contribution.name,"contributions_sha256":sha256(contribution),**row})
        files += [(report_path,f"bundle/reports/{report_path.name}"),(contribution,f"bundle/contributions/{contribution.name}")]
        for hamming in HAMMING_LIMITS:
            for second in SECOND_LIMITS:
                for right in ("continuous-itq-projection-l2","exact-e5-within-hamming"):
                    path=boot/f"{bootstrap_id(row['candidate'],row['seed'],hamming,second,right)}.json"
                    comparisons.append(validate_bootstrap(path,contribution,values,item_identity,row["candidate"],row["seed"],hamming,second,right)); files.append((path,f"bundle/bootstrap/{path.name}"))
    require(len(comparisons)==480 and identity is not None and contract is not None,"bootstrap grid is incomplete")
    expected_evaluator_sources=source_hashes(("evaluate-mih-adc-ceiling.py","evaluate-mih-banding.py","evaluate-projection-quantization.py"))
    require(contract[0]==expected_evaluator_sources and contract[1]==digest(expected_evaluator_sources),"evaluator source snapshot differs")
    source_names=["evaluate-mih-adc-ceiling.py","evaluate-mih-banding.py","evaluate-projection-quantization.py","bootstrap-mih-adc-ceiling.py","run-mih-adc-ceiling-matrix.py",Path(__file__).name]
    sources=[(Path(__file__).with_name(name),f"bundle/sources/{name}") for name in source_names]
    files += sources
    compact={"schema_version":1,"family":"mih_adc_ceiling_stage_loss_evidence_v1","matrix_sha256":sha256(args.matrix),"evaluation_identity":identity,"evaluator_source_files_sha256":contract[0],"evaluator_source_bundle_sha256":contract[1],"rows":compact_rows,"comparisons":comparisons}
    compact_path=args.input_root/"compact-manifest.json"; compact_path.write_text(json.dumps(compact,indent=2,sort_keys=True)+"\n",encoding="utf-8",newline="\n"); files.insert(0,(compact_path,"bundle/compact-manifest.json"))
    names=[name for _,name in files]; require(len(names)==len(set(names)) and all("\\" not in name for name in names),"archive names are invalid")
    entries=[{"path":name,"sha256":sha256(path),"size":path.stat().st_size} for path,name in files]
    bundle_manifest={"schema_version":1,"family":"mih_adc_ceiling_stage_loss_evidence_v1_bundle","bundle_root_sha256":digest(entries),"entries":entries}
    manifest_path=args.input_root/"evidence-bundle-manifest.json"; manifest_path.write_text(json.dumps(bundle_manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8",newline="\n"); files.append((manifest_path,"bundle/evidence-bundle-manifest.json")); names.append("bundle/evidence-bundle-manifest.json")
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.output,"w",compression=zipfile.ZIP_DEFLATED) as archive:
        for path,name in files: archive.write(path,name)
    with zipfile.ZipFile(args.output) as archive:
        require(archive.namelist()==names,"archive names differ")
        for entry in entries: require(hashlib.sha256(archive.read(entry["path"])).hexdigest()==entry["sha256"],f"archive member differs: {entry['path']}")
    return {"archive":str(args.output),"sha256":sha256(args.output),"bundle_root_sha256":bundle_manifest["bundle_root_sha256"],"rows":len(compact_rows),"comparisons":len(comparisons)}

def self_test() -> int:
    try:
        require(expected_rows(Path(__file__).with_name("mih-adc-ceiling.example.json")),"matrix rows are absent")
        with tempfile.TemporaryDirectory() as directory:
            archive_path=Path(directory)/"portable.zip"
            with zipfile.ZipFile(archive_path,"w",compression=zipfile.ZIP_DEFLATED) as archive: archive.writestr("bundle/compact-manifest.json","{}")
            with zipfile.ZipFile(archive_path) as archive: require(archive.namelist()==["bundle/compact-manifest.json"] and "\\" not in archive.namelist()[0],"portable archive self-test failed")
    except (OSError,ValueError,json.JSONDecodeError) as error:
        print(f"write-mih-adc-ceiling-evidence self-test failed: {error}",file=sys.stderr); return 1
    print("MIH ADC-ceiling evidence packager self-test passed"); return 0

def main(argv:list[str])->int:
    parser=argparse.ArgumentParser(); parser.add_argument("--input-root",type=Path); parser.add_argument("--matrix",type=Path); parser.add_argument("--bootstrap-root",type=Path); parser.add_argument("--output",type=Path); parser.add_argument("--self-test",action="store_true"); args=parser.parse_args(argv)
    try:
        if args.self_test: return self_test()
        require(all(value is not None for value in (args.input_root,args.matrix,args.bootstrap_root,args.output)),"paths are required"); print(json.dumps(write(args),sort_keys=True))
    except (OSError,ValueError,json.JSONDecodeError) as error: print(f"write-mih-adc-ceiling-evidence: {error}",file=sys.stderr); return 1
    return 0
if __name__=="__main__": raise SystemExit(main(sys.argv[1:]))
