# Growth R25 — Bridge R29 Real-Capture Consumer / Creator Handoff

R25 starts at exact Growth R24 `dc0741d5b11c1f7464ac9a6c5db80c0a4535df08`. R24 remains unchanged: its R26 fixture and capture behavior is still available and its fixture evidence is still fixture-only.

## Bridge R29 authority

R25 does not trust `agent/bridge-r29-isolated-live-video-review-20261002` as an authority. A moving branch is advisory discovery only.

A real capture must carry a `growth.bridge_r29_capture_authority.v1` provenance object with all of:

- repository exactly `foto6/WebAIBridge`;
- exact 40-hex Bridge producer SHA;
- capture contract exactly `bridge.existing_chat_video_review_capture.v1`;
- exact Git blob SHA-1 for the capture contract;
- exact Git blob SHA-1 for the capture implementation.

The caller supplies an exact authority-profile JSON and the capture provenance must match it byte-for-field. This lets the eventual R29 artifact be consumed without baking a moving branch ref into Growth.

## Genuine capture requirements

Production ingest accepts only `capture_kind=bridge_existing_chat_capture`. It requires `model_evidence=true` and `human_ground_truth=false`.

The capture must bind the exact conversation, request and operation identifiers; exact Media R18 prompt text digest; exact blinded `review-A.mp4` / `review-B.mp4` SHA-256 and byte sizes; and exact assistant response bytes plus SHA-256.

The assistant response itself is parsed through the R24 strict JSON validator. Coverage keeps `uninspected_possible=true` and `every_frame_inspected=false`. Actionable observations retain A/B labels, bounded timestamps, supported defect category, severity/evidence, proposed edit, confidence and uncertainty. Pairwise output remains exactly `A`, `B`, `tie`, or `insufficient_evidence`.

## Deterministic downstream conversion

After R29 provenance is verified, R25 deterministically emits:

1. one `growth.web_video_critic.v1` result for Media R18 A;
2. one `growth.web_video_critic.v1` result for Media R18 B;
3. one `growth.web_video_critic_pairwise.v1` result;
4. the existing R23 `growth.creator_reedit_handoff.v1` for each candidate.

The verified R29 capture digest is the attached-video transport evidence digest. No provider upload, prompt send, Creator mutation or Media mutation is performed by Growth.

## Creator R26/R27 envelope

R25 wraps each candidate handoff in `growth.creator_external_review_envelope.r25.v1`. Its nested `creator_event` uses the exact field shape of `creator.external_real_review_event.r26.v1`:

`contractVersion`, `producer`, `captureMode`, `reviewIdentity`, `handoff`.

The producer descriptor binds the exact Growth R25 producer SHA and CI run, while retaining the unchanged R23 handoff contract/schema/adapter blob identities. The envelope separately binds the R29 capture digest, assistant-response digest, source SHA, render SHA, attachment SHA/size, handoff digest and re-edit round.

Current Creator R26 pins the producer to Growth R23. R25 does not spoof that old pin. Creator R27 or a later Creator pin update must explicitly accept the exact R25 producer SHA.

## Replay

The optional `--ledger` file makes re-ingest behavior durable across process restart. The exact same capture is idempotent and produces no second handoff effect. Reuse of a capture ID with changed response/bytes, or a different capture for the same conversation/request, is a conflict.

## Current status

At implementation time the R29 branch was observed at `8b314bd020b05d90f6c45fa861727df5e78e5a39`, which is still the R28 transport commit and is **not** treated as R29 authority. No R29 workflow run or genuine capture artifact was available.

Therefore readiness is:

`SOURCE_READY`

with live gate:

`BLOCKED_WAITING_R29_CAPTURE`

No capture is fabricated and no fixture is promoted to real evidence.

## Commands

Source-ready readiness report with no capture:

```bash
python -m growth_analytics.real_capture_r25 \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id 1 \
  --observed-r29-branch-head 8b314bd020b05d90f6c45fa861727df5e78e5a39 \
  --out-dir /tmp/growth-r25 \
  --report /tmp/growth-r25-readiness.json
```

Once Bridge R29 publishes its exact authority profile and genuine capture JSON:

```bash
python -m growth_analytics.real_capture_r25 \
  --capture /path/to/bridge-r29-capture.json \
  --bridge-authority /path/to/bridge-r29-authority.json \
  --reedit-round 0 \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <exact-green-r25-ci-run-id> \
  --ledger /path/to/growth-r25-ingest-ledger.json \
  --out-dir /tmp/growth-r25-live \
  --report /tmp/growth-r25-live-readiness.json
```

A successful real ingest reports the exact Bridge R29 producer SHA, capture digest, assistant-response digest, and both Creator envelope/handoff digests.
