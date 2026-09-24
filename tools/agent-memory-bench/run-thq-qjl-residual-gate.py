#!/usr/bin/env python3
"""Source-bound QJL score correction over the canonical THQ4 -> TQ1 shell."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, sys
from pathlib import Path
import numpy as np

D, Q, TOP = 384, 152, 128

def sha256(p: Path) -> str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()

def load_qjl():
 p=Path(__file__).with_name('qjl_reference.py'); s=importlib.util.spec_from_file_location('qjl',p); m=importlib.util.module_from_spec(s); sys.modules['qjl']=m; s.loader.exec_module(m); return m

def ndcg(ids,qids,grades):
    rel={int(d):float(g) for d,g in zip(qids,grades) if int(d)>=0 and float(g)>0}; gains=np.asarray([2**rel.get(int(d),0)-1 for d in ids[:10]],float); ideal=np.sort(np.asarray([2**g-1 for g in rel.values()]))[::-1][:10]; den=np.sum(ideal/np.log2(np.arange(2,2+len(ideal)))) if len(ideal) else 0.; return float(np.sum(gains/np.log2(np.arange(2,2+len(gains)))/den)) if den else 0.

def main():
 p=argparse.ArgumentParser()
 for n in ('documents','queries','qrel-ids','qrel-scores','teacher-ids','thq4-codes','thq4-thresholds','candidate-flat','candidate-raw','candidate-receipt','tq-payload','output','artifact'):
  p.add_argument('--'+n,dest=n.replace('-','_'),type=Path,required=True)
 a=p.parse_args(); qjl=load_qjl()
 docs=np.memmap(a.documents,mode='r',dtype='<f4',shape=(1_000_000,D)); queries=np.memmap(a.queries,mode='r',dtype='<f4',shape=(Q,D)); qids=np.memmap(a.qrel_ids,mode='r',dtype='<i8',shape=(Q,20)); grades=np.memmap(a.qrel_scores,mode='r',dtype='<f4',shape=(Q,20)); teacher=np.memmap(a.teacher_ids,mode='r',dtype='<i8',shape=(Q,10)); codes=np.memmap(a.thq4_codes,mode='r',dtype=np.uint8,shape=(1_000_000,96)); thresholds=np.fromfile(a.thq4_thresholds,dtype='<f4').reshape(D,3)
 raw=json.loads(a.candidate_raw.read_text()); counts=np.asarray([int(x['candidate_count']) for x in raw['rows']],np.int64); off=np.r_[0,np.cumsum(counts)]; rec=json.loads(a.candidate_receipt.read_text()); rb=int(rec['flat_file'].get('record_bytes',100)); flat=np.memmap(a.candidate_flat,mode='r',dtype=np.uint8,shape=(int(off[-1]),rb)); shell=np.asarray(flat[:,:4]).copy().view('<i4').reshape(-1).astype(np.int64)
 tq=np.load(a.tq_payload,allow_pickle=False); unique=np.asarray(tq['document_ids'],np.int64); base=np.asarray(tq['base'],np.float32); decoded=np.asarray(tq['decoded1'],np.float32); row_ids=np.asarray(tq['row_ids'],np.int64); row_off=np.asarray(tq['row_offsets'],np.int64); pos={int(d):i for i,d in enumerate(unique)}; residual=np.asarray(docs[unique],np.float32)-base-decoded; norms=np.linalg.norm(np.asarray(docs[unique],np.float32),axis=1).astype(np.float32)
 arms=[]; projections={}
 for m in (32,64,128):
  projections[f'qjl_gaussian{m}']=qjl.make_projection(m,D,20260924+m,'gaussian'); projections[f'qjl_rademacher{m}']=qjl.make_projection(m,D,20260924+m,'rademacher')
 rows=[]
 for qi,q in enumerate(queries):
  ids=row_ids[row_off[qi]:row_off[qi+1]]; idx=np.asarray([pos[int(d)] for d in ids]); b=base[idx]+decoded[idx]; qn=max(float(np.linalg.norm(q)),1e-30); bnorm=np.maximum(np.linalg.norm(b,axis=1),1e-30); exact=np.asarray(docs[ids],np.float32); exact_scores=(exact@q)/(np.maximum(np.linalg.norm(exact,axis=1),1e-30)*qn); tq_scores=(b@q)/(bnorm*qn); order=np.lexsort((ids,-tq_scores)); ranked=ids[order]
  for m in (32,64,128):
   for dist in ('gaussian','rademacher'):
    name=f'qjl_{dist}{m}'; proj=projections[name]; signs,rn=qjl.encode(residual[idx],proj); est=qjl.estimate_dot_reference(q,signs,rn,proj) if dist=='gaussian' else qjl.estimate_dot_rademacher_control(q,signs,rn,proj); score=(b@q+est)/(np.maximum(norms[idx],1e-30)*qn); merged=ids[np.lexsort((ids,-score))]; side=m//8+4; rows.append({'query':qi,'arm':name,'top10_ids':merged[:10].astype(int).tolist(),'thq4_top128_ids':ids.astype(int).tolist(),'qrels_ndcg10':ndcg(merged,qids[qi],grades[qi]),'teacher_overlap':float(np.isin(teacher[qi],merged[:10]).sum()/10),'side_payload_bytes':side,'cascade_total_bytes':96+52+side,'bytes_touched_this_query':TOP*(52+side),'qjl_denominator':'source_document_norm','mean_abs_score_error':float(np.mean(np.abs(score-exact_scores)))})
  rows.append({'query':qi,'arm':'tq1_filter_only','top10_ids':ranked[:10].astype(int).tolist(),'thq4_top128_ids':ids.astype(int).tolist(),'qrels_ndcg10':ndcg(ranked,qids[qi],grades[qi]),'teacher_overlap':float(np.isin(teacher[qi],ranked[:10]).sum()/10),'side_payload_bytes':4,'cascade_total_bytes':152,'bytes_touched_this_query':TOP*56,'qjl_denominator':'tq_norm'})
 summaries={arm:{'mean_qrels_ndcg10':float(np.mean([r['qrels_ndcg10'] for r in rows if r['arm']==arm])),'p05_qrels_ndcg10':float(np.percentile([r['qrels_ndcg10'] for r in rows if r['arm']==arm],5)),'worst_qrels_ndcg10':float(np.min([r['qrels_ndcg10'] for r in rows if r['arm']==arm])),'mean_teacher_overlap':float(np.mean([r['teacher_overlap'] for r in rows if r['arm']==arm])),'side_payload_bytes':next(r['side_payload_bytes'] for r in rows if r['arm']==arm)} for arm in sorted({r['arm'] for r in rows})}
 a.artifact.parent.mkdir(parents=True,exist_ok=True); np.savez_compressed(a.artifact,document_ids=unique,base=base,decoded1=decoded,residual_norms=norms,**{n:np.asarray(p.matrix,dtype='<f4') for n,p in projections.items()}); src={n:getattr(a,n.replace('-','_')) for n in ('documents','queries','qrel-ids','qrel-scores','teacher-ids','thq4-codes','thq4-thresholds','candidate-flat','candidate-raw','candidate-receipt','tq-payload')}; out={'schema_version':1,'family':'thq_qjl_residual_gate_v1','status':'EXECUTED','source_replay':True,'metric':'cosine','query_count':Q,'projection_rows':[32,64,128],'distributions':['gaussian','rademacher'],'source_hashes':{n:sha256(v) for n,v in src.items()},'runner_sha256':sha256(Path(__file__)),'artifact_sha256':sha256(a.artifact),'summaries':summaries,'rows':rows,'limitations':['candidate-local canonical THQ top128 shell','QJL is a score correction, not a vector decoder','source norm denominator is serving-shaped; exact corrected norm is not charged']}; a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
if __name__=='__main__': main()
