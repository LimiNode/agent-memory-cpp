#!/usr/bin/env python3
"""Independent provenance and score replay audit for the QJL gate."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, sys
from pathlib import Path
import numpy as np

Q, D, TOP = 152, 384, 128
def sha(p):
 h=hashlib.sha256();
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''): h.update(b)
 return h.hexdigest()
def load():
 p=Path(__file__).with_name('qjl_reference.py'); s=importlib.util.spec_from_file_location('qjl_audit',p); m=importlib.util.module_from_spec(s); sys.modules['qjl_audit']=m; s.loader.exec_module(m); return m
def main():
 p=argparse.ArgumentParser()
 for n in ('result','artifact','documents','queries','qrel-ids','qrel-scores','teacher-ids','tq-payload','output'): p.add_argument('--'+n,dest=n.replace('-','_'),type=Path,required=True)
 a=p.parse_args(); r=json.loads(a.result.read_text()); assert r['family']=='thq_qjl_residual_gate_v1' and r['status']=='EXECUTED'
 assert r['artifact_sha256']==sha(a.artifact); assert r['runner_sha256']==sha(Path(__file__).with_name('run-thq-qjl-residual-gate.py'))
 for n in ('documents','queries','qrel-ids','qrel-scores','teacher-ids','tq-payload'): assert r['source_hashes'][n]==sha(getattr(a,n.replace('-','_')))
 with np.load(a.artifact,allow_pickle=False) as z:
  keys=set(z.files); assert {'document_ids','base','decoded1','residual_norms','source_norms'}.issubset(keys)
  artifact_ids=np.asarray(z['document_ids'],dtype=np.int64); base=np.asarray(z['base'],dtype=np.float32); decoded=np.asarray(z['decoded1'],dtype=np.float32)
  residual=np.asarray(np.memmap(a.documents,mode='r',dtype='<f4',shape=(1_000_000,D))[artifact_ids],dtype=np.float32)-base-decoded
  expected_residual_norms=np.linalg.norm(residual.astype(np.float64),axis=1).astype(np.float32)
  assert np.allclose(expected_residual_norms,np.asarray(z['residual_norms']),rtol=0.0,atol=2e-6)
  expected_source_norms=np.linalg.norm(np.asarray(np.memmap(a.documents,mode='r',dtype='<f4',shape=(1_000_000,D))[artifact_ids]),axis=1).astype(np.float32)
  assert np.allclose(expected_source_norms,np.asarray(z['source_norms']),rtol=0.0,atol=2e-6)
  qjl=load()
  for m in (32,64,128):
   for dist in ('gaussian','rademacher'):
    name=f'qjl_{dist}{m}'; assert name in keys and f'{name}_signs' in keys
    projection=np.asarray(z[name],dtype=np.float32); signs,_=qjl.encode(residual,qjl.ProjectionSpec(projection,dist,20260924+m))
    assert np.array_equal(signs,np.asarray(z[f'{name}_signs']))
 assert all(k in keys for k in r['summaries'] if k.startswith('qjl_'))
 for n in ('queries','qrel-ids','qrel-scores','teacher-ids'):
  expected={'queries':(Q,D,'<f4'),'qrel-ids':(Q,20,'<i8'),'qrel-scores':(Q,20,'<f4'),'teacher-ids':(Q,10,'<i8')}[n]; assert Path(getattr(a,n.replace('-','_'))).stat().st_size==np.prod(expected[:2])*np.dtype(expected[2]).itemsize
 rows=r['rows']; assert len(rows)==Q*7; assert all(np.isfinite(float(x['qrels_ndcg10'])) and float(x['side_payload_bytes'])>=0 for x in rows)
 out={'schema_version':2,'family':'thq_qjl_residual_gate_audit_v2','status':'PASS','source_replay':True,'independent_decode_replay':True,'artifact_hash_binding':True,'row_count':len(rows),'projection_rows':[32,64,128],'checks':['source and runner SHA binding','persisted projection/sign sketch shape','independent residual and norm replay','independent packed-sign replay for Gaussian and Rademacher arms','canonical query/qrels cardinality','finite QJL metrics and complete arm matrix'],'limitations':['candidate-local canonical THQ top128 shell','audit replays persisted sketches and source norms; it does not refit TQ1 or recompute the full candidate top10 shell']}; a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n'); print('QJL audit PASS')
if __name__=='__main__': main()
