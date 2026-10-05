# Growth R35 — canary evidence registry / policy readiness certificate

Growth R35 is a durable evidence registry for deciding whether a Growth policy has enough independently replayable evidence to be presented as a future **shadow canary candidate**. The entire milestone is `ADVISORY_ONLY`. It cannot authorize or execute traffic allocation, Creator mutation, provider/browser mutation, credential access, publication, or any live action.

## Exact accepted authority

R35 freezes the exact accepted Growth R34 parent:

- `foto6/video3@9ff243bc5ec6977bc5f0eb8f16cd5e51aa0dcdfc`
- CI `37244660304 SUCCESS`
- artifact `11318054384`
- digest `sha256:6fc329964c14ce7c11f27fd2dd47235912470a377c6a1f5a6f3e66f4481f5ac9`
- contract `growth.counterfactual_policy_promotion.r34.v1`

Its exact ancestry remains frozen to Growth R33 `8bedb5ad79023006b87b17933863ff915ab5e046` and Growth R32 `aacaefca808a0d71adb56fbb5b5ecf6213e874c8`.

Independent QA is bound to `foto6/boss@233a0e3d2237300a9b85b99965e27a190ebb362f`, CI `37245109258 SUCCESS`, artifact `11319180558`, digest `sha256:402f6440b1ec136de6b3ff4f0547970f6f75274f3f3f482857d54528921d3ac8`, and R6 matrix blob `249574798f19bdd1593301aee09918530c391c71`. That matrix records Growth R34 as `ACCEPTED`.

Moving refs and alternate artifact tuples are never authority.

## Contracts

The umbrella contract is `growth.canary_evidence_registry.r35.v1`.

Nested contracts:

- `growth.canary_evidence_entry.r35.v1`
- `growth.canary_evidence_registry_snapshot.r35.v1`
- `growth.shadow_canary_readiness_certificate.r35.v1`
- `growth.shadow_canary_registry_advisory.r35.v1`
- `growth.canary_evidence_registry_policy.r35.v1`
- `growth.canary_evidence_registry_authority.r35.v1`

## Immutable evidence entries

Every registry entry binds:

- immutable `registry_entry_id` and entry digest;
- exact R34/R33/R32 authority lineage plus QA-R6;
- policy and replay corpus digests;
- underlying event-corpus digest;
- exposure-identity digest;
- estimator configuration digest;
- strata/guardrail configuration digest;
- R34 decision digest and recommendation;
- confidence interval;
- effective sample size, minimum behavior propensity, maximum importance weight, clipping sensitivity and unsupported-region count;
- distribution-drift diagnostics;
- critical guardrail failures and critical stratum reversals;
- evidence creation time, maturity state/window and frozen 30-day expiry;
- source run/artifact identity;
- explicit supersession and conflict-resolution links.

Entries are immutable and never overwritten.

## Provenance classes cannot be upgraded by metadata

The frozen hierarchy is:

| Class | Priority | Diagnostic weight | Readiness eligible | Causal-capable |
|---|---:|---:|---|---|
| RANDOMIZED | 4 | 4.0 | yes | yes |
| OFF_POLICY_REPLAY | 3 | 3.0 | yes | no |
| OBSERVATIONAL | 2 | 1.0 | no | no |
| SYNTHETIC_TEST | 1 | 0.0 | no | no |

The class is derived from bound provenance, not trusted as a label:

- fixture evidence is always `SYNTHETIC_TEST`;
- observational mode is always `OBSERVATIONAL`;
- direct randomized derivation may be `RANDOMIZED`;
- randomized-corpus off-policy replay is `OFF_POLICY_REPLAY`.

A non-randomized entry cannot set `causal_claim_allowed=true`.

R35 also computes an `evidence_payload_digest` that intentionally excludes run/artifact wrapper metadata and the class label. If the same core evidence payload reappears under a different class, the registry emits `PROVENANCE_CLASS_UPGRADE_ATTEMPT`. Thus copying bytes to a new run or changing metadata cannot turn synthetic/observational evidence into randomized causal evidence.

## Duplicate and cross-run independence semantics

Exact duplicate entry registration is an idempotent no-op.

The same core evidence copied under a different run ID or artifact wrapper is detected as a duplicate group and counted once. The canonical member is deterministic.

The same policy/corpus identity with changed core evidence bytes produces `SAME_POLICY_CORPUS_CHANGED_BYTES`.

The same exposure identity appearing across different underlying event corpora produces `DUPLICATE_EXPOSURE_IDENTITY_ACROSS_CORPORA`.

Different corpora for the same policy remain distinct immutable evidence entries.

