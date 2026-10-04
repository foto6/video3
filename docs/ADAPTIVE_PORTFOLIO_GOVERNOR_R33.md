# Growth R33 — adaptive portfolio governor / safe shadow promotion

Growth R33 evaluates multiple concurrent short-form experiments at portfolio level while remaining strictly shadow/advisory only. It never edits Creator state, routes live traffic, changes provider state, allocates budget, publishes content, or claims human ground truth.

The exact accepted R32 authority is frozen to `foto6/video3@aacaefca808a0d71adb56fbb5b5ecf6213e874c8`, CI `37207893319 SUCCESS`, artifact `11304917444`, digest `sha256:a697270b382b935271fc088e856720e29f9b2e6ff2c313adc93b2789234032be`, contract `growth.sequential_experiment_policy.r32.v1`. R33 additionally binds the exact R32 authority/policy/contract/implementation blobs and the exact R31 post-publish lineage producer `311606b677d6f0d97669c905265f1eb64b7ff9a4`, CI `37204153689`, artifact `11304006807`.

## Contracts

The umbrella contract is `growth.adaptive_portfolio_governor.r33.v1`.

Nested contracts are:

- `growth.adaptive_portfolio.r33.v1`
- `growth.portfolio_campaign_bundle.r33.v1`
- `growth.adaptive_portfolio_decision.r33.v1`
- `growth.adaptive_portfolio_policy.r33.v1`
- `growth.adaptive_portfolio_governor_authority.r33.v1`

Each supplied campaign bundle contains exact R32 campaign, event, and decision bytes plus R31 lineage records. R33 parses every R32 campaign/event and deterministically recomputes the R32 decision before portfolio evaluation. Wrong R32 producer/run/artifact/digest, changed event bytes, changed metric schema/definition, or mismatched R31 lineage fails closed.

## Frozen portfolio identity and family error budget

A portfolio declares its identity, policy digest, creation/freeze timestamps, campaign priority, traffic/exposure budget share, holdback share, platform/account/topic strata, and a prospective family slot before evidence is evaluated.

The frozen family alpha budget is 0.05 with four immutable prospective reservations:

- slot 0: 0.0200
- slot 1: 0.0125
- slot 2: 0.0100
- slot 3: 0.0075

A newly registered campaign consumes only its predeclared future slot. Previous evidence and boundaries are never rewritten. Duplicate slots, duplicate campaign identities, campaign-set changes under the same portfolio identity, or registration after results have been observed fail closed or require human review.

R32's own within-campaign multiplicity control remains intact. R33 adds a second portfolio gate: a randomized R32 preference cannot become `SHADOW_PROMOTE` unless its adjusted p-value also passes the campaign's reserved family alpha.

## Evidence maturity and delayed outcomes

The policy fixes maturity windows before data inspection:

- early: 3,600 seconds
- mature: 604,800 seconds

Early and mature evidence must resolve to the same session, candidate set, control identity, publish transaction/render cohort, and declared R32 look schedule. A mature cohort with insufficient exposures, missing metrics, or stale R32 policy is not treated as mature.

Late-arriving evidence can only update a declared R32 look. It cannot create a new look or retroactively select a different maturity window.

A strong early winner without complete mature evidence returns `TEST_MORE`, never shadow promotion.

## Portfolio recommendations

The only recommendations are:

- `KEEP`
- `TEST_MORE`
- `SHADOW_PROMOTE`
- `SHADOW_ROLLBACK`
- `HUMAN_REVIEW_REQUIRED`

`SHADOW_PROMOTE` is explicitly advisory. Decision evidence persists:

- `shadow_only=true`
- `creator_mutation=false`
- `provider_mutation=false`
- `traffic_routing=false`
- `budget_allocation=false`
- `live_publish=false`

No recommendation is an instruction to mutate a live system.

## Exploration and concentration safety

The frozen policy keeps a minimum exploration floor of 0.20 and caps any recommended concentration at 0.70. Winner-take-all behavior is forbidden. A campaign declaring concentration above the cap, or holdback below 0.10, requires human review.

