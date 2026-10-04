# Growth R31 — post-publish learning policy / drift-aware experiment engine

Growth R31 is a conservative learning layer over normalized short-form publish receipts and platform metric snapshots. It produces advisory future-edit policy suggestions; it does not publish, call provider APIs, mutate Creator/Media state, or claim human ground truth.

The exact parent is Growth R30 `9313c984932d9b1bbf18f7c382533f830337971e`, CI `37202813275 SUCCESS`, artifact `11303044114`, digest `sha256:0d8a0ff48c5ade7ec4d2440227f4409d63f1b67153de1308cd53d1a4b3f57646`, external contract `growth.consensus_review.r30.v1`.

Independent Hard Wave QA-R2 has not yet been accepted in this milestone. The R31 authority therefore fixes `parent_qa_state=WAITING_PARENT_QA` and `authoritative_integration_allowed=false`. R31 can prove source readiness and experiment semantics, but current top-level status remains `WAITING_PARENT_QA`.

## Contracts

The umbrella contract is `growth.postpublish_learning.r31.v1`.

Inputs and outputs are explicitly versioned:

- `growth.postpublish_observation.r31.v1`
- `growth.randomized_assignment_exposure.r31.v1`
- `growth.future_edit_policy_suggestion.r31.v1`
- `growth.postpublish_learning_policy.r31.v1`
- `growth.postpublish_learning_authority.r31.v1`

The normalized upstream payloads remain the existing `growth.shortform_publish_result.v1` and `growth.shortform_metric_snapshot.v1` contracts.

## Attribution discipline

Observational performance is not a causal effect. Historical best performance is not a guarantee of future performance. A platform-reported follow, subscribe, click or CTA event is interpreted only according to the provider field that actually exists.

R31 never substitutes one platform metric for another. Missing metrics remain `available=false, value=null`; they are never silently replaced by zero. In particular, the current upstream metric snapshot does not expose normalized `reach` or `subscribes`, so R31 records those as unavailable rather than inferring them.

Randomized lift is computed only after a valid `growth.randomized_assignment_exposure.r31.v1` record passes assignment/exposure validation. Even then, the output says `causally_proven=false`; it reports a bounded randomized lift estimate and uncertainty, not certainty or human preference.

## Source and lineage binding

Every observation binds:

- exact publish-result ID/digest and provider receipt provenance;
- platform, pseudonymous account and external post ID;
- Creator session/tournament/winner candidate;
- published winner render SHA-256;
- Creator envelope digest;
- edit strategy and operation-graph digest;
- topic cluster and source SHA-256;
- metric snapshot digest and exact capture window;
- provider metric-schema version;
- current and optional prior metric snapshot.

A changed winner render after the publish receipt, a different external post ID, or a different account pseudonym fails closed.

## Metric normalization

R31 preserves explicit availability for:

- impressions, views and reach;
- watch time, average watch duration and completion;
- likes, comments, shares and saves;
- follows and subscribes;
- link/CTA clicks.

No cross-platform substitution occurs. Provider-normalized average watch and completion preserve their upstream normalization source.

Outcome vectors use a predeclared fallback order only for learning score construction:

1. completion rate when available;
2. average-watch-duration / media-duration;
3. engagement-per-view from only available engagement counts.

This fallback is disclosed in `outcome_scalar_source` and does not claim metric equivalence.

## Predeclared windows

The frozen policy defines:

- `early`: exactly 3,600 seconds;
- `mature`: exactly 604,800 seconds.

An evidence set may not mix 1-hour and 7-day observations. The randomized record must name its metric window and primary metric before exposure. Changing the window after seeing performance is rejected as post-hoc selection.

Observations without a usable outcome or view evidence are flagged `censored_or_incomplete=true`.

## Randomized experiment gate

R31 requires:

