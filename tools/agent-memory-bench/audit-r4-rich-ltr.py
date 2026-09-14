#!/usr/bin/env python3
"""Fail-closed audit for the apples-to-apples rich R4 LTR receipt."""
import argparse, hashlib, json
from pathlib import Path
import numpy as np
def digest(p):
 h=hashlib.sha256();
 with Path(p).open('rb') as f:
  for c in iter(lambda:f.read(1<<20),b''): h.update(c)
 return h.hexdigest()
def agg(v):
 a=np.asarray(v,dtype=float); return {'min':float(a.min()),'mean':float(a.mean()),'p05':float(np.percentile(a,5)),'p50':float(np.percentile(a,50)),'p95':float(np.percentile(a,95)),'max':float(a.max())}
def main():
 p=argparse.ArgumentParser(); p.add_argument('--result',type=Path,required=True); p.add_argument('--runner',type=Path,required=True); p.add_argument('--raw',type=Path,required=True); a=p.parse_args(); d=json.loads(a.result.read_text()); rows=json.loads(a.raw.read_text())['rows']; ev=d['eval_query_ids']; methods={r['method'] for r in rows}; expected_methods={'sampled_pointwise_logistic','model_prefix_plus_coarse_tail','prefix_oracle','arbitrary_oracle'}
 if d.get('family')!='r4_sampled_pointwise_ltr_apples_to_apples_v2' or d.get('execution_status')!='EXECUTED' or d.get('production_activation') is not False: raise SystemExit('family/execution flags invalid')
 if d.get('runner_sha256')!=digest(a.runner) or d.get('raw_output',{}).get('sha256')!=digest(a.raw) or d.get('raw_output',{}).get('bytes')!=a.raw.stat().st_size: raise SystemExit('provenance mismatch')
 expected_keys={(int(qi),method,int(budget)) for qi in ev for method in expected_methods for budget in (20000,50000,100000)}
 actual_keys={(int(r['query']),r['method'],int(r['budget'])) for r in rows}
 if actual_keys != expected_keys: raise SystemExit('method matrix incomplete, duplicated, or mislabeled')
 if set(d.get('train_query_ids',[])).intersection(ev): raise SystemExit('train/eval overlap')
 if set(int(r['query']) for r in rows)-set(ev): raise SystemExit('raw query outside eval split')
 ext=d.get('external_fusion_raw',{}); ep=Path(ext.get('path',''));
 if not ep.exists() or ext.get('sha256')!=digest(ep): raise SystemExit('external fusion raw provenance mismatch')
 for s in d['summary']:
  selected=[r for r in rows if r['method']==s['method'] and int(r['budget'])==int(s['budget'])]
  if len(selected)!=len(ev): raise SystemExit(f"row count mismatch {s['method']}/{s['budget']}")
  for metric in ('teacher_recall','unique_candidates','posting_entries','postings_touched'):
   if any(abs(float(s[metric][k])-agg([r[metric] for r in selected])[k])>1e-12 for k in s[metric]): raise SystemExit(f'aggregate mismatch {s["method"]}/{s["budget"]}/{metric}')
 expected_summary={(method,budget) for method in expected_methods for budget in (20000,50000,100000)}
 actual_summary={(s['method'],int(s['budget'])) for s in d['summary']}
 if actual_summary != expected_summary or len(d['summary']) != len(expected_summary): raise SystemExit('summary matrix incomplete or duplicated')
 print(json.dumps({'status':'PASS','methods':sorted(methods),'rows':len(rows)}))
if __name__=='__main__': main()
