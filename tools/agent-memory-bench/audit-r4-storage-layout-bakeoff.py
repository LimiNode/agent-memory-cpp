#!/usr/bin/env python3
"""Fail-closed audit for the variable-count storage layout model."""
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
    for name in ("receipt","raw","runner","candidate-receipt","candidate-raw","candidate-flat"):
        parser.add_argument(f"--{name}",type=Path,required=True)
    args=parser.parse_args(); receipt=json.loads(args.receipt.read_text()); raw=json.loads(args.raw.read_text())
    require(receipt["family"]==raw["family"]=="semantic_r4_storage_layout_bakeoff_v2","family differs")
    require(receipt["raw_sha256"]==sha(args.raw) and receipt["runner_sha256"]==sha(args.runner),"provenance differs")
    candidate_receipt=json.loads(args.candidate_receipt.read_text()); candidate_raw=json.loads(args.candidate_raw.read_text())
    require(receipt["candidate_receipt_sha256"]==sha(args.candidate_receipt) and receipt["candidate_raw_sha256"]==sha(args.candidate_raw),"candidate provenance differs")
    require(candidate_receipt["flat_file"]["sha256"]==sha(args.candidate_flat),"candidate flat provenance differs")
    counts=[int(row["candidate_count"]) for row in candidate_raw["rows"]]; total=sum(counts); require(len(counts)>0 and all(count>=5000 for count in counts),"candidate counts differ")
    require(len(raw["rows"])==8,"row count differs")
    for row in raw["rows"]:
        require(row["records"]==total and row["logical_payload_bytes"]==total*int(row["record_bytes"]),"logical bytes differ")
        require(row["physical_bytes"]>=row["logical_payload_bytes"] and row["pages"]*4096==row["physical_bytes"],"physical bytes differ")
        name=row["representation"]; payload=int(row["record_bytes"])
        if row["layout"]=="flat": pages=math.ceil(sum(counts)*payload/4096); expected=pages*4096
        elif row["layout"]=="page_blocked": pages=sum(math.ceil(count*payload/4096) for count in counts); expected=pages*4096
        elif row["layout"]=="mdbx_blob_model": pages=sum(math.ceil((16+count*payload)/4096) for count in counts); expected=pages*4096
        else:
            records_per_chunk=max(1,(4096-16)//payload); pages=sum(math.ceil(count/records_per_chunk) for count in counts); expected=pages*4096
        require(row["pages"]==pages and row["physical_bytes"]==expected,f"arithmetic differs: {name}/{row['layout']}")
    print(json.dumps({"family":"semantic_r4_storage_layout_bakeoff_audit_v2","status":"PASS","rows":len(raw["rows"]),"queries":len(counts)},sort_keys=True))
if __name__=="__main__":
    try: main()
    except Exception as error: raise SystemExit(f"audit-r4-storage-layout-bakeoff: {error}")
