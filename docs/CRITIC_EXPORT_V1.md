# Growth R16 Canonical Human-Benchmark Critic Export

## Authority and scope

Growth R16 implements the producer export required by the independent benchmark:

- authority repository: `foto6/boss`
- authority branch: `agent/human-editing-gate-supervisor-20261001-v2`
- authority head: `e0763bebf2aad9402f8de8c60edb1b9eb8c4be8e`
- protocol: `boss.human_editing_gate.v1`

The canonical producer artifact is exactly:

`growth.critic_export.v1.json`

Growth does not own or emit the benchmark human-ratings study. In particular, `human_ratings.v1.ndjson` remains benchmark-owned in `foto6/boss`.

## Required top-level contract

The export contains exactly these keys:

- `contract_version`
- `repository`
- `commit_sha`
- `source_id`
- `render_sha256`
- `critic_mode`
- `model_or_rule_identity`
- `dimension_observations`
- `timecoded_evidence`
- `hard_failure_observations`
- `pairwise_if_used`
- `human_ground_truth`

The contract version is `growth.critic_export.v1`. Repository is fixed to `foto6/video3`. The producer Git commit must be a full 40-character SHA-1 and the render identity must be a full SHA-256.

## Benchmark rubric mapping

The independent benchmark has ten per-dimension human rubric fields. Growth maps its R15 observations as follows:

| Benchmark rubric | Growth R15 source dimension |
| --- | --- |
| hook | hook_clarity_first_1_3s |
| pacing | pacing_coherence |
| semantic_cut_correctness | semantic_cut_correctness |
| framing_crop | subject_framing_crop_quality |
| broll_relevance | broll_relevance |
| captions | caption_readability_emphasis_relevance |
| continuity | visual_continuity |
| motion_appropriateness | motion_zoom_appropriateness |
| audio_balance | audio_voice_music_balance |
| payoff_cta_loop | payoff_cta_loop_coherence |

The benchmark's human `overall_preference` is deliberately not synthesized from Growth scores. Pairwise critic output stays in `pairwise_if_used` as advisory non-human evidence.

R15's additional `awkward_dead_moments` dimension is retained in time-coded evidence with `rubric_dimension=null`; it is not forced into an unrelated benchmark dimension.

## Missing evidence

Every benchmark dimension is present in `dimension_observations`, but unavailable Growth evidence is explicit:

- `available=false`
- `normalized_score=null`
- a non-empty `unavailable` reason

The exporter never invents a score to fill a missing observation.

## Evidence separation

Each rubric dimension keeps deterministic rule evidence and optional VLM observations separate. Model/VLM evidence includes provenance where present. Time-coded actionable notes and hard failures remain separate arrays.

The export never turns a normalized structural score into a 1–5 human rubric rating. It never turns VLM confidence into a human score.

## Human-ground-truth boundary

All Growth critic exports are machine/model/rule evidence. Therefore:

`human_ground_truth` is always `false`.

Validation fails closed if any of these occur:

- the top-level export sets `human_ground_truth=true`;
- a rule observation claims human ground truth;
- a VLM observation claims human ground truth;
- time-coded critic evidence claims human ground truth;
- a hard-failure machine observation claims human ground truth;
- pairwise rule/model/VLM evidence claims to be a human label or human ground truth.

The writer is filename-locked to `growth.critic_export.v1.json`. A caller attempting to write a critic export to `human_ratings.v1.ndjson` is rejected.

## Pairwise critic evidence

R15 structural pairwise and R15B Gemini native-video pairwise decisions remain advisory. The export preserves:

- comparison digest;
- candidate render SHA-256 values;
- selection and reason;
- model confidence when applicable;
- time-coded pairwise evidence where available;
- blinded Gemini provenance where applicable;
- `advisory_only=true`;
- `human_label=false`;
- `human_ground_truth=false`.

The validator also requires the exported render SHA to be one of the pairwise candidates. An unrelated pairwise result cannot be attached to a canonical export.

## Canonical fixture and producer SHA

Because a Git commit cannot contain a file that recursively names its own final commit SHA, R16 uses a two-commit provenance pattern:

1. the first R16 commit contains the exporter implementation, validator, schema, tests, docs, exports and CI gate;
2. the canonical `growth.critic_export.v1.json` committed in the second R16 commit names the exact first R16 implementation commit in `commit_sha`.

This gives the benchmark an exact immutable producer build without self-referential Git hashing. The final conformance manifest pins both the implementation commit and every relevant blob.

## Gemini/fake-transport proof

R16 does not call Gemini and does not add credentials. It consumes the already-pinned R15B fake-transport pairwise fixture as provenance evidence. The R16 non-human proof fixture records that:

- the R15 structural critic is non-human evidence;
- the R15B Gemini fake transport is VLM/model evidence;
- R15B remains `HUMAN_LEVEL_UNPROVEN`;
- Growth writes no benchmark human-ratings artifact.

No actual human ratings are fabricated or implemented in Growth.

## Authority

R16 is export-only and advisory. It performs no publishing, provider mutation, Creator mutation, Media mutation, merge, or release.
