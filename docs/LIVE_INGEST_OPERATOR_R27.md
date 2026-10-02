# Growth R27 — Live Ingest Operator

R27 is a file-only coordinator operator. It accepts an exact Media R21/R22 round-review package directory plus authority profile, and optionally an exact genuine Bridge R30/R31 dynamic capture plus capture authority profile. It performs no browser, provider, Creator, Media, publish, or human-rating mutation.

## Exact green inputs at implementation

Media R21:

- repository: `foto6/video2`
- producer SHA: `d753e9e4c1f4448386608a1425232dbc1dba87ea`
- exact-head CI: `36994000619`
- artifact: `media-r21-round-pair-review`
- artifact ID: `11221240371`
- archive digest: `sha256:1036800923196882590ace62edbaa123ab4250b9d242e14adba909ba256ab022`

Bridge R30:

- repository: `foto6/WebAIBridge`
- producer SHA: `ceaee873231a8552c5b7324083baa800eec566a8`
- exact-head CI: `36993885456`
- capture contract: `bridge.dynamic_existing_chat_video_review_capture.v1`
- dynamic handoff schema blob: `93968dc1fb65a334493acdb587b20753f0a8494a`
- capture schema blob: `2cbe22ad6c7fe877764bad8dcfc1496aef3f3737`
- dynamic implementation blob: `c5bd2f95a6d58a86cddd9a6fdc127e68e3346c20`

Additional exact-green producers became available during implementation:

- Media R22: `5c9a382b3083e30642ac27f047705a06309311a6`, CI `36998852352`, artifact `media-r22-live-review-artifact` ID `11223041326`, archive digest `sha256:7fc5fc969d3b46e9584f45b9b57d3c61a7b443e5aa37e6abc06b37de6cd69854`.
- Bridge R31: `31cfef82663d72d53e69e6345b50073ffcd461ca`, CI `36997793086`. Its coordinator output uses the same R30 capture contract plus native `bridge.r31_media_r21_authority_profile.v1` and `bridge.r31_live_dynamic_operator_result.v1`.
- Creator R29: `614d2338ab01f59130cec4b35a3b275b86c48892`, CI `36999034034`, parser blob `d8e359c9c6880b0e571fa44badded079783271b0`.

Branch names remain advisory and are never source authority.

## Media package authority

`growth.media_review_round_authority.r27.v1` binds:

- exact producer round, SHA and CI;
- exact package contract plus contract/schema/manifest/implementation/runner Git blob SHA-1s;
- Actions artifact ID/name/digest;
- exact bundle, evidence, transport handoff, sealed-mapping and prompt file SHA-256s;
- package digest;
- prompt digest;
- sealed mapping digest;
- source and brief lineage;
- review-round lineage digest;
- A/B attachment hash, byte size and MIME.

R27 includes exact profiles for both directories currently in Media R21 artifact 11221240371:

- `media-r21-initial-authority.json`
- `media-r21-round-1-authority.json`

The operator re-hashes the real package files and MP4s. It recomputes the R21 package digest, sealed mapping digest, prompt digest and round-lineage digest rather than trusting the evidence JSON alone.

For native Media R22 packages, R27 accepts the packaged `media.live_review_authority_profile.r22.v1` directly. It pins exact R22 producer/CI and exact contract, schema, implementation, exporter and verifier Git blobs; verifies the R22 package manifest and every listed payload byte; then validates the embedded R21 candidate/mapping/round lineage. R22 source authority remains in the coordinator index.

For targeted re-edit packages it also verifies baseline/challenger N -> N+1 lineage, render identities, prior selection, Growth handoff digest and Media application digest.

## Bridge live-capture authority

`growth.bridge_live_capture_authority.r27.v1` is capture-specific. It binds the exact Bridge producer SHA/CI/schema/blob identities and the exact capture-file SHA-256, plus:

- request ID;
- operation ID;
- conversation ID;
- prompt digest;
- exact Media package digest;
- exact sealed mapping digest;
- Bridge transport package digest;
- Bridge dynamic handoff SHA-256;
- source-binding fingerprint;
- assistant-response digest.

R30 profiles must exactly match the known green producer and blobs above. R31 may be supplied natively as `r31-authority-profile.json`; R27 requires the exact R31 producer/CI/source blobs and a genuine `r31-live-result.json` with state `LIVE_REVIEW_PASS`. The live-result file is auto-discovered beside the capture or may be supplied explicitly with `--bridge-live-result`. No moving branch is accepted as authority.

## Live boundary

A capture becomes live model evidence only when all of the following hold:

