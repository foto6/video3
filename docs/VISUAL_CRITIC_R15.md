# Growth R15 Post-Render Visual Critic

## Scope

Growth R15 adds an advisory post-render critic for short-form candidate edits. It consumes immutable render/probe/timeline evidence, emits dimension-specific editorial judgments and time-coded notes, and supports pairwise A-vs-B selection for later Creator tournament workflows.

It does not publish, mutate providers, modify Media outputs, modify Creator campaigns, or grant release authority.

The primary report contract is \`growth.visual_critic.r15.v1\`. Pairwise output is \`growth.visual_critic_pairwise.r15.v1\`.

## Source-bound input

Every critic input binds:

- rendered artifact identity and SHA-256 digest;
- duration, dimensions, and frame rate;
- sampled-frame digests, timecodes, shot IDs, subject boxes, motion/zoom observations, frame role, optional semantic match, and continuity markers;
- contact-sheet digest and exact sampled-frame count;
- audio/probe summary including loudness, peak, speech coverage, and silence intervals;
- caption timeline with reading speed, contrast, collision, and emphasis-relevance observations;
- overlay timeline with collision and semantic-relevance observations;
- edit cut timecodes and optional source-boundary/semantic-safe observations;
- optional source semantic timeline;
- Media QA contract identity, exact artifact digest, technical/creative state, failures, and warnings.

Input normalization is content-addressed by \`input_digest\`. Media QA must bind the exact rendered artifact digest.

## Structural critic dimensions

The deterministic baseline reports each dimension independently, with its own score, confidence, evidence list, and limitation:

1. hook clarity in the first 1–3 seconds;
2. pacing coherence;
3. semantic cut correctness;
4. subject framing/crop quality;
5. B-roll relevance;
6. caption readability and emphasis relevance;
7. visual continuity;
8. motion/zoom appropriateness;
9. audio/voice/music balance;
10. payoff/CTA/loop coherence;
11. awkward/dead moments.

Uncertainty is intentionally not collapsed into a single quality number. Missing source evidence produces \`score=null\` plus an explicit limitation.

Structural scoring only uses supplied evidence. It does not claim to see pixels that were not represented by frame/probe observations.

## Evidence classes

The contract separates four evidence classes:

- **objective hard failures** — e.g. Media technical failures, caption collisions, or clipping-threshold violations;
- **structural heuristic judgments** — deterministic rules over source-bound frame, audio, caption, cut, and semantic evidence;
- **VLM/model opinion** — optional provider observations with provider/model identity, request digest, confidence, timecode, dimension, and note;
- **human labels** — accepted only by the calibration harness as explicit \`human_provided\` pairwise labels.

A model/VLM observation is never labeled as human ground truth. Candidate critic reports contain no human labels.

## Optional VLM boundary

\`VLMVisualCriticAdapter\` is an observation-only boundary. It accepts a normalized critic input and returns versioned \`growth.visual_critic_vlm_observation.v1\` observations.

No credential fields exist in the boundary. Returned observations are recursively checked for token, authorization, password, secret, API-key, or credential-like fields/values. Missing provider state is valid and produces a deterministic structural-only report with an explicit limitation that pixels were not directly inspected by a VLM.

## Time-coded actionable notes

The critic emits actionable notes with:

- timecode or whole-clip scope;
- dimension;
- severity;
- source class (\`objective_hard_failure\`, \`structural_heuristic\`, or \`vlm_opinion\`);
- message.

The deterministic fixture localizes, among other cases:

- poor subject crop near 7.2s;
- low-relevance/extreme-motion B-roll near 11.0s;
- caption collision around 8.7s;
- weak first-seconds structural clarity;
- semantically unsafe cuts;
- voice/music masking risk;
- extended silence plus low motion.

## Pairwise comparison

Pairwise A-vs-B is the preferred selection mode.

The comparison first considers objective hard-failure counts. If those are equal, only dimensions with non-null scores and sufficient confidence are compared. The output is one of:

- \`A\`;
- \`B\`;
- \`tie\`;
- \`insufficient_evidence\`.

A tie is explicit when the dimension-specific confidence-weighted difference is inside the configured margin. Insufficient evidence is explicit when too few dimensions are comparable.

Pairwise output is machine-readable and marked as \`tournament_selection_input=true\`, but remains advisory and has \`publish_authorized=false\`.

## Human calibration and benchmark gate

Human labels use \`growth.visual_critic_human_pairwise_label.v1\` and must:

- declare \`source_kind=human_provided\`;
- bind the exact pairwise comparison ID/digest and both candidate artifact digests;
- carry an annotator reference;
- carry an observation timestamp;
- carry an external provenance digest.

Synthetic/model labels are rejected by the human-label parser.

\`growth.visual_critic_calibration.r15.v1\` computes agreement metrics only from supplied human labels. With zero labels, \`agreement_metrics=null\`.

The default readiness minimum is 30 real labels. Until that minimum is supplied, the report state is exactly:

\`HUMAN_LEVEL_UNPROVEN\`

Crossing the corpus-size threshold changes the state only to \`HUMAN_BENCHMARK_CORPUS_READY\`. It does not claim human-level performance; the report explicitly states that agreement metrics do not establish human-level editorial judgment.

## Deterministic fixtures

The R15 fixture pack contains source-bound synthetic evidence for:

- a deliberately good edit;
- a deliberately bad edit with localized crop/B-roll/caption/audio/dead-moment faults;
- two structurally equivalent candidates producing an explicit tie;
- two sparse candidates producing \`insufficient_evidence\`.

The committed fixture pack contains **zero human labels** and therefore reports \`HUMAN_LEVEL_UNPROVEN\` with no agreement metrics.

## Preserved provenance and authority

R15 starts from exact green R14 head:

\`foto6/video3@eae1dba5258ebc661332955c2561f35354245eb1\`

The conformance manifest pins R14 and all R15 source/test/doc/fixture Git blobs.

Authority is fixed:

- advisory only;
- no publishing;
- no provider mutation;
- no Media mutation;
- no Creator mutation;
- no release authorization.

No Creator or Media repository changes are part of this milestone.
