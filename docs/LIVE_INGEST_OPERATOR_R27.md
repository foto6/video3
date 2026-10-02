# Growth R27 — Exact Dynamic Authorities + Live Ingest Entrypoint

R27 starts from exact Growth R26 `e844ed2daaaca9e9694fe1e0fb6b8b7bfac69cbc` and adds an operator layer only. R24/R25 frozen/static paths and the R26 dynamic parser remain unchanged.

## Exact authorities

The authority file is:

`conformance/growth.live_ingest_operator.r27.v1/authority-profiles.json`

It freezes:

- Media R21 `foto6/video2@d753e9e4c1f4448386608a1425232dbc1dba87ea`, CI `36994000619`;
- Media R21 contract/schema/manifest/implementation/runner Git blobs;
- exact Actions artifact `11221240371`, `media-r21-round-pair-review`, digest `sha256:1036800923196882590ace62edbaa123ab4250b9d242e14adba909ba256ab022`;
- exact initial and round-1 bundle/package/prompt/sealed-mapping/handoff/attachment/source/round identities;
- Bridge R30 `foto6/WebAIBridge@ceaee873231a8552c5b7324083baa800eec566a8`, CI `36993885456`;
- Bridge R30 handoff-schema, capture-schema, implementation and contract-test Git blobs;
- Bridge R31 `foto6/WebAIBridge@104281e49122233f251c692abba726ae31cee0d5`, CI `36999908386`, including manifest/authority/result/preflight schemas and operator/finalizer/PowerShell implementation blobs;
- the exact R31-derived R30 transport digest, derived-handoff SHA-256, directory digest and source-binding fingerprint for both Media R21 bundles;
- Creator R29's canonical Growth R26 envelope authority: `e844ed2daaaca9e9694fe1e0fb6b8b7bfac69cbc`, CI `36996617627`.

No branch name is accepted as authority.

## What is validated before unblinding

The live operator validates, in order:

1. the exact immutable authority profile;
2. the materialized R21 bundle, evidence, transport handoff, prompt manifest, sealed mapping and real A/B MP4 bytes;
3. exact package digest, prompt digest, sealed-mapping digest, source identity, brief lineage and review-round lineage;
4. Bridge R30 capture contract and exact producer profile, optionally wrapped by the exact Bridge R31 terminal-result contract;
5. `LIVE_REVIEW_PASS`, `model_evidence=true`, `human_ground_truth=false`, real attachment and real Send evidence;
6. exact conversation/request/operation IDs, assistant response bytes and response digest;
7. the Bridge R30 dynamic package fingerprint computed exactly as R31 does: the materialized R21 bundle JSON SHA-256 is the producer contract blob, with exact prompt bytes, A/B bytes, sealed mapping, R21 archive/directory/file digests and nested source/round lineage;
8. strict model JSON, coverage uncertainty and timestamps.

Only after all checks pass does R27 read the sealed mapping to recover real candidate IDs/render hashes.

The R30 capture builder currently inherits an R29/R18 `promptFileSha256` compatibility field. R27 does not trust that stale field. Instead it validates the exact R21 prompt file SHA-256 and prompt-text digest through the R21 authority plus the R30 dynamic transport fingerprint, then removes only the inherited stale field before invoking the generic R26 capture parser.

## Output

A genuine ingest emits one canonical `growth.dynamic_creator_external_review_envelope.r26.v1` per real candidate plus:

`growth.dynamic_live_review_ingest_index.r27.v1.json`

The coordinator index preserves both candidates and the full pairwise context, while identifying `selected_candidate_id` and the selected envelope when the verdict is A or B. Tie and insufficient-evidence keep `selected_result=null`.

Model-facing A/B is never treated as candidate identity.

Creator R29 currently validates the canonical envelope as an exact Growth R26 artifact. R27 therefore leaves the inner `growth.dynamic_creator_external_review_envelope.r26.v1` producer pinned to exact R26 `e844ed2d…` / CI `36996617627`. The outer coordinator index separately binds the actual R27 runtime SHA/CI and R27 authority-profile digest, so the two producer layers are not conflated.

For round-pair packages, the index marks whether each candidate envelope is directly accepted by Creator R29's current round-equality guard. It never rewrites a baseline candidate's real round number to make it appear current.

## Replay

The durable ledger lives at:

`<ledger-dir>/r27-live-ingest-ledger.json`

An exact replay is a no-op and must find the already materialized coordinator index and Creator envelopes with matching digests.

Changed response bytes, package identity, sealed-mapping bytes, capture payload or transport identity under the same capture/request identity is a conflict.

## Evidence boundary

Fixture/fake-CDP input, `BLOCKED`, and any non-live disposition are rejected as live ingest. `MALFORMED_MODEL_RESPONSE` is reported as its own state and emits no Creator envelope.

Model evidence always remains:

- `human_ground_truth=false`;
- `human_rating_evidence=false`;
- `human_parity_inferred=false`.

The operator never invokes or mutates a browser, provider, Creator or Media runtime.

## One-command operator

For a genuine coordinator capture, `--capture` may point directly to `r30-live-capture.json`. It may also point to R31's `r31-live-result.json`; in that case R27 requires and cross-checks the documented sibling `r30-live-capture.json` and `r30-live-response.txt`.


```bash
python -m growth_analytics.live_ingest_operator_r27 \
  --package-dir /path/to/materialized-media-r21/round-1 \
  --authority-profile conformance/growth.live_ingest_operator.r27.v1/authority-profiles.json \
  --capture /path/to/bridge-r30-genuine-capture.json \
  --ledger-dir /path/to/durable-growth-r27-ledger \
  --out-dir /path/to/growth-r27-output \
  --growth-sha <exact-green-growth-r27-sha> \
  --growth-ci-run-id <exact-green-growth-r27-ci-run-id> \
  --report /path/to/growth-r27-output/readiness.json
```

For source/readiness validation before a genuine capture exists, omit only `--capture`:

```bash
python -m growth_analytics.live_ingest_operator_r27 \
  --package-dir /path/to/materialized-media-r21/round-1 \
  --authority-profile conformance/growth.live_ingest_operator.r27.v1/authority-profiles.json \
  --ledger-dir /path/to/durable-growth-r27-ledger \
  --out-dir /path/to/growth-r27-source-ready \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <exact-run-id> \
  --report /path/to/growth-r27-source-ready/readiness.json
```

Without a genuine coordinator capture, the required state is:

`SOURCE_READY`

with gate:

`BLOCKED_WAITING_GENUINE_CAPTURE`

No fixture or fake-CDP evidence can produce `LIVE_REVIEW_INGESTED`.
