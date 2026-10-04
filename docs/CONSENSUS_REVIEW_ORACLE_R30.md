# Growth R30 — multi-review consensus oracle / disagreement gate

Growth R30 adds a deterministic, read-only consensus gate over exactly three independently captured model reviews of one exact blinded Media R24 package and review round. The layer does not call a model, mutate a browser, publish to a provider, or create human-rating evidence.

**Model consensus is not human ground truth.** A three-model agreement is only machine evidence. R30 never claims human parity, human preference, or human-quality equivalence.

## Frozen exact authorities

R30 accepts only the following source authorities:

- Growth R29: `foto6/video3@3e4a8ad6d73b058c953abeadba7a60abe567adbc`, CI `37195373334 SUCCESS`, artifact `11301225456`, digest `sha256:40fb5e5c7ab7ccf7a02514f9b5a40eb8fc3207373c63391a17606fb12a6d0e55`.
- Media R24: `foto6/video2@244acdf154741e669991b17df3ef2a47e2dfdfa9`, CI `37195239582 SUCCESS`, canonical artifact `11301055747`, digest `sha256:fc5c9b9635d49b643e66efafe602d21ce1ef695a553f81a797bdf16d7b8cf228`.
- Bridge R34: `foto6/WebAIBridge@4e2a37545cc0cdd940cf6e86d40e0d620d6c94ae`, CI `37196102639 SUCCESS`.

Contract, schema, implementation, verifier, routing and workflow Git blobs plus upstream Actions artifact digests are frozen in `conformance/growth.consensus_review_oracle.r30.v1/authority-profiles.json`. Moving branches are not authority.

Bridge R34 is accepted specifically for its exact-ID routing invariant: `providerConversationId` is mutation authority. Title, last-open tab, only-open chat, tab ID and project slug are not authority. R30 itself performs no Bridge mutation and R34 remains non-cut-over.

## Exact Media R24 package binding

The oracle validates the canonical Media R24 directory before reading any review vote. It checks:

- exact R24 producer SHA and CI;
- export, authority-profile, package-manifest and Bridge-handoff contracts;
- package-manifest file-set digest and every listed file SHA/size;
- exact prompt bytes, SHA-256 and size;
- exact blinded A/B MP4 SHA-256, size and MIME;
- exact session ID/session identity/review round/source lineage;
- sealed mapping content and digest;
- mapping attachment identity;
- `contentsModelFacing=false`.

Only the prompt and blinded A/B media are model-facing. Sealed A/B role/candidate mapping remains machine-side.

## Verified review manifest

Each `review-*.json` uses `growth.verified_r29_r34_model_review.r30.v1` and binds:

- exact Growth R29 producer/CI/artifact and a byte-addressed R29 round result;
- exact Media R24 session/package/round/prompt/mapping/A/B identity;
- exact Bridge R34 producer/CI/contracts;
- exact `providerConversationId`, conversation ID and canonical conversation URL;
- request ID and operation ID;
- capture digest;
- exact response file bytes and SHA-256;
- provider/model metadata where available;
- both canonical Creator envelopes needed for a possible selected handoff.

The R29 round-result semantic digest is recomputed and its critical session/conversation/request/operation/response/capture/package/mapping/prompt fields must exactly match the review manifest.

## Strict normalized vote

The assistant response must be UTF-8 strict JSON with contract `growth.model_review_vote.r30.v1` and exactly:

- `winner`: `A`, `B`, `tie`, or `insufficient_evidence`;
- `confidence`: 0..1;
- `defects`: timestamped records with A/B label, interval, category, severity, evidence, requested targeted edit, edit direction, confidence and uncertainty;
- `policy_valid`;
- `format_valid`;
- `uncertainty`.

Candidate IDs, sealed mapping, role fields or equivalent mapping information in a model response are rejected.

## Independence gate

Exactly three reviews are required. R30 rejects duplicate reviewer identity before aggregation:

- three distinct conversation IDs are mandatory;
- capture digests must be unique;
- response digests must be unique;
- all three bind the same Media R24 package/session/round/prompt/A/B/mapping lineage.

No dissenting review is discarded. The complete normalized vote and model metadata for every reviewer remains in the consensus audit record.

