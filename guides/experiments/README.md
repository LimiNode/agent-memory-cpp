# Experiment Notes

`guides/experiments/` stores human-readable experiment records. These notes are
not raw benchmark dumps; they are compact research logs that explain why a run
was performed, what was expected, what happened, and what should be checked
next.

## When to write or update a note

Create or update an experiment note when a PR:

- tests a hypothesis;
- compares algorithms, encoders, indexes, storage layouts, or benchmark
  methodology;
- produces benchmark numbers that influence the roadmap;
- changes the interpretation of earlier benchmark results.

Create one note per research line, not per command invocation. If a later PR
continues the same question, append a new dated section instead of overwriting
earlier results.

## Required contents

Each note should include:

- date and PR/commit context;
- question or hypothesis;
- setup and command/config references;
- expected result;
- actual result, preferably with compact tables;
- interpretation;
- limitations and threats to validity;
- possible improvements;
- follow-up checks.

## Raw artifact policy

Do not commit large generated JSON reports by default. Commit only:

- small, stable smoke fixtures;
- example configs;
- manually curated tables or short excerpts needed to support the note.

When raw reports matter, store the command, config path, output path, git head,
and enough identifying metadata for reproduction. If a future PR needs
long-term raw artifact retention, add an explicit policy for artifact location,
size budget, and cleanup before committing dumps.

## Evidence releases

Long-lived compact research evidence belongs in GitHub Releases, not in Git
and not in the short-lived Actions artifact store. Library releases use normal
semantic tags and names such as `v0.2.0` / `agent-memory-cpp 0.2.0`. Research
releases must use an evidence namespace and a visually distinct title, for
example `evidence/mih-banding-v3` / `[Evidence] MIH banding cascade v3`.

An evidence release is published only after its archive validator succeeds.
It must state its archive SHA-256, internal bundle-root SHA-256, target commit,
and artifact scope. Draft releases are review staging only and must not be
called public in experiment notes. Do not commit large ZIPs or generated DB
files; retain a compact manifest and a stable release link instead. If evidence
outgrows practical GitHub Release size or count, publish it to an archival
service and retain the manifest plus external link here.

## Timing methodology

Experiment notes must distinguish:

- data generation;
- exact baseline build and query timing;
- encoder training/cold-start timing;
- binary materialization/build timing;
- query encoding;
- candidate search;
- exact rerank;
- process-wide memory high-water marks.

Timing values from a single local run are directional. Treat them as stable
benchmark evidence only after the harness uses repeated runs, warm-up rules,
fixed environment notes, and preserved raw outputs.
