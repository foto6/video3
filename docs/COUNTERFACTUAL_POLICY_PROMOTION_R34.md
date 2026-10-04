# Growth R34 — counterfactual policy promotion / safe offline replay gate

Growth R34 determines whether a candidate Growth policy is safe enough to become a **future canary candidate**. R34 itself remains `ADVISORY_ONLY`: it does not mutate Creator, provider, browser, budget, traffic, or publishing state.

## Frozen parent authority

R34 accepts only the exact Growth R33 parent:

- repo: `foto6/video3`
- SHA: `8bedb5ad79023006b87b17933863ff915ab5e046`
- CI: `37210963972 SUCCESS`
- artifact: `11306727215`
- artifact digest: `sha256:132845c0aaa6d4fec5aaf60e1ade60d779183f2a637a9513e4176bd2ee569660`
- contract: `growth.adaptive_portfolio_governor.r33.v1`

It also binds Hard Wave QA-R5 at `foto6/boss@1583c853108b0fb88507448eb8b4c61f0f07b0bc`, checkpoint blob `e80fae9f8e20708ddd6f98b9741ff1dd5ec8245e`, which records the exact R33 tuple as `ACCEPTED`.

Moving refs are never authority.

## Contracts

The umbrella contract is `growth.counterfactual_policy_promotion.r34.v1`.

Nested contracts:

- `growth.counterfactual_replay_corpus.r34.v1`
- `growth.counterfactual_candidate_policy.r34.v1`
- `growth.counterfactual_policy_promotion_decision.r34.v1`
- `growth.counterfactual_policy_promotion_envelope.r34.v1`
- `growth.counterfactual_policy_config.r34.v1`
- `growth.counterfactual_policy_promotion_authority.r34.v1`

## Same frozen corpus

`BASELINE_POLICY` and `CANDIDATE_POLICY` must bind the same immutable replay corpus digest. The corpus binds the exact R33 portfolio lineage, exact R31/R32/R33 event lineage digests, campaign identity, platform/account/topic strata, feature timestamps, logging propensities, mature outcomes, guardrails, and the out-of-sample outcome-model descriptor.

Event order is canonicalized. Duplicate event or exposure identities are rejected.

## Counterfactual estimator

R34 uses a deterministic doubly-robust/AIPW estimator.

For a target policy `π`, logged action `A`, logging propensity `e(A|X)`, outcome `Y`, and outcome model `Q(X,a)`, each event score is:

`Σ_a π(a|X)Q(X,a) + π(A|X)/e(A|X) × (Y - Q(X,A))`.

Baseline and candidate are estimated over the same event rows. The promotion statistic is the paired candidate-minus-baseline score difference.

Frozen assumptions:

1. **Consistency** — the logged reward corresponds to the logged action.
2. **Positivity / overlap** — every action supported by the target policy has logging probability at least 0.05.
3. **Source-bound propensity validity** — propensities are logged pre-outcome probabilities, not post-hoc reconstructions.
4. **Out-of-sample nuisance model** — the outcome model is trained outside the evaluation corpus under a declared split.
5. **Same frozen corpus** — baseline and candidate replay identical evaluation bytes.
6. **No undeclared interference** — campaign/account/exposure identities are not silently mixed.

Limitations:

- offline replay does **not** prove live-canary safety;
- doubly-robust protection requires at least one nuisance model (propensity or outcome model) to be valid;
- unmeasured confounding can invalidate causal interpretation;
- weak overlap inflates variance and extrapolation risk;
- observational replay remains association-only and never becomes causal automatically.

## Overlap, ESS and clipping

The frozen safety configuration requires:

- minimum behavior propensity: `0.05`;
- minimum effective sample size: `80`;
- maximum importance weight for sensitivity replay: `5.0`;
- maximum raw-vs-clipped estimate sensitivity: `0.02`.

Unsupported action regions, low ESS, or excessive clipped-weight sensitivity require `HUMAN_REVIEW_REQUIRED`.

## Uncertainty and promotion boundaries

R34 uses a deterministic analytic influence-function interval with `z=1.96`.

Primary decision thresholds:

