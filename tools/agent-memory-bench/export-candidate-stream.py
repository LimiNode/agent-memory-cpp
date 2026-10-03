#!/usr/bin/env python3
"""Export a query-aligned int32 candidate stream as canonical document IDs."""
from __future__ import annotations
import argparse, hashlib, json, struct
from pathlib import Path

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--stream", type=Path)
    parser.add_argument("--document-ids", type=Path)
    parser.add_argument("--query-ids", type=Path)
    parser.add_argument("--width", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_test:
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs.jsonl").write_text('{"id":"10"}\n{"id":"20"}\n', encoding="utf-8")
            (root / "queries.jsonl").write_text('{"id":"q"}\n', encoding="utf-8")
            (root / "stream.i4").write_bytes(struct.pack("<ii", 1, 0))
            main_args = ["--stream", str(root / "stream.i4"), "--document-ids", str(root / "docs.jsonl"),
                         "--query-ids", str(root / "queries.jsonl"), "--width", "2", "--output", str(root / "out.json")]
            import sys
            saved = sys.argv
            sys.argv = [saved[0], *main_args]
            try:
                main()
            finally:
                sys.argv = saved
        print("export-candidate-stream self-test PASS")
        return
    if not all((args.stream, args.document_ids, args.query_ids, args.width, args.output)):
        parser.error("--stream, --document-ids, --query-ids, --width and --output are required")
    if args.width <= 0:
        parser.error("--width must be positive")
    ids = [json.loads(line)["id"] for line in args.document_ids.read_text(encoding="utf-8").splitlines()]
    queries = [json.loads(line)["id"] for line in args.query_ids.read_text(encoding="utf-8").splitlines()]
    raw = args.stream.read_bytes()
    expected = len(queries) * args.width * 4
    if len(raw) != expected:
        raise SystemExit(f"candidate stream bytes differ: {len(raw)} != {expected}")
    values = struct.unpack("<" + "i" * (len(raw) // 4), raw)
    rows = []
    for position, query_id in enumerate(queries):
        start = position * args.width
        positions = list(values[start:start + args.width])
        if len(set(positions)) != len(positions) or any(value < 0 or value >= len(ids) for value in positions):
            raise SystemExit(f"invalid candidate row at query position {position}")
        rows.append({"query_position": position, "query_id": query_id,
                     "candidate_ids": [ids[value] for value in positions]})
    result = {"schema_version": 1, "family": "candidate_stream_export_v1",
              "status": "EXECUTED", "candidate_width": args.width,
              "rows": rows, "source": {"stream_sha256": sha256(args.stream),
              "document_ids_sha256": sha256(args.document_ids),
              "query_ids_sha256": sha256(args.query_ids)}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"query_count": len(rows), "candidate_width": args.width,
                      "output_sha256": sha256(args.output)}, sort_keys=True))

if __name__ == "__main__":
    main()