- deterministic SHA-256 bucket assignment;
- 50/50 allocation declaration;
- unique assignment and exposure IDs;
- assignment before exposure;
- predeclared window and `completion_rate` primary metric;
- exact assigned and observed render SHA;
- `treatment_visible_before_assignment=false`;
- `creative_changed_after_assignment=false`;
- `posthoc_window_selected=false`;
- at least four independent exposures per arm.

Non-random historical comparison cannot be relabeled as an experiment. Treatment leakage, duplicate exposure, creative changes after assignment and post-hoc metric/window selection fail closed.

The bounded lift estimate is the treatment-minus-control mean completion rate with a deterministic 95% normal-approximation interval, clamped to [-1,1]. A preference update is permitted only when minimum sample gates pass and the interval excludes zero.

## Robust observational aggregation

Strategy ranking is based on the median outcome scalar, not a raw mean. An individual observation more than 0.35 from its strategy median is flagged as an outlier. A single observation can never satisfy the minimum independent-sample gate, which is three observations per strategy.

Evidence confidence decays deterministically with a 21-day half-life. Evidence older than 45 days is marked stale.

## Drift detection

R31 records and compares:

- platform distribution;
- topic distribution;
- source distribution;
- metric-definition/schema versions;
- season/time distance.

Metric schema drift is a hard block. Platform/topic/source and season changes are explicit warnings with total-variation or day-distance scores. The policy does not silently transfer a stale platform definition into a new schema.

Cross-account evidence aggregation is rejected.

## Exploration guard

A noisy historical winner cannot collapse future editing to one style. Strategy priors are bounded:

- maximum single-strategy prior: 0.70;
- minimum active-strategy prior: 0.10;
- recommended exploration share: at least 0.20.

The next experiment suggestion uses deterministic SHA-256 assignment from `seed|unit|strategy` for reproducible fixtures and audit.

## Machine states

The only R31 status labels are:

- `LEARNING_SOURCE_READY`
- `WAITING_PARENT_QA`
- `INSUFFICIENT_EVIDENCE`
- `EXPERIMENT_EVIDENCE_READY`

Because the parent remains pending independent QA, the current top-level status is `WAITING_PARENT_QA`. The separate `evidence_state` may report that synthetic source evidence reaches `LEARNING_SOURCE_READY` or `EXPERIMENT_EVIDENCE_READY`; that does not authorize integration.

## Production command

Materialize one `observation-*.json` per independent published post. Optional randomized records are `exposure-*.json`.

```bash
python -m growth_analytics.postpublish_learning_policy_r31 learn \
  --observations-dir /path/to/normalized-observations \
  --experiment-dir /path/to/randomized-exposures \
  --baseline /path/to/predeclared-baseline.json \
  --authority conformance/growth.postpublish_learning.r31.v1/authority.json \
  --policy conformance/growth.postpublish_learning.r31.v1/policy.json \
  --out-dir /durable/growth-r31-learning \
  --as-of 2026-10-04T00:00:00Z \
  --exploration-seed <predeclared-seed> \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <exact-run-id>
```

The result is an advisory policy suggestion only. It contains ranked priors, confidence/uncertainty, exact evidence IDs, forbidden conclusions and a recommended next experiment.

## Deterministic CI rehearsal

```bash
python -m growth_analytics.postpublish_learning_policy_r31 rehearse-fixtures \
  --authority conformance/growth.postpublish_learning.r31.v1/authority.json \
  --policy conformance/growth.postpublish_learning.r31.v1/policy.json \
  --out-dir /tmp/growth-r31-postpublish-learning \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <exact-run-id>
```

The rehearsal contains a robust observational case with a deliberate 0.99 outlier, a valid synthetic randomized experiment, and adversarial rejection cases for duplicate provider posts, metric-snapshot replay, changed winner hash, mixed 1h/7d windows, stale provider definitions, cross-account contamination, non-random history mislabeled as experiment, treatment leakage and post-hoc selection.

No network call or live publish occurs in CI.
