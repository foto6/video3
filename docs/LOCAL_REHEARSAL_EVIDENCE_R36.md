# Growth R36 — local rehearsal evidence / offline decision adapter

Growth R36 makes the existing video-review stack consumable by a local coordinator with **zero network/provider side effects in the adapter**. It validates a materialized Media review package, normalizes the frozen review evidence, computes the existing deterministic consensus semantics, and emits an advisory canary-evidence envelope.

R36 is not a live canary launcher. It never allocates traffic, mutates Creator/provider/browser state, reads credentials, publishes, or consumes live metrics.

## Parent gate

The exact parent is Growth R35:

- repo: `foto6/video3`
- SHA: `c97f987e6ae780489f2a5fef1883d5596843c64c`
- CI: `37247403389 SUCCESS`
- artifact: `11319801779`
- digest: `sha256:d6a01a548f05c14723a0740218ce39a8a6edee902ed66cfa716e3aa78000cf02`
- contract: `growth.canary_evidence_registry.r35.v1`

At R36 implementation time, no independent QA acceptance for this exact R35 tuple was available. The checked-in authority therefore declares:

`independent_parent_qa.disposition = PENDING`

The adapter validates the complete local bundle even while parent QA is pending, but it emits:

`WAITING_PARENT_QA`

and **does not emit an effective offline decision**.

A future coordinator may supply a separate authority manifest with `independent_parent_qa.disposition=ACCEPTED`. Such a manifest must bind the exact R35 SHA/CI/artifact/digest above and include an exact QA producer SHA, CI run, artifact ID/digest and matrix digest. The adapter does not infer acceptance from a moving branch or from its own tests.

## Contract

Umbrella contract:

`growth.local_rehearsal_evidence.r36.v1`

Nested contracts:

- `growth.local_media_review_bundle.r36.v1`
- `growth.local_review_normalization.r36.v1`
- `growth.local_offline_decision.r36.v1`
- `growth.local_canary_evidence_envelope.r36.v1`
- `growth.local_rehearsal_verification.r36.v1`
- `growth.local_rehearsal_authority.r36.v1`
- `growth.local_rehearsal_policy.r36.v1`

## Frozen local video-review bundle

The checked-in fixture manifest is:

`fixtures/local_rehearsal_r36/bundle.json`

It binds the exact Media R24 artifact:

- Media SHA `244acdf154741e669991b17df3ef2a47e2dfdfa9`
- CI `37195239582`
- artifact `11301055747`
- digest `sha256:fc5c9b9635d49b643e66efafe602d21ce1ef695a553f81a797bdf16d7b8cf228`

The adapter consumes a **materialized local** `round-0` directory. It delegates byte validation to the already-frozen R30 Media validator, which verifies the actual MP4 attachment bytes, prompt, sealed mapping, manifest, nested R23 package and exact producer lineage.

Round-0 identity is frozen to:

- session: `r23-real-session`
- session identity: `e53c6c45febf4a1281de6902a77a0795011bdb99f4e7aec25afeae516ee74560`
- review round: `0`
- package digest: `65b591b7991cc4b0c4fa5ecbabda2d7bb58418b873792ce55f92c93cd08fc470`
- R29 package digest: `6a7b79f617de400a4a086e64a0cae9feceafa51a9a324074e85fb07ebf58dfc9`
- prompt digest: `2f79d24571f0e9d0fb051702678125d6b50308323aab0923b154ee7e16b3c9d2`
- sealed mapping digest: `01cef4f83ebf90c9fd11d058251a27daef2fc76b276c170b86e32d7fc98b1fa6`
- source SHA256: `bf7a2423ae26cb7bba73cb93930e46df95adad1619d79d2a08bfe3b072812309`

Candidate A is `r20-initial-alternative`, render/attachment SHA256 `70fdc25373b544b98aad4f8b5180fc56e69aea9ce11c3d5fb65b08ac02c50cb2`.

Candidate B is `r20-initial-control`, render/attachment SHA256 `1855a1745d53329294c680600299a9ca0308a7c468b27d6dab94d21a72605054`.

Blind labels are never trusted as candidate identity by themselves; the exact sealed mapping is validated before candidate IDs are exposed.

## Review normalization and consensus

