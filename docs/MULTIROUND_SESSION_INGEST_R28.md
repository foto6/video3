# Growth R28 — Multi-round session ingest

R28 wraps the exact-green R27 single-round ingest into one durable review session spanning review rounds 0, 1 and 2.

## Exact authorities

- Growth R27: `foto6/video3@d80592ad660b7b73ad13298880918a5944411c38`, CI `37002456031 SUCCESS`.
- Media R22: `foto6/video2@e82a7ac04f3758d0e3e21ea3d05265dbc2822132`, CI `37001071721 SUCCESS`, artifact `11224061610`, digest `sha256:d534f1656e22b72cf42531c827e66516965b4167549906521dee04edafd01734`.
- Bridge R31: `foto6/WebAIBridge@104281e49122233f251c692abba726ae31cee0d5`, CI `36999908386 SUCCESS`.
- Creator R29: `foto6/video1@4a58561b07d615c141fa609d212070c894bfd0cb`, CI `37002020115 SUCCESS`. The current Creator R30 branch resolves to that exact same head.

The observed Media R23 branch currently also resolves to Media R22 head `e82a7ac…`; there is no distinct exact-green Media R23 producer yet. R28 therefore accepts round 2 only when it is materialized by the exact frozen Media R22 materializer. A future distinct R23 producer is rejected until explicitly frozen into the authority profile.

## Package validation

For every round, R28 validates the outer Media R22 operator directory first:

- exact operator manifest contract/state;
- deterministic tar SHA-256/size;
- exact R22 materializer producer SHA/CI and implementation blobs;
- package-manifest file-set digest;
- every payload file SHA-256 and size;
- model-facing prompt bytes/digest;
- A/B MP4 bytes, sizes and MIME;
- machine-side sealed mapping digest and candidate/render identity;
- embedded Media R21 bundle/handoff/evidence/source/brief/round lineage.

It then reconstructs Bridge R31's derived `media.dynamic_review_handoff.v1`, including the R22 archive SHA, payload directory digest, exact R21 bundle-file SHA used as the producer contract blob, prompt bytes, A/B bytes, mapping digest and source/round lineage. The incoming R31/R30 capture must match the reconstructed dynamic package digest, handoff SHA and source-binding fingerprint.

Unblinding occurs only after all of those checks and strict assistant JSON validation pass.

## Session identity and ledger

A session is bound to:

- source ID/SHA/size;
- brief-lineage digest;
- one dedicated review conversation ID;
- the complete R28 authority-profile digest.

The durable ledger is `<session-dir>/growth-r28-session-ledger.json`. Each committed round records capture/request/operation/assistant-response identity, package/mapping identity, pairwise result, selected candidate and both Creator envelopes.

Round order is strictly `0 -> 1 -> 2`. Skips and a fourth review fail closed. Exact replay is a no-op. Changed capture, response, package or sealed-mapping bytes under the same session round/request identity are conflicts.

A winner may close the session when `--terminal-winner` is supplied. Without that flag, a winner can remain available as the selected result while the coordinator continues to a later review round. Tie, insufficient evidence and human-review states remain non-publishable and do not invent a selected winner.

## Failure boundaries

`MALFORMED_MODEL_RESPONSE` exits with state `MALFORMED_MODEL_RESPONSE` and emits no Creator executable handoff.

`RECONCILIATION_REQUIRED` exits with state `RECONCILIATION_REQUIRED` and emits no Creator executable handoff or retry authorization.

Fixture rehearsal is always `fixture_only=true`, remains `SOURCE_READY`, emits no Creator handoff, and can never become live evidence.

Model evidence always preserves:

- `human_ground_truth=false`;
- `human_rating_evidence=false`;
- `human_parity_inferred=false`.

R28 performs no browser or provider mutation.

## Genuine one-command round ingest

```bash
python -m growth_analytics.multiround_session_ingest_r28 ingest \
  --operator-dir /path/to/media-r22-or-frozen-r23-operator-round \
  --capture /path/to/r31-live-result.json \
  --authority-profile conformance/growth.multiround_session_ingest.r28.v1/authority-profiles.json \
  --session-dir /durable/growth-r28-session \
  --out-dir /durable/growth-r28-output \
  --conversation-id <dedicated-review-conversation-id> \
  --growth-sha <exact-r28-sha> \
  --growth-ci-run-id <exact-r28-ci-run>
```

`--capture` can also point directly to the genuine sibling R30 capture envelope. For R31 input, R28 requires and cross-checks sibling `r30-live-capture.json` and `r30-live-response.txt`.

To close a session on a reviewed winner, add `--terminal-winner`.

## Source-ready validation

```bash
python -m growth_analytics.multiround_session_ingest_r28 source-ready \
  --operator-dir /path/to/media-r22-operator-round \
  --authority-profile conformance/growth.multiround_session_ingest.r28.v1/authority-profiles.json \
  --session-dir /durable/growth-r28-session \
  --out-dir /durable/growth-r28-source-ready \
  --conversation-id <dedicated-review-conversation-id> \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <exact-run-id>
```

## Non-live multiround rehearsal

```bash
python -m growth_analytics.multiround_session_ingest_r28 rehearse-fixtures \
  --fixture fixtures/session_ingest_r28/multiround-rehearsal.json \
  --authority-profile conformance/growth.multiround_session_ingest.r28.v1/authority-profiles.json \
  --out-dir /tmp/growth-r28-rehearsal \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <exact-run-id>
```

The rehearsal proves sequence and boundary logic only; it is not a model review.
