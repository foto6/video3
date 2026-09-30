# Growth Analytics

Experiment and feedback layer for autonomous content production.

## Experiment & Learning Engine v2

The current implementation provides:

- provider-neutral analytics ingest with replay idempotency and conflict detection;
- channel, video, variant, retention, hypothesis, and experiment data models;
- CTR, average watch time, normalized retention curves, and retention AUC;
- deterministic scoring with explicit sampling uncertainty;
- CTR control/treatment experiment comparison;
- a versioned feedback contract for the Creator Orchestrator;
- explicit read-only vidIQ and Metricool adapter boundaries;
- local JSON fixtures and standard-library unit tests;
- multi-variant experiment lifecycle and time-window trend analysis;
- view-weighted retention cohort comparison;
- durable/replayable JSONL analytics event streams with late/duplicate/conflict semantics;
- bounded observational uncertainty reports with explicit non-causal interpretation;
- deterministic recommendation ranking and batch CreatorFeedback 1.0 generation;
- deterministic multi-experiment campaign simulation with committed JSON and Markdown reports.

Real publishing, scheduling, deletion, and account mutation are intentionally disabled in this repository.

## Run tests

```bash
python -m unittest discover -s tests -v
```

The package has no runtime dependencies beyond Python 3.11+.

## Minimal local ingest

```python
from growth_analytics import AnalyticsStore, FixtureProvider, deterministic_score

provider = FixtureProvider("fixtures/sample_events.json")
store = AnalyticsStore()
store.ingest_many(provider.fetch_events(channel_id="channel-demo"))

metrics = store.aggregate(video_id="video-demo", variant_id="A")
score = deterministic_score(metrics, duration_seconds=60.0)
print(score)
```

See `docs/ARCHITECTURE.md` for scoring formulas, uncertainty semantics, provider boundaries, and the upstream feedback contract.


## Round-2 simulation

The canonical simulation is `fixtures/campaign_round2.json`. It contains 2 experiments, 6 variant assignments, 2 time windows, and 12 analytics events.

Generated reports are committed at:

- `fixtures/campaign_round2_report.json`
- `fixtures/campaign_round2_report.md`

Regenerate them with:

```bash
python -c "from growth_analytics import write_campaign_report; write_campaign_report('fixtures/campaign_round2.json', json_path='fixtures/campaign_round2_report.json', markdown_path='fixtures/campaign_round2_report.md')"
```

The test suite compares regenerated reports byte-for-byte with the committed snapshots. Provider adapters remain read-only and expose no posting/account mutation path.

See `docs/EXPERIMENT_ENGINE_V2.md` for lifecycle, replay, uncertainty, and reporting semantics.


## Feedback delivery and replay

Wave 3 adds a replay-safe Growth -> Creator handoff without changing CreatorFeedback 1.0. The canonical consumer seed is `fixtures/creator_next_cycle_seed_v1.json`; the replay/fault fixture is `fixtures/campaign_round2_delivery.json`.

Growth owns feedback generation and durable export identity. Creator owns persisting and applying the seed to a new cycle, using the exported `idempotency_key` for persist-once behavior.

See `docs/FEEDBACK_DELIVERY_REPLAY.md` for batch identity, ledger commit semantics, crash/replay behavior, and ownership.


## Creator consumer conformance pack

Wave 4 freezes strict `growth.feedback_batch.v1` and `growth.creator_seed.v1` transport parsing and publishes a hash-manifested producer corpus under `fixtures/creator_consumer_conformance_v1/`.

The pack is cross-checked against Creator `foto6/video1 @ 7ece5182bdf0791eadda28f10e7316f3a496ded4`. Creator currently accepts strict individual `growth_feedback` seed metadata using CreatorFeedback `1.0`; it does not yet parse the outer batch/seed envelopes. The exact persist-once adapter algorithm is documented in `docs/CREATOR_CONSUMER_CONFORMANCE_V1.md`.


## Wave 5 reliability

Event-time learning now has explicit watermark/finalization semantics without changing `analytics.event.v1`. `captured_at` is event time; durable JSONL sequence is ingestion/replay order only. Finalized campaign/window identities are immutable and bind the canonical event-set digest to the existing feedback-batch and Creator-seed hashes.

The deterministic stress fixture is `fixtures/reliability_stress_v1.json` with 1,152 unique events, 128 duplicate attempts, 96 deliberately delayed events, 3 campaigns, 12 variants, and 12 finalized windows. Reports are `fixtures/reliability_report_v1.json` and `fixtures/reliability_report_v1.md`.

