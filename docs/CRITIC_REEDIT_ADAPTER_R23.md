# Growth R23 — Web-Video Critic → Creator Re-Edit Adapter

R23 converts `growth.web_video_critic.v1` output into the bounded
`growth.creator_reedit_handoff.v1` contract. It does not upload a video, mutate a
provider, edit Creator/Media state, or claim that a real MP4 was reviewed.

## Exact authorities

- Growth R22: `foto6/video3@0f6824d7c3962ccee572b21a4e1a343e6470c1a9`.
- Bridge R26: `foto6/WebAIBridge@73c13f9eed2a2cbcea881dd8c5452d054bfef940`.
  R26 remains `READY_FOR_EXPLICIT_LIVE_REHEARSAL` with `livePass=false`. Its CI
  proves bounded file-attachment/reconciliation semantics with a fake browser;
  it is not evidence of a real ChatGPT MP4 upload or review.
- Media R18: `foto6/video2@2c41f084e000eca5efd9a51d2d3752bec1bd1311`,
  exact-green CI `36967381891`, contract `media.direct_model_review_package.v1`.
  Its package explicitly records `liveUploadPerformed=false` and
  `modelJudgmentPerformed=false`.

## Deterministic re-edit projection

Every emitted directive is bound to the exact source SHA, Media producer SHA,
candidate render SHA/size/export SHA, MP4 attachment SHA/size/identity, review
bundle digest, R22 critic input digest, and critic output digest. Local defects
retain their timestamp, evidence, confidence, and uncertainty. In direct
attached-video mode a defect interval must be contained in reported inspected
coverage.

Defect categories map through a fixed allowlist to supported edit operations:
trim, cut, crop/scale/reframe, speed change, fade/transition, text overlay,
subtitles/captions, audio duck/mix, and intro/outro/CTA. The model's free-form
`proposed_edit` is preserved for audit but is always marked non-executable.
Unknown mappings fail closed.

## State and round policy

The handoff state is one of `winner`, `targeted_reedit`, `tie`,
`insufficient_evidence`, or `human_review`. At most two re-edit rounds are
allowed. A still-actionable result at the round-2 boundary becomes
`human_review`; it cannot request a third re-edit.

Any R22 fixture/non-attached execution becomes `insufficient_evidence` with no
executable directives. This is deliberate: current R22 exact lineage is
rehearsal-ready but does not prove a real MP4 review.

## Evidence and replay boundary

Model review always carries `human_ground_truth=false`. Coverage uncertainty is
preserved (`uninspected_possible=true`, `every_frame_inspected=false`).
Stale hashes, source/render/attachment mismatch, provenance drift, contradictory
timestamps, unsupported edits, and duplicate handoff replay are rejected.

The adapter is advisory only. It sets Creator mutation, Media mutation, provider
mutation, upload, publish authorization, and release authorization to false.
Thus the R23 fixture and CI perform no real MP4 review, no live upload, and no
provider mutation.
