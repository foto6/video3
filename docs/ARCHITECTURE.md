# Growth Analytics Architecture

## Scope

This repository is the measurement and feedback layer for autonomous content production. It ingests analytics, evaluates content/variants, tracks experiment hypotheses, and emits a stable feedback contract to the Creator Orchestrator. It does not publish content or mutate provider accounts.

## Data model

- `Channel`: provider-neutral channel identity.
- `Video`: content identity, channel ownership, duration, publication timestamp.
- `Variant`: experimentable treatment attached to a video and optional hypothesis.
- `AnalyticsEvent`: immutable provider event with impressions, views, clicks, watch time, and normalized retention points.
- `Hypothesis` / `Experiment`: explicit experiment intent and control/treatment identities.
- `CreatorFeedback`: versioned upstream contract containing score, uncertainty, observed metrics, recommended next tests, and evidence event IDs.

## Ingest and idempotency

Providers normalize dictionaries into `AnalyticsEvent`. The in-memory MVP store uses `provider:event_id` as the idempotency key:

1. first arrival is accepted;
2. an identical replay is ignored;
3. a conflicting replay raises an error instead of silently changing history.

This keeps fixture, vidIQ, Metricool, and future providers behind the same interface.

## Metrics

CTR is `clicks / impressions`. Average watch time is `watch_time_seconds / views`. Retention curves use normalized positions and retained fractions in the `[0, 1]` range. Multiple curves are averaged after linear interpolation at their union of sample positions.

Retention quality is summarized with trapezoidal area under the normalized curve.

## Deterministic score and uncertainty

The MVP score is deterministic and bounded to `[0, 1]`:

- 35% CTR component, saturated at 10% CTR;
- 35% average-watch-time / video-duration component;
- 30% retention-curve AUC.

Sampling uncertainty is `min(1, 1 / sqrt(impressions))`; it decreases monotonically as impression count grows. Experiment CTR uncertainty uses a 95% normal-approximation half-width from control and treatment binomial standard errors.

These formulas are intentionally explicit and reproducible. They are not claims of causal significance; later versions can introduce calibrated models behind versioned scoring contracts.

## Provider boundaries

`AnalyticsProvider` defines the ingest interface. `VidIQAnalyticsAdapter` and `MetricoolAnalyticsAdapter` are explicit read-only adapter boundaries around a future authenticated client. Both normalize `fetch_analytics(...)` responses into the shared model.

`ReadOnlyProviderBase.mutate_account` always raises `AccountMutationDisabled`. Real posting, scheduling, deletion, account edits, or other provider mutations are outside this repository.

## Creator Orchestrator feedback

`build_creator_feedback` emits contract version `1.0` with:

- content job, channel, video, and optional variant IDs;
- deterministic score and uncertainty;
- observed CTR, average watch time, retention AUC;
- bounded recommendation tokens;
- sorted evidence event IDs for lineage.

The contract is serializable with `CreatorFeedback.to_dict()`.

## Local-first validation

`fixtures/sample_events.json` exercises ingestion without external credentials. Unit tests cover idempotency conflicts, retention integration, deterministic scoring, uncertainty behavior, CTR experiment lift, feedback shape, fixture normalization, and mutation blocking.


## Experiment Engine v2

Round 2 adds a durable experiment and learning layer around the original analytics core:

- multi-variant experiment lifecycle in `growth_analytics.experiment`;
- append-only replayable JSONL event stream in `growth_analytics.event_stream`;
- time windows, trend deltas, retention cohorts, calibrated observational uncertainty, deterministic cross-job recommendation ranking, and batch feedback in `growth_analytics.engine`;
- deterministic campaign simulation plus JSON/Markdown reports in `growth_analytics.campaign`.

CreatorFeedback remains contract version `"1.0"` with the exact Round-1 wire vocabulary. V2 experiment and uncertainty objects are internal/reporting contracts and never imply causal certainty from observational provider analytics.

See `docs/EXPERIMENT_ENGINE_V2.md` and `fixtures/campaign_round2.json`.


## Feedback delivery boundary

The cycle-N -> cycle-N+1 integration boundary is additive to CreatorFeedback 1.0. `growth_analytics.delivery` owns deterministic feedback-batch identity, byte-stable Creator seed export, and the append-only delivery ledger.