- promotion margin: `+0.02`;
- harmful threshold: `-0.03`;
- baseline equivalence margin: `±0.005`.

A candidate may become `SHADOW_CANARY_CANDIDATE` only when the lower 95% bound is greater than `+0.02`.

A candidate becomes `SHADOW_ROLLBACK` only when the upper 95% bound is at or below `-0.03`.

Otherwise R34 emits `KEEP_BASELINE` or `TEST_MORE`.

## Critical guardrails and strata

Aggregate primary improvement is insufficient by itself.

Every critical guardrail must have a lower 95% difference bound at or above `-0.02`.

Every declared critical platform/account/topic stratum must contain at least 20 events and its lower 95% bound must remain at or above `-0.02`. Any critical point estimate at or below `-0.02` is a critical reversal and requires `HUMAN_REVIEW_REQUIRED`.

This is the Simpson's-paradox veto: aggregate uplift cannot override a reversed critical stratum.

## Maturity and censoring

Only the frozen mature replay window is promotion-eligible. Delayed or censored outcomes produce `TEST_MORE`. R34 cannot select a different window after seeing outcomes.

A novelty spike that disappears by the mature replay therefore cannot be promoted.

## Distribution drift

Candidate training/reference distributions are compared with the replay corpus for:

- platform;
- pseudonymous account;
- topic.

Total-variation distance above `0.25` on any dimension blocks promotion and requires human review.

## Complexity and exploration budget

A candidate policy is rejected from promotion if it exceeds any frozen bound:

- maximum 8 rules;
- minimum action exploration probability 0.20;
- maximum action concentration 0.70;
- winner-take-all forbidden.

A metric improvement cannot justify excessive concentration or policy-rule explosion.

## Leakage guards

R34 fails closed on:

- post-outcome features;
- future feature timestamps;
- undeclared feature names;
- proxy features explicitly identified as treatment/confounder leakage;
- duplicate event IDs;
- duplicate exposure IDs;
- cross-account contamination;
- candidate training on the evaluation corpus;
- outcome-model training on the evaluation corpus;
- mismatched baseline/candidate corpus identity.

## Multiplicity

Offline policy comparisons use a predeclared family. The frozen family alpha is 0.05 with at most four declared tests. Overspending the family budget requires human review.

## Recommendations

R34 emits only:

- `KEEP_BASELINE`
- `TEST_MORE`
- `SHADOW_CANARY_CANDIDATE`
- `SHADOW_ROLLBACK`
- `HUMAN_REVIEW_REQUIRED`

`SHADOW_CANARY_CANDIDATE` is not a live canary launch.

The Creator R35 envelope is emitted only for that recommendation and is permanently marked:

- `disposition=ADVISORY_ONLY`
- `creator_execution_allowed=false`
- `provider_mutation=false`
- `browser_mutation=false`
- `live_traffic_allowed=false`
- `live_publish_allowed=false`
- `human_ground_truth=false`

The envelope binds the exact R33/R34 authority tuple, baseline and candidate policy digests, decision digest, and exact replay corpus digest.

## One-command evaluation

```bash
python -m growth_analytics.counterfactual_policy_promotion_r34 evaluate \
  --corpus /path/to/growth.counterfactual_replay_corpus.r34.v1.json \
  --baseline-policy /path/to/baseline-policy.json \
  --candidate-policy /path/to/candidate-policy.json \
  --authority conformance/growth.counterfactual_policy_promotion.r34.v1/authority.json \
  --policy-config conformance/growth.counterfactual_policy_promotion.r34.v1/policy.json \
  --ledger /durable/growth-r34/ledger.json \
  --out-dir /durable/growth-r34/output \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <exact-run-id>
```

Deterministic adversarial rehearsal:

```bash
python -m growth_analytics.counterfactual_policy_promotion_r34 rehearse-fixtures \
  --authority conformance/growth.counterfactual_policy_promotion.r34.v1/authority.json \
  --policy-config conformance/growth.counterfactual_policy_promotion.r34.v1/policy.json \
  --out-dir /tmp/growth-r34-counterfactual-policy-promotion \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <exact-run-id>
```

CI uses only deterministic synthetic/offline fixtures. It performs no provider or browser call and no live traffic mutation.
