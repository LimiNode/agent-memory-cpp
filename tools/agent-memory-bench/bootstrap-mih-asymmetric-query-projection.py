#!/usr/bin/env python3
"""Deterministic paired bootstrap for the asymmetric MIH experiment."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy
METRICS=('e5_oracle_raw_union_coverage','e5_oracle_hamming_top_k_coverage','e5_oracle_second_stage_coverage','reranked_ndcg_at_10','candidate_count','posting_visit_count')
def sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()
def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--left',type=Path,required=True);p.add_argument('--right',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--id',required=True);p.add_argument('--seed',type=int,default=20260814);p.add_argument('--replicates',type=int,default=10000);a=p.parse_args()
 left=numpy.load(a.left,allow_pickle=False);right=numpy.load(a.right,allow_pickle=False)
 if not numpy.array_equal(left['query_ids'],right['query_ids']):raise ValueError('paired query IDs differ')
 rng=numpy.random.default_rng(a.seed);size=left['query_ids'].size; result={}
 for metric in METRICS:
  delta=right[metric].astype(numpy.float64)-left[metric].astype(numpy.float64);samples=numpy.empty(a.replicates)
  for i in range(a.replicates):samples[i]=delta[rng.integers(0,size,size=size)].mean()
  result[metric]={'observed_difference':float(delta.mean()),'percentile_95_ci':[float(numpy.quantile(samples,.025)),float(numpy.quantile(samples,.975))]}
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps({'schema_version':1,'family':'mih_asymmetric_query_projection_bootstrap_v1','id':a.id,'left_sha256':sha(a.left),'right_sha256':sha(a.right),'query_count':int(size),'replicates':a.replicates,'seed':a.seed,'metrics':result},indent=2,sort_keys=True)+'\n')
 return 0
if __name__=='__main__':raise SystemExit(main())
