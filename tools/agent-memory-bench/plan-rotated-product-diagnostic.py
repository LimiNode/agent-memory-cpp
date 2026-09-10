#!/usr/bin/env python3
"""Validate the small rotated-product diagnostic matrix."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from typing import Any
THIS=Path(__file__).resolve().parent; FAMILY="rotated_product_locator_diagnostic_v1"
def require(v: bool, m: str)->None:
    if not v: raise ValueError(m)
def sha256(p: Path)->str: return hashlib.sha256(p.read_bytes()).hexdigest()
def load_contract(path: Path)->dict[str,Any]:
    v=json.loads(path.read_text(encoding="utf-8")); require(v.get("schema_version")==1 and v.get("family")==FAMILY and v.get("purpose")=="small_calibration_only_test_of_orthogonal_rotation_before_product_partitioning", "rotated product contract identity differs")
    require(v.get("faiss_version")=="1.13.2" and v.get("parent_product_evidence_sha256")=="af8a6864f59d31fa098cad7e9ef4f1c34911232ed57c82e69085630fca3b8d03", "rotated product parent evidence differs")
    require(v.get("scale")=={"id":"es-1m","documents":1000000,"input_manifest_sha256":"697f81bc66b37feb47b413fa168f4ae5efd030b9dbbaeb8d0c67ac8d224a9ae7","evaluation_manifest_sha256":"616e70a3d0e21a561967b382bebc463b10038280f02ca77477b0b01331c73536"} and v.get("treatments")==["raw_contiguous_e5_product_control","opq_rotated_e5_product"] and v.get("implicit_cell_budgets")==[16384,65536] and v.get("target_candidate_fraction")==.05, "rotated product matrix differs")
    require(v.get("opq")=={"subquantizers":8,"bits_per_subquantizer":2,"iterations":25,"seed":20260828,"pq_clustering_seed":20260828,"faiss_threads":1,"training_source":"frozen_train_e5_vectors_only"} and v.get("cascade")=={"hamming_limit":768,"adc_limit":256,"exact_limit":256,"oracle_k":10} and v.get("exploratory_gate")=={"minimum_e5_oracle_survival_after_adc":.70,"meaning":"below_this_gate_freeze_rotated_product_branch"} and v.get("confirmation")=="forbidden" and v.get("library_dependency")=="forbidden_faiss_is_external_benchmark_only", "rotated product scope differs")
    return v
def main()->int:
    p=argparse.ArgumentParser(); p.add_argument("--contract",type=Path,default=THIS/"rotated-product-diagnostic.example.json");p.add_argument("--self-test",action="store_true");a=p.parse_args()
    try:
        c=load_contract(a.contract); rows=[{"treatment":t,"implicit_cell_budget":b} for t in c["treatments"] for b in c["implicit_cell_budgets"]];require(len(rows)==4,"rotated product row count differs");out={"schema_version":1,"family":"rotated_product_locator_diagnostic_plan_v1","contract_sha256":sha256(a.contract),"row_count":len(rows),"rows":rows};print("rotated product diagnostic planner self-test passed" if a.self_test else json.dumps(out,indent=2,sort_keys=True));return 0
    except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError) as e: print(f"plan-rotated-product-diagnostic: {e}");return 1
if __name__=="__main__": raise SystemExit(main())
