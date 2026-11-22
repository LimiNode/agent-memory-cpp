# Historical AMCE pre-merge v1 fixtures

These files preserve the closed experimental AMCE candidate from Draft PR #690
at `4ec7866f31e0d0b2e3d2ca1933b27c3e78f767ec`. They are not the normative
extensible-envelope vectors, are not compiled as current tests, and do not
promise compatibility with persisted records. That candidate was never an
integrated persisted contract.

The original eleven literal hex/SHA-256 vectors remain in the archived test;
the accompanying independent Python oracle reproduces those experimental bytes.
The current oracle and current codec test one directory above own qualification
of the corrected AMCE envelope v1.

`envelope_994b0d7c_goldens.inc.txt` additionally preserves the seventeen
envelope literals at `994b0d7c9ed4534a08586c3dd30c8cb0165e7b80`, before the
2026-11-23 ToolResult pre-freeze correction. Its `257/1` body required an
exact local call and a result artifact. It is historical, not another supported
schema-1 dialect; current literals include unresolved calls and absent bodies.
