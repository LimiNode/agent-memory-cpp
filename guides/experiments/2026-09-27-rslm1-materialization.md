# 2026-09-27 RSLM1 source-bound materialization

This is a bounded persisted-code gate for the official RSLM1 codec. It is not a
quality or serving claim.

## Setup

- runner: `materialize-thq-rslm-faithful-candidates.py`
- reference: `rslm-faithful-reference.py`
- source bundle: canonical DE-1M documents (`1,000,000 x 384` FP32) and the
  canonical 25k training vectors;
- shell: frozen THQ4 candidate stream, 152 rows, 100-byte records, with
  `463,258` unique document IDs;
- metric contract: cosine serving diagnostic and paper-faithful IP control;
- codec: official 4D `C4D`, 4-bit symbol per 4 coordinates, 48 symbol bytes,
  2-byte inner UE7M9 scale, and 2-byte relative outer scale.

## Evidence

The materializer completed and persisted RSLM1 symbols, inner scales, outer
scales, document IDs, and THQ4 centroids. The independent sample replay and
source-binding audit passed:

```text
status: PASS
source_binding: true
candidate_stream_replay: true
sample_replay: true
codec_widths: [1]
THQ4 + RSLM1 side: 148 B/document
```

Key hashes (artifact root outside Git):

| artifact | SHA-256 |
| --- | --- |
| `materialization.raw.json` | `713f7de81bfe193887ca543770c8f0b2e7ef5c119fb0430a04450e34aea53c61` |
| `materialization.audit.json` | `12d581afeb2c9067d9053b22a66206e2cda6a9ce3df829616b2fedf4c795b3a5` |
| `rslm1.symbols.u8` | `ee84f3d9d93987e8800b56479f31bac4399d4163f225a0acb2ba8f40a0decc9f` |
| `rslm1.inner-scale.u16` | `6238248d7f262ce4e67d8ac71c976bf3a9b116b68ecbd9910ff83a5d258a346d` |
| `rslm1.outer-scale.u16` | `22a0569e80008198352baed333d57d555fbab00cdbb7570a1363c9b3319cc02c` |

## Interpretation and limits

The RSLM1 implementation and persisted-code path are now covered by the same
official-source contract as RSLM2/3/4. This closes the missing RSLM1
implementation/materialization gate, not the complete fidelity program. No
RSLM1 nDCG, teacher-overlap, native latency, or production-selection claim is
made here: the full matched quality runner still requires the canonical 152
query/qrels/teacher binary split and an independent score replay.

The artifact directory is
`E:\\_repoz\\agent-memory-workspaces\\rslm1-faithful-closure-v1`.
