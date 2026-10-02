# Growth R26 — Dynamic Review Capture

R26 adds a separate dynamic review path. It does **not** alter the R24 static fixture path or the R25 frozen Media R18 real-capture path.

## Exact source authorities

The current exact-green Media source is:

- repo `foto6/video2`
- producer `b22174db3c772a49a21fb9f8b1d40828bf258005`
- CI `36988788032`
- artifact `media-r20-dynamic-review` / ID `11218642168`
- artifact digest `sha256:430b64164487d19ec66e76f70edeab640410f88565ed8ae854b8bbc28bd2f410`

R26 validates the actual R20 package, evidence, prompt-manifest and sealed-mapping bytes. The authority profile additionally pins the Media producer SHA, package contract, exact contract/schema/implementation Git blobs, package digest, prompt digest, attachment A/B hash+size+MIME, sealed mapping digest, source lineage and review round.

The current exact-green Bridge input is:

- repo `foto6/WebAIBridge`
- producer `ed9a35290f94607d7577f1ee9301de1bb44334f2`
- CI `36989658042`
- capture contract `bridge.existing_chat_video_review_capture.v1`

The Bridge authority profile has no branch name. It binds the exact producer SHA, capture contract/schema identity, and contract/schema/implementation Git blobs.

Branch names are discovery hints only. They are never accepted as source authority.

## Validation and unblinding order

R26 validates in this order:

1. exact Media package/evidence/prompt/sealed-mapping bytes and authority;
2. exact Bridge producer profile;
3. genuine capture evidence: exact request, prompt, attachment bytes, conversation and assistant-response digest, `model_evidence=true`, `human_ground_truth=false`, real attachment/send evidence, and `LIVE_REVIEW_PASS`;
4. strict model JSON with explicit incomplete coverage;
5. only after all previous checks pass, model-facing A/B labels are unblinded through the exact sealed mapping.

A/B is therefore never candidate identity. Candidate identity comes only from the sealed machine-side mapping.

## Dynamic package semantics

The consumer accepts R20-compatible packages whose authority declares either:

- `media.dynamic_review_package.r20.v1`
- `media.dynamic_review_package.r21.v1`

If a future R21 schema diverges from the R20-compatible shape, Growth fails closed until that schema is explicitly implemented; an exact producer/profile does not silently authorize unknown semantics.

Review rounds remain bounded to 0–2. The package review round must equal the maximum candidate round in the sealed mapping, with no skipped intermediate round.

Explicit review derivatives are supported. A derivative attachment may differ from the render hash only when the sealed mapping says it is a review derivative. Source render identity and attachment identity remain separately bound in the Creator envelope.

## Model response and edits

R26 accepts the existing `growth.live_video_review_response.v1` strict JSON shape and the strict Bridge R29 native response shape.

For the Bridge-native shape, transport normalization can generate only mechanical wrapper fields that were not present in the Bridge response. Generated summaries are marked as generated and get confidence 0. This does not create a human or model rating.

Coverage always preserves:

- `uninspected_possible=true`
- `every_frame_inspected=false`
- bounded inspected ranges
- every actionable timestamp inside an inspected range

Model `proposed_edit` text remains audit-only. It is never executable. Executable directives come only from Growth's allowlisted defect-category → edit-operation mapping.

## Creator-ready output

Each validated candidate gets a `growth.dynamic_creator_reedit_handoff.r26.v1` and a `growth.dynamic_creator_external_review_envelope.r26.v1`.

The envelope binds:

- exact Growth R26 producer SHA and CI run;
- exact Media authority and package digest;
- exact Bridge producer authority;
- capture and assistant-response digests;
- sealed mapping digest;
- source identity;
- real candidate ID;
- candidate round and review round;
- render SHA/size;
- attachment SHA/size/MIME;
- pairwise selection and unblinded selected candidate ID;
- handoff digest and allowlisted directives.

No Creator, Media or provider mutation is performed by this consumer.

## Replay

The optional durable ledger makes an exact re-ingest idempotent.

The same capture/package identity with changed response bytes, capture payload, or sealed-mapping file bytes is a conflict. A second differing capture for the same conversation/request/package is also a conflict.

## Current gate

Bridge R29 exact-green evidence reports `liveReviewPass=false` and no actual live capture, and it still materializes frozen Media R18 rather than the R20 dynamic artifact. Bridge R30 currently points at the same R29 SHA.

Therefore R26 must report:

`SOURCE_READY / BLOCKED_WAITING_DYNAMIC_CAPTURE`

It must not report `LIVE_REVIEW_INGESTED` until a genuine dynamic Bridge capture satisfying the exact package bindings is supplied.

## Reproducible source-ready command

After extracting exact Media artifact `11218642168`:

```bash
python -m growth_analytics.dynamic_review_capture_r26 \
  --media-package /path/initial-review/media.dynamic_review_package.r20.v1.json \
  --media-evidence /path/initial-review/media.dynamic_review_package.r20.evidence.json \
  --sealed-mapping /path/initial-review/media.dynamic_review_sealed_mapping.r20.v1.json \
  --prompt-manifest /path/initial-review/media.direct_model_review_prompt.v1.json \
  --media-authority conformance/growth.dynamic_live_review_capture.r26.v1/media-r20-initial-authority.json \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <exact-r26-run-id> \
  --observed-bridge-sha ed9a35290f94607d7577f1ee9301de1bb44334f2 \
  --out-dir /tmp/growth-r26-dynamic \
  --report /tmp/growth-r26-dynamic/readiness.json
```

When an exact genuine Bridge dynamic capture exists, add:

```bash
  --capture /path/to/bridge-dynamic-capture.json \
  --bridge-authority /path/to/exact-bridge-r29-or-r30-authority.json \
  --ledger /path/to/growth-r26-dynamic-ledger.json
```

The live report then contains the exact Bridge producer SHA, package digest, sealed mapping digest, capture digest, assistant-response digest and per-candidate Creator envelope/handoff digests.
