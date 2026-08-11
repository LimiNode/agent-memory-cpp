#!/usr/bin/env python3
"""Write a portable evidence archive for the native MIH rerank-cost run."""
from __future__ import annotations
import argparse, hashlib, json, sys, zipfile
from pathlib import Path

def digest(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def require(value: bool, message: str) -> None:
    if not value: raise ValueError(message)

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument('--report', type=Path, required=True); parser.add_argument('--input-manifest', type=Path, required=True); parser.add_argument('--config', type=Path, required=True); parser.add_argument('--output', type=Path, required=True); args=parser.parse_args()
    try:
        report=json.loads(args.report.read_text(encoding='utf-8')); config=json.loads(args.config.read_text(encoding='utf-8'))
        require(report.get('family')=='mih_native_hot_path_v1' and report.get('query_count')==1252 and report.get('repeat_count')==7 and report.get('hamming_limit')==768, 'native report contract is invalid')
        require(config.get('exact_rerank_limits')==[64,128,256] and report.get('binary_adc_ms_per_query_median',{}).keys()=={'64','128','256'} and report.get('exact_rerank_ms_per_query_median',{}).keys()=={'64','128','256'}, 'K2 timing grid is invalid')
        require(report['exact_rerank_ms_per_query_median']['64'] < report['exact_rerank_ms_per_query_median']['256'], 'exact rerank order is invalid')
        files=[(args.report,'bundle/report.json'),(args.input_manifest,'bundle/input-manifest.json'),(args.config,'bundle/config.json')]
        for name in ('mih_native_hot_path.cpp','materialize-mih-storage-input.py','mih-native-hot-path-32x8.example.json'):
            path=Path(__file__).with_name(name); files.append((path,f'bundle/sources/{name}'))
        manifest={'schema_version':1,'family':'mih_native_rerank_cost_evidence_v1','entries':[{'path':name,'sha256':digest(path),'size':path.stat().st_size} for path,name in files]}
        manifest['bundle_root_sha256']=hashlib.sha256(json.dumps(manifest['entries'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        temp=args.output.with_suffix('.manifest.json'); temp.write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n',encoding='utf-8',newline='\n'); files.insert(0,(temp,'bundle/evidence-bundle-manifest.json'))
        with zipfile.ZipFile(args.output,'w',zipfile.ZIP_DEFLATED) as archive:
            for path,name in files: archive.write(path,name)
        with zipfile.ZipFile(args.output) as archive: require(all('\\' not in name for name in archive.namelist()),'archive paths are not portable')
        print(json.dumps({'sha256':digest(args.output),'bundle_root_sha256':manifest['bundle_root_sha256']},sort_keys=True))
    except (OSError,ValueError,json.JSONDecodeError) as error: print(f'write-mih-rerank-cost-evidence: {error}',file=sys.stderr); return 1
    return 0
if __name__=='__main__': raise SystemExit(main())
