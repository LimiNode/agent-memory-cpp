#!/usr/bin/env python3
"""Independent INT8 packed audit for routed raw output."""
from __future__ import annotations
import argparse, hashlib, json, struct
from pathlib import Path
import numpy as np
N,D,B=1_000_000,384,96
def sha(p):
 h=hashlib.sha256();h.update(p.read_bytes());return h.hexdigest()
def self_test():
 rows=[{'query':0,'repeat':0,'top10_ids':list(range(10))}]
 def valid(value):
  return (len(value)==1 and value[0]['query']==0 and value[0]['repeat']==0 and
          value[0]['top10_ids']==list(range(10)))
 assert valid(rows)
 mutated=json.loads(json.dumps(rows)); mutated[0]['top10_ids'][0]=999
 assert not valid(mutated), 'top10 mutation was accepted'
 mutated=json.loads(json.dumps(rows)); mutated[0]['query']=1
 assert not valid(mutated), 'query mutation was accepted'
 print('packed INT8 routed audit self-test PASS')
def main():
 p=argparse.ArgumentParser();p.add_argument('--self-test',action='store_true');p.add_argument('--codes',type=Path);p.add_argument('--scales',type=Path);p.add_argument('--thq',type=Path);p.add_argument('--thresholds',type=Path);p.add_argument('--candidate-flat',type=Path);p.add_argument('--offsets',type=Path);p.add_argument('--queries',type=Path);p.add_argument('--raw',type=Path);p.add_argument('--result',type=Path);a=p.parse_args()
 if a.self_test:self_test();return
 codes=np.memmap(a.codes,mode='r',dtype='i1',shape=(N,D)); scales=np.memmap(a.scales,mode='r',dtype='<f4',shape=(N,)); thq=np.memmap(a.thq,mode='r',dtype='u1',shape=(N,B)); cuts=np.fromfile(a.thresholds,dtype='<f4').reshape(D,3); q=np.memmap(a.queries,mode='r',dtype='<f4',shape=(152,D)); flat=a.candidate_flat.read_bytes(); offs=np.fromfile(a.offsets,dtype='<u8'); rb=len(flat)//int(offs[-1]); cand=np.asarray([struct.unpack_from('<i',flat,i*rb)[0] for i in range(int(offs[-1]))],dtype='i4'); raw=[json.loads(x) for x in a.raw.read_text().splitlines() if x]; native={(r['query'],r['repeat']):r for r in raw}; mism=[]
 for qi,query in enumerate(q):
  page=cand[int(offs[qi]):int(offs[qi+1])]; lut=np.empty((D,4),dtype='f4')
  for d,v in enumerate(query.astype('f8')):
   c0,c1,c2=cuts[d];lut[d]=(max(v-c0,0)**2,0 if c0<=v<=c1 else min((v-c0)**2,(v-c1)**2),0 if c1<=v<=c2 else min((v-c1)**2,(v-c2)**2),max(c2-v,0)**2)
  lev=((thq[page,:,None]>>(2*np.arange(4,dtype='u1')))&3).astype('i4'); s=np.zeros(len(page),dtype='f4')
  for by in range(0,B,4):
   s0=np.zeros(len(page),dtype='f4'); s1=np.zeros(len(page),dtype='f4'); s2=np.zeros(len(page),dtype='f4'); s3=np.zeros(len(page),dtype='f4')
   for lane in range(4):
    s0=np.float32(s0+lut[(by+0)*4+lane,lev[:,by+0,lane]])
    s1=np.float32(s1+lut[(by+1)*4+lane,lev[:,by+1,lane]])
    s2=np.float32(s2+lut[(by+2)*4+lane,lev[:,by+2,lane]])
    s3=np.float32(s3+lut[(by+3)*4+lane,lev[:,by+3,lane]])
   s=np.float32(s+np.float32(s0+s1)+np.float32(s2+s3))
  top=page[np.lexsort((page,s))[:128]]
  # Reproduce native best_int8(linear): float32 dot of integer codes and
  # query, followed by the persisted per-document scale.
  vals=np.empty(len(top),dtype=np.float32)
  for pos,doc_id in enumerate(top):
   row=codes[int(doc_id)]; scale=scales[int(doc_id)]
   dot=np.float32(0.0)
   for dim in range(D):
    dot=np.float32(dot+np.float32(np.float32(row[dim])*query[dim]))
   vals[pos]=np.float32(dot*scale)
  ranked=[int(top[i]) for i in np.lexsort((top,-vals))[:10]]
  if ranked!=native[(qi,0)]['top10_ids']:mism.append(qi)
 out={'schema_version':1,'family':'independent_packed_int8_routed_v1','status':'PASS' if not mism else 'FAIL','query_count':152,'independent_top10_exact':f'{152-len(mism)}/152','mismatches':mism,'raw_sha256':sha(a.raw),'candidate_stream_sha256':sha(a.candidate_flat),'reference_kind':'independent_packed_replay'};a.result.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n');print(json.dumps(out,sort_keys=True));
 if mism:raise SystemExit(1)
if __name__=='__main__':main()
