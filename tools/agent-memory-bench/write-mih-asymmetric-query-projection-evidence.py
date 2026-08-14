#!/usr/bin/env python3
"""Validate and package the frozen-document asymmetric MIH experiment."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, sys, tempfile
from pathlib import Path
from typing import Any
THIS=Path(__file__).resolve()
def load(name:str,key:str)->Any:
 spec=importlib.util.spec_from_file_location(key,THIS.with_name(name)); assert spec and spec.loader
 module=importlib.util.module_from_spec(spec);sys.modules[key]=module;spec.loader.exec_module(module);return module
archive=load('write-mih-rerank-cost-evidence.py','asymmetric_evidence_archive')
def sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()
def need(value:bool,message:str)->None:
 if not value:raise ValueError(message)
def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--shared-root',type=Path,required=True);p.add_argument('--contract',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--source-commit',required=True);a=p.parse_args()
 try:
  contract=json.loads(a.contract.read_text());need(contract['family']=='mih_asymmetric_query_projection_confirmatory_v1','contract family differs');files=[(a.contract,'bundle/contract.json')]
  for seed in contract['seeds']:
   artifact=a.root/f'seed{seed}'/'artifact.json';report=a.root/f'asymmetric-seed{seed}.json';contrib=a.root/f'asymmetric-seed{seed}.npz';need(artifact.is_file() and report.is_file() and contrib.is_file(),'asymmetric row is incomplete')
   value=json.loads(artifact.read_text());training=value.get('training',{});need(value['architecture']['family']=='mih_query_aware_asymmetric_projection_v1' and value.get('input_materialization_manifest_sha256')==contract['training_materialization_manifest_sha256'] and training.get('seed')==seed and training.get('epochs')==contract['training']['epochs'] and training.get('batch_size')==contract['training']['batch_size'] and training.get('learning_rate')==contract['training']['learning_rate'] and training.get('anchor_weight')==contract['training']['anchor_weight'] and training.get('hard_negative_mining',{}).get('id')=='train_mih_candidate_union_false_positive_v1','artifact contract differs')
   need((a.root/f'seed{seed}'/'projection-weights.f32').read_bytes()==(a.shared_root/'artifacts'/f'query-aware-hamming-target-seed{seed}'/'initial-itq-projection-weights.f32').read_bytes() and (a.root/f'seed{seed}'/'thresholds.f32').read_bytes()==(a.shared_root/'artifacts'/f'query-aware-hamming-target-seed{seed}'/'initial-itq-thresholds.f32').read_bytes(),'frozen W0 differs from #137 anchor')
   report_value=json.loads(report.read_text());need(report_value.get('calibration_materialization_manifest_sha256')==contract['training_materialization_manifest_sha256'] and report_value.get('evaluation_materialization_manifest_sha256')==contract['held_out_evaluation_manifest_sha256'] and report_value.get('seed')==seed and report_value.get('band_count')==16 and report_value.get('band_width_bits')==[16]*16 and report_value.get('global_radius')==56 and report_value.get('candidate_limit')==512 and report_value.get('hamming_limit')==768 and report_value.get('second_stage')=='binary-adc' and report_value.get('second_limit')==256 and report_value.get('oracle_k')==10 and report_value.get('encoder_artifact_sha256')==sha(artifact),'report contract differs')
   for path,name in ((artifact,f'bundle/artifacts/seed{seed}/artifact.json'),(a.root/f'seed{seed}'/'projection-weights.f32',f'bundle/artifacts/seed{seed}/projection-weights.f32'),(a.root/f'seed{seed}'/'query-projection-weights.f32',f'bundle/artifacts/seed{seed}/query-projection-weights.f32'),(a.root/f'seed{seed}'/'thresholds.f32',f'bundle/artifacts/seed{seed}/thresholds.f32'),(report,f'bundle/reports/asymmetric-seed{seed}.json'),(contrib,f'bundle/contributions/asymmetric-seed{seed}.npz'),(a.root/'bootstrap'/f'itq-vs-asymmetric-seed{seed}.json',f'bundle/bootstrap/itq-vs-asymmetric-seed{seed}.json'),(a.root/'bootstrap'/f'shared-vs-asymmetric-seed{seed}.json',f'bundle/bootstrap/shared-vs-asymmetric-seed{seed}.json'),(a.shared_root/'contributions'/f'itq-control--16x16-r56-seed{seed}.npz',f'bundle/baselines/itq-seed{seed}.npz'),(a.shared_root/'contributions'/f'query-aware-hamming-target--16x16-r56-seed{seed}.npz',f'bundle/baselines/shared-seed{seed}.npz')):
    need(path.is_file(),'evidence member is missing');files.append((path,name))
  for name in (THIS.name,'train-mih-asymmetric-query-projection.py','bootstrap-mih-asymmetric-query-projection.py','evaluate-mih-banding.py','evaluate-projection-quantization.py','train-nlb-qrels-supervised.py','mih-asymmetric-query-projection.example.json','write-mih-rerank-cost-evidence.py'):files.append((THIS.with_name(name),f'bundle/sources/{name}'))
  with tempfile.TemporaryDirectory() as temp:
   compact=Path(temp)/'manifest.json';compact.write_text(json.dumps({'schema_version':1,'family':'mih_asymmetric_query_projection_evidence_v1','source_commit':a.source_commit,'contract_sha256':sha(a.contract),'baseline_policy':'#137_matched_anchor_v2_contributions'},indent=2,sort_keys=True)+'\n');files.append((compact,'bundle/compact-manifest.json'));manifest=archive.archive_manifest(files);manifest['family']='mih_asymmetric_query_projection_evidence_v1';a.output.parent.mkdir(parents=True,exist_ok=True);archive.write_archive(a.output,files,manifest)
  print(json.dumps({'sha256':sha(a.output),'bundle_root_sha256':manifest['bundle_root_sha256']},sort_keys=True))
 except (OSError,ValueError,json.JSONDecodeError) as e:print(f'write-mih-asymmetric-query-projection-evidence: {e}',file=sys.stderr);return 1
 return 0
if __name__=='__main__':raise SystemExit(main())
