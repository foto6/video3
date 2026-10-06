# Growth R37 — local-PC selection adapter / offline real-video decision gate

Growth R37 consumes a completed **local Windows Media R26 evidence bundle** and emits a deterministic Creator-consumable advisory package. It is intentionally unable to authorize live publication or provider mutation.

The umbrella contract is:

`growth.local_pc_selection_adapter.r37.v1`

## Frozen parent

R37 binds the exact Growth R36 parent:

- repo: `foto6/video3`
- SHA: `a53f9deb180bb256d193f0422c6ffd7a5923d97a`
- CI: `37403145649 SUCCESS`
- contract: `growth.local_rehearsal_evidence.r36.v1`
- authority blob: `a44963e13c10fd607cf4259e355dc990a297ecd9`
- policy blob: `e6f6dd25b70f1e1ec4031d7eb2d0e8bb7ceb1b82`
- contract blob: `3e0cd5baa85b4c59eb75386766aed0300a91b16d`
- implementation blob: `3eeb7bbacb423c3ed4dcd66391147d0e9fd58c37`
- verifier blob: `a35051e47a6289f1cc04c3ae3b73516dbc45378e`

R37 preserves R36's anti-leakage and no-live-action boundaries.

## Media R26 authority is deliberately unresolved

The checked-in R37 authority manifest does **not** guess a Media R26 SHA, CI run or artifact. It contains:

`media_r26_authority.state = UNRESOLVED`

and the required producer contract:

`media.local_render_runner.r26.v1`

Until independent QA supplies an exact accepted Media R26 tuple, every structurally valid local bundle emits:

`WAITING_MEDIA_R26_AUTHORITY`

No positive `KEEP_BASELINE`, `SELECT_CANDIDATE` or `TARGETED_REEDIT_RECOMMENDED` decision is allowed from the checked-in authority.

A future accepted authority manifest must contain:

- exact Media source Git SHA;
- exact Media CI run ID;
- CI conclusion `SUCCESS`;
- exact artifact tuple when the accepted Media evidence has an artifact;
- independent QA producer SHA/run/artifact/digest/matrix digest;
- exact `media.local_render_runner.r26.v1` contract ID.

An accepted authority with an unavailable Media artifact may explicitly use `artifact.availability=UNAVAILABLE` with null artifact fields. The adapter never infers or invents artifact identity.

## Required local Media evidence

The consumer input contract is:

`growth.local_pc_media_evidence.r37.v1`

Every bundle requires:

- Media contract ID exactly `media.local_render_runner.r26.v1`;
- claimed Media source SHA;
- source CI run and `SUCCESS` conclusion;
- Media artifact tuple, or explicit `UNAVAILABLE`;
- local-run manifest and exact manifest digest;
- exact input video SHA256 and size;
- candidate IDs, operation IDs, render SHA256 values and sizes;
- candidate review evidence;
- targeted re-edit ID, source lineage, artifact SHA256, operation-graph digest and review evidence;
- final artifact SHA256, size and lineage;
- exact ffmpeg and ffprobe binary/version digests;
- phase timings;
- resume events and checkpoint digests;
- a zero-side-effect local boundary.

The adapter treats the values inside a local bundle as **claims** until the Media authority manifest is independently accepted.

## Sealed manifest

The local-run manifest commits:

- input video identity;
- ffmpeg/ffprobe runtime identity;
- candidate ID → render hash mapping;
- all candidate/re-edit review evidence bytes;
- targeted re-edit hash;
- final artifact hash;
- phase timing digest;
- resume-event digest.

Changing a score, render hash, runtime hash, final hash, phase or resume event without resealing the manifest produces `EVIDENCE_INVALID`.

## Candidate identity and scoring

Selection never depends on filename or list order.

There must be exactly one baseline role and at least two distinct candidate identities. Candidate rendered bytes must also be distinct.

Each review binds:

- exact candidate/re-edit ID;
- exact source render SHA256;
- evidence class;
- score in `[0,1]`;
- evidence digest;
- current R37 review-policy contract/version;
- capture timestamp;
- `human_ground_truth=false`.

Evidence classes are explicit:

- `FIXTURE`
- `OFFLINE_MODEL`
- `GENUINE_REVIEW`

Fixture reviews are not eligible for positive selection. Offline-model and genuine-review evidence remain separate in normalization and audit output.

The frozen deterministic rule is:

- minimum 2 eligible reviews per candidate/re-edit;
- maximum within-subject score spread 0.25;
- baseline tie margin 0.02;
- alternative must beat baseline by at least 0.05;
- targeted re-edit must beat its selected source candidate by at least 0.03.

Tie with baseline → `KEEP_BASELINE`.

