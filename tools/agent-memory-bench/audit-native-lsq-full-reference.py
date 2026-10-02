#!/usr/bin/env python3
"""Independent packed replay for full-flat LSQ32/48 payloads."""
from __future__ import annotations
import argparse,hashlib,json,struct
from pathlib import Path
import numpy as np
D,N,THQ_BYTES=384,1_000_000,96
def sha(p):
 h=hashlib.sha256();
 with p.open('rb') as f:
  for c in iter(lambda:f.read(1<<20),b''): h.update(c)
 return h.hexdigest()
def read_raw(p):
 rows={}; seen=set()
 for line in p.read_text().splitlines():
  if not line.strip(): continue
  r=json.loads(line); q,rep,ids=r.get('query'),r.get('repeat'),r.get('top10_ids')
  if not isinstance(q,int) or not isinstance(rep,int) or not isinstance(ids,list) or len(ids)!=10 or (q,rep) in seen: raise ValueError('raw shape differs')
  seen.add((q,rep));
  if rep==0: rows[q]=ids
 if len(seen)!=760 or len(rows)!=152: raise ValueError('raw coverage differs')
 return rows
def main():
 p=argparse.ArgumentParser(); p.add_argument('--payload',type=Path,required=True); p.add_argument('--thq',type=Path,required=True); p.add_argument('--queries',type=Path,required=True); p.add_argument('--raw',type=Path,required=True); p.add_argument('--result',type=Path,required=True); p.add_argument('--chunk-size',type=int,default=100_000); a=p.parse_args(); data=a.payload.read_bytes(); stages,dim,count=struct.unpack_from('<III',data,8)
 if data[:7]!=b'AMLSQ01' or (dim,count)!=(D,N): raise ValueError('LSQ header differs')
 off=20; ids=np.frombuffer(data,dtype='<i4',count=N,offset=off); off+=N*4; codes=np.frombuffer(data,dtype=np.uint8,count=N*stages,offset=off).reshape(N,stages); off+=N*stages; books=np.frombuffer(data,dtype='<f4',count=stages*256*D,offset=off).reshape(stages,256,D); off+=stages*256*D*4; cent=np.frombuffer(data,dtype='<f4',count=D*4,offset=off).reshape(D,4); off+=D*4*4; norms=np.frombuffer(data,dtype='<f4',count=N,offset=off); thq=np.memmap(a.thq,mode='r',dtype=np.uint8,shape=(N,THQ_BYTES)); queries=np.memmap(a.queries,mode='r',dtype='<f4',shape=(152,D)); native=read_raw(a.raw); mismatches=[]
 for qi,q in enumerate(queries):
  base=np.empty((THQ_BYTES,256),dtype=np.float64)
  for byte in range(THQ_BYTES):
   for packed in range(256):
    levels=[(packed>>(2*lane))&3 for lane in range(4)]; base[byte,packed]=sum(float(cent[4*byte+lane,levels[lane]])*float(q[4*byte+lane]) for lane in range(4))
  lut=np.einsum('scd,d->sc',books,q.astype(np.float64)); qn=float(np.linalg.norm(q.astype(np.float64))); ranked=[]
  for begin in range(0,N,a.chunk_size):
   end=min(N,begin+a.chunk_size); levels=thq[begin:end]; score=base[np.arange(THQ_BYTES),levels].sum(axis=1)
   for stage in range(stages): score+=lut[stage,codes[begin:end,stage]]
   score/=np.maximum(norms[begin:end].astype(np.float64)*qn,np.finfo(np.float64).tiny); take=min(32,end-begin); sel=np.argpartition(-score,take-1)[:take]; ranked.extend((float(score[i]),int(ids[begin+i])) for i in sel)
  ranked.sort(key=lambda x:(-x[0],x[1])); expected=[x[1] for x in ranked[:10]]
  if expected!=native[qi]: mismatches.append(qi)
 result={'status':'PASS' if not mismatches else 'FAIL','metric':'reconstructed_cosine','codec':f'LSQ{stages}','query_count':152,'independent_top10_exact':f'{152-len(mismatches)}/152','mismatches':mismatches[:16],'payload_sha256':sha(a.payload),'raw_sha256':sha(a.raw),'thq_sha256':sha(a.thq),'queries_sha256':sha(a.queries),'reference_runner_sha256':sha(Path(__file__))}; a.result.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n'); print(json.dumps(result,sort_keys=True));
 if mismatches: raise SystemExit(1)
if __name__=='__main__': main()
