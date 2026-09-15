#!/usr/bin/env python3
"""Fail-closed audit for seed-count/A Pareto diagnostics."""
from __future__ import annotations
import argparse, hashlib, json, math
from pathlib import Path
import numpy as np

SEEDS={2026082701,2026082702,2026082703}; A={8192,16384}; Q=152
def sha256(p:Path)->str:
 h=hashlib.sha256();
 with p.open('rb') as f:
  for x in iter(lambda:f.read(1<<20),b''): h.update(x)
 return h.hexdigest()
def agg(v):
 a=np.asarray(v,float); return {'min':float(a.min()),'mean':float(a.mean()),'p05':float(np.percentile(a,5)),'p50':float(np.percentile(a,50)),'p95':float(np.percentile(a,95)),'max':float(a.max())}
def main():
 p=argparse.ArgumentParser(); p.add_argument('--receipt',type=Path,required=True); p.add_argument('--raw',type=Path,required=True); p.add_argument('--runner',type=Path,required=True); a=p.parse_args()
 r=json.loads(a.receipt.read_text()); raw=json.loads(a.raw.read_text()); assert r['family']==raw['family']=='semantic_r4_k1_seed_pareto_v1'; assert r['raw_output']['sha256']==sha256(a.raw)==hashlib.sha256(a.raw.read_bytes()).hexdigest(); assert r['runner_sha256']==sha256(a.runner)
 rows=raw['rows']; assert len(rows)==7*2*Q; ids=set()
 for x in rows:
  key=(tuple(x['seeds']),int(x['addresses_refined_per_seed']),int(x['query'])); assert key not in ids; ids.add(key); assert set(x['seeds'])<=SEEDS and 1<=len(x['seeds'])<=3 and x['addresses_refined_per_seed'] in A and 0<=x['query']<Q; assert x['candidate_count']>=5000; assert 0<=x['teacher_recall']<=1; assert len(x['teacher_provenance'])==10
  for t in x['teacher_provenance']: assert t['hit']==(t['candidate_entry_rank'] is not None); assert len(t['seeds'])==len(x['seeds'])
 assert len(ids)==7*2*Q
 for s in r['summaries']:
  x=[z for z in rows if tuple(z['seeds'])==tuple(s['seeds']) and z['addresses_refined_per_seed']==s['addresses_refined_per_seed']]; assert s['query_count']==len(x)
  for k in ('teacher_recall','candidate_count','postings_touched','posting_entries_touched'):
   for name,v in agg([z[k] for z in x]).items(): assert math.isclose(s[k][name],v,abs_tol=1e-9)
 print(json.dumps({'family':'semantic_r4_k1_seed_pareto_audit_v1','status':'PASS','rows':len(rows)},sort_keys=True))
if __name__=='__main__': main()
