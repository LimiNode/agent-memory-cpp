# Lexical research runner

This directory is an offline research harness. It is intentionally outside the
C++ library target and has no third-party Python dependency.

Run the committed smoke fixture:

```powershell
py -3 tools/lexical-research/run_lexical_protocol.py `
  --dataset tools/lexical-research/fixtures/lexical_protocol_smoke.json `
  --self-test
```

Write a compact JSON receipt for a local run:

```powershell
py -3 tools/lexical-research/run_lexical_protocol.py `
  --dataset tools/lexical-research/fixtures/lexical_protocol_smoke.json `
  --repeats 5 `
  --output .artifacts/lexical-protocol-smoke.json
```

The runner currently implements only the protocol BM25 control. Its tokenizer
and scorer are a portable fixture implementation, not evidence that the Python
path is byte-for-byte equivalent to `ExactLexicalIndex`. PR3 must add an
explicit parity check against the C++ baseline before using it for algorithmic
claims. Model inference, WAND/BMW traversal, compressed postings, and production
MDBX are intentionally out of scope.
