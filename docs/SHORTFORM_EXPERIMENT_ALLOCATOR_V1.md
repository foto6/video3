# Growth R13 Short-Form Experiment Allocator

## Scope

Growth R13 converts source-bound historical short-form evidence plus one durable R10 next-cycle seed into a bounded advisory experiment plan for Creator. It does not publish, edit, delete, mutate provider accounts, or authorize release.

The plan contract is \`growth.shortform_experiment_plan.v1\`. Historical allocator evidence uses \`growth.shortform_allocator_evidence.v1\`. Durable exactly-once allocator state uses \`growth.shortform_experiment_allocator_ledger.v1\`.

## Creative dimensions

Every cell carries a complete creative configuration across:

- hook type;
- first-3-second pacing;
- caption density;
- CTA;
- duration bucket;
- loop ending;
- B-roll density;
- edit style.

Allowed values are versioned in source and validated exactly. Treatments alter a bounded configuration while the control/holdback cell preserves a stable reference.

## Source-bound evidence

Allocator history embeds the exact \`growth.shortform_metric_snapshot.v1\` object that produced the allocator record. Parsing revalidates the snapshot digest, source class, platform, post identity, cycle revision, metric window, views, and completion-rate outcome.

The allocator rejects mixed source classes. A synthetic R10 seed therefore consumes only synthetic historical snapshots and produces a synthetic advisory plan. Synthetic evidence is never relabeled live.

Each history row labels its evidence basis as either:

- \`observational\`: no experiment, assignment, or registry reference may be present;
- \`randomized\`: requires experiment ID, variant ID, assignment digest, and registry freeze hash.

Randomized assignment support is reported separately. The allocator does not re-estimate causal effects and does not convert observational associations into lift claims.

## Overfitting controls

The default allocator policy requires at least 30 historical posts and at least 5 observations for both a treatment value and its control value before historical direction can prioritize that test.

Every plan reserves a control/holdback allocation and an exploration allocation. Defaults are 25% control and 25% exploration, leaving bounded treatment capacity. The default maximum is four cells.

Historical comparisons use completion rate only as a deterministic directional ranking signal. Their rationale explicitly states that the observed difference is not a causal lift estimate.

Randomized support requires at least eight randomized historical records for the candidate before the cell is labeled \`randomized_assignment_supported\`. Even then, the uncertainty state states that randomized history is not re-estimated by this allocator.

## Deduplication and anti-loop behavior

Every non-control cell has a deterministic experiment signature derived from the changed dimension/value relative to the control configuration.

The durable ledger counts prepared signatures. A signature is excluded after its configured reissue limit, which defaults to two. This prevents the same low-value experiment from being proposed indefinitely while preserving bounded rechecks.

The exploration candidate is selected deterministically from the least-observed, non-exhausted configuration. If every candidate is exhausted, its allocation returns to holdback rather than creating an unbounded new test family.

## Campaign/batch allocation

A plan is generated for an explicit \`batch_id\` and \`batch_size\`. Cell sample targets always sum exactly to the requested batch size.

For the deterministic 12-reel conformance case the allocator produces:

- 3 control/holdback reels;
- 6 directional-test reels;
- 3 exploration reels.

The plan remains advisory. Its authority block hard-codes \`advisory_only=true\`, \`auto_publish=false\`, \`external_mutation=false\`, \`publish_authorized=false\`, and \`release_authorized=false\`.

## Restart and exactly-once semantics

The allocator ledger persists two event types:

1. durable seed consumption keyed by the R10 next-cycle seed idempotency key;
2. durable plan preparation keyed by batch ID.

A single seed may produce at most one durable batch plan. Reusing the same seed for another batch fails closed.

If the process crashes after seed consumption, restart replays the same seed consumption idempotently and prepares one plan. If it crashes after plan preparation, restart returns the exact stored plan. Batch identity cannot change plan content.

All rows are canonical JSON, append-only, sequence checked, flushed, and fsynced.

## Deterministic 100-post / 12-reel simulation

The R13 focused test constructs 100 source-bound historical snapshots. Ninety are observational and ten carry randomized assignment references. A stable result-first hook has the strongest deterministic historical completion-rate association.

The simulation verifies that history-order permutations produce byte-equivalent plans, the 12-reel allocation remains 3/6/3, all cell signatures are unique within the plan, and no publish/provider authority is introduced.

The replay descriptor is \`fixtures/experiment_allocator_v1/replay_report.json\`.

## R10-R12 provenance

R13 starts from and preserves exact commit:

\`foto6/video3@2633aee487b126ce97bbd0fa79b9b8b1fd76f2e7\`

The conformance manifest pins the exact R10 autonomous-reels contract, R11 provider-ingestion manifest, R12 scheduler manifest, and their Git blob identities. No Creator or Media repository is modified.
