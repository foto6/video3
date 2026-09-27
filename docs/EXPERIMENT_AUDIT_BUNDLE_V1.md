# Reproducible Experiment Audit Bundle v1

## Purpose

Wave 10 adds experiment_audit_bundle.v1, a read-only, content-addressed manifest that lets an independent reviewer reconstruct why an experiment conclusion was or was not allowed using only committed durable fixtures.

The audit layer does not query Metricool, vidIQ, or any network/provider API. It does not publish, mutate accounts, change CreatorFeedback 1.0, or relabel historical observational analytics as randomized evidence.

## Canonical reconstruction chain

The canonical bundle binds these durable sources by exact SHA-256:

- fixtures/experiment_plan_v1.json
- fixtures/experiment_registry_corpus_v1.json
- fixtures/experiment_sequential_corpus_v1.json
- fixtures/experiment_integrity_report_v1.json
- fixtures/experiment_sequential_report_v1.json
- fixtures/experiment_family_report_v1.json

For synthetic-growth-exp-v1, reconstruction parses and hash-verifies the frozen registry lineage; canonicalizes randomized evidence; collapses exact replay duplicates while rejecting conflicting duplicate ids; recomputes assignment and event/window digests; verifies integrity, sequential stopping policy and multiplicity-family bindings; classifies the conclusion; and finally computes the canonical outer bundle digest.

## Result classifications

The bundle exposes exactly: confirmatory_supported, confirmatory_not_supported, exploratory_only, invalid_integrity, and insufficient_evidence.

invalid_integrity takes precedence over strong-looking treatment evidence. Raw comparison metrics and family evidence remain present even when interpretation is blocked. A non-preregistered metric remains exploratory_only and is never promoted after results are seen.

## Amendment lineage and replay determinism

The registry section retains revision, creation/freeze hashes, predecessor freeze hash, amendment reason, timestamps, hypothesis and primary metrics. The canonical audited experiment has no amendment; Wave 10 tests also inspect the existing two-revision amendment fixture and require predecessor/reason visibility.

Equivalent logical evidence is order-independent. Exact duplicate replay rows collapse to one logical observation; reordered input and exact duplicate replay produce identical assignment, event-corpus and audit bundle digests. A duplicate observation id with changed payload fails closed.

## Tamper behavior

Verification fails closed for altered frozen registry data, tampered integrity bindings, a wrong family report, a missing assignment digest, unknown bundle fields, conflicting duplicate ids, different sequential stopping policy, source fixture byte/hash mismatch, and outer bundle digest mismatch.

## Canonical result

Bundle fixture: fixtures/experiment_audit_bundle_v1.json

Independent verification: fixtures/experiment_audit_verification_v1.json

Bundle digest: 788eca36cd305ff62209bec4ff76ce5b3531589234cc698d4c774844694ca5c3

Classification: confirmatory_not_supported

Reason: holm_adjusted_above_family_alpha

Component digests:
- registry freeze: 5bc0a1c9c7f54d2b6da38ffdb78f219a950c6958f1535b998c68916628f0f07e
- assignment/randomization: 93213ea319ddb3eab151f150e4e7a4cf74a571038fdc9fd807a1e8e84dff1903
- event/window corpus: be82e5f8339f3c384f6e7a450c11e68826cbcdec583d4813bd0d46dda0fb2e43
- integrity result: e2be2db3d1b201297d9d2bccc3b19aa16975d3e465d028d2829b33c171264226
- sequential result: 5927a735c1e52aa2633e5c75d92ce3e973ba60389f3e35b0bbf1157ffe9c24a7
- multiplicity family component: e7b1425cb45aa98ae8f200e885cd238fa752a56a25f96130892e1f826a4abb95

## Offline commands

Rebuild:

    python -m growth_analytics.experiment_audit rebuild --root . --output fixtures/experiment_audit_bundle_v1.json

Verify:

    python -m growth_analytics.experiment_audit verify --root . --bundle fixtures/experiment_audit_bundle_v1.json --report fixtures/experiment_audit_verification_v1.json

No provider credentials or network access are required.

## Boundaries

auto_publish=false

external_mutation=false

Metricool and vidIQ remain read-only. CreatorFeedback 1.0, growth.feedback_batch.v1, and growth.creator_seed.v1 are unchanged. Historical analytics retain their observational/non-causal interpretation.
