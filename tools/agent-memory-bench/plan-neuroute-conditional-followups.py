#!/usr/bin/env python3
"""Validate the conditional codec and overcomplete ADC screens."""
import argparse, json
from pathlib import Path

THIS = Path(__file__).resolve().parent

def require(v, m):
    if not v: raise ValueError(m)

def load_contract(path):
    v=json.loads(path.read_text(encoding="utf-8"))
    require(v.get("family")=="neuroute_conditional_representation_followups", "conditional family differs")
    require(v.get("datasets")==["de-25k","fr-25k","ja-25k","de-1m"], "conditional datasets differ")
    require([x["id"] for x in v.get("codec_screen",[])]==["int8_document","int7_document","int6_document","int8_block3x128","int7_block3x128","int8_zigzag_vbyte_control"], "conditional codecs differ")
    require(v.get("overcomplete_screen",{}).get("widths")==[512,768,1024], "conditional widths differ")
    require(all(len(x)==64 for x in v.get("activation",{}).values()), "conditional activation differs")
    return v

def main():
    p=argparse.ArgumentParser(); p.add_argument("--contract",type=Path,default=THIS/"neuroute-conditional-followups.example.json"); a=p.parse_args()
    try:
        c=load_contract(a.contract); print(json.dumps({"codec_rows":4*3*6,"overcomplete_rows":4*3*3,"total_rows":108},indent=2)); return 0
    except (OSError,ValueError,KeyError,json.JSONDecodeError) as e: print(f"plan-neuroute-conditional-followups: {e}"); return 1

if __name__=="__main__": raise SystemExit(main())