The three local review manifests are the existing frozen R30 fixture reviews. R36 verifies each top-level review manifest by Git blob identity, then R30 verifies the nested response bytes, R29 round-result bytes, exact conversation/request/operation/capture/response bindings and inner candidate envelopes.

The normalized output contains:

- exact source/candidate/round hashes;
- package/prompt/mapping identity;
- canonical review order;
- review capture/response identities;
- structured votes and timestamped defects;
- consensus state/digest;
- disagreement score and rule/rejection reasons.

The consensus algorithm remains the frozen R30 policy. R36 does not tune thresholds.

## Offline decision

When parent QA is explicitly accepted, R36 emits `growth.local_offline_decision.r36.v1`.

For accepted consensus it emits an exact winner bound to:

- candidate ID;
- blind label;
- source SHA;
- render SHA;
- attachment SHA;
- review round;
- consensus digest.

When no winner is accepted, R36 may emit targeted re-edit directives derived only from the frozen allowlist:

`trim, cut, crop_scale_reframe, speed_change, fade_transition, text_overlay, subtitles_captions, audio_duck_mix, intro_outro_cta`.

Model free-form `requested_edit` text is **audit-only** and is never promoted into an executable operation.

With current parent QA state `PENDING`, only a non-effective preview is emitted and `offline-decision.json` is absent.

## Canary evidence envelope

R36 always emits `growth.local_canary_evidence_envelope.r36.v1`.

For the checked-in fixture:

- source class = `SYNTHETIC_TEST`;
- readiness eligible = `false`;
- registry submission allowed = `false`;
- live authorization = `false`;
- provider mutation allowed = `false`;
- Creator mutation allowed = `false`;
- publish allowed = `false`;
- human ground truth = `false`.

Thus even a hypothetical QA-accepted fixture rehearsal cannot promote itself into R35 readiness evidence.

## Independent verifier

The independent verifier is a separate module:

`growth_analytics.local_rehearsal_verifier_r36`

It does not call the adapter. It independently recomputes normalization/result/decision/envelope/report digests, verifies exact parent tuple and source/candidate/round binding, checks bundle linkage, and fails if any live/provider/Creator side-effect flag becomes true.

## Local Windows command

First materialize the exact Media R24 artifact locally and extract it so that a `round-0` directory exists. Artifact retrieval is a coordinator preparation step; the R36 adapter itself performs no network call.

From the repository checkout in PowerShell:

```powershell
python -m growth_analytics.local_rehearsal_evidence_r36 run `
  --media-dir "C:\evidence\media-r24\round-0" `
  --reviews-dir ".\fixtures\consensus_review_r30\accepted" `
  --bundle-manifest ".\fixtures\local_rehearsal_r36\bundle.json" `
  --authority ".\conformance\growth.local_rehearsal_evidence.r36.v1\authority.json" `
  --policy ".\conformance\growth.local_rehearsal_evidence.r36.v1\policy.json" `
  --r30-authority-profile ".\conformance\growth.consensus_review_oracle.r30.v1\authority-profiles.json" `
  --r30-policy ".\conformance\growth.consensus_review_oracle.r30.v1\aggregation-policy.json" `
  --out-dir ".\.artifacts\growth-r36-local-rehearsal" `
  --growth-sha (git rev-parse HEAD) `
  --growth-ci-run-id 1
```

With the checked-in authority this intentionally exits with code `3` and writes `WAITING_PARENT_QA` evidence.

Verify the output independently:

```powershell
python -m growth_analytics.local_rehearsal_verifier_r36 `
  --output-dir ".\.artifacts\growth-r36-local-rehearsal" `
  --authority ".\conformance\growth.local_rehearsal_evidence.r36.v1\authority.json" `
  --bundle-manifest ".\fixtures\local_rehearsal_r36\bundle.json" `
  --report ".\.artifacts\growth-r36-local-rehearsal\independent-verification.json"
```

## Adversarial rehearsal

CI runs at least 35 deterministic scenarios including parent-QA spoofing/drift, wrong Media/source/candidate/round hashes, prompt/mapping/package drift, review byte tampering, Media MP4 byte tampering, R30 authority/policy drift, order invariance, verifier tampering, unsafe live-authorization mutation, directive allowlisting and fixture non-promotion.

A synthetic accepted-parent authority is used only inside the adversarial test harness to prove the positive code path. It is explicitly marked scenario-only and is never reported as real R35 acceptance.
