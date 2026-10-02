# Growth R24 — Live Review Ingest + Creator Re-Edit Handoff

R24 adds a fail-closed ingest boundary for an **already captured existing-chat assistant response** that reviewed the exact blinded Media R18 MP4 pair. It does not upload media, send a provider prompt, mutate Creator or Media, or turn model opinion into human evidence.

## Exact authorities

The branch starts from Growth R23 `26f769abceb43a63677ea8f7ba028369db371696`. The capture must bind Bridge R26 `foto6/WebAIBridge@73c13f9eed2a2cbcea881dd8c5452d054bfef940` and Media R18 `foto6/video2@2c41f084e000eca5efd9a51d2d3752bec1bd1311`, CI `36967381891`.

The exact Media R18 Actions artifact is `media-r18-direct-model-review`, artifact ID `11210373001`, digest `sha256:1f2ac715ec787be14564201445737f19825f56267a7cab6fb99553aaaff013eb`. Its exact review prompt file SHA-256 is `98e377f73f26d9960acc34703797f962ef75472597acdbefc6dfdc82636b06b6`.

The blinded files are:

- A: `review-A.mp4`, candidate-1, SHA-256 `3bd12d999264cb932cb3ef15dfbd96e002ede517f2273795b593553b9642d864`, 575465 bytes.
- B: `review-B.mp4`, candidate-2, SHA-256 `cdae63d5ccce67332e607af7fd87b18c31da8dc77c2f0ce5182550321767286c`, 576763 bytes.

## Capture and response boundary

`growth.live_video_review_capture.v1` binds the conversation ID, review-request ID, assistant-message ID, exact raw response SHA-256, exact prompt digest, blind labels, attachment SHA/size, source/render lineage, Media authority, and Bridge authority.

The captured assistant body must be strict JSON using `growth.live_video_review_response.v1`. Free-form-only answers are rejected. Each actionable defect requires an A/B attachment label, bounded timestamp interval, supported defect category, severity, evidence, description, proposed edit, confidence, and uncertainty. Its timestamp interval must be contained in a declared inspected range.

Coverage is required for both attachments and always preserves `uninspected_possible=true` and `every_frame_inspected=false`. Pairwise selection is restricted to `A`, `B`, `tie`, or `insufficient_evidence`.

A captured model result is always model evidence: `human_ground_truth=false`, `human_label=false`, `human_parity_inferred=false`, and it is not live platform evidence.

## R22 → R23 handoff

After strict capture validation, R24 deterministically creates one `growth.web_video_critic.v1` result for each blinded candidate plus one `growth.web_video_critic_pairwise.v1` result. The exact validated capture digest is the transport-evidence digest.

R22 historically rejects attached-video mode because its pinned R25 transport authority records `livePass=false`. R24 preserves that default. The attached-video parser only admits this path when the caller supplies the same externally validated capture digest; unverified R22 attached-mode callers still fail closed.

The validated critic result is passed to the existing R23 `growth.creator_reedit_handoff.v1` adapter. R23 remains responsible for supported operation mapping, exact binding, coverage checks, decision states, and the maximum two re-edit rounds. R24 does not execute those edits.

## Replay behavior

An exact re-ingest of the same captured response returns the identical `growth.live_video_review_ingest.v1` digest and records no second handoff effect. Reuse of a capture ID with different response bytes, or a different response for the same conversation/review-request identity, is rejected as conflicting evidence.

## Readiness evidence

The committed fixture is parser/negative-test evidence only. Successful fixture validation is reported as:

`FIXTURE_VALIDATED`

It must never be promoted to:

`REAL_ATTACHED_VIDEO_REVIEW_INGESTED`

No genuine Bridge capture of an assistant response reviewing the exact R18 attachments was available while implementing this milestone. Therefore the readiness report deliberately remains:

`BLOCKED_REAL_ATTACHED_VIDEO_REVIEW_CAPTURE_REQUIRED`

That gate can only become satisfied by ingesting a genuine `capture_kind=bridge_existing_chat_capture` envelope with exact R18/Bridge/prompt provenance.

## Reproduction

Fixture validation, which proves parser and boundary behavior but not a real video review:

```bash
python -m growth_analytics.live_review_ingest \
  --fixture fixtures/live_review_ingest_r24/captured_review_fixture.json \
  --output /tmp/growth-r24-fixture-validation.json \
  --report /tmp/growth-r24-live-review-readiness.json \
  --growth-sha "$(git rev-parse HEAD)" \
  --run-id local
```

When a genuine Bridge capture exists, ingest it without any provider mutation or upload:

```bash
python -m growth_analytics.live_review_ingest \
  --capture /path/to/genuine-bridge-existing-chat-capture.json \
  --reedit-round 0 \
  --output /tmp/growth-r24-live-ingest.json \
  --report /tmp/growth-r24-live-review-readiness.json \
  --growth-sha "$(git rev-parse HEAD)" \
  --run-id local
```

The real-capture command parses existing captured evidence only. It does not send a prompt, upload an MP4, publish content, or mutate a provider.
