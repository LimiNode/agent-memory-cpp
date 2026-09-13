#!/usr/bin/env python3
"""Fail-closed audit for selective-spill control receipts."""
import argparse,hashlib,json
from pathlib import Path
import numpy as np
def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def agg(v):
 a=np.asarray(v,float); return {'min':float(a.min()),'mean':float(a.mean()),'p05':float(np.percentile(a,5)),'p50':float(np.percentile(a,50)),'p95':float(np.percentile(a,95)),'max':float(a.max())}
def main():
 p=argparse.ArgumentParser(); p.add_argument('--result',type=Path,required=True); p.add_argument('--runner',type=Path,required=True); p.add_argument('--raw',type=Path,required=True); a=p.parse_args(); d=json.loads(a.result.read_text()); rows=json.loads(a.raw.read_text())['rows']; q=int(d['query_count'])
 if d['execution_status']!='EXECUTED' or d['production_activation'] is not False: raise SystemExit('execution flags invalid')
 if d['runner_sha256']!=digest(a.runner) or d['raw_output']['sha256']!=digest(a.raw) or d['raw_output']['bytes']!=a.raw.stat().st_size: raise SystemExit('provenance mismatch')
 for s in d['summary']:
  selected=[r for r in rows if r['policy']==s['policy'] and float(r['replication_factor'])==float(s['replication_factor']) and int(r['budget'])==int(s['budget'])]
  if len(selected)!=q: raise SystemExit('row count mismatch')
  for m in ('teacher_recall','unique_candidates','posting_entries','postings_touched'):
   if agg([r[m] for r in selected])!=s[m]: raise SystemExit(f'aggregate mismatch {m}')
 print(json.dumps({'status':'PASS','rows':len(rows)}))
if __name__=='__main__': main()
