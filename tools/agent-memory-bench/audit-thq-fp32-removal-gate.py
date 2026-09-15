#!/usr/bin/env python3
"""Fail-closed audit for the THQ versus compact-reranker gate."""
from __future__ import annotations
import argparse, hashlib, json, math
from pathlib import Path
def require(value: bool, message: str) -> None:
    if not value: raise RuntimeError(message)
def sha(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1<<20),b""): digest.update(chunk)
    return digest.hexdigest()
def main() -> None:
    parser=argparse.ArgumentParser()
    for name in ("receipt","raw","runner","thq-manifest","candidate-receipt","candidate-raw","candidate-flat"):
        parser.add_argument(f"--{name}",type=Path,required=True)
    args=parser.parse_args(); receipt=json.loads(args.receipt.read_text()); raw=json.loads(args.raw.read_text())
    require(receipt["family"]==raw["family"]=="semantic_thq_fp32_removal_gate_v1","family differs")
    require(receipt["execution_status"]=="EXECUTED" and not receipt["production_activation"],"status differs")
    require(receipt["raw_output"]["sha256"]==sha(args.raw) and receipt["runner_sha256"]==sha(args.runner),"runner/raw provenance differs")
    require(receipt["thq_manifest_sha256"]==sha(args.thq_manifest) and receipt["candidate_receipt_sha256"]==sha(args.candidate_receipt) and receipt["candidate_raw_sha256"]==sha(args.candidate_raw) and receipt["candidate_flat_sha256"]==sha(args.candidate_flat),"input provenance differs")
    require(raw.get("protocol", {}).get("scope") == "full-candidate diagnostic; no THQ shortlist stage", "scope differs")
    rows=raw["rows"]; names={"thq_thermometer","packed_ordinal","int8_reranker","int4_reranker","fp32_exact"}
    require(len(rows)==152*5 and {row["representation"] for row in rows}==names,"row matrix differs")
    by_query={}
    for row in rows:
        query=int(row["query"]); name=row["representation"]; by_query.setdefault(query,{})[name]=row
        require(0<=query<152 and int(row["candidate_count"])>=5000 and 0<=float(row["exact_top10_overlap"])<=1 and 0<=float(row["teacher_top10_recall"])<=1 and 0<=float(row["candidate_teacher_survival"])<=1 and 0<=float(row["qrels_ndcg10"])<=1 and int(row["rank_inversions_top256"])>=0,"metric range differs")
        expected={"thq_thermometer":144,"packed_ordinal":96,"int8_reranker":388,"int4_reranker":196,"fp32_exact":1536}[name]
        require(int(row["payload_bytes_per_document"])==expected,"payload size differs")
    require(len(by_query)==152 and all(set(group)==names for group in by_query.values()),"query matrix differs")
    for query,group in by_query.items():
        thermometer=group["thq_thermometer"]; ordinal=group["packed_ordinal"]
        for field in ("candidate_count","candidate_teacher_survival","exact_top10_overlap","teacher_top10_recall","qrels_ndcg10","rank_inversions_top256"):
            require(math.isclose(float(thermometer[field]),float(ordinal[field]),abs_tol=1e-12),f"ordinal parity differs: {query}/{field}")
        require(group["fp32_exact"]["exact_top10_overlap"]==1.0 and group["fp32_exact"]["rank_inversions_top256"]==0,"FP32 self parity differs")
    print(json.dumps({"family":"semantic_thq_fp32_removal_gate_audit_v1","status":"PASS","rows":len(rows)},sort_keys=True))
if __name__=="__main__":
    try: main()
    except Exception as error: raise SystemExit(f"audit-thq-fp32-removal-gate: {error}")
