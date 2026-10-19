# M0 compact durable fencing token proposal

This is the implementation contract for the compact fencing-token follow-up to
the importer root-cause investigation. The full serialized intent remains the
recovery record; the token is the hot-path admission guard.

## Contract

Keep the existing serialized intent as the recovery record, including the full
resource body. Add a separate fixed-size token value keyed by `ResourceId`:

```text
token = version || fencing_epoch || owner_id || sha256(serialized_intent)
```

The implementation stores a fixed 32-byte SHA-256 digest of a versioned domain
string and the serialized intent. It therefore changes whenever owner, epoch,
lease, publication profile, or resource request changes. The token is an
admission guard, not a replacement recovery payload. Existing v4 readers continue to recover from the intent value. A writer that does not
understand the token must be rejected by an explicit schema/version marker;
there is no rolling compatibility promise with a writer that can update only
the old intent table.

## Atomic operations

1. Claim and takeover write the intent, token, scope binding, and epoch in one
   writable transaction.
2. Every guarded lexical, raw-unit, and manifest transaction reads the token
   inside that same transaction and compares the expected epoch/digest.
3. Release deletes the intent and token only when both still match the expected
   token; otherwise it fails closed.

The token-aware implementation checks the compact value in the same writable
transaction as each guarded publication. The raw KnowledgeUnit store has a
dedicated guarded `create_or_get_unit` entry point; the existing unguarded API
is unchanged for non-importer callers. A local cached token or an end-only
check is not equivalent and must not be substituted.

## Recovery and crash cases

The negative test matrix must cover:

- crash after intent/token claim;
- crash after lexical publication;
- crash after a raw unit commit;
- takeover between the pre-write and post-write guard;
- stale release after a successful takeover;
- token digest mismatch with an unchanged epoch;
- missing token for an otherwise valid legacy intent;
- reopen with an orphan token and no intent.

Recovery rejects a legacy pending intent until an offline upgrade has created
and verified its token. It never silently reconstructs token state while an old
writer may still be active. Orphan tokens, missing tokens, digest mismatches,
and unsupported schema markers fail closed before automatic recovery starts.

## Migration and compatibility

The current v4 intent remains readable. Enabling token-guarded publication
requires an environment marker/profile version and a mandatory quiesced/offline
upgrade. A previously released legacy binary does not know this marker and
cannot automatically fail closed; safe rolling upgrade with such a writer is
not supported. Stop all legacy writers, create/verify tokens for pending
intents, then enable the token-aware writer. Only versions that explicitly
understand the marker may provide automatic admission rejection. No general
migration manager is required.

The guard must be integrated into every participating durable write boundary:
canonical content, lexical catalog, raw-unit store, manifest publication, and
intent release. Checking only the outer `assert_ownership()` calls is
insufficient because a takeover can race an inner transaction.

## Scope and expected payoff

This proposal changes only the repeated fencing read path. It does not batch
KnowledgeUnit commits, alter MDBX durability, modify ContentHash identity, or
change manifest visibility. The measured 4 MiB control suggests that removing
full-intent materialization is worth evaluating before any index redesign, but
the implementation must be a separate PR with the negative tests above.
