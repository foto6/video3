# Growth R20 Closed-Loop Feedback Bundle

R20 defines `growth.closed_loop_feedback.v1` as the Creator-facing, source-bound bundle that unifies R18 candidate-decision evidence with optional R19 post-publish learning.

The bundle binds:
- exact source ID/SHA and cycle revision;
- R18 decision ID/digest/revision;
- optional R19 learning ID/digest/revision;
- when R19 exists, exact publish-result digest, provider-receipt digest, Media render SHA-256, metric snapshot digest, and observation window;
- a content-addressed next-cycle brief seed.

Evidence remains separated into six classes:
1. objective defects;
2. model/aesthetic judgment;
3. actual human evidence;
4. observed provider metrics;
5. derived analytics;
6. speculative hypotheses.

Model/VLM evidence is always non-human. Actual human labels remain human-provided only. Synthetic R19 evidence never sets `live_platform_metrics=true`.

If R19 is missing, stale, in backoff, terminal/broken, or blocked by clock-skew/out-of-order freshness, the bundle preserves a bounded candidate-decision-only path and records explicit `unavailable_evidence`. Partial R19 fields are propagated into namespaced unavailable evidence instead of becoming zeros.

Every next-cycle hypothesis contains:
- a target segment;
- a concrete directive;
- an expected observable;
- a falsification criterion;
- the target metric when applicable;
- evidence references;
- `causal_claim=false`;
- `human_preference_inferred=false`.

Candidate-decision re-edit hypotheses use exact R18 timecodes. R19 hypotheses map to concrete semantic/time segments such as the first 0-3000 ms hook, ending payoff/loop, CTA segment, or downstream-after-hook test region. Metric hypotheses are framed for the next matched/control or randomized test rather than as causal conclusions from historical observations.

The durable ledger is `growth.closed_loop_feedback_ledger.v1`. Identical same-revision bundles replay idempotently, changed payloads at the same revision conflict, and lower revisions fail closed across restart.

One-command replay:

`python -m growth_analytics.closed_loop_feedback_replay --output-dir /tmp/growth-r20-replay`

The committed replay uses synthetic evidence only and explicitly records:
- synthetic metrics do not count as live;
- model scores do not create human labels;
- no provider mutation.

R20 has no publish, provider, Creator, Media, release, or credential authority.
