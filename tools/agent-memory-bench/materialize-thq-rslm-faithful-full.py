#!/usr/bin/env python3
"""Materialize the faithful RSLM1 representation for all DE-1M rows."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json
from pathlib import Path
import numpy as np
D, N = 384, 1_000_000

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1<<20), b''): h.update(chunk)
    return h.hexdigest()

def load(name: str, path: Path):
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None: raise RuntimeError(f'cannot load {path}')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

def main() -> None:
    parser=argparse.ArgumentParser()
    for name in ('documents','train-vectors','thq4-codes','thq4-thresholds','output'):
        parser.add_argument(f'--{name}',dest=name.replace('-','_'),type=Path,required=True)
    parser.add_argument('--chunk-size',type=int,default=4096); args=parser.parse_args()
    if args.documents.stat().st_size != N*D*4: raise SystemExit('documents must be DE-1M FP32x384')
    docs=np.memmap(args.documents,mode='r',dtype='<f4',shape=(N,D)); train=np.asarray(np.memmap(args.train_vectors,mode='r',dtype='<f4',shape=(args.train_vectors.stat().st_size//(D*4),D)),dtype=np.float32); thq=np.memmap(args.thq4_codes,mode='r',dtype=np.uint8,shape=(N,96)); thresholds=np.fromfile(args.thq4_thresholds,dtype='<f4').reshape(D,3)
    frontier=load('rslm_frontier_full',Path(__file__).with_name('run-thq4-residual-frontier.py')); faithful=load('rslm_faithful_full',Path(__file__).with_name('rslm-faithful-reference.py')); centroids=frontier.fit_centroids(train,thresholds); out=args.output; out.mkdir(parents=True,exist_ok=True)
    ids=np.arange(N,dtype='<i4'); ids.tofile(out/'candidate.ids.i4'); np.asarray(centroids,dtype='<f4').tofile(out/'thq4-centroids.f32')
    symbols=np.memmap(out/'rslm1.symbols.u8',mode='w+',dtype=np.uint8,shape=(N,48)); inner=np.memmap(out/'rslm1.inner-scale.u16',mode='w+',dtype='<u2',shape=(N,)); outer=np.memmap(out/'rslm1.outer-scale.u16',mode='w+',dtype='<u2',shape=(N,)); final_norm=np.memmap(out/'rslm1.final-norm.f32',mode='w+',dtype='<f4',shape=(N,))
    for start in range(0,N,args.chunk_size):
        stop=min(N,start+args.chunk_size); levels=frontier.unpack_thq(np.asarray(thq[start:stop])); base=centroids[np.arange(D)[None,:],levels]; values=np.asarray(docs[start:stop],dtype=np.float32); packed,scales=faithful.encode(values-base,1); decoded=faithful.decode(packed,scales,1); combined=base+decoded; ratio=np.sqrt(np.sum(values*values,axis=1)/np.maximum(np.sum(combined*combined,axis=1),np.finfo(np.float32).tiny)); symbols[start:stop]=packed; inner[start:stop]=scales; outer[start:stop]=np.asarray([faithful.ue7m9_encode(float(v)) for v in ratio],dtype='<u2'); final_norm[start:stop]=np.linalg.norm(combined.astype(np.float64),axis=1).astype(np.float32)
    for array in (symbols,inner,outer,final_norm): array.flush()
    receipt={'schema_version':1,'family':'thq_rslm1_faithful_full_materialization_v1','status':'EXECUTED','candidate_local':False,'documents':N,'dimension':D,'side_bytes_per_document':56,'symbols_sha256':sha256(out/'rslm1.symbols.u8'),'inner_scale_sha256':sha256(out/'rslm1.inner-scale.u16'),'outer_scale_sha256':sha256(out/'rslm1.outer-scale.u16'),'final_norm_sha256':sha256(out/'rslm1.final-norm.f32'),'centroids_sha256':sha256(out/'thq4-centroids.f32'),'documents_sha256':sha256(args.documents),'train_vectors_sha256':sha256(args.train_vectors),'thq4_codes_sha256':sha256(args.thq4_codes),'thq4_thresholds_sha256':sha256(args.thq4_thresholds),'materializer_sha256':sha256(Path(__file__)),'faithful_reference_sha256':sha256(Path(__file__).with_name('rslm-faithful-reference.py'))}
    (out/'materialization.receipt.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n',encoding='utf-8'); print(json.dumps(receipt,sort_keys=True))
if __name__=='__main__': main()
