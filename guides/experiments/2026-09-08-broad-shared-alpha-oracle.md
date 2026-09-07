# Broad shared-alpha anchor oracle closure

Date: 2026-09-08.

## Question

The earlier quota replay was exhaustive only inside a global-cosine top-eight
anchor screen.  This diagnostic asks whether a wider top-64 screen hides a
materially better shared-alpha anchor family before the line is archived.

## Setup

The replay used the authoritative seed `2026082701` mapping: 454,322 K8
prototypes, 65,108 R4 addresses, one full document-to-address assignment and
exact E5 top-10 document targets.  Eight queries were evaluated in two
four-query runs.  For each query, shared-alpha scores were computed for the
global cosine top-64 prototypes, with 32-prototype quota steps.  The eight
anchors with the best downstream single-anchor scores were retained and quota
allocation over up to four retained anchors was exhaustive.  The retained
selection and target masks are privileged diagnostics, not runtime behavior.

## Results

| total prototype budget | top-8 single | top-64 best single | retained quota oracle |
|---:|---:|---:|---:|
| 1,024 | .763 | .800 | .800 |
| 2,048 | .813 | .838 | .863 |
| 4,096 | .875 | .888 | — |
| 8,192 | .913 | .963 | — |

The top-64 result is strongly query-dependent: the worst document survival
was `.40/.50/.60/.80` at budgets `1024/2048/4096/8192`.  At `P=1024`, only
three of eight queries retained all ten targets; at `P=2048`, four of eight
did.  The quota oracle adds only `.025` over the top-eight single-anchor
screen at `P=2048` and does not improve the top-64 best single result at
`P=1024`.

## Interpretation

The top-8 screen was a real limitation: a wider global screen can expose a
better anchor and raises the privileged mean at practical budgets.  It still
does not approach the `.995` document gate at `P=1024–2048`; quality reaches
that neighborhood only at the much larger `P=8192` diagnostic budget, with a
long tail of failures.  This keeps the specific architecture

```text
shared-alpha prototype ranking -> small anchor union -> quota probing
```

out of the primary retrieval path.  It does not prove that every possible
joint or non-ray directional geometry is impossible.  A learned selector is
also not justified yet: its candidate-family ceiling is still below the gate,
and the reported screen is target-leaking.

## Limitations and follow-up

The sample is only eight queries, so the curve is closure evidence rather than
a corpus-wide confidence interval.  `P=4096/8192` is included to expose the
quality-versus-expansion frontier, not as a serving recommendation.  Any
future reopening must introduce a fundamentally different joint routing
geometry or demonstrate a materially higher global anchor ceiling on a held
out split.

Runner: `tools/agent-memory-bench/evaluate-broad-shared-alpha-oracle.py`.
Raw reports are retained under `tmp/broad-anchor-oracle-4q.json` and
`tmp/broad-anchor-oracle-global-q4-7.json`.
