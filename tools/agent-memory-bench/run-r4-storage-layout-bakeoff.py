#!/usr/bin/env python3
"""Compare persistent-layout models using whole-posting candidate counts."""
from __future__ import annotations
import argparse, hashlib, json, math
from pathlib import Path
import numpy as np

PAGE=4096; RECORD_THQ=144; RECORD_ID=4
def require(value: bool, message: str) -> None:
    if not value: raise RuntimeError(message)
def sha(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1<<20),b""): digest.update(chunk)
    return digest.hexdigest()
def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--candidate-flat",type=Path,required=True)
    parser.add_argument("--candidate-receipt",type=Path,required=True)
    parser.add_argument("--candidate-raw",type=Path,required=True)
    parser.add_argument("--output-root",type=Path,required=True)
    args=parser.parse_args()
    receipt=json.loads(args.candidate_receipt.read_text()); raw=json.loads(args.candidate_raw.read_text())
    require(receipt["raw_sha256"]==sha(args.candidate_raw),"candidate raw provenance differs")
    require(receipt["flat_file"]["sha256"]==sha(args.candidate_flat),"candidate flat provenance differs")
    rows=raw["rows"]; counts=[int(row["candidate_count"]) for row in rows]; total=sum(counts); require(all(count>=5000 for count in counts),"whole-posting counts required")
    expected_bytes=total*(RECORD_ID+RECORD_THQ); require(receipt["flat_file"]["bytes"]==expected_bytes,"candidate byte count differs")
    mapped=np.memmap(args.candidate_flat,mode="r",dtype=np.uint8,shape=(total,RECORD_ID+RECORD_THQ))
    ids=np.frombuffer(np.asarray(mapped[:,:RECORD_ID]).tobytes(),dtype="<i4"); require(np.all(ids>=0),"candidate IDs differ")
    args.output_root.mkdir(parents=True,exist_ok=True); output_rows=[]
    for name,payload in (("thermometer",RECORD_ID+RECORD_THQ),("packed_ordinal",RECORD_ID+96)):
        logical=total*payload; flat_pages=math.ceil(logical/PAGE); offset=0
        blocked_pages=blob_pages=chunk_pages=0; records_per_chunk=max(1,(PAGE-16)//payload)
        for count in counts:
            blocked_pages+=math.ceil(count*payload/PAGE)
            blob_pages+=math.ceil((16+count*payload)/PAGE)
            chunk_pages+=math.ceil(count/records_per_chunk)
            offset+=count
        for layout,pages,overhead in (("flat",flat_pages,0),("page_blocked",blocked_pages,0),("mdbx_blob_model",blob_pages,len(counts)*16),("mdbx_chunk_model",chunk_pages,chunk_pages*16)):
            physical=pages*PAGE
            output_rows.append({"representation":name,"layout":layout,"records":total,"record_bytes":payload,
                                "logical_payload_bytes":logical,"physical_bytes":physical,
                                "pages":pages,"padding_bytes":physical-logical,"key_overhead_bytes":overhead,
                                "records_per_chunk":records_per_chunk if layout=="mdbx_chunk_model" else None,
                                "candidate_count_min":min(counts),"candidate_count_mean":float(np.mean(counts)),
                                "candidate_count_max":max(counts)})
    raw_out={"schema_version":2,"family":"semantic_r4_storage_layout_bakeoff_v2","rows":output_rows,
             "input":{"path":str(args.candidate_flat),"sha256":sha(args.candidate_flat),"records":total,
                      "query_count":len(rows),"candidate_counts":counts}}
    raw_path=args.output_root/"storage-bakeoff.raw.json"; raw_path.write_text(json.dumps(raw_out,indent=2)+"\n")
    (args.output_root/"storage-bakeoff.receipt.json").write_text(json.dumps({
        "family":raw_out["family"],"execution_status":"EXECUTED","production_activation":False,
        "raw_sha256":sha(raw_path),"runner_sha256":sha(Path(__file__)),"candidate_receipt_sha256":sha(args.candidate_receipt),
        "candidate_raw_sha256":sha(args.candidate_raw),"input_sha256":sha(args.candidate_flat),"rows":output_rows},indent=2)+"\n")
if __name__=="__main__": main()
