#!/usr/bin/env python3
"""IMI and SPANN-like coarse-posting controls on frozen THQ4 codes.

These are in-memory, deterministic surrogates.  They measure candidate recall
and posting work only; they do not claim an R4 mapping or an MDBX backend.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np

def sha256(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for c in iter(lambda:f.read(1<<20),b''): h.update(c)
 return h.hexdigest()

def decode(path,n,width=16):
 raw=np.memmap(path,mode='r',dtype=np.uint8,shape=(n,144))[:,:6]
 bits=np.unpackbits(np.asarray(raw),axis=1,bitorder='little')[:,:width*3]
 return bits.reshape(n,width,3).sum(axis=2).astype(np.uint8)

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--thq-manifest',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--query-limit',type=int,default=8); ap.add_argument('--budgets',default='1,2,4,8,16,32'); args=ap.parse_args()
 m=json.loads(args.thq_manifest.read_text()); n=int(m['documents']); qn=min(args.query_limit,int(m['queries'])); codes=decode(m['outputs']['thq4_document_codes']['path'],n); qcodes=decode(m['outputs']['thq4_query_codes']['path'],int(m['queries']))[:qn]; teachers=np.memmap(m['references']['teacher_ids']['path'],mode='r',dtype='<i8',shape=(qn,10)); budgets=[int(x) for x in args.budgets.split(',')]
 # SPANN-like 64 posting prototypes: exact 3-level signatures (4^3 cells).
 spann_sig=codes[:,:3]; spann_cell=spann_sig[:,0]*16+spann_sig[:,1]*4+spann_sig[:,2]; postings=[np.flatnonzero(spann_cell==c) for c in range(64)]; proto=np.asarray(np.unravel_index(np.arange(64),(4,4,4))).T
 # IMI: two independent 3-coordinate subspaces (64 x 64 Cartesian cells).
 a=codes[:,:3]; b=codes[:,3:6]; ca=a[:,0]*16+a[:,1]*4+a[:,2]; cb=b[:,0]*16+b[:,1]*4+b[:,2]; imi_cell=ca*64+cb; imi_post=[np.flatnonzero(imi_cell==c) for c in range(4096)]
 rows=[]
 for qi in range(qn):
  q0=qcodes[qi,:3]; q1=qcodes[qi,3:6]; spann_order=np.argsort(np.abs(proto-q0).sum(axis=1),kind='stable'); qca=q1[0]*16+q1[1]*4+q1[2]; qcb=q1[0]*16+q1[1]*4+q1[2];
  imi_proto=np.asarray(np.unravel_index(np.arange(4096),(64,64))).T; imi_left=np.asarray(np.unravel_index(np.arange(64),(4,4,4))).T; imi_cost=np.abs(imi_left[imi_proto[:,0]]-q0).sum(axis=1)+np.abs(imi_left[imi_proto[:,1]]-q1).sum(axis=1); imi_order=np.argsort(imi_cost,kind='stable')
  sr=[]; ir=[]
  for L in budgets:
   sc=np.concatenate([postings[c] for c in spann_order[:L] if postings[c].size]) if any(postings[c].size for c in spann_order[:L]) else np.empty(0,dtype=np.int64); ic=np.concatenate([imi_post[c] for c in imi_order[:L] if imi_post[c].size]) if any(imi_post[c].size for c in imi_order[:L]) else np.empty(0,dtype=np.int64); sr.append({'L':L,'postings_touched':L,'candidate_docs':int(sc.size),'teacher_recall':float(np.isin(teachers[qi],sc).sum()/10)}); ir.append({'L':L,'cells_touched':L,'candidate_docs':int(ic.size),'teacher_recall':float(np.isin(teachers[qi],ic).sum()/10)})
  rows.append({'query':qi,'spann_like':sr,'imi':ir})
 out={'schema_version':1,'family':'thq_index_wave2_imi_spann_oracle_v1','fixture_manifest_sha256':sha256(args.thq_manifest),'runner_sha256':sha256(Path(__file__)),'queries':qn,'rows':rows,'protocol':{'spann_prototypes':'64 exact 3-level signature postings','imi_cells':'64x64 Cartesian 3-level signatures','budgets':budgets,'payload_rerank':'not executed; candidate recall only'},'execution_status':'EXECUTED_SMOKE' if qn<152 else 'EXECUTED','production_activation':False}
 args.output.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
if __name__=='__main__': main()
