#!/usr/bin/env python3
"""Fail-closed audit for deep ranker receipts, raw rows, and split provenance."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np

def digest(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for c in iter(lambda:f.read(1<<20),b''): h.update(c)
 return h.hexdigest()
def aggregate(values):
 a=np.asarray(values,dtype=np.float64)
 return {'min':float(a.min()),'mean':float(a.mean()),'p05':float(np.percentile(a,5)),'p50':float(np.percentile(a,50)),'p95':float(np.percentile(a,95)),'max':float(a.max())}
def check_aggregate(expected, values, label):
 actual=aggregate(values)
 for key,val in actual.items():
  if abs(float(expected[key])-val)>1e-12: raise ValueError(f'aggregate mismatch {label}.{key}')
def main():
 p=argparse.ArgumentParser(); p.add_argument('--result',type=Path,required=True); p.add_argument('--runner',type=Path,required=True); p.add_argument('--raw',type=Path,required=True); a=p.parse_args()
 d=json.loads(a.result.read_text()); raw=json.loads(a.raw.read_text()); rows=raw.get('rows',[])
 checks={'execution_status':d.get('execution_status')=='EXECUTED','production_activation':d.get('production_activation') is False,'runner_sha256':d.get('runner_sha256')==digest(a.runner),'raw_sha256':d.get('raw_output',{}).get('sha256')==digest(a.raw),'raw_bytes':d.get('raw_output',{}).get('bytes')==a.raw.stat().st_size,'held_out':d.get('held_out_queries',0)>0,'row_count':len(rows)==int(d.get('held_out_queries',0))*len(d.get('held_out_summary',[])),'split_ids':sorted({int(r['query']) for r in rows})==sorted(int(x) for x in d.get('held_out_query_ids',[])),'split_disjoint':not set(int(x) for x in d.get('train_query_ids',[])).intersection(int(x) for x in d.get('held_out_query_ids',[]))}
 if not all(checks.values()): raise SystemExit(json.dumps({'status':'FAIL','checks':checks},indent=2))
 for summary in d.get('held_out_summary',[]):
  b=int(summary['budget']); selected=[r for r in rows if int(r['budget'])==b]
  if len(selected)!=int(d['held_out_queries']): raise SystemExit(f'budget row count mismatch {b}')
  for metric in ('teacher_recall','actual_unique_candidates','posting_entries_touched','postings_touched'):
   check_aggregate(summary[metric],[float(r[metric]) for r in selected],f'{b}.{metric}')
 print(json.dumps({'status':'PASS','checks':checks},indent=2))
if __name__=='__main__': main()
