# Experiment Registry, Preregistration & Multiplicity Guards v1

## Purpose

Wave 9 adds durable preregistration and family-level multiplicity governance above the Wave 7 sequential protocol and Wave 8 integrity gate.

Frozen contracts remain unchanged:
- CreatorFeedback contract_version "1.0"
- growth.feedback_batch.v1
- growth.creator_seed.v1
- experiment.plan.v1
- experiment_integrity.v1
- historical analytics remain observational and outside randomized experiment inference

The registry entry version is experiment_registry.v1.

## Frozen registry entry

Each frozen entry binds:
- experiment id and monotonic revision;
- predecessor freeze hash and amendment reason for revisions after 1;
- preregistered hypothesis;
- primary metric set;
- guardrail metrics;
- arms and allocation weights;
- population assignment unit, strata, and eligibility declaration;
- experiment start/stop window;
- exact sequential policy and plan digest;
- planned confirmatory analyses;
- multiplicity family id, method, and family alpha;
- creation/freeze timestamps;
- creation SHA-256 and freeze SHA-256;
- auto_publish=false and external_mutation=false.

Unknown fields and bad hashes fail closed.
## Durable freeze ledger

experiment_registry_ledger.v1 is append-only JSONL with fsync on every accepted row.

Freeze rules:
- first revision must be 1;
- identical replay of the same frozen entry is idempotent;
- same experiment id + revision with changed contents fails closed;
- a new revision must be exactly previous revision + 1;
- amendment predecessor must exactly equal the current freeze hash;
- amendment reason is mandatory.

Outcome consumption is also durably recorded. Outcome data can only reference an already frozen latest revision.

Once outcome consumption is recorded for the latest revision, another amendment from that revision is rejected. This enforces preregistration before outcome use rather than allowing retrospective mutation.

The canonical durable fixture persists revision 1, a valid pre-outcome revision 2, then outcome consumption, and reopens after restart with all three rows intact.
## Immutable fields and amendments

Within a frozen experiment/revision identity, hypothesis, primary metric, allocation, and stopping policy are immutable because any content change changes the creation/freeze hashes and conflicts with the existing ledger identity.

A change may only be represented by a new revision with explicit predecessor and amendment reason, and only before outcome data have been consumed from the prior revision.

Wave 9 tests independently mutate:
- hypothesis;
- primary metric;
- allocation weights;
- sequential look schedule.

Every same-revision mutation fails closed.

## Metric swapping

Only metrics present in primary_metrics and planned_analyses are confirmatory.

A reported metric not preregistered as primary is retained as exploratory evidence only:
- label=exploratory;
- confirmatory=false;
- family_adjusted_p_value=null;
- decision=false;
- decision_reason=metric_not_preregistered_primary.

It cannot be promoted to confirmatory after seeing results.
## Multiplicity accounting

Family method: holm_bonferroni_v1.

Multiplicity is applied in two layers:

1. Within an experiment, the raw two-sided p-value is multiplied by the predeclared Wave 7 sequential multiplicity scope, capped at 1.0.
2. Across all preregistered confirmatory analyses in the family, Holm-Bonferroni is applied to those sequentially valid p-values.

All planned confirmatory slots remain in the family. Missing evidence or integrity-invalid evidence receives family input p=1.0 rather than disappearing from the denominator.

For ordered family input p-values p(1)...p(m), Holm candidates are:
(m - i + 1) * p(i)

Adjusted values are monotone cumulative maxima, capped at 1.0.

This conservative composition prevents extra testing from making existing adjusted evidence more favorable.

The family report exposes raw p, sequentially valid p, family input p, Holm rank/multiplier, adjusted p, integrity reference, label, decision, and machine-readable decision reason.
## Integrity interaction

experiment_integrity.v1 remains authoritative before inference.

If integrity status is invalid:
- raw evidence remains inspectable;
- sequential evidence remains inspectable;
- family input p is forced to 1.0;
- family decision is false;
- treatment_effect_interpretation_allowed=false;
- decision_reason=integrity_invalid.

A tiny p-value cannot override failed integrity.

## Historical observational evidence

Historical observational evidence may be represented for inspection but is outside randomized inference.

If source_kind=historical_observational:
- family input p is 1.0;
- decision=false;
- treatment_effect_interpretation_allowed=false;
- decision_reason=historical_observational_outside_randomized_inference.

No historical analytics contract is relabeled causal or randomized.
## Deterministic Wave 9 corpus

Seed: 920260927.

Canonical cases:
- valid_preregistered
- many_null_experiments
- metric_swap_after_results
- duplicate_experiment_id_changed_hypothesis
- amendment_before_vs_after_outcome
- sequential_family_budget_interaction
- integrity_invalid_block

The many-null family contains 40 preregistered null experiments. Four raw nominal p-values are below 0.05, while the sequential + Holm family report produces zero confirmatory decisions.

The metric-swap fixture includes a post-hoc raw p=0.000001. It remains exploratory with no adjusted confirmatory decision.

The integrity-invalid fixture uses raw p=0.0000001. Its integrity gate is invalid, so family input p=1.0 and no inferential conclusion is permitted.

Sequential-family example:
- experiment 1: raw 0.004 -> sequential 0.016 -> Holm 0.048 -> decision true;
- experiment 2: raw 0.008 -> sequential 0.032 -> Holm 0.064 -> decision false;
- experiment 3: raw 0.020 -> sequential 0.080 -> Holm 0.080 -> decision false.
## Canonical artifacts and hashes

Artifacts:
- fixtures/experiment_registry_corpus_v1.json
- fixtures/experiment_registry_ledger_v1.jsonl
- fixtures/experiment_registry_governance_v1.json
- fixtures/experiment_family_report_v1.json
- fixtures/experiment_family_report_v1.md

Hashes:
- registry corpus: 3e0399e44f3aa0ceb52e2cb64872bb2fb4f17cb75468a48e48aaa196a24a0cfd
- family report: 02f3d76bf6a1892e8cf82b6147c803c1b710c87644ab3f5a8703d90935286186
- durable registry ledger: ffe4e13bca2530989592464305aea01a08d6585684b2e7235c43eb2ec40c42ac
- governance fixture: 6e27e7a4392583750ad5adc403dd84a9678d2565b2b3d8845833160b6a726304

## Provider and Creator boundaries

Registry and family reports are read-only governance artifacts. They cannot publish, schedule, delete, or mutate provider state.

Metricool and vidIQ remain read-only and account mutation remains blocked.

CreatorFeedback 1.0 and all Growth-to-Creator delivery bytes remain unchanged.

## Validation

Focused:
python -m unittest discover -s tests -p "test_experiment_registry_wave9.py" -v

Full:
python -m unittest discover -s tests -v