## Expiry and drift

The frozen evidence validity window is 30 days. Expired evidence remains in the snapshot and historical record but is excluded from current positive readiness.

Delayed/censored entries are retained but not readiness-eligible.

Material distribution shift marks evidence `DISTRIBUTION_SHIFTED`; it is retained and cannot confer readiness.

Stale or alternate R34/R33/R32/QA authority is rejected or results in no current readiness. Evidence is never silently rebound to a moving authority.

## Deterministic aggregation

R35 does not cherry-pick the strongest corpus.

All fresh, mature, independent readiness-eligible evidence participates. Source-class weights are emitted only as a diagnostic summary; the weighted score cannot override a veto.

Frozen candidate requirements:

- at least two independent readiness-eligible entries;
- at least two distinct underlying event corpora;
- each active candidate lower 95% bound at least `+0.02`;
- ESS at least 80;
- behavior propensity at least 0.05;
- no unsupported action region;
- no unresolved conflict;
- no distribution-shift exclusion;
- no critical guardrail failure;
- no critical stratum reversal.

Negative critical evidence cannot be averaged away.

Fresh contradictory mature positive and negative evidence produces `HUMAN_REVIEW_REQUIRED`, including randomized-vs-observational contradictions.

## Supersession without history deletion

Supersession is explicit. A successor must name its parent and must have a strictly stronger source class.

Old evidence stays in the snapshot permanently. Supersession never deletes a rollback or human-review result.

A negative conflict is considered resolved only when a strictly stronger successor both supersedes and explicitly names the old entry in `resolves_conflict_entry_ids`. Without that link, prior negative evidence remains an unresolved conflict and candidate readiness is forbidden.

Cycles, missing parents, weaker/equal supersession and fake conflict resolution are explicit conflicts.

## Leakage discipline

Post-outcome and future-feature leakage are hard rejected.

Readiness-eligible evidence trained or tuned on its own evaluation corpus is hard rejected.

Observational or synthetic entries may explicitly record evaluation-corpus training only as non-causal, readiness-disqualified evidence. They remain visible but cannot contribute to canary readiness.

## Readiness certificate

`growth.shadow_canary_readiness_certificate.r35.v1` contains:

- exact registry snapshot digest;
- exact evidence entry IDs used;
- every excluded entry and reason;
- unresolved conflicts;
- negative-history IDs;
- critical-veto IDs;
- policy and corpus digests;
- exact R34/R33/R32/QA authority tuple;
- deterministic aggregation diagnostics;
- readiness:
  - `NOT_READY`
  - `TEST_MORE`
  - `SHADOW_CANARY_CANDIDATE`
  - `HUMAN_REVIEW_REQUIRED`
  - `SHADOW_ROLLBACK`

Every certificate permanently contains `live_authorization=false`.

## Creator advisory interface

`growth.shadow_canary_registry_advisory.r35.v1` is the only downstream-facing interface. It is evidence for a later Creator R35 consumer, not execution authority.

It always contains:

- `disposition=ADVISORY_ONLY`
- `live_authorization=false`
- `provider_mutation_allowed=false`
- `creator_mutation_allowed=false`
- `browser_mutation_allowed=false`
- `traffic_allocation_allowed=false`
- `publish_allowed=false`
- `credential_access_allowed=false`
- `human_ground_truth=false`

## Durable command

```bash
python -m growth_analytics.canary_evidence_registry_r35 build \
  --entries /path/to/evidence-entries.json \
  --registry-id policy-registry-1 \
  --registry-ledger /durable/growth-r35/registry.json \
  --authority conformance/growth.canary_evidence_registry.r35.v1/authority.json \
  --policy conformance/growth.canary_evidence_registry.r35.v1/policy.json \
  --snapshot-at 2026-10-05T00:00:00Z \
  --as-of 2026-10-05T00:00:00Z \
  --target-policy-digest <sha256> \
  --out-dir /durable/growth-r35/output \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <exact-run-id>
```

CI rehearsal:

```bash
python -m growth_analytics.canary_evidence_registry_r35 rehearse-fixtures \
  --authority conformance/growth.canary_evidence_registry.r35.v1/authority.json \
  --policy conformance/growth.canary_evidence_registry.r35.v1/policy.json \
  --out-dir /tmp/growth-r35-canary-evidence-registry \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <exact-run-id>
```

The CI registry snapshot intentionally contains only `SYNTHETIC_TEST` evidence and therefore produces `NOT_READY`. Adversarial cases exercise hypothetical dispositions but are explicitly marked scenario-only; they are not live canary evidence.
