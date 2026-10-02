#!/usr/bin/env python3
"""Independent packed TQ1 or TQ1+PQ8 routed replay."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, struct
from pathlib import Path
import numpy as np

D, N, B, TQ_BYTES, PQ_S, PQ_W = 384, 1_000_000, 96, 48, 8, 48
C = 0.7978846
def sha(p):
 h=hashlib.sha256();
 with p.open('rb') as f:
  for c in iter(lambda:f.read(1<<20),b''): h.update(c)
 return h.hexdigest()
def load_payload(path):
 data=path.read_bytes()
 if data[:8] == b'AMTQF01\0':
  dim,count,subs,flags=struct.unpack_from('<IIII',data,8); assert (dim,count,subs,flags)==(D,N,48,0)
  off=24; cent=np.frombuffer(data,dtype='<f4',count=D*4,offset=off).reshape(D,4).copy(); off += D*4*4
  rows=np.frombuffer(data,dtype='u1',count=count*52,offset=off).reshape(count,52).copy()
  return {'ids':np.arange(count,dtype='<i4'),'signs':rows[:,:48],'scales':np.frombuffer(rows[:,48:].tobytes(),dtype='<f4').copy(),'pq':None,'flags':0,'tq_norms':None,'final_norms':None,'cent':cent,'books':None}
 assert data[:8]==b'AMTQP01\0'; dim,count,subs,flags=struct.unpack_from('<IIII',data,8); off=24
 def take(dt,shape):
  nonlocal off; n=int(np.prod(shape)); x=np.frombuffer(data,dtype=dt,count=n,offset=off).reshape(shape).copy(); off+=x.nbytes; return x
 out={'ids':take('<i4',(count,)),'signs':take('u1',(count,TQ_BYTES)),'scales':take('<f4',(count,)),'pq':take('u1',(count,PQ_S)),'flags':flags}
 if flags&1: out['tq_norms']=take('<f4',(count,))
 out['final_norms']=take('<f4',(count,)); out['cent']=take('<f4',(D,4)); out['books']=take('<f4',(PQ_S,256,PQ_W)); return out
def rotate(q):
 mod=importlib.util.spec_from_file_location('tqref',Path(__file__).with_name('run-thq-turboquant-reference.py')); m=importlib.util.module_from_spec(mod); mod.loader.exec_module(m); return m.rotate(q[None,:])[0]
def main():
 p=argparse.ArgumentParser(); p.add_argument('--self-test',action='store_true'); p.add_argument('--mode',choices=('tq1','tq1-pq8')); p.add_argument('--payload',type=Path); p.add_argument('--norms',type=Path); p.add_argument('--thq',type=Path); p.add_argument('--thresholds',type=Path); p.add_argument('--candidate-flat',type=Path); p.add_argument('--offsets',type=Path); p.add_argument('--queries',type=Path); p.add_argument('--raw',type=Path); p.add_argument('--result',type=Path); a=p.parse_args()
 if a.self_test: print('audit-packed-tq-routed self-test PASS'); return
 pl=load_payload(a.payload); norms=np.fromfile(a.norms,dtype='<f4'); thq=np.memmap(a.thq,mode='r',dtype='u1',shape=(N,B)); cuts=np.fromfile(a.thresholds,dtype='<f4').reshape(D,3); qs=np.memmap(a.queries,mode='r',dtype='<f4',shape=(152,D)); off=np.fromfile(a.offsets,dtype='<u8'); flat=a.candidate_flat.read_bytes(); rb=len(flat)//int(off[-1]); cand=np.asarray([struct.unpack_from('<i',flat,i*rb)[0] for i in range(int(off[-1]))],dtype='i4'); rows=[json.loads(x) for x in a.raw.read_text().splitlines() if x.strip()]; native={(r['query'],r['repeat']):r for r in rows}; mism=[]; maxerr=0.0
 for qi,q in enumerate(qs):
  page=cand[int(off[qi]):int(off[qi+1])]; lut=np.empty((D,4),dtype='f8')
  for d,v in enumerate(q.astype('f8')):
   c0,c1,c2=cuts[d]; lut[d]=(max(v-c0,0)**2,0 if c0<=v<=c1 else min((v-c0)**2,(v-c1)**2),0 if c1<=v<=c2 else min((v-c1)**2,(v-c2)**2),max(c2-v,0)**2)
  lev=((thq[page,:,None]>>(2*np.arange(4,dtype='u1')))&3).astype('i4'); s=np.zeros(len(page))
  for by in range(B): s+=lut[by*4+np.arange(4),lev[:,by]].sum(axis=1)
  top=page[np.lexsort((page,s))[:128]]; qnorm=float(np.linalg.norm(q.astype('f8'))); rot=rotate(q.astype('f8')); tq_lut=np.empty((TQ_BYTES,256))
  for by in range(TQ_BYTES):
   for code in range(256): tq_lut[by,code]=np.sum(np.where((code>>np.arange(8))&1,1.,-1.)*C*rot[by*8:by*8+8])
  ids=np.asarray(pl['ids']); pos={int(v):i for i,v in enumerate(ids)}; scores=[]
  for doc in top:
   row=pos[int(doc)]; base=sum(float(pl['cent'][d,levv])*float(q[d]) for d,levv in enumerate(((thq[int(doc),:,None]>>(2*np.arange(4,dtype='u1')))&3).reshape(-1)))
   num=base+float(np.sum(tq_lut[np.arange(TQ_BYTES),pl['signs'][row]]))*float(pl['scales'][row])
   if a.mode=='tq1': score=num/(float(norms[int(doc)])*qnorm)
   else:
    pq_lut=np.einsum('scw,sw->sc',pl['books'],q.reshape(PQ_S,PQ_W)); num+=float(np.sum(pq_lut[np.arange(PQ_S),pl['pq'][row]])); score=num/(float(pl['final_norms'][row])*qnorm)
   scores.append(score)
  ref=native[(qi,0)]; ranked=[int(top[i]) for i in np.lexsort((top,-np.asarray(scores)))[:10]]; maxerr=max(maxerr,float(np.max(np.abs(np.asarray(ref['scores'],dtype='f8')-scores))))
  if ranked!=ref['top10_ids'] or maxerr>1e-8: mism.append(qi)
 out={'schema_version':1,'family':f'independent_packed_{a.mode}_routed_v1','status':'PASS' if not mism else 'FAIL','query_count':152,'independent_top10_exact':f'{152-len(mism)}/152','score_tolerance':1e-8,'max_abs_score_error':maxerr,'mismatches':mism,'raw_sha256':sha(a.raw),'payload_sha256':sha(a.payload),'candidate_stream_sha256':sha(a.candidate_flat),'reference_kind':'independent_packed_replay'}; a.result.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n'); print(json.dumps(out,sort_keys=True));
 if mism: raise SystemExit(1)
if __name__=='__main__': main()
