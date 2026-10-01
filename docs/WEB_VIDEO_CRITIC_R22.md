# Growth R22 Direct-Video Web-Chat Critic Contract

R22 defines `growth.web_video_critic.v1` for source-bound critique of an actual MP4 attached to a ChatGPT web conversation. It also defines `growth.web_video_critic_pairwise.v1` for deterministic A/B comparison of two exact candidate MP4s.

This milestone defines contracts and validators only. Integration state is pinned to:

`WAITING_FOR_ATTACHMENT_TRANSPORT`

No live web-video review is claimed until Bridge R25 attachment transport has independent green evidence.

## Input binding

Every single-candidate input binds:

- exact source ID, SHA-256, and size;
- exact Media repository and producer Git SHA;
- candidate ID;
- exact final render SHA-256 and byte size;
- exact render-export SHA-256;
- exact R21 real-artifact review-bundle digest;
- attachment SHA-256, size, MIME type, and content-addressed attachment identity;
- the review goal, requested focus, platform, and constraints.

The attachment SHA/size must exactly equal the candidate render SHA/size. A stale expected source, Media producer, or review-bundle digest fails closed.

## Output

A critic output contains:

- exact input/attachment/render/source/review-bundle lineage;
- explicit coverage ranges and method;
- `uninspected_possible=true`;
- `every_frame_inspected=false`;
- time-coded observations with defect category, severity, evidence, description, proposed edit, confidence, and uncertainty;
- whole-video summary with uncertainty;
- deterministic Creator re-edit directives projected only from local non-info observations;
- `human_ground_truth=false`;
- `human_label=false`;
- `live_platform_metrics=false`;
- `human_parity_gate_eligible=false`.

A local defect must include `start_ms` and `end_ms`. Whole-video observations may not masquerade as local time-coded evidence.

Allowed defect categories cover hook clarity, pacing, semantic cuts, framing/crop, B-roll relevance, captions, continuity, motion/zoom, audio balance, payoff/CTA/loop, and awkward/dead moments.

## Coverage and model boundary

The contract explicitly forbids a claim that every frame was inspected. Direct web-video review is model opinion, not human ground truth. It cannot create a human label, count as live platform evidence, or advance a human-parity gate.

While integration state is `WAITING_FOR_ATTACHMENT_TRANSPORT`, only `fixture_validation` execution mode validates. Any payload claiming `web_chat_attached_video` execution fails closed, even if it contains a plausible transport digest.

The fixture pack uses exact R21 source/candidate metadata only to exercise schema and lineage logic. Its observations explicitly state that they are fixtures and are not claims about the actual MP4.

## Pairwise A/B

Pairwise inputs require exactly two distinct render hashes from the same exact source and review bundle. Presentation order is deterministic from the content-addressed comparison digest.

When `producer_identity_blinded=true`, producer identity is retained in the source-bound candidate binding but omitted from the review presentation shown to the critic. Pairwise output supports `A`, `B`, `tie`, and `insufficient_evidence`. A/B is mapped back to the exact candidate ID only after the selection is validated.

Pairwise model selection is advisory only and is never serialized as a human label or human preference.

## Creator consumption

`creator_reedit_directives` is a bounded deterministic projection of validated local observations. Each directive carries:

- exact time range;
- defect category;
- severity;
- concrete edit directive;
- evidence text;
- confidence;
- uncertainty;
- source observation ID.

Creator can consume this without interpreting model prose or granting the critic publish/release authority.

R22 does not publish, mutate providers, modify Creator/Media, create credentials, or fabricate a video review.
