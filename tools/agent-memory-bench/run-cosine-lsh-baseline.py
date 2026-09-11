#!/usr/bin/env python3
"""Corrected independent Gaussian-hyperplane cosine-LSH evaluation."""
from __future__ import annotations
import argparse, hashlib, json, time
from pathlib import Path
import numpy as np

def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1<<20),b''):h.update(block)
    return h.hexdigest()

def top_by(score,ids,k,descending=False):
    if not len(ids):return ids
    order=np.lexsort((ids,-score if descending else score));return ids[order[:min(k,len(ids))]]

def main():
    p=argparse.ArgumentParser();p.add_argument('--thq-manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--query-limit',type=int,default=152);p.add_argument('--tables',type=int,default=4);p.add_argument('--bits-per-table',type=int,default=16);p.add_argument('--seeds',default='20260911,20260912,20260913,20260914,20260915');p.add_argument('--chunk',type=int,default=50000);a=p.parse_args()
    if a.bits_per_table>16:raise ValueError('this corrected baseline supports at most 16 bits/table')
    m=json.loads(a.thq_manifest.read_text());n,d=int(m['documents']),int(m['dimension']);q=min(int(m['queries']),a.query_limit);refs=m['references'];docs=np.memmap(refs['document_vectors']['path'],mode='r',dtype='<f4',shape=(n,d));queries=np.asarray(np.memmap(refs['queries']['path'],mode='r',dtype='<f4',shape=(q,d)));teachers=np.memmap(refs['teacher_ids']['path'],mode='r',dtype='<i8',shape=(q,10));seeds=[int(x) for x in a.seeds.split(',')]
    seed_results=[]
    for seed in seeds:
        rng=np.random.default_rng(seed);planes=rng.normal(size=(a.tables,a.bits_per_table,d)).astype(np.float32);planes/=np.linalg.norm(planes,axis=2,keepdims=True);codes=np.zeros((n,a.tables),np.uint16)
        for i in range(0,n,a.chunk):
            j=min(n,i+a.chunk);x=np.asarray(docs[i:j])
            for table in range(a.tables):
                bits=(x@planes[table].T)>0; packed=np.packbits(bits,axis=1,bitorder='little');codes[i:j,table]=packed[:,0].astype(np.uint16)|(packed[:,1].astype(np.uint16)<<8)
        postings=[];table_stats=[]
        for table in range(a.tables):
            order=np.argsort(codes[:,table],kind='stable');keys=codes[order,table];unique,first,counts=np.unique(keys,return_index=True,return_counts=True);mapping={int(unique[k]):order[first[k]:first[k]+counts[k]] for k in range(len(unique))};postings.append(mapping);prob=counts.astype(np.float64)/n;entropy=float(-(prob*np.log2(prob)).sum());table_stats.append({'occupied_buckets':int(len(unique)),'entropy_bits':entropy,'effective_buckets':float(2**entropy),'bucket_size_mean':float(counts.mean()),'bucket_size_p50':float(np.quantile(counts,.5)),'bucket_size_p95':float(np.quantile(counts,.95)),'bucket_size_max':int(counts.max())})
        qcodes=np.zeros((q,a.tables),np.uint16)
        for table in range(a.tables):
            bits=(queries@planes[table].T)>0;packed=np.packbits(bits,axis=1,bitorder='little');qcodes[:,table]=packed[:,0].astype(np.uint16)|(packed[:,1].astype(np.uint16)<<8)
        rows=[]
        for qi in range(q):
            started=time.perf_counter();parts=[postings[t].get(int(qcodes[qi,t]),np.empty(0,np.int64)) for t in range(a.tables)];nonempty=[x for x in parts if len(x)];cand=np.unique(np.concatenate(nonempty)) if nonempty else np.empty(0,np.int64);generation_ms=(time.perf_counter()-started)*1000
            union_recall=float(np.isin(teachers[qi],cand).sum())/10
            if len(cand):
                cosine=np.asarray(docs[cand])@queries[qi];exact_top=top_by(cosine,cand,256,True);xor=np.bitwise_xor(codes[cand],qcodes[qi]);hamming=np.unpackbits(xor.view(np.uint8),axis=1,bitorder='little').sum(1,np.uint16);hash_top=top_by(hamming,cand,256)
            else:exact_top=hash_top=cand
            rows.append({'query':qi,'candidate_count':int(len(cand)),'postings_touched':int(sum(len(x) for x in parts)),'query_bucket_sizes':[int(len(x)) for x in parts],'generation_ms':generation_ms,'teacher_recall_in_union':union_recall,'exact_cosine_survival_256':float(np.isin(teachers[qi],exact_top).sum())/10,'hash_hamming_survival_256':float(np.isin(teachers[qi],hash_top).sum())/10})
        seed_results.append({'seed':seed,'table_stats':table_stats,'rows':rows,'summary':{key:{'mean':float(np.mean([r[key] for r in rows])),'p50':float(np.quantile([r[key] for r in rows],.5)),'p95':float(np.quantile([r[key] for r in rows],.95)),'min':float(np.min([r[key] for r in rows])),'max':float(np.max([r[key] for r in rows]))} for key in ('candidate_count','postings_touched','teacher_recall_in_union','exact_cosine_survival_256','hash_hamming_survival_256')}})
    result={'schema_version':2,'family':'cosine_lsh_baseline_corrected_v2','documents':n,'queries':q,'dimension':d,'hash_family':'independent_gaussian_random_hyperplanes','tables':a.tables,'bits_per_table':a.bits_per_table,'probe_policy':'exact_bucket_union_only','rerankers':['exact_cosine','hash_hamming_diagnostic'],'seeds':seed_results,'fixture_manifest_sha256':sha256(a.thq_manifest),'runner_sha256':sha256(Path(__file__)),'production_activation':False};a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':main()
