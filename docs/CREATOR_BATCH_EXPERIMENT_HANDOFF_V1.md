# Growth R14 Creator Batch Experiment Handoff

## Scope

Growth R14 packages one exact R13 experiment allocation into a strict, durable handoff that Creator batch campaigns can consume without granting publish authority. The contract is \`growth.creator_batch_experiment_handoff.v1\`.

This layer remains inside Growth. It does not modify Creator or Media repositories, call provider mutation APIs, publish content, or authorize release.

## Content-addressed source binding

Each handoff embeds:

- the exact R10 \`growth.reels_next_cycle_seed.v1\` source seed;
- the exact R13 \`growth.shortform_experiment_plan.v1\` allocator plan;
- content-addressed references for every historical allocator-evidence record;
- the exact historical evidence-set digest used by R13;
- the exact R13 plan ID and digest;
- allocator implementation revision 1;
- pinned R13 producer commit \`d4e610e43759c9892309f878d3524220f21be76c\`;
- pinned R13 allocator source blob \`ea3eecd7605936fc7dc1c0414f8b448f50f7ef55\`.

Every source-evidence reference binds platform, post ID, metric-snapshot digest, allocator evidence ID/digest, evidence kind, and randomized experiment/variant/assignment/registry references when those exist. Observational evidence is forbidden from carrying randomized-assignment fields.

The handoff ID is content-addressed from campaign identity, source cycle, source seed, allocator plan, and evidence-set digest. Delivery idempotency is independently bound to the exact campaign revision, batch ID, and allocator-plan digest.

## Creator batch constraints

The handoff derives rather than invents its constraints from the validated R13 plan:

- batch size;
- maximum number of cells;
- actual cell count;
- control target and minimum holdback fraction;
- exploration target and minimum exploration fraction;
- minimum historical-evidence gate;
- exact per-cell target counts;
- the eight allowed creative dimensions;
- mutually exclusive constraints.

The mutually exclusive rules require at most one cell membership per batch item, exactly one control cell, and exactly one changed creative dimension for every non-control cell relative to control.

Creator-facing cells contain only the bounded cell ID, role, target count, complete creative-dimension configuration, evidence state, uncertainty state, and experiment signature. Cell target counts must sum exactly to the batch size.

## Eligibility and fail-closed validation

The reference Creator-side validator is implemented in Growth only. A caller supplies the expected campaign ID, campaign revision, and source cycle revision.

Validation fails closed for:

- wrong campaign identity;
- stale or future campaign revision;
- stale or future source cycle revision;
- source-seed / allocator-plan mismatch;
- allocator revision mismatch;
- missing or reordered historical evidence;
- evidence-set digest mismatch;
- source-class mismatch;
- duplicate source post evidence;
- conflicting control cells;
- unsupported or additional creative dimensions;
- cell targets that do not equal the batch size;
- any publish/provider-mutation authority.

Synthetic fixtures remain conformance-only. They can be validated only with explicit synthetic-fixture opt-in and are always marked \`creator_consumer.eligible=false\`. Live source-bound handoffs become Creator-consumer eligible only when the R13 minimum-evidence gate is met.

## Non-causal semantics

The handoff preserves R13 evidence labels. Observational history remains directional and is never described as causal lift. Randomized evidence remains explicitly identified by assignment and registry references, but this handoff does not estimate or assert treatment effects.

## Exactly-once delivery

\`growth.creator_batch_experiment_handoff_outbox.v1\` is an append-only prepare/ack outbox keyed by the handoff idempotency key.

Preparing identical bytes twice is an idempotent duplicate. Reusing the same idempotency key with changed handoff bytes fails closed.

\`growth.creator_batch_experiment_consumer_ledger.v1\` is a Growth-owned reference implementation of Creator-side durable acceptance. It validates the complete handoff before committing one logical acceptance.

If delivery loses acknowledgement after the consumer has durably accepted the handoff, restart behavior is:

1. the outbox replays the identical prepared handoff;
2. the reference consumer returns \`duplicate\`;
3. the outbox records one acknowledgement;
4. no second logical handoff or consumer acceptance is created.

All durable ledgers use canonical JSON, contiguous sequence numbers, flush, and fsync.

## Deterministic 12-reel fixture

The canonical R14 fixture binds 30 source evidence records to a 12-reel batch:

- 3 control/holdback reels;
- 6 directional-test reels;
- 3 exploration reels.

The fixture uses synthetic source data and is therefore explicitly ineligible for live Creator execution. It exists to prove byte-stable Creator R14 campaign semantics without fabricating live performance evidence.

The machine-readable handoff is \`fixtures/creator_batch_experiment_handoff_v1/canonical_handoff.json\`; replay semantics are pinned in \`fixtures/creator_batch_experiment_handoff_v1/replay_report.json\`.

## Preserved provenance

R14 starts from exact green R13 head:

\`foto6/video3@d4e610e43759c9892309f878d3524220f21be76c\`

The R14 conformance manifest pins the R10 autonomous-reels contract, R11 provider-ingestion manifest, R12 scheduler manifest, R13 allocator manifest and implementation, plus every R14 source/test/doc/fixture blob.

No provider mutation, live publishing, merge, release, Creator repository change, or Media repository change is part of this milestone.
