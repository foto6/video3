# Growth R15B Concrete Gemini Native-Video Critic

## Scope

R15B adds one concrete optional Google Gemini native-video provider to the existing Growth R15 visual critic. The deterministic structural critic remains the default and remains valid when no Gemini provider is configured.

The provider is advisory only. It does not publish, mutate provider accounts, modify Media renders, modify Creator campaigns, or grant release authority.

## Explicit enablement

Live Gemini execution is disabled unless the process environment contains a non-empty `GEMINI_API_KEY`.

Configuration:

- `GEMINI_API_KEY`: required for any live request.
- `GEMINI_MODEL`: optional model override. The default is `gemini-3.8-flash`.
- `GEMINI_VIDEO_MODE`: optional `static` or `agentic`; default `static`.
- `GEMINI_VIDEO_FPS`: optional static sampling FPS; default `2.0`.

The API key is kept only in runtime memory and request headers. It is never added to critic reports, request digests, fixtures, conformance manifests, or logs emitted by this implementation.

CI uses only an injected fake transport and no live credentials.

## Native-video path

The concrete adapter requires the actual finished `video/mp4` file. Before upload it verifies:

1. local file exists and has an `.mp4` suffix;
2. file size is non-zero and below the configured upload bound;
3. SHA-256 of the actual MP4 bytes equals the render artifact digest in `growth.visual_critic.r15.v1`;
4. `ffprobe` duration matches the critic-input render duration within the configured tolerance.

The MP4 is then uploaded through Gemini's Files API and sent to the Interactions API as a native `video` input. The prompt follows the video input and contains compact source-semantic/edit/QA grounding only. It deliberately excludes the R15 sampled-frame/contact-sheet payload from the primary judgment path.

This follows the current Google Gemini video-understanding guidance: native video inputs can be uploaded with the Files API, referenced by URI in Interactions, can include timestamp-oriented prompts, and can use static or supported agentic video processing. Static mode can specify FPS.

## Single-candidate structured observations

The adapter requires Gemini to return exactly one time-coded observation for every R15 critic dimension:

- hook clarity in the first 1–3 seconds;
- pacing coherence;
- semantic cut correctness;
- subject framing/crop quality;
- B-roll relevance;
- caption readability/emphasis relevance;
- visual continuity;
- motion/zoom appropriateness;
- audio/voice/music balance;
- payoff/CTA/loop coherence;
- awkward/dead moments.

Every observation must include a dimension, timestamp, judgment, confidence, and concise note. Timestamps are validated against the `ffprobe` duration. Missing dimensions, duplicate dimensions, malformed JSON, invalid confidence, unsupported judgments, or out-of-range timestamps fail closed.

The existing R15 VLM observation contract remains the integration surface for the base critic. Every converted Gemini observation carries:

- provider: `google_gemini`;
- configured model;
- native-video processing mode in the note and wrapper metadata;
- deterministic request digest;
- confidence;
- timecode;
- explicit VLM-opinion semantics.

The R15B wrapper `growth.visual_critic_gemini_native_video.r15b.v1` also records provider/model/mode/request digest and verified video/ffprobe provenance. `human_ground_truth` is always false.

## Pairwise blinded native-video mode

R15B pairwise output uses `growth.visual_critic_gemini_native_video_pairwise.r15b.v1`.

The structural R15 A-vs-B comparison is computed first. Candidate presentation order for Gemini is then derived deterministically from SHA-256 of the structural comparison digest.

Gemini receives only blinded labels `candidate_1` and `candidate_2`. Compact pairwise contexts omit candidate IDs and render digests. Upload metadata uses the generic display name `native-video.mp4` rather than the local filename.

Only after Gemini has returned and the strict response has been validated is `candidate_1` or `candidate_2` mapped back to A or B.

A Gemini pairwise preference is allowed to resolve a structural tie when confidence meets the configured gate and time-coded evidence is present. Weak Gemini evidence does not override the structural result. In particular, a structural `insufficient_evidence` result remains `insufficient_evidence` when the Gemini response is weak.

The pairwise result retains:

- blinded-presentation provenance;
- presentation digest;
- request digest;
- provider/model/mode;
- blind selection;
- mapped selection;
- confidence;
- mapped time-coded evidence;
- structural comparison;
- final advisory selection.

It never labels the model result as human preference ground truth.

## Bounded reliability and cleanup

Defaults are intentionally bounded:

- maximum upload size: 100 MiB;
- request timeout: 75 seconds;
- upload timeout: 75 seconds;
- transport retries: 2 after the first attempt;
- file processing polls: 45;
- poll interval: 2 seconds;
- ffprobe timeout: 8 seconds;
- duration tolerance: 0.35 seconds.

Uploaded Gemini files are deleted in `finally` paths after single-candidate and pairwise evaluation. Cleanup also runs on malformed model output.

## Live smoke command

Live smoke is opt-in and is not part of CI:

`python -m growth_analytics.gemini_visual_critic_smoke --video /path/final.mp4 --critic-input /path/critic_input.json`

The command refuses to run without `GEMINI_API_KEY`. The critic input must already be a valid `growth.visual_critic.r15.v1` document whose render SHA-256 matches the MP4 bytes.

## Human benchmark status

R15B does not add, synthesize, or infer human labels. The inherited benchmark status remains exactly:

`HUMAN_LEVEL_UNPROVEN`

until a real blinded human pairwise corpus satisfies the R15 calibration gate. Gemini preference, even when high-confidence and evidence-backed, remains model opinion.

## Test strategy

CI uses a fake Gemini transport only. Regression coverage includes:

- provider absent -> original deterministic structural-only critic;
- explicit key/model enablement;
- actual MP4 digest binding;
- ffprobe duration binding;
- all-dimension structured output requirement;
- timestamp rejection;
- bounded retry;
- cleanup after success and malformed output;
- no credential material in reports;
- deterministic blinded pairwise ordering;
- strong fake Gemini evidence resolving a structural tie;
- weak fake Gemini evidence preserving structural insufficient-evidence.

No live provider request is issued by CI.

## Preserved authority

R15B starts from exact green R15 head:

`foto6/video3@c881932dd90f14d4fd0a090d1809192873e95177`

No publishing, provider mutation, Media mutation, Creator mutation, merge, or release is part of this milestone.
