# Growth R32 — sequential experiment / drift-aware decision policy

Growth R32 turns R31 post-publish learning evidence into an advisory sequential decision layer. It does not edit Creator state, call a browser/provider, publish content, merge, or claim human ground truth.

The exact parent candidate is Growth R31 `311606b677d6f0d97669c905265f1eb64b7ff9a4`, CI `37204153689 SUCCESS`, artifact `11304006807`, digest `sha256:fab2ab2df9a4c8106352c5e6c17da3b3281f15a2b0e2015ec285304b2070334d`.

Independent QA-R3 has accepted exactly that frozen R31 authority. The bound acceptance evidence is QA HEAD `2a48c909bfb5785409b591253f6085642b962d0d`, CI `37207701514 SUCCESS`, artifact `11305557095`, digest `sha256:4c5cb2c476643a03865ec37c084650db4c98c84c3aed04aafeb35b81b4e9fba0`, disposition `ACCEPTED`. The acceptance is valid only when the nested accepted R31 SHA/run/artifact/digest match the exact parent tuple above; any QA or parent-pin drift fails closed.

R32 itself is not accepted by that parent QA. Every R32 result now has top-level status `SOURCE_READY_PENDING_R32_QA`, parent state `ACCEPTED`, self state `PENDING_QA_R4`, and `authoritative_integration=false` until independent QA-R4 accepts R32.

## Contracts

Umbrella: `growth.sequential_experiment_policy.r32.v1`.

Nested contracts:

- `growth.sequential_experiment_campaign.r32.v1`
- `growth.sequential_experiment_event.r32.v1`
- `growth.sequential_experiment_decision.r32.v1`
- `growth.sequential_experiment_policy_config.r32.v1`
- `growth.sequential_experiment_authority.r32.v1`

Every campaign binds exact session identity, policy version/digest, primary and guardrail metrics, declared candidates, predeclared looks, max horizon, metric schema hash, metric-definition hash, and the exact R31 parent SHA.

The R32 authority additionally binds the exact QA-R3 acceptance tuple above. Parent authority validation is byte/identity strict: wrong QA HEAD, run, artifact ID, artifact digest, disposition, or nested R31 pin is rejected before any decision evaluation.

Every event additionally binds publish transaction, Creator session, published candidate/render SHA-256, exposure identity/time, metric-capture time, platform/account/topic/source, policy and metric hashes, and its own digest.

## Evidence modes stay separate

`randomized_controlled` and `observational_monitoring` are separate modes.

Observational monitoring may report candidate associations and hypotheses only. It can return `TEST_MORE`, `KEEP`, or `HUMAN_REVIEW_REQUIRED` as appropriate, but never a causal winner and never `PROMOTE_CANDIDATE` on association alone.

Randomized evidence can support `PROMOTE_CANDIDATE` or `ROLLBACK_RECOMMENDED` only after all sequential, exposure, metric, drift, guardrail, and multiple-comparison gates pass. Even a supported randomized preference is not labeled “causally proven.”

## Predeclared sequential policy

The frozen policy declares before data inspection:

- primary metric: `completion_rate`;
- guardrails: `share_rate`, `comment_rate`;
- minimum exposures per arm: 20;
- maximum exposures per arm: 80;
- maximum horizon: 320 total exposures;
- looks: 25%, 50%, 75%, 100%;
- total alpha/error budget: 0.05;
- cumulative alpha spend: 0.005, 0.0125, 0.025, 0.05;
- multiple-comparison correction: Bonferroni across candidate comparisons × tested metrics;
- minimum promotion effect: +0.05;
- rollback effect: -0.05;
- guardrail maximum relative degradation: 20%.

An undeclared look is rejected. A declared look is also rejected if prior-look history is missing, reordered, or otherwise inconsistent with the fixed look schedule. This prevents unbudgeted repeated peeking.

## Failure gates

R32 fails closed or requires human review for:

- sample-ratio mismatch;
- severe allocation imbalance;
- missing primary/guardrail metrics;
- late metric capture;
- metric-schema drift;
- metric-definition changes or metric switching;
- treatment/exposure leakage;
- duplicate event, exposure, publish-transaction or campaign identity;
- wrong/mixed Creator session;
- cross-account evidence;
- tiny sample or look reached before its required exposure count;
- max-horizon overflow;
- material nonstationarity;
- material platform/topic distribution shift;
- unbudgeted extra looks.

A changed campaign or event replay under the same identity is a conflict.

## Drift and stale policy

R32 records a policy training distribution and compares evaluation evidence against it. It also compares the first and second halves of the current evidence stream.

If primary-outcome nonstationarity or material platform/topic distribution shift crosses the frozen threshold, `policy_state=STALE_POLICY_REVIEW_REQUIRED` and the recommendation becomes `HUMAN_REVIEW_REQUIRED`.

Metric schema or definition drift fails even earlier at event parsing.

## Recommendations

The only recommendations are:

- `KEEP`
- `TEST_MORE`
- `ROLLBACK_RECOMMENDED`
- `PROMOTE_CANDIDATE`
- `HUMAN_REVIEW_REQUIRED`

All are advisory. `creator_mutation=false` and `provider_publish=false` are persisted in decision evidence.

## Anti-p-hacking invariants

- Same event bytes in another input order produce the same decision digest.
- Extra looks outside the declared schedule are rejected.
- Repeated peeking with incomplete prior-look history is rejected.
- Switching metric definition mid-test is a conflict.
- Multiple candidates increase the correction factor and cannot bypass the minimum-effect gate.

## Deterministic simulations

The CI rehearsal covers:

- true positive uplift → supported promotion;
- null effect → keep;
- early noisy uplift → test more;
- early uplift that reverses → rollback recommendation;
- sample-ratio mismatch → rejected/human review;
- missing metrics → rejected/human review;
- drift after half horizon → stale policy/human review;
- observational confounding → association-only test-more;
- multiple candidates → corrected/no false promotion;
- exposure leakage → rejected;
- undeclared look → rejected;
- repeated peeking → rejected;
- schema drift → rejected;
- metric-definition switching → rejected;
- tiny sample → rejected;
- duplicate event identity → rejected.

## Command

```bash
python -m growth_analytics.sequential_experiment_policy_r32 evaluate \
  --campaign /path/to/growth.sequential_experiment_campaign.r32.v1.json \
  --events /path/to/events.json \
  --authority conformance/growth.sequential_experiment_policy.r32.v1/authority.json \
  --policy conformance/growth.sequential_experiment_policy.r32.v1/policy.json \
  --look-fraction 0.5 \
  --prior-looks 0.25 \
  --out-dir /durable/growth-r32 \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <exact-run-id>
```

Deterministic source-ready rehearsal:

```bash
python -m growth_analytics.sequential_experiment_policy_r32 rehearse-fixtures \
  --authority conformance/growth.sequential_experiment_policy.r32.v1/authority.json \
  --policy conformance/growth.sequential_experiment_policy.r32.v1/policy.json \
  --out-dir /tmp/growth-r32-sequential-experiment-policy \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <exact-run-id>
```

Model review remains not human ground truth. Observational monitoring remains not causal. QA-R3 acceptance of R31 is not QA-R4 acceptance of R32. No browser/provider mutation or publish occurs.
