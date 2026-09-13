#!/usr/bin/env python3
"""Follow-up oracle: adaptive posting utility, spilling, and multi-anchor fusion."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json
from pathlib import Path
import numpy as np

def sha256(p):
 h=hashlib.sha256();
 with Path(p).open('rb') as f:
  for c in iter(lambda:f.read(1<<20),b''): h.update(c)
 return h.hexdigest()
def agg(v):
 a=np.asarray(v,float); return {'min':float(a.min()),'mean':float(a.mean()),'p05':float(np.percentile(a,5)),'p50':float(np.percentile(a,50)),'p95':float(np.percentile(a,95)),'max':float(a.max())}
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

def main():
 p=argparse.ArgumentParser(); p.add_argument('--thq-manifest',type=Path,required=True); p.add_argument('--r4-manifest',type=Path,required=True); p.add_argument('--r4-root',type=Path,required=True); p.add_argument('--output',type=Path,required=True); p.add_argument('--raw-output',type=Path,required=True); a=p.parse_args()
 frozen=json.loads(a.thq_manifest.read_text()); n=int(frozen['documents']); teachers=np.fromfile(Path(frozen['references']['teacher_ids']['path']),dtype='<i8').reshape(152,10); a.depth=8192
 fusion=load('fusion',Path(__file__).with_name('run-r4-fusion-upper-bounds.py')); routes=fusion.reconstruct(a,frozen,json.loads(a.r4_manifest.read_text())); names=['seed-2026082702','seed-2026082703','deep-8192']; budgets=[20000,50000,100000]; raw=[]
 for qi in range(152):
  t=np.asarray(teachers[qi],dtype=np.int64); rows={k:[] for k in ('adaptive','equal_quota','greedy_gain')}
  teacher_pos={int(x):i for i,x in enumerate(t)}; masks={k:{} for k in names}
  for k in names:
   for ad in routes[k]['order'][qi]:
    mask_value=0
    for x in routes[k]['postings'][int(ad)]:
     j=teacher_pos.get(int(x))
     if j is not None: mask_value |= 1<<j
    masks[k][int(ad)]=mask_value
  # Non-leaking adaptive utility: marginal unique candidates per posting entry.
  ptr={k:0 for k in names}; seen=np.zeros(n,bool); selected=0; entries=0; touched=0
  while selected<max(budgets):
   choices=[]
   for k in names:
    order=routes[k]['order'][qi]
    if ptr[k]>=len(order): continue
    ids=routes[k]['postings'][int(order[ptr[k]])]; fresh=int(np.count_nonzero(~seen[ids])); cost=max(1,len(ids)); choices.append((fresh/cost,k,fresh,ids))
   if not choices: break
   _,k,fresh,ids=max(choices,key=lambda x:(x[0],x[2],-len(x[3]),x[1])); ptr[k]+=1; touched+=1; entries+=len(ids); fresh_ids=ids[~seen[ids]]; seen[fresh_ids]=True; selected+=len(fresh_ids)
   for b in budgets:
    if selected>=b and not any(r['budget']==b for r in rows['adaptive']): rows['adaptive'].append({'budget':b,'unique_candidates':selected,'posting_entries':entries,'postings_touched':touched,'teacher_recall':float(np.count_nonzero(seen[t])/len(t))})
  # Equal-quota multi-anchor fusion (non-leaking control).
  for mode in ('equal_quota','greedy_gain'):
   for b in budgets:
    cap=b//len(names); chosen=[]
    if mode=='equal_quota':
     for k in names: chosen.extend((k,int(ad)) for ad in routes[k]['order'][qi][:cap])
    else:
     # Teacher-leaking upper bound: greedily select the next address with maximum
     # uncovered teacher gain per posting entry.
     pools={k:list(map(int,routes[k]['order'][qi])) for k in names}; seen_addr=set(); mask=0; entries=0
     while entries<b:
      best=None
      for k,pool in pools.items():
       while pool and pool[0] in seen_addr: pool.pop(0)
       if not pool: continue
       ad=pool[0]; ids=routes[k]['postings'][ad]; gain=(masks[k][ad] & ~mask).bit_count(); score=gain/max(1,len(ids))
       cand=(score,gain,-len(ids),k,ad)
       if best is None or cand>best: best=cand
      if best is None: break
      _,gain,_,k,ad=best; pools[k].pop(0); seen_addr.add(ad); chosen.append((k,ad)); entries+=len(routes[k]['postings'][ad]); mask|=masks[k][ad]
    union=[]; seen_ids=set();
    for k,ad in chosen:
      ids=routes[k]['postings'][int(ad)]
      for x in ids:
       if int(x) not in seen_ids: seen_ids.add(int(x)); union.append(int(x))
      if len(union)>=b: break
    hit=sum(int(x) in seen_ids for x in t); rows[mode].append({'budget':b,'unique_candidates':len(union),'posting_entries':entries,'postings_touched':len(chosen),'teacher_recall':hit/len(t)})
  # Balanced subposting accounting: split each selected posting into fixed chunks.
  for chunk in (256,512,1024):
   for b in budgets:
    entries=sum(min(chunk,len(routes[names[0]]['postings'][int(ad)])) for ad in routes[names[0]]['order'][qi][:max(1,b//1000)])
    raw.append({'query':qi,'chunk':chunk,'budget':b,'logical_block_entries':entries})
  for mode,vals in rows.items():
   for x in vals: raw.append({'query':qi,'mode':mode,**x})
 summary=[]
 for mode in ('adaptive','equal_quota','greedy_gain'):
  for b in budgets:
   s=[x for x in raw if x.get('mode')==mode and x['budget']==b]; summary.append({'mode':mode,'budget':b,'teacher_recall':agg([x['teacher_recall'] for x in s]),'unique_candidates':agg([x['unique_candidates'] for x in s]),'posting_entries':agg([x['posting_entries'] for x in s]),'postings_touched':agg([x['postings_touched'] for x in s])})
 raw_bytes=(json.dumps({'schema_version':1,'rows':raw},separators=(',',':'),sort_keys=True)+'\n').encode(); a.raw_output.parent.mkdir(parents=True,exist_ok=True); a.raw_output.write_bytes(raw_bytes)
 out={'schema_version':1,'family':'r4_adaptive_spill_multianchor_v1','execution_status':'EXECUTED','production_activation':False,'fixture_manifest_sha256':sha256(a.thq_manifest),'r4_manifest_sha256':sha256(a.r4_manifest),'runner_sha256':sha256(Path(__file__)),'summary':summary,'balanced_subposting_chunks':[256,512,1024],'raw_output':{'path':str(a.raw_output),'bytes':len(raw_bytes),'sha256':hashlib.sha256(raw_bytes).hexdigest()},'protocol':{'adaptive_scheduler_teacher_free':True,'greedy_gain_teacher_leaking_upper_bound':True,'budget_definition':'unique document ids','physical_page_bytes':'not measured','secondary_assignment':'simulated by route replication; no payload duplication'}}
 a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(out,indent=2)+'\n')
if __name__=='__main__': main()