Tiny or incomplete mature samples cannot trigger shadow promotion.

## Interference and confounding gates

R33 checks campaign bundles jointly. Material interference includes:

- overlapping exposure IDs across campaigns;
- the same source identity in overlapping campaigns;
- the same candidate bytes reused across campaigns;
- cross-platform reuse of the same candidate bytes;
- account/platform/topic evidence outside the declared campaign strata;
- external campaign changes during measurement.

Material interference produces `HUMAN_REVIEW_REQUIRED`.

Multiple accounts, platforms, and topics are allowed when each campaign stays within its frozen declared strata and cross-campaign evidence identities remain independent.

## Simpson's-paradox guard

R33 emits evidence summaries by platform, pseudonymous account, and topic. Overall uplift cannot override a materially reversed critical stratum.

The aggregation policy is fixed as equal event weighting with critical-reversal veto. If aggregate effect is at least +0.05 while any supported critical stratum is at or below -0.05, the portfolio requires human review.

## Novelty and decay

R33 distinguishes early novelty from persistent effect. A strong early spike of at least +0.08 is not promotable until mature evidence exists. If mature effect falls below +0.02, the advisory recommendation becomes `SHADOW_ROLLBACK` with `NOVELTY_SPIKE_DECAYED`.

Stale R32 policy or materially shifted platform/topic distribution requires human review.

## Rollback memory

A shadow rollback is durable in the portfolio ledger. Replaying the same candidate/policy identity cannot erase a prior rollback or convert it into promotion. Exact replay is idempotent.

A genuinely new candidate or policy identity receives a new identity digest and may be tested independently. Prior failure evidence remains retained rather than permanently poisoning unrelated future candidates.

## Replay and canonicalization

Campaign bundles are canonically sorted by family slot and campaign key. Reordering equivalent inputs yields the same semantic decision digest.

The ledger keys decisions by exact `portfolio_id@portfolio_look_id`. Exact replay is a no-op. Changed campaign set, decision bytes, or metric bytes under the same portfolio/look identity is a conflict.

## Deterministic adversarial suite

The CI rehearsal contains at least 25 deterministic scenarios, including persistent winner, null effect, delayed winner illusion, censored loser, novelty decay, Simpson reversal, observational confounding, duplicated exposures, source/candidate identity reuse, cross-platform candidate reuse, cross-account mixing, external campaign change, post-hoc campaign addition, traffic concentration, insufficient holdback, conflicting R32/R31 authority, metric schema/definition switches, stale-policy tamper, platform/topic shift, duplicate campaigns, campaign-set mutation, alpha-slot conflict, R31 lineage mismatch, ordering invariance, replay conflict, rollback memory, and a genuinely new identity after rollback.

Synthetic fixtures remain synthetic. They are never promoted to human ground truth or live platform evidence.

## Portfolio status command

One deterministic command emits campaign states, blockers, evidence maturity, portfolio recommendation, concentration guard, and the exact authority tuple:

```bash
python -m growth_analytics.adaptive_portfolio_governor_r33 status \
  --portfolio /path/to/growth.adaptive_portfolio.r33.v1.json \
  --bundles /path/to/campaign-bundles.json \
  --authority conformance/growth.adaptive_portfolio_governor.r33.v1/authority.json \
  --policy conformance/growth.adaptive_portfolio_governor.r33.v1/policy.json \
  --portfolio-look-id look-1 \
  --baseline /path/to/predeclared-portfolio-baseline.json \
  --ledger /durable/growth-r33/ledger.json \
  --out-dir /durable/growth-r33/output \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <exact-run-id>
```

Deterministic CI rehearsal:

```bash
python -m growth_analytics.adaptive_portfolio_governor_r33 rehearse-fixtures \
  --authority conformance/growth.adaptive_portfolio_governor.r33.v1/authority.json \
  --policy conformance/growth.adaptive_portfolio_governor.r33.v1/policy.json \
  --out-dir /tmp/growth-r33-adaptive-portfolio-governor \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <exact-run-id>
```

Model review remains not human ground truth. Observational evidence remains non-causal. R33 has no live action path.