Conflicting/missing eligible evidence → `HUMAN_REVIEW_REQUIRED`.

A clear alternative → `SELECT_CANDIDATE`.

A clear re-edit improvement → `TARGETED_REEDIT_RECOMMENDED`.

## Lineage gates

R37 distinguishes four byte identities:

1. input video bytes;
2. candidate rendered bytes;
3. targeted re-edit bytes;
4. final artifact bytes.

The targeted re-edit must bind the exact selected source candidate and its render hash.

The final artifact must be byte-identical to the declared selected candidate or targeted re-edit lineage. A filename such as `final.mp4` is never trusted as evidence.

Wrong final lineage, changed render bytes, duplicate candidate bytes or a targeted re-edit derived from a non-selected source fail closed.

## Resume/checkpoint gates

Every resumed phase must include a checkpoint digest and a matching resume event.

The resume event binds:

- resume event ID;
- phase ID;
- operation ID;
- checkpoint digest;
- resume timestamp;
- deterministic event digest.

Missing or mismatched checkpoint evidence produces `EVIDENCE_INVALID`.

Duplicate phase/operation/resume identities are rejected.

## Local evidence cannot masquerade as hosted CI evidence

A valid bundle must state:

- `execution_context.mode=LOCAL_WINDOWS`
- `execution_context.evidence_origin=LOCAL_PC`
- `hosted_ci=false`
- `local_run=true`
- provider network used = false
- browser used = false

A local rehearsal cannot be relabeled as hosted-CI execution evidence.

## R36 normalization bridge

R37 emits a lossless `r36_projection` inside `growth.local_pc_selection_normalization.r37.v1`.

It retains:

- R36 parent contract identity;
- source/input hash;
- all candidate operation/render identities;
- review evidence classes/scores/digests;
- targeted re-edit lineage;
- final artifact lineage;
- Media R26 claim;
- local-run manifest digest;
- runtime identity digest;
- phase/resume digests.

This projection preserves lineage rather than converting local evidence into a weaker filename/order-based summary.

## Advisory outputs

The only recommendation values are:

- `KEEP_BASELINE`
- `SELECT_CANDIDATE`
- `TARGETED_REEDIT_RECOMMENDED`
- `HUMAN_REVIEW_REQUIRED`
- `WAITING_MEDIA_R26_AUTHORITY`
- `EVIDENCE_INVALID`

The Creator-facing contract is:

`growth.local_pc_selection_advisory.r37.v1`

Every envelope permanently carries:

- `disposition=ADVISORY_ONLY`
- `live_authorization=false`
- `provider_mutation_allowed=false`
- `creator_mutation_allowed=false`
- `browser_call_allowed=false`
- `provider_call_allowed=false`
- `social_publish_allowed=false`
- `credential_access_allowed=false`
- `human_ground_truth=false`

A positive advisory never authorizes provider mutation by itself.

## Local coordinator command

After Media R26 produces a completed local evidence bundle:

```powershell
python -m growth_analytics.local_pc_selection_adapter_r37 run `
  --bundle "C:\evidence\media-r26\local-run-evidence.json" `
  --authority ".\conformance\growth.local_pc_selection_adapter.r37.v1\authority.json" `
  --policy ".\conformance\growth.local_pc_selection_adapter.r37.v1\policy.json" `
  --out-dir ".\.artifacts\growth-r37-selection" `
  --growth-sha (git rev-parse HEAD) `
  --growth-ci-run-id 1
```

With the checked-in unresolved authority the command intentionally exits with code `3` and emits `WAITING_MEDIA_R26_AUTHORITY`.

It still writes normalized lineage and a Creator advisory so the coordinator can inspect the local evidence without silently accepting it.

## Deterministic adversarial rehearsal

CI runs:

```bash
python -m growth_analytics.local_pc_selection_adapter_r37 rehearse-fixtures \
  --authority conformance/growth.local_pc_selection_adapter.r37.v1/authority.json \
  --policy conformance/growth.local_pc_selection_adapter.r37.v1/policy.json \
  --out-dir /tmp/growth-r37-local-pc-selection \
  --growth-sha "$GITHUB_SHA" \
  --growth-ci-run-id "$GITHUB_RUN_ID"
```

The rehearsal contains at least 25 cases and includes ties, conflicting scores, missing reviews, partial candidates, duplicate identities/bytes, wrong final lineage, wrong targeted re-edit source, duplicate operation IDs, input/runtime drift, missing resume checkpoints, manifest tampering, hosted-CI impersonation and stale review policy.

Synthetic accepted Media authority appears only inside adversarial fixtures to exercise positive branches. It is not written into the checked-in authority and is never reported as actual Media R26 acceptance.
