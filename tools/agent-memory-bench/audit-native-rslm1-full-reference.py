#!/usr/bin/env python3
"""Independent packed replay for the faithful full-corpus RSLM1 gate."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json
from pathlib import Path
import numpy as np
D,N,THQ_BYTES=384,1_000_000,96

def sha256(path: Path)->str:
 h=hashlib.sha256();
 with path.open('rb') as stream:
  for chunk in iter(lambda:stream.read(1<<20),b''): h.update(chunk)
 return h.hexdigest()
def load_ref():
 path=Path(__file__).with_name('rslm-faithful-reference.py'); spec=importlib.util.spec_from_file_location('rslm_ref',path)
 if spec is None or spec.loader is None: raise RuntimeError('cannot load RSLM reference')
 mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
def read_raw(path:Path)->dict[int,list[int]]:
 rows={}; seen=set()
 for line in path.read_text().splitlines():
  if not line.strip(): continue
  row=json.loads(line); q,rep=row.get('query'),row.get('repeat'); ids=row.get('top10_ids')
  if not isinstance(q,int) or not isinstance(rep,int) or not isinstance(ids,list) or len(ids)!=10 or (q,rep) in seen: raise ValueError('raw shape differs')
  seen.add((q,rep));
  if rep==0: rows[q]=ids
 if len(seen)!=760 or len(rows)!=152: raise ValueError('raw coverage differs')
 return rows
def main()->None:
 p=argparse.ArgumentParser(); p.add_argument('--symbols',type=Path,required=True); p.add_argument('--inner',type=Path,required=True); p.add_argument('--final-norm',type=Path,required=True); p.add_argument('--thq',type=Path,required=True); p.add_argument('--centroids',type=Path,required=True); p.add_argument('--queries',type=Path,required=True); p.add_argument('--raw',type=Path,required=True); p.add_argument('--result',type=Path,required=True); p.add_argument('--chunk-size',type=int,default=100_000); a=p.parse_args()
 native=read_raw(a.raw); symbols=np.memmap(a.symbols,mode='r',dtype=np.uint8,shape=(N,48)); inner=np.memmap(a.inner,mode='r',dtype='<u2',shape=(N,)); norms=np.memmap(a.final_norm,mode='r',dtype='<f4',shape=(N,)); thq=np.memmap(a.thq,mode='r',dtype=np.uint8,shape=(N,THQ_BYTES)); cent=np.fromfile(a.centroids,dtype='<f4').reshape(D,4); queries=np.memmap(a.queries,mode='r',dtype='<f4',shape=(152,D)); ref=load_ref(); mismatches=[]
 cache=a.result.with_suffix('.reconstructed.tmp.f32'); reconstructed=np.memmap(cache,mode='w+',dtype='<f4',shape=(N,D))
 for begin in range(0,N,a.chunk_size):
  end=min(N,begin+a.chunk_size); levels=np.empty((end-begin,D),dtype=np.uint8)
  for byte in range(THQ_BYTES):
   code=thq[begin:end,byte]; levels[:,4*byte:4*byte+4]=np.stack((code&3,(code>>2)&3,(code>>4)&3,(code>>6)&3),axis=1)
  reconstructed[begin:end]=cent[np.arange(D)[None,:],levels]+ref.decode_joint1(np.asarray(symbols[begin:end]),np.asarray(inner[begin:end]))
 reconstructed.flush()
 for qi,query in enumerate(queries):
  base_lut=np.empty((THQ_BYTES,256),dtype=np.float64)
  for byte in range(THQ_BYTES):
   for packed in range(256):
    levels=[(packed>>(2*lane))&3 for lane in range(4)]; base_lut[byte,packed]=sum(float(cent[4*byte+lane,levels[lane]])*float(query[4*byte+lane]) for lane in range(4))
  qnorm=float(np.linalg.norm(query.astype(np.float64))); ranked=[]
  for begin in range(0,N,a.chunk_size):
   end=min(N,begin+a.chunk_size); score=np.asarray(reconstructed[begin:end],dtype=np.float64)@query.astype(np.float64); score=score/(np.maximum(np.asarray(norms[begin:end],dtype=np.float64)*qnorm,np.finfo(np.float64).tiny)); take=min(32,end-begin); sel=np.argpartition(-score,take-1)[:take]; ranked.extend((float(score[i]),begin+int(i)) for i in sel)
  ranked.sort(key=lambda x:(-x[0],x[1])); expected=[x[1] for x in ranked[:10]]
  if expected!=native[qi]: mismatches.append(qi)
 result={'status':'PASS' if not mismatches else 'FAIL','metric':'reconstructed_cosine','query_count':152,'independent_top10_exact':f'{152-len(mismatches)}/152','mismatches':mismatches[:16],'raw_sha256':sha256(a.raw),'reference_runner_sha256':sha256(Path(__file__)),'symbols_sha256':sha256(a.symbols),'inner_sha256':sha256(a.inner),'final_norm_sha256':sha256(a.final_norm),'thq_sha256':sha256(a.thq),'queries_sha256':sha256(a.queries)}; a.result.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n'); cache.unlink(missing_ok=True); print(json.dumps(result,sort_keys=True));
 if mismatches: raise SystemExit(1)
if __name__=='__main__': main()
