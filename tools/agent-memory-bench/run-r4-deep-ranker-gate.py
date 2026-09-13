#!/usr/bin/env python3
"""Evaluate a held-out full-frontier R4 address ranker.

The model is trained only on the training query split.  Teacher ids are used
for labels during training and evaluation, never to build postings or runtime
candidate lists.  All budgets count unique document ids; posting work is
reported separately.
"""
from __future__ import annotations
import argparse, hashlib, json, importlib.util, time
from pathlib import Path
from typing import Any
import numpy as np
from sklearn.linear_model import LogisticRegression

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for c in iter(lambda:f.read(1<<20),b''): h.update(c)
    return h.hexdigest()

def agg(v):
    a=np.asarray(v,dtype=np.float64)
    return {'min':float(a.min()),'mean':float(a.mean()),'p05':float(np.percentile(a,5)),
            'p50':float(np.percentile(a,50)),'p95':float(np.percentile(a,95)),'max':float(a.max())}

def load_mod(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--thq-manifest',type=Path,required=True)
    p.add_argument('--r4-manifest',type=Path,required=True)
    p.add_argument('--r4-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--raw-output',type=Path,required=True)
    p.add_argument('--depth',type=int,default=8192)
    p.add_argument('--train-queries',type=int,default=114)
    p.add_argument('--budgets',default='20000,50000,100000')
    a=p.parse_args(); frozen=json.loads(a.thq_manifest.read_text()); manifest=json.loads(a.r4_manifest.read_text())
    n,dim=int(frozen['documents']),int(frozen['dimension']); qcount=min(152,int(frozen['queries']))
    queries=np.memmap(Path(frozen['references']['queries']['path']),mode='r',dtype='<f4',shape=(qcount,dim))
    teachers=np.memmap(Path(frozen['references']['teacher_ids']['path']),mode='r',dtype='<i8',shape=(qcount,10))
    docs=np.memmap(Path(frozen['references']['document_vectors']['path']),mode='r',dtype='<f4',shape=(n,dim))
    rec=next(x for x in manifest['seeds'] if int(x['seed'])==2026082701)
    root=a.r4_root/'materialized'/'seed-2026082701'; m={x['role']:x for x in rec['mappings']}
    occ=np.fromfile(root/m['occupied_addresses']['file'],dtype='<u4'); counts=np.fromfile(root/m['address_counts']['file'],dtype='<u4'); offs=np.fromfile(root/m['address_offsets']['file'],dtype='<u4')
    phys=np.fromfile(root/m['physical_to_document']['file'],dtype='<i4'); order_docs=[phys[int(o):int(o+c)] for o,c in zip(offs,counts)]
    fp=next(x for x in rec['layouts'] if x['role']=='address_major_fp32'); records=np.memmap(root/fp['file'],mode='r',dtype='<f4',shape=(n,dim))
    # Build immutable address representatives from the address-major payload.
    cent=np.empty((len(order_docs),dim),dtype=np.float32)
    for i,(o,c) in enumerate(zip(offs,counts)): cent[i]=np.asarray(records[int(o):int(o+c)],dtype=np.float32).mean(axis=0)
    cent_norm=np.linalg.norm(cent,axis=1); cent_norm[cent_norm==0]=1
    cent/=cent_norm[:,None]
    qnorm=np.asarray(queries,dtype=np.float32); qn=np.linalg.norm(qnorm,axis=1,keepdims=True); qn[qn==0]=1; qnorm=qnorm/qn
    # Deep route order is the frozen model prefix followed by regenerated tail.
    deep_short=np.fromfile(root/m['shortlist_rows']['file'],dtype='<u4').reshape(qcount,1024)
    route_orders=[]
    for qi in range(qcount):
        scores=qnorm[qi]@cent.T
        tail=np.lexsort((np.arange(len(cent),dtype=np.int32),-scores))
        seen=set(int(x) for x in deep_short[qi]); merged=list(map(int,deep_short[qi]))+[int(x) for x in tail if int(x) not in seen]
        route_orders.append(np.asarray(merged[:a.depth],dtype=np.int32))
    train_q=np.arange(min(a.train_queries,qcount),dtype=np.int32); eval_q=np.arange(min(a.train_queries,qcount),qcount,dtype=np.int32)
    # Feature columns: cosine(query,address), inverse deep rank, log posting size,
    # and coarse cosine rank.  No teacher-derived feature is used at inference.
    X=[]; y=[]
    for qi in train_q:
        scores=qnorm[qi]@cent.T; ranks=np.empty(len(cent),dtype=np.int32); ranks[route_orders[qi]]=np.arange(len(route_orders[qi]),dtype=np.int32)
        for r,ad in enumerate(route_orders[qi]):
            label=int(any(int(t) in set(order_docs[int(ad)]) for t in teachers[qi]))
            X.append([float(scores[ad]),1.0/(1.0+r),float(np.log1p(counts[ad])),float(r/len(route_orders[qi]))]); y.append(label)
    X=np.asarray(X,dtype=np.float32); y=np.asarray(y,dtype=np.int8)
    fit=time.perf_counter(); model=LogisticRegression(max_iter=200,class_weight='balanced',random_state=20260913); model.fit(X,y); fit_ms=(time.perf_counter()-fit)*1000
    budgets=[int(x) for x in a.budgets.split(',') if x]; rows=[]
    for qi in eval_q:
        scores=qnorm[qi]@cent.T; order=route_orders[qi]; feats=np.asarray([[float(scores[ad]),1.0/(1.0+r),float(np.log1p(counts[ad])),float(r/len(order))] for r,ad in enumerate(order)],dtype=np.float32)
        pred=model.predict_proba(feats)[:,1]; ranked=order[np.lexsort((order,-pred))]
        seen=np.zeros(n,dtype=np.bool_); selected=0; entries=0; touched=0; teachers_q=np.asarray(teachers[qi],dtype=np.int64); snaps=[]
        for ad in ranked:
            ids=order_docs[int(ad)]; touched+=1; entries+=len(ids); fresh=ids[~seen[ids]]; seen[fresh]=True; selected+=len(fresh)
            for b in budgets:
                if selected>=b and not any(s['budget']==b for s in snaps): snaps.append({'budget':b,'actual_unique_candidates':int(selected),'posting_entries_touched':int(entries),'postings_touched':int(touched),'teacher_recall':float(np.count_nonzero(seen[teachers_q])/len(teachers_q))})
            if len(snaps)==len(budgets): break
        for b in budgets:
            row=next((s for s in snaps if s['budget']==b),{'budget':b,'actual_unique_candidates':int(selected),'posting_entries_touched':int(entries),'postings_touched':int(touched),'teacher_recall':float(np.count_nonzero(seen[teachers_q])/len(teachers_q))})
            rows.append({'query':int(qi),'split':'held_out',**row})
    # In-sample control exposes overfitting without affecting the held-out claim.
    control=[]
    for qi in train_q[:min(16,len(train_q))]:
        control.append(int(np.count_nonzero([int(t) in set(np.concatenate([order_docs[int(ad)] for ad in route_orders[qi][:1024]])) for t in teachers[qi]])))
    summary=[]
    for b in budgets:
        s=[r for r in rows if r['budget']==b]; summary.append({'budget':b,'teacher_recall':agg([r['teacher_recall'] for r in s]),'actual_unique_candidates':agg([r['actual_unique_candidates'] for r in s]),'posting_entries_touched':agg([r['posting_entries_touched'] for r in s]),'postings_touched':agg([r['postings_touched'] for r in s])})
    raw=(json.dumps({'schema_version':1,'rows':rows},separators=(',',':'),sort_keys=True)+'\n').encode(); a.raw_output.parent.mkdir(parents=True,exist_ok=True); a.raw_output.write_bytes(raw)
    out={'schema_version':1,'family':'r4_deep_full_frontier_ranker_v1','execution_status':'EXECUTED','production_activation':False,'fixture_manifest_sha256':sha256(a.thq_manifest),'r4_manifest_sha256':sha256(a.r4_manifest),'runner_sha256':sha256(Path(__file__)),'depth':a.depth,'train_queries':len(train_q),'held_out_queries':len(eval_q),'feature_columns':['address_cosine','inverse_deep_rank','log_posting_size','normalized_rank'],'fit_ms':fit_ms,'held_out_summary':summary,'raw_output':{'path':str(a.raw_output),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()},'protocol':{'teacher_ids_used_for_training_labels':True,'teacher_ids_used_at_inference':False,'budget_definition':'unique document ids','physical_page_bytes':'not measured','production_activation':False}}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(out,indent=2)+'\n')
if __name__=='__main__': main()
