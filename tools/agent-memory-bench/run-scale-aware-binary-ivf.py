#!/usr/bin/env python3
"""Run frozen-scale external BinaryIVF candidate/E5-survival calibration."""
from __future__ import annotations
import argparse, hashlib, json, time
from pathlib import Path
from typing import Any
import faiss, numpy

THIS=Path(__file__).resolve().parent

def require(v: bool,m: str)->None:
    if not v: raise ValueError(m)
def sha256(p: Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p: Path)->dict[str,Any]:
    v=json.loads(p.read_text(encoding='utf-8')); require(v['family']=='scale_aware_binary_ivf_v1' and v['faiss_version']=='1.13.2' and faiss.__version__=='1.13.2' and v['candidate_fractions']==[.05,.10,.25] and v['cascade']=={'hamming_limit':768,'adc_limit':256,'oracle_k':10},'scale BinaryIVF contract differs');return v
def codes(p:Path,n:int)->numpy.ndarray:return numpy.fromfile(p,dtype='<u8').reshape(n,4).view(numpy.uint8).reshape(n,32).copy()
def pc_table()->numpy.ndarray:return numpy.asarray([x.bit_count() for x in range(256)],dtype=numpy.uint8)
def adc(bits,projection,centroids,candidates):
    table=(projection[:,None]-centroids)**2; distance=table[numpy.arange(256)[None,:],bits[candidates]].sum(axis=1);return candidates[numpy.lexsort((candidates,distance))[:256]]
def exact_oracle(document_path:Path,query_path:Path,n:int,q:int,d:int)->numpy.ndarray:
    docs=numpy.memmap(document_path,dtype='<f4',mode='r',shape=(n,d)); queries=numpy.memmap(query_path,dtype='<f4',mode='r',shape=(q,d)); best_scores=numpy.full((q,10),-numpy.inf,dtype=numpy.float32); best_ids=numpy.full((q,10),n,dtype=numpy.int64)
    for start in range(0,n,20000):
        scores=numpy.asarray(queries@docs[start:min(n,start+20000)].T,dtype=numpy.float32); ids=numpy.arange(start,start+scores.shape[1],dtype=numpy.int64)
        total_scores=numpy.concatenate((best_scores,scores),axis=1); total_ids=numpy.concatenate((best_ids,numpy.broadcast_to(ids,(q,ids.size))),axis=1)
        order=numpy.argsort(-total_scores,axis=1,kind='stable')[:,:10]; best_scores=numpy.take_along_axis(total_scores,order,axis=1); best_ids=numpy.take_along_axis(total_ids,order,axis=1)
    return best_ids
def run(a):
    c=load(a.contract); out=[]
    for s in c['scales']:
        root=a.scale_root/s['id']; inp=root/'input'; ev=root/'e5'; im=inp/'manifest.json'; em=ev/'manifest.json'; m=json.loads(im.read_text()); e=json.loads(em.read_text());n=s['documents'];q=m['query_count'];require(sha256(im)==s['input_manifest_sha256'] and sha256(em)==s['evaluation_manifest_sha256'] and n==m['document_count']==e['outputs']['evaluation_document_vectors']['count'] and q==648,'scale frozen manifests differ')
        docs=codes(inp/m['document_codes_file'],n); queries=codes(inp/m['query_codes_file'],q);bits=numpy.unpackbits(docs,bitorder='little',axis=1); projections=numpy.fromfile(inp/m['query_itq_projections_file'],dtype='<f4').reshape(q,256);centroids=numpy.fromfile(inp/m['binary_adc_centroids_file'],dtype='<f4').reshape(256,2); oracle=exact_oracle(ev/e['outputs']['evaluation_document_vectors']['path'],ev/e['outputs']['evaluation_query_vectors']['path'],n,q,e['outputs']['evaluation_document_vectors']['dimension'])
        for nl in s['nlist_values']:
            index=faiss.IndexBinaryIVF(faiss.IndexBinaryFlat(256),256,nl);index.cp.seed=c['training_seed'];index.train(docs);index.add(docs);path=a.output_root/s['id']/ 'indexes'/f'nlist{nl}.faiss';path.parent.mkdir(parents=True,exist_ok=True);faiss.write_index_binary(index,str(path));index=faiss.read_index_binary(str(path)); ih=sha256(path)
            for f in c['candidate_fractions']:
                np=max(1,round(f*nl));index.nprobe=np;_,lids=index.quantizer.search(queries,np);counts=[sum(index.invlists.list_size(int(x)) for x in row if x>=0) for row in lids];survival=[];samples=[]
                for i,query in enumerate(queries):
                    st=time.perf_counter();dist,ids=index.search(query.reshape(1,-1),768);samples.append((time.perf_counter()-st)*1000);valid=ids[0]>=0;order=numpy.lexsort((ids[0,valid],dist[0,valid]));candidate=ids[0,valid][order].astype(numpy.int64);require(candidate.size==768,'BinaryIVF candidates below 768'); final=adc(bits,projections[i],centroids,candidate);survival.append(float(numpy.isin(oracle[i],final).sum())/10)
                out.append({'scale':s['id'],'nlist':nl,'nprobe':np,'index_sha256':ih,'target_candidate_fraction':f,'actual_candidate_fraction':float(numpy.mean(counts))/n,'candidate_count_p95':float(numpy.quantile(counts,.95)),'search_p50_ms_per_query':float(numpy.quantile(samples,.5)),'search_p95_ms_per_query':float(numpy.quantile(samples,.95)),'e5_oracle_survival_after_adc':float(numpy.mean(survival))})
    a.output_root.mkdir(parents=True,exist_ok=True);(a.output_root/'summary.json').write_text(json.dumps({'schema_version':1,'family':'scale_aware_binary_ivf_v1','contract_sha256':sha256(a.contract),'faiss_version':faiss.__version__,'rows':out},indent=2,sort_keys=True)+'\n',encoding='utf-8',newline='\n')
def main():
 p=argparse.ArgumentParser();p.add_argument('--contract',type=Path,default=THIS/'scale-aware-binary-ivf.example.json');p.add_argument('--scale-root',type=Path,required=True);p.add_argument('--output-root',type=Path,required=True);a=p.parse_args()
 try:run(a);return 0
 except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError) as e:print(f'run-scale-aware-binary-ivf: {e}');return 1
if __name__=='__main__':raise SystemExit(main())
