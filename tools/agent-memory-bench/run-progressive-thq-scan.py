#!/usr/bin/env python3
"""Correct tie-safe progressive ordinal/ADC pruning oracle."""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
import numpy as np
from numba import njit, prange

CHECKPOINTS = (32, 64, 96, 128, 160, 192, 256, 320, 384)

@njit(parallel=True)
def full_scores(levels, query_levels, costs):
    count, dimension = levels.shape
    hamming = np.empty(count, np.uint16)
    adc = np.empty(count, np.float64)
    for row in prange(count):
        h = 0
        score = 0.0
        for col in range(dimension):
            level = np.int16(levels[row, col])
            delta = level - np.int16(query_levels[col])
            if delta < 0:
                delta = -delta
            h += delta
            score += costs[col, level]
        hamming[row] = h
        adc[row] = score
    return hamming, adc

@njit(parallel=True)
def progressive_masks(levels, query_levels, costs, order, cutoff, use_adc):
    count = levels.shape[0]
    masks = np.empty((count, len(CHECKPOINTS)), np.uint8)
    for row in prange(count):
        score = 0.0
        checkpoint = 0
        for position in range(levels.shape[1]):
            col = order[position]
            level = np.int16(levels[row, col])
            if use_adc:
                score += costs[col, level]
            else:
                delta = level - np.int16(query_levels[col])
                if delta < 0:
                    delta = -delta
                score += delta
            if position + 1 == CHECKPOINTS[checkpoint]:
                masks[row, checkpoint] = 1 if score <= cutoff else 0
                checkpoint += 1
                if checkpoint == len(CHECKPOINTS):
                    break
    return masks

def interval_costs(t, queries):
    q = queries[:, :, None]
    a, b, c = (t[:, i][None, :] for i in range(3))
    out = np.stack((np.maximum(q[..., 0] - a, 0), np.where(q[..., 0] < a, a-q[..., 0], np.where(q[..., 0] >= b, q[..., 0]-b, 0)), np.where(q[..., 0] < b, b-q[..., 0], np.where(q[..., 0] >= c, q[..., 0]-c, 0)), np.maximum(c-q[..., 0], 0)), axis=2)
    return (out * out).astype(np.float32)

def main():
    p=argparse.ArgumentParser();p.add_argument('--thq-manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--query-limit',type=int,default=152);p.add_argument('--chunk',type=int,default=50000);a=p.parse_args()
    m=json.loads(a.thq_manifest.read_text());n,d=int(m['documents']),int(m['dimension']);q=min(int(m['queries']),a.query_limit);o=m['outputs'];dc=np.memmap(o['thq4_document_codes']['path'],mode='r',dtype=np.uint8,shape=(n,144));qc=np.memmap(o['thq4_query_codes']['path'],mode='r',dtype=np.uint8,shape=(q,144));teachers=np.memmap(m['references']['teacher_ids']['path'],mode='r',dtype='<i8',shape=(q,10));t=np.asarray(np.memmap(o['thq4_thresholds']['path'],mode='r',dtype='<f4',shape=(d,3)))
    lev=np.empty((n,d),np.uint8)
    for i in range(0,n,a.chunk):
        j=min(n,i+a.chunk);lev[i:j]=np.unpackbits(np.asarray(dc[i:j]),axis=1,bitorder='little')[:,:d*3].reshape(j-i,d,3).sum(2)
    ql=np.unpackbits(np.asarray(qc),axis=1,bitorder='little')[:,:d*3].reshape(q,d,3).sum(2).astype(np.uint8);adc=interval_costs(t,np.asarray(np.memmap(m['references']['queries']['path'],mode='r',dtype='<f4',shape=(q,d))));var=np.var(lev.astype(np.float32),axis=0);systems={'hamming':[],'adc_squared':[]}
    for qi in range(q):
        full_h,full_a=full_scores(lev,ql[qi],adc[qi])
        for metric, scores in (('hamming',full_h),('adc_squared',full_a)):
            cutoff=float(np.partition(scores,255)[255]);orders={'fixed':np.arange(d),'variance':np.argsort(-var),'query_adaptive':np.argsort(-np.abs(lev[:min(100000,n)].mean(0)-ql[qi]))};
            for order_name,order in orders.items():
                previous=0;seen=[]
                for end in CHECKPOINTS:
                    seen.extend(int(x) for x in order[previous:end]);previous=end
                if sorted(seen)!=list(range(d)): raise AssertionError('coordinate coverage failure')
                masks=progressive_masks(lev,ql[qi],adc[qi],order.astype(np.int64),cutoff,metric=='adc_squared')
                rows=[]
                for index,end in enumerate(CHECKPOINTS):
                    active=int(masks[:,index].sum());rows.append({'coordinates':end,'active':active,'evaluated_fraction':active/n})
                final_mask=scores <= cutoff
                if not np.array_equal(masks[:,-1].astype(bool),final_mask): raise AssertionError(f'{metric} final mask mismatch')
                top=np.lexsort((np.arange(n),scores))[:256];systems[metric].append({'query':qi,'order':order_name,'cutoff':cutoff,'checkpoints':rows,'survival_256':float(np.isin(teachers[qi],top).sum())/10})
    result={'schema_version':2,'family':'progressive_thq_scan_oracle_corrected_v2','documents':n,'queries':q,'dimension':d,'checkpoints':list(CHECKPOINTS),'prune_rule':'partial score <= exact top-256 cutoff','systems':systems,'production_activation':False};a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':main()
