# Growth R21 Real-Artifact Decision Pack

R21 is the Human Gate V4 producer for one exact Media R16 candidate-batch artifact. It does not use synthetic candidate fixtures for the decision.

## Exact Media authority

- repository: `foto6/video2`
- producer SHA: `231a0680c8939cfec77aaa283e507e93f383ad73`
- workflow run: `36865890506`
- artifact ID: `11163920921`
- artifact name: `media-r16-candidate-batch-demo`
- archive SHA-256: `d929b592c76ec93e41b54701376f4b02366a3bdbd127d3472d73ae277d450d0f`

The producer verifies the downloaded ZIP bytes before opening it. It then verifies the batch manifest, producer pin, source.mp4 bytes, every final.mp4 hash/size, every media.render_export.v1 raw-file hash, render-export artifact binding, and exact source lineage.

The independently fetched archive verified on 2026-10-01 contains:

| candidate | final.mp4 SHA-256 | bytes | render-export SHA-256 |
| --- | --- | ---: | --- |
| candidate-1 | `3bd12d999264cb932cb3ef15dfbd96e002ede517f2273795b593553b9642d864` | 575465 | `2b5177c00bb054184a239c7eddb1383ad253922e8eb686f5aa2e56c91a1335ac` |
| candidate-2 | `cdae63d5ccce67332e607af7fd87b18c31da8dc77c2f0ce5182550321767286c` | 576763 | `b349b897f8f86bc9257e2622f564d6430f3864ff605ae4b82ba0cb09c59298f6` |
| candidate-3 | `3bd12d999264cb932cb3ef15dfbd96e002ede517f2273795b593553b9642d864` | 575465 | `26fe61f0644e263ce047869334ba65c5973eba809ff45f76286490f573d61b8e` |

The exact source is:

- source ID: `r16-demo-source`
- source SHA-256: `7b484abef5de1569e1b7f91a5d780f17c6d687ef68b3c42c9e54375f4e5e434b`
- source bytes: 763377

Candidate 1 and candidate 3 are byte-identical MP4s but have distinct render-export sidecars/plans. R18 requires distinct render hashes, so R21 retains candidate 3 in the pack as an explicit alias of candidate 1 and submits candidate 1 plus candidate 2 as the two distinct-render representatives. This is not hidden deduplication.

## Critic/decision path

R21 converts exact Media technical/creative QA evidence into an R15 structural critic report and canonical R16 critic export for each candidate. It does not invent frame-semantic, VLM, or human evidence that the artifact does not contain.

For the exact Media artifact:

- all Media technical QA passes;
- all Media creative QA passes;
- no production VLM observation is bound to the exact candidate bytes;
- no human pairwise label is supplied;
- no live platform metric exists.

Therefore editorial dimensions unsupported by the artifact remain unavailable rather than receiving fabricated scores. R18 runs on the exact two distinct render hashes and must return `insufficient_evidence` unless future real evidence satisfies winner policy. R20 then emits its decision-only feedback path.

No model output is serialized as `human_ground_truth=true`. No synthetic/offline metric is represented as live.

## Reproducible command

Given the exact downloaded Media artifact ZIP:

```bash
python -m growth_analytics.real_artifact_decision \
  --artifact-zip media-r16-candidate-batch-demo.zip \
  --output-dir r21-decision-pack \
  --growth-sha "$GITHUB_SHA" \
  --growth-run-id "$GITHUB_RUN_ID"
```

The command fails before decision construction if the archive digest, producer SHA, source, candidate bytes, size, render-export hash, or render-export lineage differs.

Outputs include:

- `growth.real_artifact_decision_pack.v1.json`
- `growth.real_artifact_verification.r21.v1.json`
- `growth.candidate_decision.v1.json`
- `growth.closed_loop_feedback.v1.json`
- one canonical `growth.critic_export.v1.json` per candidate
- `growth.real_artifact_decision_output_manifest.r21.v1.json`

The GitHub Actions workflow downloads the exact public Media artifact again, checks the archive SHA-256, runs the command at exact `GITHUB_SHA`, binds `GITHUB_RUN_ID`, and uploads the output directory as the non-empty artifact `growth-r21-real-artifact-decision`.

## Authority

R21 is read-only and advisory. It has no provider mutation, publishing, Creator mutation, Media mutation, release, credential, or human-rating authority.