Batch identity is derived from campaign/window/evidence inputs; a separate payload digest protects the canonical v1 payload set. This allows identical replay after restart to collapse to one logical delivery while a reused identity with changed feedback fails closed.

Growth generates and exports feedback only. Creator owns persisting/applying the exported seed to the next cycle. No Growth component posts content, mutates provider accounts, or mutates Creator workflow state.


## Creator seed consumer conformance

Wave 4 freezes strict producer parsers/serializers for `growth.feedback_batch.v1` and `growth.creator_seed.v1`. The canonical corpus is stored in `fixtures/creator_consumer_conformance_v1/` with exact file SHA-256 hashes, batch/payload/seed digests, duplicate/conflict expectations, Growth producer provenance, and the inspected Creator consumer SHA.

The Creator cross-check is against `foto6/video1 @ 7ece5182bdf0791eadda28f10e7316f3a496ded4`: its current public integration boundary accepts exact CreatorFeedback `1.0` as `growth_feedback` seed metadata but has no outer batch/seed parser. Growth does not edit or mutate Creator state; Creator owns persist-once application using `idempotency_key=batch_id`.

See `docs/CREATOR_CONSUMER_CONFORMANCE_V1.md`.


## Event-time finalization and crash consistency

Wave 5 separates event time from ingestion order explicitly. `AnalyticsEvent.captured_at` controls window membership; stream `sequence` records only durable arrival order. `WindowFinalizationLedger` closes a campaign/window only after its explicit watermark passes end plus allowed lateness and binds the canonical event set to the existing batch/payload/Creator-seed digests.

Once finalized, exact event replays are duplicate/no-op, while new in-window identities are rejected. Finalized windows are not reopened under the same identity. This prevents late data or restart order from silently changing an already delivered next-cycle seed.

The event stream may optionally recover only an unterminated syntactically invalid final JSONL line. All semantically ambiguous storage faults—including conflicting duplicate ids and sequence corruption—fail closed. Delivery-ledger sequence corruption likewise fails closed.

The Wave5 stress and metamorphic suites prove that identical semantic event sets yield identical finalized-learning bytes and frozen Growth-to-Creator delivery hashes across ingestion permutations and injected restarts. See `docs/EVENT_STREAM_RELIABILITY_V1.md`.


## Offline evaluation boundary

Wave 6 adds growth_analytics.evaluation as a read-only offline layer. It consumes canonical finalized analytics events, splits whole finalized campaign/window identities into disjoint historical and replay partitions, calibrates observational uncertainty using historical transitions only, and evaluates replay stability without changing any delivery contract.

Evaluation output is separate metadata. It is never serialized into CreatorFeedback 1.0, growth.feedback_batch.v1, or growth.creator_seed.v1, and it cannot dispatch or publish. The gate can only describe evidence as insufficient, unstable, or stable enough for an experiment.

See docs/OFFLINE_EVALUATION_V1.md.


## Deterministic experiment protocol boundary

Wave 7 adds growth_analytics.experiment_protocol as a pure read-only assignment/evaluation layer. experiment.plan.v1 freezes eligible variants, weighted allocation, stable assignment identity, strata, experiment window, metrics, guardrails and sequential decision rules.

Experiment evidence is separate from Growth-to-Creator feedback delivery. Only validated synthetic randomized-assignment fixtures enter sequential experiment inference; historical analytics remain explicitly observational. Replay is canonicalized by event time and stable ids, and no experiment result can authorize publishing or account mutation.

See docs/EXPERIMENT_PROTOCOL_V1.md.


## Experiment integrity boundary

Wave 8 inserts growth_analytics.experiment_integrity before treatment-effect interpretation. It consumes the exact experiment plan, raw randomized evidence, and sequential report, then emits experiment_integrity.v1 bound to exact plan/randomization/corpus/report hashes.

Integrity invalidation is fail-closed for inference but non-destructive for evidence inspection. CreatorFeedback 1.0 and Growth-to-Creator delivery payloads are unaffected. Historical analytics remain observational and cannot be promoted to randomized evidence by the integrity layer.

Sequential-report generation supports an optional integrity reference; omission preserves Wave 7 core output. See docs/EXPERIMENT_INTEGRITY_V1.md.
