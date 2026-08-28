#!/usr/bin/env python3
"""Replay conditional representation quality evidence."""
import argparse, importlib.util, json, sys, tempfile
from pathlib import Path
THIS=Path(__file__).resolve().parent; sys.dont_write_bytecode=True
def load():
 s=importlib.util.spec_from_file_location("conditional_evidence_runner",THIS/"run-neuroute-conditional-followups.py"); m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); return m
r=load()
def require(v,m):
 if not v: raise ValueError(m)
def self_test(): require(r.planner.load_contract(THIS/"neuroute-conditional-followups.example.json")["overcomplete_screen"]["widths"][-1]==1024,"conditional evidence differs"); print("NeuRoute conditional follow-ups evidence self-test passed")
def run(a):
 result=json.loads(a.result.read_text()); require(result.get("family")=="neuroute_conditional_representation_quality_result" and result.get("contract_sha256")==r.sha256(a.contract) and result.get("source_files_sha256")==r.source_hashes(),"conditional evidence binding differs")
 with tempfile.TemporaryDirectory(prefix="neuroute-conditional-replay-") as d:
  values=vars(a).copy(); values["output"]=Path(d)/"result.json"; replay=argparse.Namespace(**values); r.run(replay); require(replay.output.read_bytes()==a.result.read_bytes(),"conditional quality replay differs")
 receipt={"schema_version":1,"family":"neuroute_conditional_representation_evidence","passed":True,"contract_sha256":r.sha256(a.contract),"result_sha256":r.sha256(a.result),"source_files_sha256":r.source_hashes(),"writer_sha256":r.sha256(Path(__file__)),"quality_replay_byte_identical":True,"decision":result["decision"]}; a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_bytes(r.canonical(receipt))
def main():
 p=argparse.ArgumentParser(); p.add_argument("--contract",type=Path,default=THIS/"neuroute-conditional-followups.example.json"); p.add_argument("--result",type=Path); p.add_argument("--final-result",type=Path); p.add_argument("--final-materialization-root",type=Path); p.add_argument("--final-evidence",type=Path); p.add_argument("--v4-contract",type=Path); p.add_argument("--scale-contract",type=Path); p.add_argument("--german-split-result",type=Path)
 for l in ("de","fr","ja"):
  for n in ("result","e5","input"): p.add_argument(f"--{l}-{n}-root",type=Path)
 p.add_argument("--de-1m-e5-root",type=Path); p.add_argument("--de-1m-input-root",type=Path); p.add_argument("--output",type=Path); p.add_argument("--self-test",action="store_true"); a=p.parse_args()
 try:
  if a.self_test:self_test();return 0
  if any(v is None for n,v in vars(a).items() if n not in ("self_test","contract")):p.error("all paths required")
  run(a);return 0
 except (OSError,ValueError,TypeError,KeyError,json.JSONDecodeError) as e: print(f"write-neuroute-conditional-followups-evidence: {e}",file=sys.stderr);return 1
if __name__=="__main__":raise SystemExit(main())
