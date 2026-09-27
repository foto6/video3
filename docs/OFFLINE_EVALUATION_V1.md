# Offline Evaluation & Uncertainty Calibration v1

## Purpose

This framework evaluates whether Growth ranking and recommendation heuristics are stable enough to supply Creator with observational guidance. It uses only already-captured read-only analytics events and never publishes, mutates provider accounts, or claims treatment/business causality.

Frozen integration contracts remain unchanged:

- CreatorFeedback contract_version "1.0"
- growth.feedback_batch.v1
- growth.creator_seed.v1

Evaluation metadata is stored only in offline reports. It is never inserted into CreatorFeedback or delivery payloads.

## Historical/replay split

The split contract is versioned as growth.offline_split.v1.

For each campaign, finalized windows are ordered by event-time start. The canonical Wave6 fixture uses the first two finalized windows as historical input and the later two as replay holdout.

The split records exact historical/replay campaign-window identities, exact historical/replay event idempotency keys, and a SHA-256 split digest. Window identities or event keys may not appear in both partitions. Duplicate finalized campaign/window identities fail closed. Event ingestion order does not affect partition membership.

Canonical sample counts: 6 historical windows, 6 replay windows, 576 historical events, 576 replay events, and 24 evaluated variant-window observations.

## Current heuristic and baseline

The current policy is growth_deterministic_score_v2, using the same CTR/watch/retention weighting that feeds CreatorFeedback 1.0.

The baseline is historical_ctr_only_v1, which ranks variants only by historical CTR with deterministic variant-id tie breaking. Both are compared against replay-window score ordering. The comparison is descriptive predictive stability only; no difference is called causal lift.

## Metrics

The report includes pairwise ranking agreement and its bootstrap band, baseline ranking agreement, descriptive difference versus baseline, absolute score error and bootstrap band, evidence coverage, uncertainty interval coverage, calibration error versus 0.95 nominal coverage, mean uncertainty half-width, recommendation turnover, deterministic missing-event robustness, and out-of-order robustness.

## Uncertainty calibration

Calibration uses only the historical partition. For each campaign, score movement between successive historical finalized windows is divided by the earlier window's observational uncertainty half-width. The scale is the maximum observed historical residual-to-bound ratio, never less than 1.0.

Replay outcomes do not influence that scale. The replay holdout only evaluates whether the historically calibrated interval covers later observed scores.

Bootstrap method: fixed_seed_noncausal_bootstrap_mean_v1, default seed 6102026, 1000 resamples, sampling with replacement, empirical 2.5%/97.5% quantiles. These are observational diagnostics, not causal confidence intervals.

## Gate metadata

Evaluation returns one of insufficient_evidence, unstable, or stable_enough_for_experiment. auto_publish is always false.

Current thresholds require enough replay events/variant-windows, at least 0.90 evidence coverage, at least 0.70 ranking stability, bootstrap score-error upper band at most 0.20, calibrated uncertainty coverage at least 0.80, recommendation turnover at most 0.60, and missing-event ranking robustness at least 0.75.

The canonical Wave6 report gates unstable: ranking order is stable, but replay uncertainty coverage is insufficient. Strong ranking performance does not override failed calibration.

## Robustness fixtures

fixtures/offline_evaluation_scenarios_v1.json defines exact duplicate replay, out-of-order/reversed/seeded-shuffled presentation, omission of 96 designated delayed events, sparse replay evidence, and deterministic replay-only distribution shift.

Exact duplicates and order permutations must yield the same semantic evaluation. Sparse evidence must gate conservatively. Distribution shift must be visible in ranking stability/gating.

## Canonical report

Files:
- fixtures/offline_evaluation_report_v1.json
- fixtures/offline_evaluation_report_v1.md
- fixtures/offline_evaluation_scenarios_v1.json

Canonical JSON report SHA-256:
a029a4949ff75ef91320e7a659bd3ecd7c275d3881b74cb8d3ee07ba4ecb03ad

Primary highlights:
- ranking stability: 1.0, bootstrap [1.0, 1.0]
- CTR-only baseline ranking stability: 1.0
- absolute score error: 0.03688286, bootstrap [0.03324122, 0.04061784]
- historical uncertainty scale: 1.38953368
- calibrated replay uncertainty coverage: 0.0
- evidence coverage: 1.0
- recommendation turnover: 0.33333333
- missing-event ranking robustness: 1.0
- out-of-order ranking robustness: 1.0
- primary gate: unstable
- sparse gate: insufficient_evidence
- distribution-shift gate: unstable

## Delivery compatibility

Tests load the existing Wave4 canonical feedback batch and Creator seed bytes, run the evaluator, and require the exact same growth.feedback_batch.v1, growth.creator_seed.v1, and embedded CreatorFeedback 1.0 bytes afterward. Evaluation cannot authorize or trigger delivery.

## Provider boundary

Metricool and vidIQ remain read-only analytics adapters. Mutation continues to raise AccountMutationDisabled.

## Validation

Focused: python -m unittest discover -s tests -p "test_offline_evaluation.py" -v

Full: python -m unittest discover -s tests -v
