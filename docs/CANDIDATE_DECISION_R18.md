# Growth R18 Candidate Decision / Targeted Re-edit Policy

R18 adds `growth.candidate_decision.v1` for advisory selection across 2-4 rendered candidates while preserving all R10-R17 contracts.

Evidence classes remain separate:
- objective defects: hard failures from R15/R16 critic exports;
- deterministic structural/editorial rule evidence;
- model/VLM pairwise and per-dimension opinion, always non-human;
- actual human pairwise labels only when supplied through the existing R15 human-label contract;
- lineage-verified live platform observations from R17;
- historical metrics, which remain directional/observational and never causal winner evidence.

Winner policy is fail-closed. Missing expected candidates produce `insufficient_evidence`. A unique zero-hard-failure candidate may win over objectively defective alternatives. Otherwise a structural winner requires enough comparable rule dimensions, confidence, and margin. Contradictory non-human critics or human labels prevent a clean aesthetic winner. Pairwise model output never becomes human preference.

Live metrics are accepted only when their publish result is `platform_export`, the R17 runtime status binds the exact publish result/provider receipt/Media render SHA, and the supplied metric snapshot is also live and lineage-matched. Stale/partial/no-data runtime states remain visible but are not eligible live evidence. Synthetic metrics are rejected as live evidence.

Historical metric context must explicitly use `directional_observational_not_causal` with `causal=false`; it is never winner-eligible.

Targeted re-edit guidance is generated only from concrete hard failures or time-coded warning/hard-failure evidence. Each directive includes candidate ID, segment start/end, defect/dimension, evidence class, concrete directive, and evidence note.

Durability uses append-only `growth.candidate_decision_ledger.v1`. Duplicate identical revisions replay idempotently; same revision with changed bytes conflicts; lower revisions are rejected as out-of-order.

Authority remains advisory only: no publish, provider, Creator, Media, or re-edit mutation authority.
