#!/usr/bin/env python3
"""Decompose R4 fusion limits with privileged and non-leaking oracles."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json
from pathlib import Path
from typing import Any
import numpy as np
THIS = Path(__file__).resolve().parent
def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for c in iter(lambda:f.read(1<<20),b''): h.update(c)
    return h.hexdigest()
def load(name: str, filename: str) -> Any:
    spec=importlib.util.spec_from_file_location(name, THIS/filename)
    if spec is None or spec.loader is None: raise RuntimeError(filename)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod
def aggregate(values: list[float]) -> dict[str,float]:
    a=np.asarray(values,dtype=np.float64)
    return {'min':float(a.min()),'mean':float(a.mean()),'p05':float(np.percentile(a,5)),'p50':float(np.percentile(a,50)),'p95':float(np.percentile(a,95)),'max':float(a.max())}

def validate_external(record: dict[str, Any]) -> dict[str, Any]:
    path = Path(record["path"])
    actual = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    expected = {"bytes": int(record["bytes"]), "sha256": record["sha256"]}
    if actual != expected:
        raise ValueError(f"frozen input mismatch for {path}: {actual} != {expected}")
    return {"path": str(path), **actual}

def jump_stream(route: dict[str, Any], qi: int, cap: int, prefix_length: int = 1024) -> np.ndarray:
    """Bound deep-prefix work, then jump over the remaining prefix to the tail."""
    order = route["order"][qi]
    postings = route["postings"]
    selected: list[int] = []
    consumed = 0
    if cap > 0:
        for address in order[:prefix_length]:
            selected.append(int(address))
            consumed += int(postings[int(address)].size)
            if consumed >= cap:
                break
    # Deliberately skip unvisited prefix addresses; no teacher information is used.
    selected.extend(int(address) for address in order[prefix_length:])
    return np.asarray(selected, dtype=np.uint32)
def reconstruct(args, frozen, manifest):
    n=int(frozen['documents']); q=np.memmap(Path(frozen['references']['queries']['path']),mode='r',dtype='<f4',shape=(152,384)); comp=load('ub_comp','run-r4-frozen-comparator.py'); cov=load('ub_cov','run-neuroute-r4-coverage-saturation.py'); routes={}
    for rec in manifest['seeds']:
        seed=int(rec['seed'])
        if seed==2026082701: continue
        root=args.r4_root/'materialized'/f'seed-{seed}'; m={x['role']:x for x in rec['mappings']}; counts=np.fromfile(root/m['address_counts']['file'],dtype='<u4'); offs=np.fromfile(root/m['address_offsets']['file'],dtype='<u4'); phys=np.fromfile(root/m['physical_to_document']['file'],dtype='<i4'); postings=[phys[int(o):int(o+c)] for o,c in zip(offs,counts)]; doc_address=np.empty(n,dtype=np.int32)
        for address,ids in enumerate(postings): doc_address[ids]=address
        short=np.fromfile(root/m['shortlist_rows']['file'],dtype='<u4').reshape(152,1024); fp=next(x for x in rec['layouts'] if x['role']=='address_major_fp32'); validated=[comp.file_record(root,x) for x in [*rec['mappings'],fp,*rec['model']]]; records=np.memmap(root/fp['file'],mode='r',dtype='<f4',shape=(n,384)); d2p=np.fromfile(root/m['document_to_physical']['file'],dtype='<u4'); order,_=comp.model_order(root,rec,np.asarray(q),short,records,d2p)
        if any(np.unique(row).size!=row.size for row in order): raise ValueError(f'duplicate shallow stream {seed}')
        routes[f'seed-{seed}']={'order':order,'postings':postings,'counts':counts,'doc_address':doc_address,'validated_artifacts':validated}
    rec=next(x for x in manifest['seeds'] if int(x['seed'])==2026082701); root=args.r4_root/'materialized'/'seed-2026082701'; m={x['role']:x for x in rec['mappings']}; occ=np.fromfile(root/m['occupied_addresses']['file'],dtype='<u4'); counts=np.fromfile(root/m['address_counts']['file'],dtype='<u4'); offs=np.fromfile(root/m['address_offsets']['file'],dtype='<u4'); phys=np.fromfile(root/m['physical_to_document']['file'],dtype='<i4'); addr=np.empty(n,dtype=np.uint32)
    for row,(o,c) in enumerate(zip(offs,counts)): addr[phys[int(o):int(o+c)]]=occ[row]
    idx=cov.scale.build_index(addr,16); docs=np.memmap(Path(frozen['references']['document_vectors']['path']),mode='r',dtype='<f4',shape=(n,384)); occ2,prot,eff,_=cov.multi.build_nested_prototypes(docs,addr,idx,8)
    if not np.array_equal(np.sort(occ),np.sort(occ2)): raise ValueError('deep occupied set mismatch')
    if not np.array_equal(occ,occ2):
        mp={int(a):i for i,a in enumerate(occ2)}; ix=np.asarray([mp[int(a)] for a in occ],dtype=np.int64); prot,eff=prot[ix],eff[ix]
    contract=cov.base.planner.load_contract(THIS/'neuroute-nonlinear-listwise-reranker.example.json'); deep_short,_=cov.base.prepare_query_features(np.asarray(q),occ,prot,eff,idx['counts'],n,args.depth,contract['training']['feature_query_batch_size']); lookup=cov.fine.address_lookup(occ); deep_rows=lookup[np.asarray(deep_short,dtype=np.uint32)]
    if any(np.unique(row).size!=row.size for row in deep_rows): raise ValueError('duplicate regenerated deep address')
    old=np.fromfile(root/m['shortlist_rows']['file'],dtype='<u4').reshape(152,1024); fp=next(x for x in rec['layouts'] if x['role']=='address_major_fp32'); records=np.memmap(root/fp['file'],mode='r',dtype='<f4',shape=(n,384)); prefix,_=comp.model_order(root,rec,np.asarray(q),old,records,np.fromfile(root/m['document_to_physical']['file'],dtype='<u4')); deep=[]
    for qi in range(152):
        seen=set(prefix[qi].tolist()); order=np.asarray(prefix[qi].tolist()+[int(x) for x in deep_rows[qi] if int(x) not in seen],dtype=np.uint32)
        if order.size!=args.depth or np.unique(order).size!=order.size: raise ValueError('deep order length/uniqueness failure')
        deep.append(order)
    deep_fp=next(x for x in rec['layouts'] if x['role']=='address_major_fp32')
    deep_validated=[comp.file_record(root,x) for x in [*rec['mappings'],deep_fp,*rec['model']]]
    deep_postings=[phys[int(o):int(o+c)] for o,c in zip(offs,counts)]; deep_doc_address=np.empty(n,dtype=np.int32)
    for address,ids in enumerate(deep_postings): deep_doc_address[ids]=address
    routes['deep-8192']={'order':np.asarray(deep),'postings':deep_postings,'counts':counts,'doc_address':deep_doc_address,'validated_artifacts':deep_validated}
    return routes
def prefix_options(route, qi, teachers):
    options={0:0}; cost=0; seen=0
    teacher_masks={}
    for i,t in enumerate(teachers):
        address=int(route['doc_address'][int(t)])
        teacher_masks[address]=teacher_masks.get(address,0)|(1<<i)
    for a in route['order'][qi]:
        ids=route['postings'][int(a)]; cost+=int(ids.size); gained=teacher_masks.get(int(a),0)
        seen|=gained
        if gained: options[cost]=seen
    return list(options.items())
def prefix_dp(routes,names,qi,teachers,budgets):
    dp={0:(0,[0]*len(names))}
    for ri,name in enumerate(names):
        nxt={}
        for mask,(cost,sel) in dp.items():
            for extra,gain in prefix_options(routes[name],qi,teachers):
                total=cost+extra; nm=mask|gain
                if total<=max(budgets) and (nm not in nxt or total<nxt[nm][0]): s=sel.copy(); s[ri]=extra; nxt[nm]=(total,s)
        dp=nxt
    out=[]
    for b in budgets:
        gain,cost,sel=max((m.bit_count(),c,s) for m,(c,s) in dp.items() if c<=b); out.append({'budget':b,'teacher_count':gain,'recall':gain/len(teachers),'posting_entries':cost,'route_prefix_costs':sel})
    return out
def arbitrary_dp(routes,names,qi,teachers,budgets):
    dp={0:0}
    for name in names:
        addresses=sorted({int(routes[name]['doc_address'][int(t)]) for t in teachers})
        for a in addresses:
            ids=routes[name]['postings'][a]; mask=sum(1<<i for i,t in enumerate(teachers) if int(routes[name]['doc_address'][int(t)])==a)
            c=int(ids.size)
            for old,oc in list(dp.items()):
                nm=old|mask; dp[nm]=min(dp.get(nm,1<<60),oc+c)
    out=[]
    for b in budgets:
        feasible=[(m.bit_count(),c) for m,c in dp.items() if c<=b]
        gain,cost=max(feasible,key=lambda x:(x[0],-x[1])) if feasible else (0,0)
        out.append({'budget':b,'teacher_count':gain,'recall':gain/len(teachers),'posting_entries':cost})
    return out
def main():
    p=argparse.ArgumentParser(); p.add_argument('--thq-manifest',type=Path,required=True); p.add_argument('--r4-manifest',type=Path,required=True); p.add_argument('--r4-root',type=Path,required=True); p.add_argument('--output',type=Path,required=True); p.add_argument('--raw-output',type=Path); p.add_argument('--depth',type=int,default=8192); a=p.parse_args(); frozen=json.loads(a.thq_manifest.read_text()); manifest=json.loads(a.r4_manifest.read_text()); n=int(frozen['documents']); teachers=np.fromfile(Path(frozen['references']['teacher_ids']['path']),dtype='<i8').reshape(152,10); routes=reconstruct(a,frozen,manifest); names=['seed-2026082702','seed-2026082703','deep-8192']; budgets=[20000,50000,100000]; prefix=[]; arbitrary=[]
    input_artifacts={role: validate_external(frozen['references'][role]) for role in ('document_vectors','queries','teacher_ids')}
    for qi in range(152): prefix.extend({'query':qi,**x} for x in prefix_dp(routes,names,qi,np.asarray(teachers[qi]),budgets)); arbitrary.extend({'query':qi,**x} for x in arbitrary_dp(routes,names,qi,np.asarray(teachers[qi]),budgets))
    shallow_miss=set()
    for qi in range(152):
        route_address_sets={name:{int(x) for x in routes[name]['order'][qi]} for name in names[:2]}
        route_address_sets['deep-8192']={int(x) for x in routes['deep-8192']['order'][qi][:1024]}
        for t in teachers[qi]:
            covered=any(int(routes[name]['doc_address'][int(t)]) in route_address_sets[name] for name in names)
            if not covered: shallow_miss.add((qi,int(t)))
    residual=[]
    for qi,t in sorted(shallow_miss):
        ranks=[i+1 for i,ad in enumerate(routes['deep-8192']['order'][qi]) if int(t) in routes['deep-8192']['postings'][int(ad)]]; rank=ranks[0] if ranks else a.depth+1; cumulative=sum(int(routes['deep-8192']['postings'][int(ad)].size) for ad in routes['deep-8192']['order'][qi][:rank]) if ranks else None; residual.append({'query':qi,'teacher':t,'deep_address_rank':rank,'deep_cumulative_posting_entries':cumulative})
    def summ(rows,kind): return [{'kind':kind,'budget':b,'teacher_recall':aggregate([x['recall'] for x in rows if x['budget']==b]),'posting_entries':aggregate([x['posting_entries'] for x in rows if x['budget']==b])} for b in budgets]
    union=load('ub_union','run-r4-seed-union-gate.py')
    jump_caps=[0,2500,5000,10000]; jump_rows=[]
    for cap in jump_caps:
        for qi in range(152):
            jump_routes=[routes['seed-2026082702']['order'][qi], routes['seed-2026082703']['order'][qi], jump_stream(routes['deep-8192'], qi, cap)]
            snapshots=union.merge_snapshot(jump_routes, [routes[name]['postings'] for name in names], [routes[name]['counts'] for name in names], np.asarray(teachers[qi]), budgets, n)
            for row in snapshots: jump_rows.append({'jump_cap_posting_entries':cap,'query':qi,**row})
    jump_summary=[]
    for cap in jump_caps:
        for b in budgets:
            selected=[x for x in jump_rows if x['jump_cap_posting_entries']==cap and x['requested_candidate_budget']==b]
            jump_summary.append({'jump_cap_posting_entries':cap,'budget':b,'query_count':152,**{m:aggregate([float(x[m]) for x in selected]) for m in ('actual_unique_candidates','posting_entries_touched','postings_touched','duplication_ratio','teacher_recall')}})
    raw_path=a.raw_output or a.output.with_name(a.output.stem+'-raw.json')
    raw_payload={'schema_version':1,'prefix_allocation_oracle':prefix,'arbitrary_posting_oracle':arbitrary,'non_leaking_jump_scheduler':jump_rows}
    raw_bytes=json.dumps(raw_payload,indent=2,sort_keys=True).encode() + b'\n'; raw_path.parent.mkdir(parents=True,exist_ok=True); raw_path.write_bytes(raw_bytes)
    out={'schema_version':1,'family':'semantic_r4_fusion_upper_bounds_v1','execution_status':'EXECUTED','production_activation':False,'fixture_manifest_sha256':sha256(a.thq_manifest),'r4_manifest_sha256':sha256(a.r4_manifest),'runner_sha256':sha256(Path(__file__)),'budgets':budgets,'routes':names,'prefix_allocation_oracle':summ(prefix,'teacher_leaking_prefix_dp'),'arbitrary_posting_oracle':summ(arbitrary,'teacher_leaking_arbitrary_posting_dp'),'non_leaking_jump_scheduler':{'caps_posting_entries':jump_caps,'summaries':jump_summary},'residual_diagnostics':{'count':len(residual),'rows':residual},'input_artifacts':{'frozen':input_artifacts,'r4':{name:routes[name]['validated_artifacts'] for name in names}},'raw_output':{'path':str(raw_path),'sha256':hashlib.sha256(raw_bytes).hexdigest(),'bytes':len(raw_bytes)},'protocol':{'teacher_leaking_oracles':True,'teacher_ids_used_for_index':False,'physical_page_bytes':'not measured','jump_scheduler':'deep prefix cap then skip to tail; no teacher IDs used','production_activation':False}}; a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
if __name__=='__main__': main()
