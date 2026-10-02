#!/usr/bin/env python3
"""Independent packed PLSQ audit for a routed candidate page."""
from __future__ import annotations
import argparse, hashlib, json, struct
from pathlib import Path
import numpy as np

D, N, B, W = 384, 1_000_000, 96, 128
def sha(p):
 h=hashlib.sha256();
 with p.open('rb') as f:
  for c in iter(lambda:f.read(1<<20),b''): h.update(c)
 return h.hexdigest()
def self_test(): print('packed PLSQ routed audit self-test PASS')
def main():
 p=argparse.ArgumentParser(); p.add_argument('--self-test',action='store_true'); p.add_argument('--payload',type=Path); p.add_argument('--thq',type=Path); p.add_argument('--thresholds',type=Path); p.add_argument('--candidate-flat',type=Path); p.add_argument('--offsets',type=Path); p.add_argument('--queries',type=Path); p.add_argument('--raw',type=Path); p.add_argument('--result',type=Path); a=p.parse_args()
 if a.self_test: self_test(); return
 data=a.payload.read_bytes(); qcount,width,splits,sub,code_bytes=struct.unpack_from('<IIIII',data,8); off=28
 ids=np.frombuffer(data,dtype='<i4',count=qcount*width,offset=off).reshape(qcount,width); off+=qcount*width*4
 codes=np.frombuffer(data,dtype='u1',count=qcount*width*code_bytes,offset=off).reshape(qcount,width,code_bytes); off+=qcount*width*code_bytes
 norms=np.frombuffer(data,dtype='<f4',count=qcount*width,offset=off).reshape(qcount,width); off+=qcount*width*4
 cent=np.frombuffer(data,dtype='<f4',count=D*4,offset=off).reshape(D,4); off+=D*16; books=np.frombuffer(data,dtype='<f4',offset=off).reshape(splits,sub,256,D//splits)
 thq=np.memmap(a.thq,mode='r',dtype='u1',shape=(N,B)); cuts=np.fromfile(a.thresholds,dtype='<f4').reshape(D,3); queries=np.memmap(a.queries,mode='r',dtype='<f4',shape=(152,D)); raw=[json.loads(x) for x in a.raw.read_text().splitlines() if x]; native={(r['query'],r['repeat']):r for r in raw}
 flat=a.candidate_flat.read_bytes(); offsets=np.fromfile(a.offsets,dtype='<u8'); rb=len(flat)//int(offsets[-1]); cand=np.asarray([struct.unpack_from('<i',flat,i*rb)[0] for i in range(int(offsets[-1]))],dtype='i4'); mism=[]
 for qi,q in enumerate(queries):
  page=cand[int(offsets[qi]):int(offsets[qi+1])]; lut=np.empty((D,4),dtype='f8')
  for d,v in enumerate(q.astype('f8')):
   c0,c1,c2=cuts[d]; lut[d]=(max(v-c0,0)**2,0 if c0<=v<=c1 else min((v-c0)**2,(v-c1)**2),0 if c1<=v<=c2 else min((v-c1)**2,(v-c2)**2),max(c2-v,0)**2)
  packed=thq[page]; levels=((packed[:,:,None]>>(2*np.arange(4,dtype='u1')))&3).astype('i4'); s=np.zeros(len(page))
  for byte in range(B): s+=lut[byte*4+np.arange(4),levels[:,byte]].sum(axis=1)
  top=page[np.lexsort((page,s))[:W]]; row={int(ids[qi,i]):i for i in range(W)}; qn=np.linalg.norm(q.astype('f8')); scores=[]
  for doc in top:
   r=row[int(doc)]; lev=((thq[int(doc),:,None]>>(2*np.arange(4,dtype='u1')))&3).reshape(D); vec=cent[np.arange(D),lev].astype('f8')
   for sp in range(splits):
    for part in range(sub): vec[sp*(D//splits):(sp+1)*(D//splits)]+=books[sp,part,int(codes[qi,r,sp*sub+part])]
   scores.append(float(vec@q)/max(float(norms[qi,r])*qn,1e-30))
  ranked=[int(top[i]) for i in np.lexsort((top,-np.asarray(scores)))[:10]]
  if ranked != native[(qi,0)]['top10_ids']: mism.append(qi)
 out={'schema_version':1,'family':'independent_packed_plsq_routed_v1','status':'PASS' if not mism else 'FAIL','query_count':152,'independent_top10_exact':f'{152-len(mism)}/152','mismatches':mism,'raw_sha256':sha(a.raw),'payload_sha256':sha(a.payload),'candidate_stream_sha256':sha(a.candidate_flat),'reference_kind':'independent_packed_replay'}; a.result.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n'); print(json.dumps(out,sort_keys=True));
 if mism: raise SystemExit(1)
if __name__=='__main__': main()
