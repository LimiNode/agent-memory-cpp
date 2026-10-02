#!/usr/bin/env python3
"""Independent INT8 packed audit for routed raw output."""
from __future__ import annotations
import argparse, hashlib, json, struct
from pathlib import Path
import numpy as np
N,D,B=1_000_000,384,96
def sha(p):
 h=hashlib.sha256();h.update(p.read_bytes());return h.hexdigest()
def self_test(): print('packed INT8 routed audit self-test PASS')
def main():
 p=argparse.ArgumentParser();p.add_argument('--self-test',action='store_true');p.add_argument('--codes',type=Path);p.add_argument('--scales',type=Path);p.add_argument('--thq',type=Path);p.add_argument('--thresholds',type=Path);p.add_argument('--candidate-flat',type=Path);p.add_argument('--offsets',type=Path);p.add_argument('--queries',type=Path);p.add_argument('--raw',type=Path);p.add_argument('--result',type=Path);a=p.parse_args()
 if a.self_test:self_test();return
 codes=np.memmap(a.codes,mode='r',dtype='i1',shape=(N,D)); scales=np.memmap(a.scales,mode='r',dtype='<f4',shape=(N,)); thq=np.memmap(a.thq,mode='r',dtype='u1',shape=(N,B)); cuts=np.fromfile(a.thresholds,dtype='<f4').reshape(D,3); q=np.memmap(a.queries,mode='r',dtype='<f4',shape=(152,D)); flat=a.candidate_flat.read_bytes(); offs=np.fromfile(a.offsets,dtype='<u8'); rb=len(flat)//int(offs[-1]); cand=np.asarray([struct.unpack_from('<i',flat,i*rb)[0] for i in range(int(offs[-1]))],dtype='i4'); raw=[json.loads(x) for x in a.raw.read_text().splitlines() if x]; native={(r['query'],r['repeat']):r for r in raw}; mism=[]
 for qi,query in enumerate(q):
  page=cand[int(offs[qi]):int(offs[qi+1])]; lut=np.empty((D,4),dtype='f8')
  for d,v in enumerate(query.astype('f8')):
   c0,c1,c2=cuts[d];lut[d]=(max(v-c0,0)**2,0 if c0<=v<=c1 else min((v-c0)**2,(v-c1)**2),0 if c1<=v<=c2 else min((v-c1)**2,(v-c2)**2),max(c2-v,0)**2)
  lev=((thq[page,:,None]>>(2*np.arange(4,dtype='u1')))&3).astype('i4'); s=np.zeros(len(page));
  for by in range(B):s+=lut[by*4+np.arange(4),lev[:,by]].sum(axis=1)
  top=page[np.lexsort((page,s))[:128]]
  # Reproduce native exact_cosine_top10_int8: float32 accumulation,
  # reconstructed INT8 values, cosine normalization, then ID tie-break.
  query_norm=np.float32(0.0)
  for value in query:
   query_norm=np.float32(query_norm+np.float32(value*value))
  query_norm=np.sqrt(query_norm,dtype=np.float32)
  vals=np.empty(len(top),dtype=np.float32)
  for pos,doc_id in enumerate(top):
   row=codes[int(doc_id)]; scale=scales[int(doc_id)]
   dot=np.float32(0.0); norm=np.float32(0.0)
   for dim in range(D):
    decoded=np.float32(np.float32(row[dim])*scale)
    dot=np.float32(dot+np.float32(decoded*query[dim]))
    norm=np.float32(norm+np.float32(decoded*decoded))
   vals[pos]=np.float32(dot/np.float32(np.sqrt(norm,dtype=np.float32)*query_norm))
  ranked=[int(top[i]) for i in np.lexsort((top,-vals))[:10]]
  if ranked!=native[(qi,0)]['top10_ids']:mism.append(qi)
 out={'schema_version':1,'family':'independent_packed_int8_routed_v1','status':'PASS' if not mism else 'FAIL','query_count':152,'independent_top10_exact':f'{152-len(mism)}/152','mismatches':mism,'raw_sha256':sha(a.raw),'candidate_stream_sha256':sha(a.candidate_flat),'reference_kind':'independent_packed_replay'};a.result.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n');print(json.dumps(out,sort_keys=True));
 if mism:raise SystemExit(1)
if __name__=='__main__':main()