See `docs/EVENT_STREAM_RELIABILITY_V1.md` for watermark closure, late-event policy, JSONL recovery boundaries, crash semantics, and deterministic-learning guarantees.


## Offline evaluation and calibration

Wave 6 adds a deterministic historical/replay evaluation layer over finalized read-only analytics. It compares the existing score/ranking heuristic to a CTR-only baseline, measures rank stability, score error, uncertainty calibration/coverage, recommendation turnover, and missing/out-of-order robustness, and emits non-publishing gate metadata.

The canonical report is fixtures/offline_evaluation_report_v1.json; scenarios are in fixtures/offline_evaluation_scenarios_v1.json. The primary fixture gates unstable because uncertainty coverage fails despite stable ranking, while sparse evidence gates insufficient_evidence.

See docs/OFFLINE_EVALUATION_V1.md.


## Deterministic experiment protocol

Wave 7 adds strict experiment.plan.v1 assignment and experiment.evidence.v1 sequential evaluation as a separate read-only artifact path. Stable unit+seed+strata hashing drives assignment; allocation diagnostics cover SRM/coverage/imbalance; sequential looks use a predeclared Bonferroni family-wise-error-safe boundary.

The canonical synthetic corpus covers null, positive synthetic effect, SRM, sparse evidence, late/replay, and guardrail breach. Historical analytics remain observational and are rejected by randomized experiment inference.

See docs/EXPERIMENT_PROTOCOL_V1.md.


## Experiment integrity gate

Wave 8 adds experiment_integrity.v1 as a pre-inference gate over Wave 7 randomized experiment artifacts. It validates A/A null calibration, SRM/allocation, duplicate and cross-arm assignment, differential missingness, experiment-window leakage, pre-randomization strata balance, sequential peek/stopping conformance, and guardrail data coverage.

Invalid integrity blocks treatment-effect interpretation while retaining the raw sequential report. The canonical Monte Carlo A/A calibration uses seed 820260927 and 1000 null simulations; observed false-positive rate is 0.03 with Wilson 95% upper 0.04250368 against family-wise alpha 0.05.

See docs/EXPERIMENT_INTEGRITY_V1.md.


## Experiment registry and multiplicity governance

Wave 9 adds experiment_registry.v1 with an fsynced append-only freeze/outcome ledger, preregistration hashes, pre-outcome amendment rules, and Holm-Bonferroni family accounting layered after the existing sequential adjustment.

The canonical 40-null family contains four nominal raw p-values below 0.05 and zero confirmatory family decisions after sequential + Holm correction. Post-hoc metrics remain exploratory, and integrity-invalid evidence is blocked regardless of p-value.

See docs/EXPERIMENT_REGISTRY_V1.md.


## Reproducible experiment audit bundle

Wave 10 adds experiment_audit_bundle.v1 as a content-addressed, read-only reconstruction layer over the frozen registry, randomized evidence, experiment_integrity.v1, sequential report, and Holm family report.

The canonical bundle is fixtures/experiment_audit_bundle_v1.json and independently verifies from durable fixtures only. Equivalent replay/order permutations produce the same logical audit digest; tampered or conflicting component references fail closed.

See docs/EXPERIMENT_AUDIT_BUNDLE_V1.md.


## Audit-bound Creator decision handoff

Wave 11 adds growth.decision_handoff.v1, a read-only Creator-facing handoff bound to one exact experiment_audit_bundle.v1 digest. Recommendation content is separate from authority: auto-publish, external mutation, release authorization and publish authorization are always false.

Invalid integrity and exploratory-only evidence cannot emit confirmatory recommendations. Guardrail regressions and integrity warnings conservatively downgrade stronger audit outcomes. Exact duplicate handoff replay is an idempotent no-op through the durable handoff ledger.

See docs/DECISION_HANDOFF_V1.md.


## Autonomous short-form feedback loop

R10 adds a read-only post-publication loop for Instagram Reels, TikTok, and YouTube Shorts style outputs. Versioned publish-result and platform-metrics events are normalized into a source-bound metric snapshot and a durable `growth.reels_next_cycle_seed.v1`.

The seed links Creative/Media artifact identity to platform post identity, metric snapshot, experiment/decision evidence, and machine-readable next-cycle recommendations. Synthetic fixtures are rejected by default and can never become live/Creator-cycle eligible by changing outer seed fields.

Exactly-once ingestion, prepare/ack delivery, process restart, duplicate/out-of-order metrics, partial exports, stale cycle revisions, and lost acknowledgements are covered by deterministic replay tests.

See `docs/AUTONOMOUS_REELS_FEEDBACK_V1.md` and `conformance/growth.autonomous_reels.v1/contract.json`.
