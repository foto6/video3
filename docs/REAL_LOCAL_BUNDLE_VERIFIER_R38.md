# Growth R38 — real local bundle verifier

R38 defines `growth.real_local_bundle_verifier.r38.v1`. It verifies the forthcoming Media R27 local Windows evidence bundle byte-for-byte and emits a deterministic advisory result for Creator. It does not render, browse, call a provider, publish, or authorize publication.

## Exact authorities

Growth parent is the exact-green R37 verifier:

- `foto6/video3@f3cb8ec0d8aa7155b6169d5f86828de3fbc9d3ba`
- CI `37406299930 SUCCESS`
- artifact `11387826461`
- digest `sha256:d53c063ffd68481fd88901111e1683a175e8c54fd8fc07574b6c798cd12ff463`
- contract `growth.local_fullstack_verifier.r37.v1`

Current Media authority is:

- `foto6/video2@183838a24205c6885b2366ad6ffa394164283d91`
- CI `37451069045 SUCCESS`
- artifact `11406357346`
- digest `sha256:9cfa2ddd358f2b25a3066ee60792c44c460c62715590210bacf5c5430d14a4b5`
- contract `media.real_input_local_rehearsal.r27.v1`
- Growth bundle `media.real_input_growth_bundle.r27.v1`
- producer authority state `PENDING_INDEPENDENT_QA`

No independent QA acceptance for this exact Media R27 tuple was available when R38 was implemented. Therefore the checked-in authority remains QA-pending and a valid real bundle returns `WAITING_MEDIA_R27_QA`.

Current Creator authority binder is source-pinned at:

- `foto6/video1@b7dd4f7297db2d90317236b4108b6d3133979d2f`
- contract `creator.current_authority_binder.r39.v1`
- runtime blob `be96165df080a6646ac45cb9a0e1a5b4e963e4bd`
- assignment anchor `66efe791d816f07aca42d772e57eb53ad7ed331f`
- task blob `b37a8e28be305c43cd9d4f83ec6e3b1382374b2a`

Creator R39 itself pins the same Media R27 tuple and keeps Media QA pending. R38 does not substitute Creator R38 or a moving branch name for the current R39 source authority.

## Media bundle verification

Pass the extracted `growth/payload` directory from a completed Media R27 local Windows run. The directory must contain:

`media.real_input_growth_bundle.r27.manifest.json`

R38 verifies:

- exact Media producer SHA and contract IDs;
- manifest digest;
- original input video hash and size;
- normalized source hash, size, and normalization-spec digest;
- exactly four distinct candidate IDs and render hashes;
- every file listed by the sealed manifest, by SHA256 and byte size;
- `evidence/media.real_input_local_rehearsal.r27.evidence.json`;
- runtime manifest hash plus ffmpeg/ffprobe version identity;
- all sixteen Media R27 phase timing entries;
- restart/resume evidence;
- candidate hashes repeated in the Media summary;
- targeted re-edit hash;
- final artifact hash equal to targeted re-edit lineage;
- provider/browser/social/live-authorization boundaries.

A filename is never treated as proof of identity.

## Evidence classes

Optional review evidence uses `growth.real_local_review_evidence.r38.v1` and one explicit class:

- `FIXTURE`
- `OFFLINE_MODEL`
- `GENUINE_REVIEW`

The classes are not interchangeable. Media's built-in deterministic review decision is treated as `FIXTURE` when no review sidecar is provided. A fixture cannot produce a positive local-rehearsal-ready decision even after QA.

`OFFLINE_MODEL` and `GENUINE_REVIEW` remain model/review evidence only. Both require `human_ground_truth=false`.

Every review sidecar binds the exact Media manifest digest, input hash, all four candidate hashes, targeted re-edit hash, reviewer identities, response digests, winner and confidence.

## Deterministic consensus

The frozen policy requires three independent reviews. Positive consensus requires:

- unanimous winner;
- every confidence >= 0.82;
- mean confidence >= 0.86.

`tie`, `insufficient_evidence`, disagreement, low confidence, or too few reviews becomes `HUMAN_REVIEW_REQUIRED`.

If the unanimous winner is the sealed targeted re-edit, the advisory decision may be `READY_FOR_CREATOR_LOCAL_REHEARSAL` after exact Media QA acceptance.

A baseline or initial candidate winner produces `NEEDS_REEDIT`; R38 never silently treats Media's fixture-targeted final as that different lineage.

## Independent Media QA

The optional QA certificate contract is:

`growth.media_r27_independent_qa.r38.v1`

It must bind the exact Media R27 SHA, CI, artifact ID/digest, producer contract, and Growth bundle contract. QA certificates marked `fixture_only=true` are rejected in real verification mode.

Without a real accepted certificate, the result remains:

`WAITING_MEDIA_R27_QA`

## Durable replay ledger

R38 stores `growth-r38-real-local-ledger.json` under the supplied ledger directory.

The ledger binds:

- Media operation binding identity;
- sealed manifest digest;
- review evidence digest;
- Media QA digest;
- Creator R39 authority digest.

Exact replay is a no-op. Reusing the same Media operation identity with changed sealed manifest bytes is a hard replay conflict.

## Windows verification command

After Media R27 finishes and the bundle tar is extracted:

```powershell
python -m growth_analytics.real_local_bundle_verifier_r38 verify `
  --bundle-dir "C:\media-r27-output\growth\payload" `
  --authority ".\conformance\growth.real_local_bundle_verifier.r38.v1\authority.json" `
  --policy ".\conformance\growth.real_local_bundle_verifier.r38.v1\policy.json" `
  --ledger-dir "C:\media-r27-output\growth-r38-ledger" `
  --out "C:\media-r27-output\growth-r38-verification.json" `
  --growth-sha (git rev-parse HEAD) `
  --growth-ci-run-id 1
```

When an exact independent Media QA certificate becomes available, add:

```powershell
  --media-qa "C:\evidence\media-r27-independent-qa.json"
```

For external review evidence, also add:

```powershell
  --review-evidence "C:\evidence\growth-r38-review-evidence.json"
```

With the current authority the expected exit code is `3`, meaning the real bundle can be verified but Media R27 remains QA-pending.

## Safety boundary

Every successful or blocked result carries:

- `publish_authorized=false`
- `live_authorization=false`
- `provider_mutation_authorized=false`
- `browser_mutation_authorized=false`
- `creator_mutation_authorized=false`
- `credential_access_authorized=false`
- `human_ground_truth=false`

`READY_FOR_CREATOR_LOCAL_REHEARSAL` is not publish authorization.

## Adversarial coverage

CI runs at least 35 deterministic cases: missing QA, fixture-class non-promotion, offline/genuine review separation, candidate/final byte tamper, summary tamper, manifest tamper, producer/contract drift, duplicate candidate IDs/hashes, review digest/lineage drift, duplicate reviewers, low confidence, ties, disagreement, runtime/phase/resume loss, effect-boundary drift, wrong QA tuple, fixture-QA rejection, replay no-op, replay conflict, Creator authority drift, and Media artifact authority drift.
