# Growth R19 Post-Publish Learning Loop

R19 adds `growth.post_publish_learning.v1` and `growth.post_publish_brief_seed.v1` on top of the existing R17 platform runtime and R18 candidate-decision contracts.

The learning record binds the exact publish result, Media render SHA-256, metric snapshot digest/event, observation window, cycle revision, and optional exact-lineage R18 candidate decision.

Evidence is kept in three separate layers:
- observed provider metrics: only fields directly present in the selected platform metrics event;
- derived analytics: explicitly labeled derivations such as watch-time/views, completed-views/views, retention AUC, and link CTR;
- speculative hypotheses: bounded testable next-cycle changes with target metric, expected direction, evidence references, and `causal_claim=false`.

Historical performance may prioritize a test hypothesis, but the contract hard-codes that it does not prove causality and cannot retroactively create a human-preference label.

Missing or delayed snapshots produce `insufficient_or_delayed` evidence and a collect-more-evidence hypothesis rather than invented values. Partial metrics remain null with explicit `unavailable_evidence`. R17 runtime states for deleted posts, provider outages, backoff, stale data, or clock-skew/out-of-order conditions remain visible and block treatment as fresh evidence.

R18 linkage is optional. When supplied, the decision must validate under `growth.candidate_decision.v1`; the decision cycle revision must match the publish result and exactly one candidate render SHA must match the published Media render SHA. Otherwise the learning record marks candidate-decision evidence unavailable rather than fabricating lineage.

Durability uses `growth.post_publish_learning_ledger.v1`. Duplicate identical revisions are idempotent; changed bytes at the same revision conflict; lower revisions and observation windows that move backwards are rejected.

The next brief seed is advisory only. It contains bounded, testable hypotheses plus a hold-constant directive for other dimensions. Synthetic replay output is always `source_class=synthetic_fixture`, never claims live performance, and is not Creator-cycle eligible.

One-command replay:

`python -m growth_analytics.post_publish_learning_replay --output-dir /tmp/growth-r19-replay`

No provider mutation, posting, credentials, Creator/Media repository mutation, or HUMAN_LEVEL claim is part of R19.
