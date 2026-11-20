# R5 closure and combined integration gate

Date: 2026-11-20. Status: IN_PROGRESS; research-only, no production activation.
Integration baseline: `bfa01a22d4091bb5c5f7c17606199fda6342b790`.

## Question and scope

Can the nine independently tested R5 research PRs be assembled without losing
CI gates, source identity, historical evidence or C++17 compatibility?
Separate PR checks do not answer this integration question. This preflight
branch is an evidence assembly, not a tenth production merge candidate.

The exact nine input commits are recorded in
[`r5-integration-inputs.json`](../../tools/agent-memory-bench/r5-integration-inputs.json).
That file is a build-input manifest, not another algorithm registry. The
existing [algorithm registry](2026-11-18-algorithm-research-sweep.md) owns
research decisions and historical disposition.

## Reproduction and accessible artifacts

The source branch is `preflight/r5-closure-integration`. After checkout of the
exact published assembly SHA:

```sh
python -m pip install -r tools/requirements-tools.txt
python -m pip install scipy==1.17.1 scikit-learn==1.9.0 faiss-cpu==1.15.0
python tools/agent-memory-bench/collect-r5-integration-evidence.py --self-test
python tools/agent-memory-bench/collect-r5-integration-evidence.py --audit-assembly
gh workflow run ci.yml --repo LimiNode/agent-memory-cpp --ref preflight/r5-closure-integration
gh workflow run packaging.yml --repo LimiNode/agent-memory-cpp --ref preflight/r5-closure-integration
```

The manual CI event enables the real `Full research CTest`, not the normally
skipped PR lane. The combined workflow also executes all specialized R5 jobs.
The integration collector runs after `CI gate` and full research CTest; it
cannot promote a failed or skipped prerequisite to PASS.

The `r5-combined-integration-<assembly SHA>` Actions artifact contains:

- a machine-readable receipt and SHA-256 sidecar;
- a `git archive` of the exact tested source tree, with its SHA-256;
- downloaded research receipts and sidecars from the same workflow run;
- full configured CTest plan, JUnit XML, `LastTest.log`, and CMake cache;
- byte hashes of every collected artifact and exact unique input blobs.

The collector checks the workflow DAG and each required R5 gate. Unique input
files must match their declared PR commit byte-for-byte as Git blobs. The
three intentionally shared build files are checked by structural audit and
the actual combined configure/build/CTest. The executed JUnit names must match
the complete configured CTest plan; four targeted tests cannot masquerade as
the full suite. Packaging remains a separate
workflow and must be linked separately; this receipt never implies packaging.
The source archive excludes submodule payloads; their pinned commits remain in
the Git tree and receipt. Initialize only the flat dependencies required by
each job. The archive preserves Git blobs, not Windows checkout line endings;
the per-platform research receipts separately bind compiled checkout bytes.

Actions retention is not a permanent evidence release. A later long-lived
archive follows the existing experiment archive policy and requires separate
publication authority. No release or tag is created by this preflight.

The [original local R5 handoff](../../artifacts/r5-closure-handoff/original-handoff.md)
is now preserved as accessible historical material, byte-for-byte (SHA-256
`31c3e1862abb64530e9e4dfb1ed8d5125140920ee220927d9236515cbfbc409c`).
Its previous HEADs, measurements and local 11-test observation retain their
original scope; they are not the acceptance evidence for this closure run.

## Dependency and maintenance matrix

All nine PRs are independently based on the integration baseline. Their build
files overlap, so independence does not mean conflict-free sequential merges.

| PR | Role retained | Overlap / maintenance boundary | Disposition before closure CI |
| --- | --- | --- | --- |
| #640 | Registry and evidence disposition | Documentation gate; no second global registry | MERGE_CANDIDATE pending exact-head CI and combined run |
| #641 | Feasibility history, including Faiss version boundaries | No duplicate executable E-means study | MERGE_CANDIDATE after review |
| #633 | Admission kernel/scaling controls | #666 owns matched full-path qualification | MERGE_CANDIDATE after review |
| #635 | Numerical reduction controls | #664 owns actual-index Decimal qualification | MERGE_CANDIDATE after review |
| #638 | IVF study scaffold and historical Lloyd control | #664 owns corrected executable E-means comparison | MERGE_CANDIDATE after review |
| #642 | Selection-only kernels | #665 owns actual lexical-search qualification | MERGE_CANDIDATE after review |
| #664 | Actual vector index, independent oracle, CPU E-means | Historical v1 disposition is executable | MERGE_CANDIDATE pending combined run |
| #665 | Full lexical path and direct stage profiling | Generated private class, not a production selector API | MERGE_CANDIDATE pending combined run |
| #666 | Matched full retrieve path | Generated private admission variants, not a dispatcher | MERGE_CANDIDATE pending combined run and independent review |