## Aggregation rule — frozen before fixtures

The policy was committed before R30 fixture review data in `aggregation-policy.json`.

Thresholds:

- high-confidence floor: `0.82`;
- accepted winner mean confidence: `0.86`;
- maximum dissent confidence for 2-of-3 acceptance: `0.45`;
- maximum 2-of-3 disagreement score: `0.30`.

Disagreement score:

```
min(
  1,
  0.35 * categorical_disagreement
  + 0.15 * confidence_spread
  + 0.25 * dissent_strength
  + 0.15 * directive_conflict
  + 0.10 * invalid_fraction
)
```

Unanimous A/B is accepted only when every confidence is at least 0.82, mean confidence is at least 0.86, all reviews are policy/format valid, and timestamped edit directions do not conflict.

A 2-of-3 A/B majority is accepted only when both majority confidences are at least 0.82, their mean is at least 0.86, dissent confidence is at most 0.45, disagreement score is at most 0.30, all reviews are valid, and edit directions do not conflict.

Malformed review, tie/insufficient modal result, no two-vote winner, low confidence, strong dissent, excessive disagreement, policy/format failure or overlapping contradictory edit directions produces `HUMAN_REVIEW_REQUIRED`.

## Replay and canonical ordering

Inputs are canonically sorted by review conversation ID. Reordering the same three inputs therefore produces the same semantic consensus and digest.

The durable ledger binds each reviewer to session + round + conversation and fingerprints response/capture/R29-result bytes. Exact replay is a no-op. Changed bytes under the same reviewer/session identity are a conflict. Duplicate reviewer, capture or response identity is rejected before aggregation.

## Creator handoff

`growth.consensus_creator_handoff.r30.v1` is emitted only when:

1. the consensus state is `CONSENSUS_ACCEPTED`; and
2. all three input reviews are genuine `LIVE_REVIEW_INGESTED` evidence.

The chosen `growth.dynamic_creator_external_review_envelope.r26.v1` is embedded unchanged as the canonical inner envelope. The outer handoff binds exact Growth R30 SHA/CI, the frozen authority profile and aggregation-policy digest.

Fixture/source-ready reviews may demonstrate `CONSENSUS_ACCEPTED` semantics, but they never emit an executable Creator handoff.

## One-command ingest

The review directory must contain exactly three top-level files named `review-*.json`; referenced response/R29/envelope files may be below that directory.

```bash
python -m growth_analytics.consensus_review_oracle_r30 consensus \
  --media-dir /path/to/extracted-media-r24/round-N \
  --reviews-dir /path/to/three-verified-r29-r34-reviews \
  --authority-profile conformance/growth.consensus_review_oracle.r30.v1/authority-profiles.json \
  --policy conformance/growth.consensus_review_oracle.r30.v1/aggregation-policy.json \
  --ledger-dir /durable/growth-r30-ledger \
  --out-dir /durable/growth-r30-consensus \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <exact-run-id>
```

Output is exactly `CONSENSUS_ACCEPTED` or `HUMAN_REVIEW_REQUIRED` for structurally valid evidence. Invalid authority/package/replay/independence evidence fails closed as `BLOCKED_INVALID_EVIDENCE`.

## Deterministic rehearsal

```bash
python -m growth_analytics.consensus_review_oracle_r30 rehearse-fixtures \
  --media-dir /path/to/extracted-media-r24/round-0 \
  --reviews-dir fixtures/consensus_review_r30/accepted \
  --authority-profile conformance/growth.consensus_review_oracle.r30.v1/authority-profiles.json \
  --policy conformance/growth.consensus_review_oracle.r30.v1/aggregation-policy.json \
  --out-dir /tmp/growth-r30-rehearsal \
  --growth-sha "$(git rev-parse HEAD)" \
  --growth-ci-run-id <exact-run-id>
```

The rehearsal includes one accepted unanimous case plus high-disagreement 2–1, unanimous-low-confidence, contradictory timestamped directives, and duplicate-conversation rejection. CI additionally tests copied reviews, malformed responses, stale package identity, swapped A/B mapping, changed-byte replay and wrong round.

No fixture represents a real model call or human evaluation.
