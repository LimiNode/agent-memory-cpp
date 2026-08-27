#!/usr/bin/env python3
"""Validate the predeclared dynamic false-positive v3 protocol."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from typing import Any
THIS = Path(__file__).resolve().parent
def require(value: bool, message: str) -> None:
    if not value: raise ValueError(message)
def load_contract(path: Path) -> dict[str, Any]:
    value=json.loads(path.read_text(encoding="utf-8")); require(value.get("schema_version")==1 and value.get("family")=="neuroute_dynamic_false_positive_v3", "v3 family differs")
    dataset=value.get("dataset",{}); require(dataset.get("id")=="frozen_de_25k_v1" and dataset.get("documents")==25000 and dataset.get("queries")==305 and dataset.get("dimension")==384 and all(isinstance(dataset.get(k),str) and len(dataset[k])==64 for k in ("prepared_manifest_sha256","e5_manifest_sha256","input_manifest_sha256")), "v3 dataset differs")
    require(value.get("partitions")=={"algorithm":"sha256_utf8_order_v1","prefix_utf8":"neuroute-v3-de-v1\0","training":153,"configuration_selection":76,"internal_evaluation":76,"internal_evaluation_may_not_select":True}, "v3 partitions differ")
    encoder=value.get("encoder",{}); require(encoder=={"input_dimensions":384,"hidden_dimensions":[96,64],"bits":12,"seeds":[2026082701,2026082702,2026082703],"epochs":80,"batch_size":512,"training_query_batch_size":128,"learning_rate":.001,"weight_decay":.0001,"torch_threads":18}, "v3 encoder differs")
    require(value.get("positive_geometry")=={"document_neighbours":16,"query_document_neighbours":10,"pair_slot":"epoch_modulo_neighbour_count_v1","loss":"source_cosine_to_normalized_latent_cosine_mse_v1"}, "v3 positive geometry differs")
    dynamic=value.get("dynamic_false_positives",{}); require(dynamic=={"treatments":["positive_only_control","dynamic_false_positive"],"warmup_epochs":20,"remine_epochs":[20,40,60],"latent_neighbour_pool":32,"selected_e5_farthest":4,"pair_slot":"epoch_modulo_selected_false_positives_v1","margin":.05,"loss":"relu_latent_cosine_minus_source_cosine_minus_margin_squared_v1","weight":1.0}, "v3 dynamic negatives differ")
    require(value.get("routing")=={"document_placement":"median_threshold_single_address_v3","query_order":"independent_logit_best_first_v2","configuration_frontier_probes":[64,128,256,512],"headline_probes":512,"candidate_mass_target":.1}, "v3 routing differs")
    require(value.get("cascade")=={"oracle_k":10,"hamming_limit":768,"adc_limit":256,"exact_limit":256}, "v3 cascade differs")
    gates=value.get("gates",{}); require(gates.get("mechanism_minimum_survival_gain")==.05 and gates.get("architecture_minimum_survival_gain")==.03 and gates.get("maximum_ndcg_loss")==.01 and gates.get("bootstrap_resamples")==10000 and gates.get("bootstrap_seed")==2026082799, "v3 gates differ")
    return value
def plan(value: dict[str, Any]) -> list[dict[str, Any]]: return [{"treatment":t,"seed":s,"bits":12} for t in value["dynamic_false_positives"]["treatments"] for s in value["encoder"]["seeds"]]
def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--contract",type=Path,default=THIS/"neuroute-dynamic-false-positive-v3.example.json"); args=parser.parse_args()
    try: print(json.dumps({"schema_version":1,"family":"neuroute_dynamic_false_positive_v3_plan","rows":plan(load_contract(args.contract))},indent=2,sort_keys=True)); return 0
    except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError) as error: print(f"plan-neuroute-dynamic-false-positive-v3: {error}",file=sys.stderr); return 1
if __name__=="__main__": raise SystemExit(main())
