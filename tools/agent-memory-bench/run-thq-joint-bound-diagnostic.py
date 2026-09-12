#!/usr/bin/env python3
"""Compare marginal and joint Block-Min bound degeneracy without payload I/O."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np

def sha256(p):
 h=hashlib.sha256();
 with Path(p).open('rb') as f:
  for c in iter(lambda:f.read(1<<20),b''): h.update(c)
 return h.hexdigest()

def lut(q,t):
 out=np.empty((q.size,4),np.float32)
 for i,v in enumerate(q):
  a,b,c=t[i]; d=np.array((max(v-a,0), max(a-v,0) if v<a else max(v-b,0), max(b-v,0) if v<b else max(v-c,0), max(c-v,0)),np.float32); out[i]=d*d
 return out

def build_joint_cost(L, digits, dim, width, g):
 blocks=dim//width; groups=width//g; states=digits.shape[0]
 out=np.empty((blocks,groups,states),np.float32)
 for bi in range(blocks):
  for gi in range(groups):
   base=bi*width+gi*g
   for s in range(states): out[bi,gi,s]=sum(float(L[base+j,digits[s,j]]) for j in range(g))
 return out

def aggregate_marginal(mask_cost, masks, width):
 total=0.0
 for bi in range(masks.shape[0]):
  base=bi*width
  total+=float(mask_cost[masks[bi],np.arange(base,base+width)].sum())
 return total

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--layout-manifest',type=Path,required=True); ap.add_argument('--marginal-manifest',type=Path,required=True); ap.add_argument('--joint-manifest',type=Path,required=True); ap.add_argument('--thq-manifest',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--query-limit',type=int,default=32); args=ap.parse_args()
 layout=json.loads(args.layout_manifest.read_text()); marginal=json.loads(args.marginal_manifest.read_text()); joint=json.loads(args.joint_manifest.read_text()); m=json.loads(args.thq_manifest.read_text()); qn=min(args.query_limit,int(m['queries'])); dim=int(m['dimension']); width=int(layout['coords_per_block']); g=int(joint['group_size']); groups=width//g
 qs=np.memmap(m['references']['queries']['path'],mode='r',dtype='<f4',shape=(qn,dim)); th=np.memmap(m['outputs']['thq4_thresholds']['path'],mode='r',dtype='<f4',shape=(dim,3)); teachers=np.memmap(m['references']['teacher_ids']['path'],mode='r',dtype='<i8',shape=(qn,10));
 mt={int(x['tile']):np.fromfile(x['path'],dtype=np.uint8).reshape(int(x['blocks']),width) for x in marginal['tiles']}; jt={int(x['tile']):np.fromfile(x['path'],dtype=np.uint8).reshape(int(x['blocks']),groups,(4**g)//8) for x in joint['tiles']}; tiles=sorted(mt); rows=[]
 for qi in range(qn):
  L=lut(np.asarray(qs[qi]),np.asarray(th)); one=[]; two=[]; full=[]
  mask_cost=np.full((16,dim),np.inf,np.float32)
  for mask in range(1,16): mask_cost[mask]=np.min(np.where([(mask>>l)&1 for l in range(4)],L,np.inf),axis=1)
  states=4**g; digits=np.asarray([np.unravel_index(s,(4,)*g) for s in range(states)],dtype=np.int8)
  joint_cost=build_joint_cost(L,digits,dim,width,g)
  for tile in tiles:
   masks=mt[tile]; full.append(float(np.mean(masks==15)))
   one.append(aggregate_marginal(mask_cost,masks,width))
   bits=np.unpackbits(jt[tile],axis=2,bitorder='little')[...,:states]
   two.append(float(np.min(np.where(bits,joint_cost, np.inf),axis=2).sum()))
  one=np.asarray(one); two=np.asarray(two); order=np.argsort(two,kind='stable'); teacher_tiles=np.asarray(teachers[qi])//int(layout['tile_docs']); ranks=[int(np.flatnonzero(order==np.flatnonzero(np.asarray(tiles)==t)[0])[0])+1 if t in tiles else None for t in teacher_tiles]
  rows.append({'query':qi,'marginal_lb_min':float(one.min()),'marginal_lb_p50':float(np.median(one)),'marginal_lb_p90':float(np.percentile(one,90)),'marginal_lb_max':float(one.max()),'marginal_fraction_lb_zero':float(np.mean(one==0)),'joint_lb_min':float(two.min()),'joint_lb_p50':float(np.median(two)),'joint_lb_p90':float(np.percentile(two,90)),'joint_lb_max':float(two.max()),'joint_fraction_lb_zero':float(np.mean(two==0)),'joint_unique_lb':int(np.unique(two).size),'teacher_tile_ranks':ranks,'fraction_marginal_masks_1111':float(np.mean(full))})
 out={'schema_version':1,'family':'thq_joint_block_min_bound_diagnostic_v1','fixture_manifest_sha256':sha256(args.thq_manifest),'layout_manifest_sha256':sha256(args.layout_manifest),'marginal_manifest_sha256':sha256(args.marginal_manifest),'joint_manifest_sha256':sha256(args.joint_manifest),'runner_sha256':sha256(Path(__file__)),'queries':qn,'group_size':g,'rows':rows,'execution_status':'EXECUTED_SMOKE' if qn<152 else 'EXECUTED','correction_status':'CORRECTED_UPPER_TAIL_LUT_DIRECTION_AND_GLOBAL_COORDINATE_INDEXING','production_activation':False}
 args.output.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
if __name__=='__main__': main()