- contract is `bridge.dynamic_existing_chat_video_review_capture.v1`;
- disposition is exactly `LIVE_REVIEW_PASS`;
- `model_evidence=true`;
- `human_ground_truth=false`;
- `human_label=false`;
- `realAttachment=true`;
- `realSendCaptured=true`;
- strict response JSON validation succeeded;
- request/operation/conversation IDs exactly match the authority profile;
- prompt, A/B attachment bytes, dynamic Media producer/round/source lineage, sealed mapping reference and assistant-response digest all match.

Fixture capture, fake-CDP evidence, `MALFORMED_MODEL_RESPONSE`, and `BLOCKED` are rejected as live input.

## Unblinding and Creator output

A/B is never treated as candidate identity. R27 validates the Media package, Bridge capture and strict model response first. Only then does it read the exact sealed mapping.

The validated evidence is converted through the R26 dynamic review converter into per-candidate `growth.web_video_critic.v1` evidence and pairwise output.

For every candidate R27 writes the canonical existing contract:

`growth.dynamic_creator_external_review_envelope.r26.v1`

No manual field rewriting is required. The coordinator index records each envelope filename/digest and handoff digest.

Creator R29 is exact-green at `614d2338ab01f59130cec4b35a3b275b86c48892`, CI `36999034034`. Its parser accepts the canonical R26 dynamic envelope and freezes the Growth R26, Media R21 and Bridge R30 compatibility authority surface.

R27 therefore separates **source authority** from **Creator compatibility authority**. The coordinator index preserves the exact R21/R22 and R30/R31 producer evidence and true candidate generation round. Each Creator envelope preserves the canonical R26 contract and exact Growth R26 producer identity `e844ed2daaaca9e9694fe1e0fb6b8b7bfac69cbc` / CI `36994388154`. For R22 or a baseline from a prior generation round, only Creator-facing compatibility fields are normalized and all dependent directive/handoff/envelope digests are regenerated. The index retains both the original source handoff digest and the Creator-compatible handoff digest, so no lineage is lost or silently overwritten.

## Coordinator index

`growth.live_ingest_operator_index.r27.v1` is the single handoff index.

For an A/B verdict it contains:

- full pairwise rationale/confidence/uncertainty;
- both candidate results;
- real candidate IDs recovered from the sealed mapping;
- both canonical Creator envelope paths and digests;
- selected candidate ID/result/envelope/handoff digest.

For `tie` or `insufficient_evidence`, `selected_result` is null while both candidate results and pairwise context remain present.

## Replay

Live mode requires a durable ledger.

Before semantic parsing, R27 fingerprints the actual capture bytes and actual package/mapping/prompt/attachment bytes. This allows replay classification to happen before stale content can be misclassified as a new review.

- exact capture + package + mapping replay: no-op;
- same capture identity with changed response bytes: conflict;
- same capture identity with changed package bytes: conflict;
- same capture identity with changed sealed-mapping bytes: conflict;
- same conversation/request with changed capture/package: conflict.

An exact replay emits the same coordinator-index digest and `new_handoff_effect=false`.

## One-command source-ready verification

After extracting Media R21 artifact 11221240371:

```bash
python -m growth_analytics.live_ingest_operator_r27 \
  --media-package-dir /path/to/media-r21/round-1 \
  --media-authority conformance/growth.live_ingest_operator.r27.v1/media-r21-round-1-authority.json \
  --ledger /tmp/growth-r27/ledger.json \
  --out-dir /tmp/growth-r27/out \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <exact-r27-ci-run-id> \
  --report /tmp/growth-r27/readiness.json
```

Without a genuine Bridge capture the result is exactly:

`SOURCE_READY / BLOCKED_WAITING_DYNAMIC_CAPTURE`

No `LIVE_REVIEW_INGESTED` claim is fabricated.

## One-command genuine live ingest

When the coordinator has a genuine R30 capture with a Growth authority profile, or a genuine R31 capture with its native authority/live-result files:

```bash
python -m growth_analytics.live_ingest_operator_r27 \
  --media-package-dir /path/to/exact-media-r21-or-r22-package \
  --media-authority /path/to/exact-media-authority.json \
  --bridge-capture /path/to/genuine-bridge-dynamic-capture.json \
  --bridge-authority /path/to/exact-capture-authority-or-r31-authority-profile.json \
  --bridge-live-result /path/to/r31-live-result.json \
  --ledger /path/to/durable-growth-r27-ledger.json \
  --out-dir /path/to/creator-r29-handoff \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <exact-r27-ci-run-id> \
  --report /path/to/creator-r29-handoff/readiness.json
```

Successful live mode writes:

- `growth.dynamic_live_review_capture.r26.v1.json`;
- one `growth.dynamic_creator_external_review_envelope.r26.v1` file per real candidate;
- `growth.live_ingest_operator_index.r27.v1.json`;
- `operator-effect.json`;
- readiness JSON.

All model evidence remains `human_ground_truth=false`; the operator never reads or writes a human-rating artifact.
