# Growth R29 — exact Media R23 + Bridge R32 session ingest

R29 closes the authority gap that intentionally remained in R28. It adds exact source-bound acceptance for Media R23 review-session packages and Bridge R32 live session-round results without changing R28's default frozen package behavior.

## Exact green authorities

- Growth R28: `foto6/video3@629a2b9ddf59b84eee4e87b257c161dad42831dc`, CI `37006476121 SUCCESS`.
- Media R23: `foto6/video2@78c6982a91d7e3e8c037cd9ce740ee077babdccc`, CI `37007419237 SUCCESS`.
- Media R23 artifact: `11226183002 media-r23-review-session-package`, digest `sha256:5170ada97f8c86421f4bee34c97fbfa5701bef74ef406f18889a1a790ae3ac66`.
- Bridge R32: `foto6/WebAIBridge@805bf628d3d2844549b54db1112736fae0200fc7`, CI `37006524677 SUCCESS`.
- Creator R30: `foto6/video1@50c17852a910c57f0894dcdb356d4d4923edb62b`, CI `37006988818 SUCCESS`.

All contract/schema/implementation Git blobs and relevant Actions artifact digests are frozen in:

`conformance/growth.exact_session_ingest.r29.v1/authority-profiles.json`

Moving branch names are never authority.

## R23 validation

The input is a Media R23 round directory containing:

```
media.review_session_request.r23.v1.json
media.review_session_package.r23.v1.json
media.review_session_package.r23.evidence.json
operator/
```

R29 first verifies exact R23 producer SHA/CI/contracts and the request/package/evidence byte bindings. It recomputes the R23 session identity and verifies source, brief lineage, selected Growth lineage, candidate rounds, render hashes, and the exact nested R21/R22 authorities.

The nested `operator/` directory is then passed through the R28 validator. R29 uses a strict opt-in exact-package override derived only from the already validated R23 wrapper. R28's default path is unchanged: callers that do not provide that override retain the old exact R22 round freeze.

The nested checks still include:

- every R22 package-manifest payload hash and size;
- exact R21 bundle/handoff/evidence contracts;
- package digest;
- prompt bytes and prompt digest;
- exact A/B MP4 hashes, sizes and MIME;
- sealed-mapping content/digest;
- source/brief/round lineage;
- candidate/render identity;
- reconstructed R31/R30 transport package and source-binding fingerprints.

The official Media R23 CI artifact contains byte-verified round 0, 1 and 2 packages. Their exact identities are frozen as evidence, but R29 can also validate later packages produced by the same exact R23 implementation when all source-bound checks pass.

## R32 validation

The primary live input is:

`r32-round-N-result.json`

with contract `bridge.r32_live_review_session_round_result.v1`.

R29 requires:

- `LIVE_REVIEW_PASS`;
- `model_evidence=true`;
- `human_ground_truth=false`;
- no retry-upload or retry-Send authorization;
- exact R23 session ID and review round;
- exact dedicated conversation ID and canonical URL;
- R32 source, brief, package, session-package, prompt and A/B attachment fingerprints equal the validated package;
- exact request ID, operation ID, response digest and capture digest.

A sanitized R32 result does not contain the assistant response bytes. Therefore R29 also requires the exact R31 result and its sibling R30 capture/response evidence before producing executable Growth output. By default it locates:

`round-sidecars/round-N/r31-live-result.json`

relative to the R32 result directory. `--r31-result` can explicitly provide the exact path.

R29 cross-checks R32 request/operation/response/capture identities against R31/R30 and then invokes the existing R28/R26 strict response parser. Model-facing A/B labels are unblinded only after all of these checks pass.

## Session and replay

The R29 session identity binds:

- Media session ID;
- source ID/SHA/size;
- brief-lineage digest;
- one dedicated review conversation ID;
- exact R29 authority-profile digest;
- exact Growth R28, Media R23, Bridge R32 and Creator R30 producer authorities.

Ledger:

`<session-dir>/growth-r29-session-ledger.json`

Review sequence remains exactly `0 -> 1 -> 2`. A skip or fourth review fails closed. Exact replay is a no-op. Changed response, capture, R23 session-package, nested mapping, R32 round-result, or request identity under an existing round is a conflict.

## Creator output

For a genuine live round R29 writes:

- both canonical `growth.dynamic_creator_external_review_envelope.r26.v1` candidate envelopes;
- full pairwise context;
- one `growth.exact_session_round_result.r29.v1`;
- when A/B resolves to a real candidate, one outer `growth.creator_r30_selected_review_result.r29.v1`.

The inner envelope contract and its canonical Growth R26 producer authority are preserved for compatibility. The outer selected result separately binds the actual Growth R29 SHA/CI and exact Creator R30 authority.

Tie and insufficient-evidence never invent a winner. Human-review remains non-publishable. `--terminal-winner` can explicitly close the Growth session after a winner.

## Genuine one-command ingest

```bash
python -m growth_analytics.exact_session_ingest_r29 ingest \
  --media-session-dir /path/to/media-r23-round-N \
  --bridge-round-result /path/to/r32-session-state/r32-round-N-result.json \
  --authority-profile conformance/growth.exact_session_ingest.r29.v1/authority-profiles.json \
  --session-dir /durable/growth-r29-session \
  --out-dir /durable/growth-r29-output \
  --conversation-id <dedicated-review-conversation-id> \
  --growth-sha <exact-r29-sha> \
  --growth-ci-run-id <exact-r29-ci-run>
```

The CLI surface is also the future R24/R33 entrypoint. A Media R24 package or Bridge R33 result is rejected until its exact SHA/CI/contracts/blobs have been frozen into the R29 authority profile.

## Source-ready and fixture evidence

Validate an exact Media R23 package without a capture:

```bash
python -m growth_analytics.exact_session_ingest_r29 source-ready \
  --media-session-dir /path/to/media-r23-round-N \
  --authority-profile conformance/growth.exact_session_ingest.r29.v1/authority-profiles.json \
  --out-dir /tmp/growth-r29-source-ready \
  --conversation-id <dedicated-review-conversation-id> \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <run-id>
```

Validate the exact Bridge R32 CI rehearsal without promoting it:

```bash
python -m growth_analytics.exact_session_ingest_r29 fixture-readiness \
  --bridge-fixture /path/to/r32-multiround-evidence.json \
  --authority-profile conformance/growth.exact_session_ingest.r29.v1/authority-profiles.json \
  --out-dir /tmp/growth-r29-r32-fixture \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <run-id>
```

Bridge R32 CI evidence is fake-CDP/session rehearsal evidence only: `SOURCE_READY`, `model_evidence=false`, no real upload and no real prompt Send. It can never produce `LIVE_REVIEW_INGESTED`.

`MALFORMED_MODEL_RESPONSE` and `RECONCILIATION_REQUIRED` emit readiness evidence only and never write executable Creator handoffs.

R29 performs no browser or provider mutation and does not merge.
