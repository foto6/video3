# Durable Growth -> Creator Decision Delivery Audit v1

## Purpose

This milestone closes the previously unsent Growth -> Creator decision-delivery gap without changing CreatorFeedback 1.0 or enabling any provider/account mutation.

The delivery path is content-addressed, source-bound and replay-safe:

1. Read-only analytics events are normalized from a complete export manifest.
2. Duplicate/out-of-order events collapse to one canonical event set; conflicting identities fail closed.
3. A source snapshot records event-set digest, denominators, CTR, watch time, retention, and uncertainty metadata.
4. The existing growth.decision_handoff.v1 is bound to that source snapshot.
5. Growth produces a signed growth.decision_delivery_audit.v1.
6. A self-contained growth.creator_decision_seed.v1 is prepared in a durable outbox.
7. Creator validates the seed fail-closed and records the delivery id exactly once.
8. Growth records acknowledgement only after Creator acceptance. Lost acknowledgement therefore resends the identical seed after restart; Creator returns duplicate and does not create a second logical decision.

## Versions

- growth.readonly_export_manifest.v1
- growth.decision_source_snapshot.v1
- growth.decision_delivery_audit.v1
- growth.creator_decision_seed.v1
- growth.decision_delivery_outbox.v1
- growth.creator_decision_consumer_ledger.v1
- growth.creator_decision_delivery_contract.v1

Machine-readable Creator contract:

conformance/growth.creator_decision_seed.v1/contract.json

## Source provenance and anti-masquerade rules

Production live performance may only use source_class=provider_export with complete read-only export receipts from metricool and/or vidiq.

A provider export is rejected if:

- the top-level export is incomplete;
- any provider receipt is incomplete;
- event providers differ from receipt providers;
- any provider is outside metricool/vidiq;
- fixture provenance is attached to a live export;
- a fixture signing key id is used for a live export.

Synthetic conformance data must use source_class=synthetic_fixture, provider=fixture, a committed fixture source SHA-256, and a fixture: key id.

Creator validation defaults to allow_synthetic_fixture=false. Therefore repository fixtures cannot be accepted as live performance unless a test explicitly opts into fixture conformance.

The fixture HMAC key is intentionally public and must never be trusted for live decisions. Production keys are supplied externally through Creator/Growth keyrings and are not stored in this repository.

## Metrics and denominators

The canonical source snapshot carries:

- impressions denominator for CTR;
- views denominator for average watch time;
- retention_views denominator for retention;
- event count;
- clicks numerator;
- total watch-time seconds;
- CTR;
- average watch-time seconds;
- retention AUC and retention curve.

CTR uncertainty uses a deterministic 95% Wilson interval.

The provider event model contains aggregate watch-time and retention exports rather than individual viewer samples. The bundle therefore marks watch-time and retention interval uncertainty as aggregate_export_not_estimable instead of fabricating confidence intervals.

All provider-derived snapshot metrics remain observational and preserve:

Observational analytics describe associations only; they do not establish causal effects.

## Multiplicity and experiment safeguards

The signed audit embeds the already-frozen decision handoff safeguards:

- experiment audit bundle digest;
- registry revision and freeze hash;
- integrity status/reasons;
- guardrail state;
- family multiplicity method;
- raw, sequentially adjusted and family-adjusted evidence;
- classification;
- auto_publish=false;
- external_mutation=false;
- release_authorized=false;
- publish_authorized=false.

The canonical conformance decision remains confirmatory_not_supported because Holm-adjusted evidence does not support the tested change.

## Signing and hashing

The audit uses HMAC-SHA256 over canonical JSON.

The signed structure has:

- content_digest: SHA-256 of the unsigned audit material;
- signature.algorithm = hmac-sha256;
- signature.key_id;
- signature.value = HMAC-SHA256 over the canonical material including content_digest;
- bundle_digest: SHA-256 over the signed audit including the authentication tag.

HMAC is a shared-key authenticity mechanism. Key trust is external to this repository. The public fixture key exists only to let independent consumers reproduce conformance validation.

Canonical fixture values:

- producer Git SHA: 5c01ae8a402342ebef1d8b30b4c763e78ef61751
- source snapshot digest: 5c038cc32def1f5bb799ab2a782bee7b0e31d5071e17968fbe6eb08d5b694449
- source event-set digest: cb60e052b61f58e5327e6c90b8196f1e4410943e77a12027fee6244d6471613d
- signed audit content digest: 51d5ab9e3f67e0aa2b1f4a11b9b72e7a9d7c52e96df2cd6239b5ae453ba51539
- signed audit bundle digest: 743f7f5c691454327c3bb6805e595b5587e4672078ea539ae1a9187b87086d47
- Creator delivery id: gcd1:a2883171c82d5b8285aefb3d455d39cfd5e0740980ab362a8535dab277c0ea21
- Creator seed digest: 116728c98d100a3d496ce190b0fe18ed3d3a2a50251fcdb4efa90723fbf59b1b

## Canonical fixture metrics

The conformance source is synthetic_fixture and is not live performance.

Overall fixture metrics:

- impressions: 8330
- views: 3575
- retention views: 3575
- events: 8
- clicks: 682
- watch time: 110340.0 seconds
- CTR: 0.08187275
- average watch time: 30.86433566 seconds
- retention AUC: 0.55992657
- CTR Wilson 95% interval: [0.07617587, 0.08795511]

These values exist only to exercise the transport, hashing, uncertainty and replay rules.

## Exactly-once lost-acknowledgement protocol

Growth outbox:

- prepare writes the complete seed with fsync;
- same delivery id + same seed digest returns duplicate;
- same delivery id + changed payload fails closed;
- pending() returns prepared, unacknowledged seeds after restart;
- acknowledge appends a separate fsynced acknowledgement row.

Creator reference consumer:

- validates exact fields and all digest bindings;
- verifies the audit HMAC through a trusted key id;
- validates the expected active registry revision;
- rejects synthetic fixtures by default;
- records one accepted row keyed by delivery_id;
- exact replay returns duplicate;
- conflicting reuse fails closed.

Lost acknowledgement proof:

1. Growth prepares one seed.
2. Creator accepts it once.
3. Growth crashes before recording the acknowledgement.
4. Growth reopens the outbox and sees one pending seed.
5. Growth resends the identical seed.
6. Creator returns duplicate and still has accepted_count=1.
7. Growth records acknowledgement.
8. Growth pending set becomes empty.

## Deterministic failure gates

Tests prove fail-closed behavior for:

- duplicate/out-of-order provider events;
- conflicting duplicate event identity;
- partial export;
- stale experiment revision;
- process restart;
- lost acknowledgement;
- signature tampering;
- unknown seed fields;
- fixture source attempting a live key;
- default rejection of synthetic fixture seeds;
- conflicting delivery payload;
- provider mutation attempts.

## Fixtures

- fixtures/decision_delivery_v1/synthetic_provider_events.json
- fixtures/decision_delivery_v1/synthetic_export_manifest.json
- fixtures/decision_delivery_v1/canonical_signed_audit.json
- fixtures/decision_delivery_v1/canonical_creator_seed.json
- fixtures/decision_delivery_v1/contract_manifest.json
- fixtures/decision_delivery_v1/recovery_report.json

## Preserved boundaries

CreatorFeedback 1.0 bytes are unchanged.

growth.feedback_batch.v1, growth.creator_seed.v1, experiment_registry.v1, experiment_integrity.v1, multiplicity controls, experiment_audit_bundle.v1 and growth.decision_handoff.v1 are unchanged.

Metricool and vidIQ remain read-only. No release, publishing or account mutation is performed.

## Validation

Focused:

python -m unittest discover -s tests -p "test_decision_delivery_milestone.py" -v

Full:

python -m unittest discover -s tests -v

## Cross-repo blocker

This writable scope is restricted to foto6/video3. The Creator repository is not modified here.

Therefore this milestone publishes the exact consumer contract, canonical fixtures, reference validator and replay semantics, but it does not independently prove that the current Creator runtime has implemented growth.creator_decision_seed.v1. Creator adoption remains an explicit cross-repo integration gate.
