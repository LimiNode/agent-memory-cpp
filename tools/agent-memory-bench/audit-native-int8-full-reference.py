#!/usr/bin/env python3
"""Independent packed replay for the INT8 full-flat control."""
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
import numpy as np
N,D=1_000_000,384
def sha(p):
 h=hashlib.sha256();
 with p.open('rb') as f:
  for c in iter(lambda:f.read(1<<20),b''): h.update(c)
 return h.hexdigest()
def main():
 p=argparse.ArgumentParser(); p.add_argument('--codes',type=Path,required=True); p.add_argument('--scales',type=Path,required=True); p.add_argument('--norms',type=Path,required=True); p.add_argument('--queries',type=Path,required=True); p.add_argument('--raw',type=Path,required=True); p.add_argument('--result',type=Path,required=True); p.add_argument('--metric',choices=('reconstructed_cosine_exact','scaled_reconstructed_dot'),default='reconstructed_cosine_exact'); p.add_argument('--chunk-size',type=int,default=100_000); a=p.parse_args(); codes=np.memmap(a.codes,mode='r',dtype=np.int8,shape=(N,D)); scales=np.memmap(a.scales,mode='r',dtype='<f4',shape=(N,)); inverse_norms=np.memmap(a.norms,mode='r',dtype='<f4',shape=(N,)); queries=np.memmap(a.queries,mode='r',dtype='<f4',shape=(152,D)); rows={}; seen=set()
 for line in a.raw.read_text().splitlines():
  if not line.strip(): continue
  r=json.loads(line); q,rep,ids=r['query'],r['repeat'],r['top10_ids'];
  if (q,rep) in seen or len(ids)!=10: raise ValueError('raw shape differs')
  seen.add((q,rep));
  if rep==0: rows[q]=ids
 if len(seen)!=760 or len(rows)!=152: raise ValueError('raw coverage differs')
 mismatches=[]
 for qi,q in enumerate(queries):
  q64=q.astype(np.float64); qn=float(np.linalg.norm(q64)); ranked=[]
  for begin in range(0,N,a.chunk_size):
   end=min(N,begin+a.chunk_size); dot=np.asarray(codes[begin:end],dtype=np.float64)@q64
   if a.metric == 'reconstructed_cosine_exact':
    score=dot*np.asarray(inverse_norms[begin:end],dtype=np.float64)/max(qn,1e-30)
   else:
    score=dot*np.asarray(scales[begin:end],dtype=np.float64)/max(qn,1e-30)
   take=min(32,end-begin); sel=np.argpartition(-score,take-1)[:take]; ranked.extend((float(score[i]),begin+int(i)) for i in sel)
  ranked.sort(key=lambda x:(-x[0],x[1])); expected=[x[1] for x in ranked[:10]]
  if expected!=rows[qi]: mismatches.append(qi)
 reconstructed_norms=np.asarray(scales,dtype=np.float64)/np.maximum(np.asarray(inverse_norms,dtype=np.float64),1e-30)
 norm_delta=np.abs(reconstructed_norms-1.0)
 result={'status':'PASS' if not mismatches else 'FAIL','codec':'INT8','metric':a.metric,'query_count':152,'independent_top10_exact':f'{152-len(mismatches)}/152','mismatches':mismatches[:16],'codes_sha256':sha(a.codes),'scales_sha256':sha(a.scales),'inverse_code_norms_sha256':sha(a.norms),'queries_sha256':sha(a.queries),'raw_sha256':sha(a.raw),'reference_runner_sha256':sha(Path(__file__)),'reference_kind':'independent_packed_replay','reconstructed_norm_distribution':{'min':float(np.min(reconstructed_norms)),'max':float(np.max(reconstructed_norms)),'mean':float(np.mean(reconstructed_norms)),'max_abs_delta_from_one':float(np.max(norm_delta)),'p95_abs_delta_from_one':float(np.percentile(norm_delta,95))}}; a.result.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n'); print(json.dumps(result,sort_keys=True));
 if mismatches: raise SystemExit(1)
if __name__=='__main__': main()