`MERGE_CANDIDATE` is not an APPROVED review. Recommended initial order after
independent review is #641 -> #633 -> #635 -> #638 -> #642 -> #664 ->
#665 -> #666 -> #640. The registry is last so it indexes the completed owner
corrections without presenting its external pointers as replayed PRIMARY data.
Before each merge recheck the actual main, source-pinned transforms,
conflicts and exact-head CI. Preserve independent scopes during any restack;
use expected-head safety if history rewriting is required. Never merge this
assembled evidence branch as a shortcut around review of the nine PRs.

## Bounded decisions and remaining checks

- Compensated production cosine: KEEP CURRENT. The independent Decimal oracle
  remains independent from C++ production regression tests.
- CPU E-means v2: EXECUTED synthetic smoke, production adoption DEFER. The
  representative-corpus experiment is NOT_EXECUTED unless a suitable frozen,
  leakage-free corpus can actually be used without expanding dependencies.
- Lexical selection: CONDITIONAL, no universal heap adoption. Direct phase
  timing and varied workloads are corrective work, not production activation.
- Indexed reranker admission: no real request-frequency distribution is
  inferred from chosen fixture sizes. KEEP CURRENT until workload evidence
  justifies a dispatcher and its maintenance cost.
- Authorization/freshness belong to the established retrieval boundary, not
  automatically to the lexical index. An unavailable integration is explicitly
  NOT_EXECUTED, not replaced by an equality check on metadata.

Final results, exact run links and updated dispositions are appended after
the corrective commits and actual combined CI have completed.

The preliminary Windows/GNU combined suite completed 339/342 tests. Two
stage-local THQ self-tests required SciPy/scikit-learn and Faiss imports absent
from the initially selected Python interpreter. The full research CI lane
now installs these pinned, research-only dependencies explicitly. The third
failure exposed the existing native MIH reader's v1 construction guard against
the frozen v2 conformance fixture; it requires a separate semantic correction,
not a skipped test or a changed golden digest. This preliminary run is not
closure acceptance evidence.

All four downloaded Linux/Windows closure raw receipts from owner runs
[#665 CI 44644634821](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44644634821)
and [#666 CI 44645703444](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44645703444)
passed a separate source/fixture/generated manifest and sidecar replay. Exact
synthetic merge checkouts were reconstructed with LF for Linux and CRLF for
Windows; manually edited local trees are not assumed byte-identical to a CI
checkout. Expected PR HEADs remain separate from the compiled merge SHAs.
This verifies the downloaded owner evidence, not a not-yet-executed combined
CI run. The registry preserves its seven historical rows and adds these four
reviewed external identities without relabeling any historical measurement.

## R6 preparation only

R6 experiments are NOT_EXECUTED. Start requires R5 closure, approved R5 tooling
merged into main, the actual post-merge SHA, successful post-merge CI and
accessible R5 artifacts. Merge, defaults, persisted formats and production
implementation remain separate decisions.

Do not reimplement an existing family under a new label. In particular:

| Requested family | Existing control to inspect before a new hypothesis |
| --- | --- |
| Packed scalar/LUT ADC | [native packed THQ ADC](2026-09-11-native-packed-thq-adc-gate.md), [packed TQ1/PQ8 native serving](2026-09-26-packed-tq1-pq8-serving-closure.md) |
| PRR / PR-IVF | Existing prototype-IVF studies and [fresh route decomposition](2026-10-03-fresh-prototype-ivf.result.json); PRR terminology alone is not a new algorithm |
| Exact lexical pruning | R5 stage profile first; no WAND without safe BM25 bounds and demonstrated scoring cost |
| MIH probe-state variants | Existing native MIH controls; compare a new duplicate-state mechanism, not another wrapper |
| Hybrid fusion / adaptive budgets | Existing RRF implementation and sealed qrels coverage; no held-out tuning or synthetic quality generalization |

After the start gates pass, select at most three concurrent hypotheses with
a named baseline, falsifier and stop rule. Prioritize genuinely new packed
layout/scoring or occupancy-aware routing ablations under matched bytes and
scanned-document budgets; defer quality conclusions if a sealed corpus is
unavailable. This is a plan, not a claim that R6 has been executed.
