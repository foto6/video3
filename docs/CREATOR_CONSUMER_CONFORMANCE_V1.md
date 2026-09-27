# Creator Seed Consumer Conformance v1

## Scope

This pack freezes the Growth-owned transport edge for cycle-N analytics feedback entering Creator as the next-cycle seed. Growth owns generation, canonicalization, batch identity, payload hashing, replay/delivery semantics, and the conformance corpus. Creator owns persistence and application of the seed to a new cycle.

Frozen transports:
- outer batch: `growth.feedback_batch.v1`
- Creator handoff: `growth.creator_seed.v1`
- embedded feedback: `CreatorFeedback` with `contract_version` exactly `"1.0"`

No provider account mutation, posting, scheduling, deletion, or live publication is introduced.

## Exact producer and consumer heads

The conformance corpus was generated against Growth transport commit `bcd58e510cc129d6f02504856a1bb9de6dbef358`.

The consumer expectations were independently inspected at Creator `foto6/video1 @ 7ece5182bdf0791eadda28f10e7316f3a496ded4`.

At that Creator head:
- `integration.py` freezes `GROWTH_FEEDBACK_CONTRACT_VERSION = "1.0"`;
- `validate_growth_feedback(...)` requires the exact CreatorFeedback field set and rejects unknown fields/versions;
- `build_seed_artifact(...)` accepts `SeedArtifactInput(kind="growth_feedback", metadata=<CreatorFeedback 1.0>)`;
- `Orchestrator.create_job(...)` accepts a sequence of seed artifacts and persists the resulting job through `JsonJobStore`;
- there is no parser for `growth.feedback_batch.v1` or `growth.creator_seed.v1`.

Therefore this corpus is the producer-side conformance pack for the narrow adapter Creator needs next; it does not claim that Creator already consumes the outer seed envelope.

## Strict transport behavior

Both outer transports require exact fields only.

`FeedbackBatch.from_dict/from_json` rejects unknown/missing fields, versions other than `growth.feedback_batch.v1`, non-string/empty window values, non-canonical feedback ordering, invalid CreatorFeedback 1.0 entries, `causal != false`, changed observational interpretation, mismatched batch identities, and mismatched payload digests.

`CreatorSeedHandoff.from_dict/from_json` additionally requires `handoff_version == "growth.creator_seed.v1"`, `seed_kind == "growth_feedback_batch"`, `idempotency_key == batch_id`, and the same canonical batch identity/payload digest rebuilt from embedded v1 feedback.

Serializers use sorted-key compact JSON and the embedded CreatorFeedback v1 serializer remains unchanged.

## Self-contained corpus

Directory: `fixtures/creator_consumer_conformance_v1/`

Cases:
- `canonical_batch.json` — valid canonical Growth batch.
- `creator_seed.json` — valid canonical Creator seed.
- `duplicate_replay.json` — byte-identical seed replay; duplicate/no-op after first commit.
- `conflicting_same_id_batch.json` — valid transport with the same batch identity but a different payload digest; conflict if that identity is already committed.
- `unknown_version.json` — unsupported Creator seed version.
- `malformed_payload.json` — malformed embedded CreatorFeedback value.
- `changed_payload_digest.json` — digest does not match canonical feedback bytes.
- `changed_evidence_identity.json` — changed evidence while reusing the original batch id; strict parsing rejects it.
- `manifest.json` — exact file hashes, batch/payload/seed digests, expected semantics, producer SHA, and Creator SHA.

## Exact persist-once algorithm for Creator

Creator should use `idempotency_key = batch_id` as the durable seed key.

1. Strictly parse `growth.creator_seed.v1`.
2. Revalidate every embedded item with Creator's existing `validate_growth_feedback(...)`.
3. Verify canonical seed digest and payload digest before touching job state.
4. Look up a Creator-owned durable seed receipt keyed by `idempotency_key`.
5. If absent, atomically persist an `accepted` receipt containing the batch id, payload digest, seed digest, canonical seed bytes, and deterministic destination cycle/job identity; flush/fsync before job creation.
6. If present with identical digests, treat it as the same logical handoff and do not insert another receipt.
7. If present with the same id but different payload or seed digest, fail closed.
8. If `accepted` but not yet applied, verify whether the deterministic destination job already exists. If absent, convert embedded feedback in canonical order to `SeedArtifactInput(kind="growth_feedback", metadata=<validated v1 payload>)` and create the next-cycle job once. If present, verify its seed artifacts match instead of calling `create_job` again.
9. After the new job is durably saved, mark the seed receipt `applied`.
10. If a crash occurs after job save but before step 9, restart from the `accepted` receipt, verify the existing job, and only mark `applied`; never overwrite/recreate the job.

This ordering is required because Creator `7ece5182...` has deterministic seed artifact IDs, but `Orchestrator.create_job(...)` itself does not guard against repeated calls for the same job id.

## Replay convergence and byte stability

The Growth conformance suite proves crash-before-commit, crash-after-commit, and full analytics event replay all converge to the same seed digest and exactly one logical delivery. It also tests rotations, reverse rotations, event-id sorting, and capture-time sorting of semantically identical events; every permutation regenerates the exact same Creator seed JSON bytes.

## Observational semantics

All corpus payloads keep `causal: false`. Positive handoffs require the existing interpretation:

`Observational analytics describe associations only; they do not establish causal effects.`

The corpus tests also prohibit causal-lift wording.

## Provider boundary

Metricool and vidIQ remain analytics-only provider adapters. Mutation continues to raise `AccountMutationDisabled`.

## Validation

Run `python -m unittest discover -s tests -v`.
