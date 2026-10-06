# Growth R37 — Local Closed-Loop Verifier / Final Decision Evidence

Growth R37 verifies a sealed local Media rehearsal bundle and emits one machine-readable final decision for Creator R38. It is a local verifier only. It does not open a browser, call a provider, access credentials, publish, allocate traffic, or mutate Creator/Media/provider state.

## Current authority state

Parent Growth R36 is frozen exactly:

- repository: \`foto6/video3\`
- SHA: \`a53f9deb180bb256d193f0422c6ffd7a5923d97a\`
- CI: \`37398558145 SUCCESS\`
- artifact: \`11383953328\`
- artifact digest: \`sha256:89e719718b5480ad889190a09c837efdfff31a15ea899f1e836ddb9d033f0894\`
- contract: \`growth.local_rehearsal_evidence.r36.v1\`

Creator R38 is **not yet an exact authority**. The observed branch
\`agent/creator-r38-local-fullstack-rehearsal-20261006\` currently points at
\`1f7cb9ed8f1985cd4faca79ce55f1c5fda9e3a57\`, whose commit is
\`Add R37 local integration driver\`. That is an R37 Creator commit, not a distinct R38 authority.

Therefore the checked-in Creator authority file exposes:

\`WAITING_CREATOR_AUTHORITY\`

and does not infer R38 acceptance from the branch name.

The observed Media R25 local-fix branch is also not treated as an exact-green authority. The last observed multicandidate run \`37247948495\` failed, and the newer local-fix branch has no exact-green run. R37 never treats those moving branches as authority.

## Contract

Primary contract:

\`growth.local_fullstack_verifier.r37.v1\`

Nested contracts:

- \`growth.sealed_media_local_evidence.r37.v1\`
- \`growth.local_review_vote.r37.v1\`
- \`growth.local_final_manifest.r37.v1\`
- \`growth.creator_r38_authority.r37.v1\`
- \`growth.local_fullstack_verification.r37.v1\`
- \`growth.local_fullstack_verifier_ledger.r37.v1\`

Final decisions are exactly:

- \`READY_FOR_LOCAL_DEMO\`
- \`NEEDS_REEDIT\`
- \`HUMAN_REVIEW_REQUIRED\`
- \`BLOCKED_INCOMPLETE_EVIDENCE\`

The verifier may separately expose state \`WAITING_CREATOR_AUTHORITY\` while the final decision remains \`BLOCKED_INCOMPLETE_EVIDENCE\`.

## Sealed Media bundle requirements

The local coordinator must pass:

1. a local bundle directory containing \`manifest.json\`;
2. the exact expected bundle manifest digest separately via CLI;
3. every source/candidate/final file referenced by the manifest;
4. an exact Media authority tuple;
5. exact review response files for every round;
6. final lineage manifest;
7. current Creator R38 authority manifest.

For real local evidence the Media tuple must contain:

- repository \`foto6/video2\`;
- exact producer SHA;
- exact CI run ID;
- exact artifact ID;
- artifact name;
- artifact digest;
- Media contract;
- authority class \`EXACT_GREEN\`.

No branch ref is accepted as authority.

Synthetic fixtures use \`SYNTHETIC_FIXTURE\` and can never reach \`READY_FOR_LOCAL_DEMO\`.

## Byte and lineage validation

Before review logic runs, R37 verifies:

- caller-supplied expected sealed-manifest digest;
- manifest self-digest;
- source bytes/hash/size;
- every candidate bytes/hash/size;
- no duplicate candidate IDs;
- no duplicate candidate MP4 bytes;
- every candidate source hash;
- targeted re-edit parent/child round lineage;
- exact applied directive digest;
- prompt bytes/hash/size per review round;
- exact three review files per round;
- no duplicate review bytes;
- distinct reviewer identities;
- review package/source/candidate hash binding;
- review timestamp inside the declared round/seal window;
- final.mp4 bytes/hash/size;
- final manifest bytes and semantic digest;
- final manifest timestamp;
- final candidate/source/Media-authority lineage.

Any missing candidate, source drift, stale review, duplicate candidate bytes, altered sealed manifest, wrong final.mp4, or changed final manifest fails closed.

## Closed-loop decision replay

R37 recomputes deterministic consensus from sealed review evidence. Review evidence classes remain distinct:

- \`FIXTURE\`
- \`OFFLINE_MODEL\`
- \`GENUINE_REVIEW\`

They are never relabeled as human ground truth.

The predeclared consensus rule uses three reviewers. A unanimous high-confidence A/B winner requires each confidence >= 0.82 and mean >= 0.86. A 2-of-3 winner uses the same majority confidence gate and requires dissent confidence <= 0.45. Tie, insufficient evidence, invalid format/policy, or excessive disagreement yields \`HUMAN_REVIEW_REQUIRED\`.

If an accepted winner contains high-severity defects that map to the fixed edit-operation allowlist, R37 deterministically derives targeted re-edit directives. Model free-form requested-edit text is audit-only; executable operations come only from the frozen mapping.

A next round is accepted only if:

- the targeted re-edit parent is the selected candidate;
- the child is exactly one round later;
- the exact derived directive list and digest match;
- the child candidate binds that same directive digest;
- the child appears in the next review round.

Otherwise the result is \`NEEDS_REEDIT\` or fails closed if the sealed lineage itself is inconsistent.

## Final.mp4 rule

\`READY_FOR_LOCAL_DEMO\` requires the sealed \`final.mp4\` to match the candidate selected by the final resolved review round, including candidate ID, round, SHA256 and size. The independent final manifest must bind the same source hash, candidate hash, final hash, Media authority digest and selection.

A final file cannot override review lineage.

## Creator R38 gate

The checked-in file:

\`conformance/growth.local_fullstack_verifier.r37.v1/creator-r38-authority.json\`

is currently \`WAITING_CREATOR_AUTHORITY\`.

When a distinct R38 candidate becomes exact-green, the coordinator may supply another manifest with:

- \`status=ACCEPTED\`;
- exact Creator repository/SHA/CI/artifact ID/name/digest/contract;
- \`distinct_r38_authority_available=true\`;
- \`exact_authority_digest\` equal to the canonical digest of that exact tuple.

The Creator SHA must differ from the currently observed R37 SHA. Until then R37 cannot emit a final demo-ready decision.

## Replay and resume

The durable ledger is:

\`growth.local_fullstack_verifier_ledger.r37.v1\`

It stores the sealed bundle digest by bundle identity and evaluation results by bundle + Creator-authority context.

- exact replay is an idempotent no-op;
- re-running after output loss reproduces the same verification digest;
- the same bundle identity with changed sealed bytes is a hard conflict;
- later Creator R38 authority can be evaluated against the same unchanged sealed bundle as a new authority context.

## Fixture

\`fixtures/local_fullstack_r37/base\` is a deterministic synthetic two-round closed loop.

Round 0 selects \`challenger\` but finds three high-severity pacing intervals. R37 derives three \`trim\` directives. The sealed targeted re-edit \`targeted-reedit\` binds exactly those directives.

Round 1 compares the baseline against \`targeted-reedit\` and selects the re-edit. \`final.mp4\` is byte-identical to that selected candidate.

The fixture bundle digest is:

\`ab3adf05edc79f570b04d5b129bc302ad336592ab6402843c049978d26ce529d\`

Its review evidence class is \`FIXTURE\`, so its final externally usable decision is still blocked.

## Creator R38 local command

Creator R38 can call the verifier from a Windows checkout after Media has sealed the local evidence directory:

\`\`\`powershell
python -m growth_analytics.local_fullstack_verifier_r37 verify \`
  --bundle-dir "C:\\local-rehearsal\\media-evidence" \`
  --expected-bundle-digest "<sealed manifest SHA256>" \`
  --authority ".\\conformance\\growth.local_fullstack_verifier.r37.v1\\authority.json" \`
  --policy ".\\conformance\\growth.local_fullstack_verifier.r37.v1\\policy.json" \`
  --creator-authority "C:\\local-rehearsal\\creator-r38-authority.json" \`
  --ledger-dir "C:\\local-rehearsal\\growth-r37-ledger" \`
  --out "C:\\local-rehearsal\\growth-r37-final-decision.json" \`
  --growth-sha (git rev-parse HEAD) \`
  --growth-ci-run-id 1
\`\`\`

With the checked-in pending Creator authority and fixture bundle, exit code \`3\` is intentional: the output is machine-readable \`WAITING_CREATOR_AUTHORITY / BLOCKED_INCOMPLETE_EVIDENCE\`.

## Safety boundary

Even \`READY_FOR_LOCAL_DEMO\` means only that the sealed local montage evidence passed the verifier. It never means:

- publish authorization;
- provider mutation authorization;
- browser mutation authorization;
- credential access authorization;
- human-ground-truth validation.

All corresponding fields in R37 output are permanently \`false\`.
