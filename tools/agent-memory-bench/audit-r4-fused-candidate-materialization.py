#!/usr/bin/env python3
"""Fail-closed audit for actual fused candidate-stream materialization."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

PAGE=4096; RECORD=148; QUERIES=152; BUDGET=5000
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for x in iter(lambda:f.read(1<<20),b''): h.update(x)
 return h.hexdigest()
def main():
 p=argparse.ArgumentParser(); p.add_argument('--receipt',type=Path,required=True); p.add_argument('--raw',type=Path,required=True); p.add_argument('--runner',type=Path,required=True); a=p.parse_args()
 r=json.loads(a.receipt.read_text()); raw=json.loads(a.raw.read_text()); assert r['family']==raw['family']=='semantic_r4_fused_candidate_materialization_v1'; assert r['raw_sha256']==sha(a.raw); assert r['runner_sha256']==sha(a.runner)
 rows=raw['rows']; assert len(rows)==QUERIES; assert all(x['candidate_count']==BUDGET and x['mean_abs_doc_delta']>=0 for x in rows)
 flat=Path(r['flat_file']['path']); blocked=Path(r['blocked_file']['path']); assert flat.is_file() and blocked.is_file(); assert sha(flat)==r['flat_file']['sha256'] and sha(blocked)==r['blocked_file']['sha256']; logical=QUERIES*BUDGET*RECORD; assert r['flat_file']['bytes']==logical; assert r['blocked_file']['bytes']==QUERIES*((BUDGET+26)//27)*PAGE; assert r['blocked_file']['bytes']>=r['flat_file']['bytes']
 print(json.dumps({'family':'semantic_r4_fused_candidate_materialization_audit_v1','status':'PASS','rows':len(rows),'logical_bytes':logical},sort_keys=True))
if __name__=='__main__': main()
