# Feedback Delivery & Replay Conformance

## Ownership boundary

Growth Analytics owns measurement, experiment learning, CreatorFeedback generation, feedback-batch identity, and export/delivery commit evidence.

Creator Orchestrator owns applying a delivered Growth handoff to a new content cycle. Growth never mutates an existing Creator job and never decides how Creator persists or executes the seed. The handoff carries an idempotency key so Creator can persist the same logical seed exactly once.

Provider access remains read-only. Metricool and vidIQ adapters can fetch analytics only; account mutation and publishing remain disabled.

## Frozen CreatorFeedback payload

Each feedback item inside the delivery layer is still the Round-1 CreatorFeedback contract with:

    contract_version = "1.0"

The delivery layer adds metadata outside those payloads. It does not add, remove, or rename any CreatorFeedback field. Unknown CreatorFeedback versions continue to fail closed through the strict v1 parser.

## Feedback batch identity

The outer transport is growth.feedback_batch.v1.

A batch identity is deterministic SHA-256 over canonical JSON containing only:
- campaign_id;
- the exact window label/start/end;
- each next-cycle content_job_id;
- variant_id;
- sorted evidence_event_ids.

The feedback objects are sorted deterministically by content job, variant, video, and canonical v1 JSON before export.

The payload_digest is separate from the batch identity. It is SHA-256 over the newline-joined canonical CreatorFeedback v1 JSON payload set. This separation detects a reused logical identity whose metrics/score/payload changed while its campaign/window/evidence identity stayed the same.

## Creator seed handoff

The read-only consumer fixture uses growth.creator_seed.v1 and seed_kind growth_feedback_batch.

It contains:
- idempotency_key equal to batch_id;
- campaign/window metadata;
- payload_digest;
- causal=false and the observational interpretation;
- the canonical ordered list of CreatorFeedback 1.0 objects.

Canonical fixture:

    fixtures/creator_next_cycle_seed_v1.json

Creator can use idempotency_key as its persist-once key. Growth does not call Creator or mutate Creator storage.

## Durable delivery ledger

FeedbackDeliveryLedger is an append-only JSONL commit ledger. Each committed row stores:
- ledger version;
- contiguous sequence;
- batch_id;
- payload_digest;
- Creator seed digest;
- committed status.

Commit behavior:
1. Validate and canonicalize the feedback batch.
2. Derive the canonical Creator seed handoff and seed digest.
3. If batch_id is already committed with identical digests, return duplicate without appending.
4. If batch_id is already committed with different payload/seed digest, raise DeliveryConflictError.
5. Otherwise append one row, flush, fsync, then expose the committed receipt.

A reopened ledger rebuilds the durable index, so identical replay after restart remains a duplicate rather than a second logical delivery.

## Fault injection semantics

The ledger exposes deterministic test-only fault points:
- before_commit: raises before any durable ledger write; replay commits once.
- after_commit: appends and fsyncs first, then raises; replay after restart observes the existing commit and returns duplicate.

The campaign replay fixture also injects:
- out-of-order/late analytics events;
- duplicate analytics event replays;
- repeated feedback export;
- changed evidence under a reused batch identity;
- same batch identity with a changed feedback payload.

Malformed or conflicting cases fail closed.

## Observational semantics

Delivery preserves the Experiment Engine v2 non-causal interpretation:

    Observational analytics describe associations only; they do not establish causal effects.

Batch and Creator seed envelopes require causal=false. Existing score and uncertainty values are transported unchanged from CreatorFeedback 1.0; the delivery layer does not relabel an association as causal lift.

## Canonical replay fixture

The conformance fixture is:

    fixtures/campaign_round2_delivery.json

It refers to the existing Round-2 campaign, identifies the duplicate replay events and late arrivals, and fixes the expected batch identity/payload digest. Replaying the unique durable event stream in captured-at order must regenerate byte-identical next-cycle feedback and exactly one logical delivery commit.

## Validation

Run:

    python -m unittest discover -s tests -v

The delivery suite covers deterministic ordering, byte stability, restart/replay, crash-before/crash-after commit, duplicate export, conflicting duplicate identity, changed evidence under reused identity, late and duplicate events, CreatorFeedback version rejection, canonical seed fixture equality, one-logical-handoff conformance, and read-only Metricool/vidIQ boundaries.
