# Growth R22 Direct-Video Web-Chat Critic — Bridge R25 Binding

R22 defines `growth.web_video_critic.v1` and `growth.web_video_critic_pairwise.v1` for source-bound critique of exact candidate MP4s. This follow-up consumes independently verified Bridge R25 attachment transport evidence.

## Exact Bridge R25 authority

Growth is pinned to:

- repository: `foto6/WebAIBridge`
- branch: `agent/bridge-r25-file-attachment-20261001`
- exact SHA: `bfe6b043460b6c0c3d712cbcc7e0c9772d6bd3af`
- exact-head CI: `36888741188` — SUCCESS
- contract: `bridge.chat_file_attachment.v1`
- per-file cap: `500000000` bytes
- disposition: `READY_FOR_EXPLICIT_LIVE_REHEARSAL`
- `livePass=false`
- `NO_LIVE_DEPLOY`
- `NO_CUTOVER`
- no real user-chat upload was performed.

The two Bridge readiness artifacts were independently downloaded and their ZIP SHA-256 values matched GitHub metadata exactly:

- Ubuntu artifact `11176350316`, `r25-readiness-ubuntu-latest`, SHA-256 `0c9fcd14e9eae8cc7e19375ddd753e6bc91f0ad71829ad06dde7301690fcfbf6`
- Windows artifact `11176260440`, `r25-readiness-windows-latest`, SHA-256 `b28c110abef306c6479043a671f3a4e6ebb875de463f4e59e709f8a3e5746a78`

Both readiness reports bind the exact Bridge SHA/run/contract/cap, disposition `READY_FOR_EXPLICIT_LIVE_REHEARSAL`, and `livePass=false`. Both state that the rehearsal used a deterministic fake browser rather than a live ChatGPT upload DOM, so a separately authorized live rehearsal is still required before `LIVE_PASS`.

## Growth transport state

Growth advances only from `WAITING_FOR_ATTACHMENT_TRANSPORT` to:

`READY_FOR_EXPLICIT_LIVE_REHEARSAL`

This is not `LIVE_PASS`. It does not mean a candidate MP4 has been uploaded to or reviewed in ChatGPT.

Every critic input now carries `growth.web_video_attachment_transport_binding.r22.v1`, content-addressed over the exact Bridge repository, branch, SHA, CI run, attachment contract, 500 MB cap, disposition, `live_pass=false`, release/cutover gates, and both readiness artifact IDs/digests.

Wrong Bridge SHA, CI run, readiness artifact digest, disposition, or live-pass value fails closed.

## Exact video lineage

The existing R22 input binding remains unchanged in substance:

- exact source ID/SHA/size;
- exact Media repository and producer SHA;
- candidate ID;
- exact final MP4 SHA-256 and byte size;
- exact render-export SHA-256;
- exact R21 review-bundle digest;
- exact MP4 attachment identity;
- review brief.

The candidate MP4 SHA/size must equal the attachment SHA/size, and size must not exceed the Bridge R25 500,000,000-byte cap.

## Live-pass boundary

`web_chat_attached_video` output is still rejected while the exact bound Bridge transport has `live_pass=false`. R22 may validate fixture/schema logic and is ready for an explicitly authorized transport rehearsal, but it cannot serialize a real direct-video review yet.

No payload may claim:
- `LIVE_PASS`;
- actual attached-video review;
- human ground truth or human labels;
- live platform evidence;
- human-parity eligibility.

## Critic output and Creator directives

The timestamped defect schema is preserved. Local defects require bounded `start_ms`/`end_ms`, category, severity, evidence, description, proposed edit, confidence, and uncertainty.

Coverage always preserves uncertainty:
- `uninspected_possible=true`
- `every_frame_inspected=false`

Creator re-edit directives remain deterministic projections of validated local non-info observations. The critic remains advisory and has no publish/release/provider/Creator/Media mutation authority.

## Pairwise A/B

Pairwise comparison still requires exactly two distinct render hashes from the same source and review bundle. Candidate bindings now also carry the exact Bridge R25 transport-binding digest, and both candidates must resolve to the same exact transport authority.

Producer identity can remain blinded in critic-facing A/B presentation. Pairwise model preference remains non-human advisory evidence and cannot advance a human-parity gate.

No live upload is performed by this milestone.
