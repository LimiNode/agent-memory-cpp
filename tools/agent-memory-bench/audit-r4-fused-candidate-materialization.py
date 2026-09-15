#!/usr/bin/env python3
"""Fail-closed audit for whole-posting fused candidate materialization."""
from __future__ import annotations
import argparse, hashlib, json, struct
from pathlib import Path
import numpy as np

SEEDS=(2026082701,2026082702,2026082703); QUERIES=152; A=8192; BUDGET=5000; PAGE=4096; RECORD=148
def require(value: bool, message: str) -> None:
    if not value: raise RuntimeError(message)
def sha(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1<<20), b""): digest.update(chunk)
    return digest.hexdigest()
def read_order(path: Path) -> list[tuple[np.ndarray,np.ndarray]]:
    raw=path.read_bytes(); magic,version,queries,count,passes=struct.unpack_from("<5I",raw,0)
    require((magic,version,queries,passes)==(0x314F5243,1,QUERIES,1),"order header differs")
    values=struct.unpack_from(f"<{count}I",raw,20); offset=20+4*count; dtype=np.dtype([("address","<u4"),("score","<f4")]); out=[]
    for _ in range(QUERIES):
        chosen=None
        for value in values:
            pairs=np.frombuffer(raw,dtype=dtype,count=value,offset=offset).copy(); offset+=value*8
            if value==A: chosen=(pairs["address"].astype(np.int64),pairs["score"].astype(np.float32))
        require(chosen is not None and np.unique(chosen[0]).size==A,"order A differs"); out.append(chosen)
    require(offset==len(raw),"order trailing bytes"); return out
def load_route(root: Path, record: dict) -> list[np.ndarray]:
    rr=root/f"seed-{int(record['seed'])}"; mapping={str(x["role"]):x for x in record["mappings"]}; arrays=[]
    for role,dtype in (("address_offsets","<u4"),("address_counts","<u4"),("physical_to_document","<i4")):
        path=rr/mapping[role]["file"]; require(path.is_file() and sha(path)==mapping[role]["sha256"],f"route {role} provenance differs"); arrays.append(np.fromfile(path,dtype=dtype))
    offsets,counts,physical=arrays; require(int(counts.sum())==len(physical),"route size differs")
    return [physical[int(offset):int(offset+count)] for offset,count in zip(offsets,counts)]
def main() -> None:
    parser=argparse.ArgumentParser()
    for name in ("receipt","raw","runner","thq-manifest","r4-layout-manifest","r4-layout-root","native-receipt"):
        parser.add_argument(f"--{name}",type=Path,required=True)
    args=parser.parse_args()
    receipt=json.loads(args.receipt.read_text()); raw=json.loads(args.raw.read_text())
    require(receipt["family"]==raw["family"]=="semantic_r4_fused_candidate_materialization_v1","family differs")
    require(receipt["raw_sha256"]==sha(args.raw) and receipt["runner_sha256"]==sha(args.runner),"provenance differs")
    thq=json.loads(args.thq_manifest.read_text()); layout=json.loads(args.r4_layout_manifest.read_text()); native=json.loads(args.native_receipt.read_text())
    require(receipt["thq_manifest_sha256"]==sha(args.thq_manifest) and receipt["layout_manifest_sha256"]==sha(args.r4_layout_manifest) and receipt["native_receipt_sha256"]==sha(args.native_receipt),"input provenance differs")
    routes={int(record["seed"]):load_route(args.r4_layout_root,record) for record in layout["seeds"]}; orders={}
    for seed in SEEDS:
        binding=next(x for x in native["native_outputs"] if int(x["seed"])==seed and x["layout"]=="aosoa_avx2" and int(x["lanes"])==32)
        order=Path(binding["order"]); require(sha(order)==binding["order_sha256"] and order.stat().st_size==int(binding["order_bytes"]),"native order provenance differs"); orders[seed]=read_order(order)
    documents=int(thq["documents"]); rows=raw["rows"]; require(len(rows)==QUERIES,"row count differs")
    for row in rows:
        query=int(row["query"]); seen=np.zeros(documents,dtype=np.bool_); selected=[]; positions=[0,0,0]; touched=entries=0
        while len(selected)<BUDGET:
            available=[i for i in range(3) if positions[i]<A]; require(available,"route exhausted")
            stream=max(available,key=lambda i:(float(orders[SEEDS[i]][query][1][positions[i]]),-i)); address=int(orders[SEEDS[stream]][query][0][positions[stream]]); positions[stream]+=1
            posting=routes[SEEDS[stream]][address]; fresh=posting[~seen[posting]]; seen[posting]=True; selected.extend(int(x) for x in fresh); touched+=1; entries+=len(posting)
        require(len(selected)==int(row["candidate_count"]) and len(selected)>=BUDGET and int(row["postings_touched"])==touched and int(row["posting_entries_touched"])==entries,"candidate stream differs")
    flat=Path(receipt["flat_file"]["path"]); blocked=Path(receipt["blocked_file"]["path"]); require(flat.is_file() and blocked.is_file(),"materialized files missing")
    require(sha(flat)==receipt["flat_file"]["sha256"] and sha(blocked)==receipt["blocked_file"]["sha256"],"materialized file provenance differs")
    require(receipt["flat_file"]["bytes"]==sum(int(row["candidate_count"])*RECORD for row in rows),"flat size differs")
    require(receipt["blocked_file"]["bytes"]==sum((int(row["candidate_count"])+26)//27*PAGE for row in rows),"blocked size differs")
    print(json.dumps({"family":"semantic_r4_fused_candidate_materialization_audit_v2","status":"PASS","rows":len(rows)},sort_keys=True))
if __name__=="__main__":
    try: main()
    except Exception as error: raise SystemExit(f"audit-r4-fused-candidate-materialization: {error}")
