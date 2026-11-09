# Persistent lexical candidate parity (Track C)

Date: 2026-11-07
Initial research base: `main` at `cdd552e96fb093b369443d9f883aa0ca94fe61cf`
Current integration base: `main` at `36577361d1c1b15d92541779de932a9c84220f8c`

## Hypothesis

The durable flat lexical index can provide bounded, typed candidate identities
for the existing revision-bound admission path without becoming a second source
of canonical or publication authority.

## Implementation boundary

`MdbxPersistentLexicalCandidateAdapter` reads one `MdbxPersistentLexicalIndex`
snapshot and emits candidates carrying the knowledge-unit identity, scope,
resource generation, projection kind, and projection version observed by that
snapshot. It does not return canonical text. `admit_persistent_lexical_candidates`
passes the identity and score channels through `RevisionBoundLexicalRetriever`,
then checks the observed resource generation against the admitted manifest.
Posting reads use a bounded prefix API; the candidate limit therefore does not
materialize a complete high-document-frequency posting list. The persistent
flat snapshot itself is still reconstructed in memory by the existing
`read_snapshot()` baseline. The returned `LexicalReadFrontier` is diagnostic
metadata only; generic admission does not trust it as canonical evidence.

The adapter is not enabled by default and does not add a second publication
coordinator or a new resource-revision authority. Metadata filters, empty
queries, and queries over the configured term budget are explicitly reported as
`UnsupportedQuery`. Posting and candidate limits report `Truncated`; callers
must not interpret that response as an exhaustive top-K result.

## Checks

The focused MDBX test uses the same frozen two-unit input for the persistent
index and the dependency-free `FlatLexicalIndex` rebuild control. It verifies:

- candidate membership parity for a shared term (ranking is TF-only here and is
  not claimed to be BM25 parity);
- typed scope/resource-generation and projection-version observations;
- replacement removes the old term and exposes the new unit revision;
- erase removes candidates;
- reopen preserves the current candidate set;
- a bounded one-posting query remains `Complete`.
- missing token statistics and both DF/physical-posting mismatches fail closed;
- short posting-window reads classify as `InconsistentSnapshot`;
- duplicate unit observations and conflicting unit revisions are rejected.

Commands:

```text
cmake -S . -B .build/candidate-mingw -DAGENT_MEMORY_ENABLE_MDBX=ON \
  -DAGENT_MEMORY_MDBX_DEPS_MODE=BUNDLED -DAGENT_MEMORY_BUILD_TESTS=ON \
  -DAGENT_MEMORY_ENABLE_JSON=OFF
cmake --build .build/candidate-mingw --target \
  agent_memory_mdbx_persistent_lexical_candidate_adapter_test \
  agent_memory_revision_bound_retrieval_test -j 2
ctest --test-dir .build/candidate-mingw -R \
  'agent_memory_mdbx_persistent_lexical_candidate_adapter_test|agent_memory_revision_bound_retrieval_test' \
  --output-on-failure
```

Result: 2/2 tests passed on MinGW C++17 with MDBX enabled.

## Limitations and next gates

- The candidate score is term-frequency accumulation, not BM25/BM25+/BM25L
  parity. Ranking differences must be reported until a shared scorer consumes
  the persistent statistics.
- The bounded prefix applies to per-term posting consumption; snapshot
  reconstruction remains an O(N) flat baseline and is not a serving-scale
  memory result.
- Candidate generation and canonical admission are separate reads. The
  generic admission seam is revision-bound but not a shared MDBX transaction;
  snapshot-consistent durable composition remains a follow-up to the existing
  MDBX snapshot retriever.
- The focused test does not claim hydrated source bytes, metadata-filter
  support, phrase/positional search, or production-scale memory/latency.
- No default backend activation or segmented-v2 migration is included.
