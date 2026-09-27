from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .experiment_integrity import randomization_digest
from .experiment_protocol import (
    ExperimentPlan,
    parse_experiment_evidence,
    plan_digest,
)
from .experiment_registry import ExperimentRegistryEntry

AUDIT_BUNDLE_VERSION = "experiment_audit_bundle.v1"
VERIFICATION_VERSION = "experiment_audit_verification.v1"
PRIMARY_METRIC_DEFINITION_VERSION = "experiment_primary_metric_definition.v1"

CLASSIFICATIONS = frozenset({
    "confirmatory_supported",
    "confirmatory_not_supported",
    "exploratory_only",
    "invalid_integrity",
    "insufficient_evidence",
})

CANONICAL_FIXTURES = {
    "plan": "fixtures/experiment_plan_v1.json",
    "registry": "fixtures/experiment_registry_corpus_v1.json",
    "evidence": "fixtures/experiment_sequential_corpus_v1.json",
    "integrity": "fixtures/experiment_integrity_report_v1.json",
    "sequential": "fixtures/experiment_sequential_report_v1.json",
    "family": "fixtures/experiment_family_report_v1.json",
}


class ExperimentAuditError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentAuditError(f"cannot load durable fixture: {path}") from exc


def _canonical_observations(
    observations: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    by_id: dict[str, dict[str, Any]] = {}
    for raw in observations:
        if not isinstance(raw, Mapping):
            raise ExperimentAuditError("observation must be an object")
        observation_id = raw.get("observation_id")
        if not isinstance(observation_id, str) or not observation_id:
            raise ExperimentAuditError("observation_id is required")
        normalized = json.loads(canonical_json(dict(raw)))
        previous = by_id.get(observation_id)
        if previous is not None and previous != normalized:
            raise ExperimentAuditError(
                f"conflicting duplicate observation id: {observation_id}"
            )
        by_id[observation_id] = normalized
    return tuple(sorted(
        by_id.values(),
        key=lambda row: (
            str(row.get("observed_at", "")),
            row["observation_id"],
        ),
    ))


def observation_corpus_digest(
    observations: Sequence[Mapping[str, Any]],
    *,
    window: Mapping[str, str],
) -> str:
    canonical = _canonical_observations(observations)
    return sha256_json({
        "window": {
            "start": window["start"],
            "stop": window["stop"],
        },
        "observations": list(canonical),
    })


def _verify_lineage(
    lineage: Sequence[Mapping[str, Any]],
) -> tuple[ExperimentRegistryEntry, ...]:
    if not lineage:
        raise ExperimentAuditError("registry lineage is required")
    entries = tuple(
        ExperimentRegistryEntry.from_dict(raw) for raw in lineage
    )
    experiment_id = entries[0].experiment_id
    for index, entry in enumerate(entries):
        if entry.experiment_id != experiment_id:
            raise ExperimentAuditError(
                "registry lineage mixes experiment ids"
            )
        if index == 0:
            if entry.revision != 1:
                raise ExperimentAuditError(
                    "registry lineage must start at revision 1"
                )
        else:
            previous = entries[index - 1]
            if entry.revision != previous.revision + 1:
                raise ExperimentAuditError(
                    "registry lineage revision gap"
                )
            if entry.predecessor_freeze_hash != previous.freeze_hash:
                raise ExperimentAuditError(
                    "registry predecessor hash mismatch"
                )
            if not entry.amendment_reason:
                raise ExperimentAuditError(
                    "registry amendment reason missing"
                )
    return entries


def _lineage_summary(
    entries: Sequence[ExperimentRegistryEntry],
) -> list[dict[str, Any]]:
    return [{
        "revision": entry.revision,
        "creation_hash": entry.creation_hash,
        "freeze_hash": entry.freeze_hash,
        "predecessor_freeze_hash": entry.predecessor_freeze_hash,
        "amendment_reason": entry.amendment_reason,
        "created_at": entry.created_at,
        "frozen_at": entry.frozen_at,
        "hypothesis": entry.hypothesis,
        "primary_metrics": list(entry.primary_metrics),
    } for entry in entries]


def _check_sequential_policy(
    entry: ExperimentRegistryEntry,
    sequential_result: Mapping[str, Any],
) -> None:
    policy = entry.sequential_policy
    if sequential_result.get("plan_digest") != policy["plan_digest"]:
        raise ExperimentAuditError(
            "sequential report plan digest mismatch"
        )
    method = sequential_result.get("method")
    if not isinstance(method, Mapping):
        raise ExperimentAuditError("sequential method missing")
    if method.get("name") != policy["method"]:
        raise ExperimentAuditError(
            "different sequential stopping policy"
        )
    if method.get("max_looks") != policy["max_looks"]:
        raise ExperimentAuditError(
            "different sequential max_looks"
        )
    history = sequential_result.get("look_history")
    if not isinstance(history, list) or not history:
        raise ExperimentAuditError(
            "sequential look history missing"
        )
    sizes = list(policy["look_sample_sizes"])
    for look in history:
        number = look.get("look_number")
        size = look.get("look_sample_size")
        if (
            not isinstance(number, int)
            or number < 1
            or number > len(sizes)
        ):
            raise ExperimentAuditError("undeclared sequential look")
        if size != sizes[number - 1]:
            raise ExperimentAuditError(
                "different sequential stopping policy"
            )


def _family_rows(
    family_report: Mapping[str, Any],
    *,
    experiment_id: str,
    metric: str,
) -> tuple[dict[str, Any] | None, tuple[dict[str, Any], ...]]:
    confirmatory = [
        dict(row)
        for row in family_report.get("confirmatory", [])
        if (
            row.get("experiment_id") == experiment_id
            and row.get("metric") == metric
        )
    ]
    exploratory = tuple(
        dict(row)
        for row in family_report.get("exploratory", [])
        if row.get("experiment_id") == experiment_id
    )
    if len(confirmatory) > 1:
        raise ExperimentAuditError(
            "conflicting family confirmatory rows"
        )
    return (
        confirmatory[0] if confirmatory else None,
        exploratory,
    )


def classify_conclusion(
    *,
    entry: ExperimentRegistryEntry,
    metric: str,
    integrity_result: Mapping[str, Any],
    sequential_result: Mapping[str, Any],
    family_report: Mapping[str, Any],
) -> tuple[str, str]:
    if integrity_result.get("status") == "invalid":
        return (
            "invalid_integrity",
            "experiment_integrity.v1 is invalid",
        )

    if metric not in entry.primary_metrics:
        return (
            "exploratory_only",
            "metric was not preregistered as primary",
        )

    family_row, exploratory = _family_rows(
        family_report,
        experiment_id=entry.experiment_id,
        metric=metric,
    )
    if family_row is None:
        if exploratory:
            return (
                "exploratory_only",
                "family report labels metric exploratory",
            )
        return (
            "insufficient_evidence",
            "family report has no confirmatory evidence",
        )

    if family_row.get("decision_reason") == "integrity_invalid":
        return (
            "invalid_integrity",
            "family inference is blocked by integrity",
        )

    state = sequential_result.get("state")
    if state in {"insufficient_evidence", "continue"}:
        return (
            "insufficient_evidence",
            f"sequential state is {state}",
        )

    if family_row.get("decision") is True:
        return (
            "confirmatory_supported",
            str(family_row.get("decision_reason")),
        )

    return (
        "confirmatory_not_supported",
        str(family_row.get("decision_reason")),
    )


def build_audit_bundle(
    *,
    plan_payload: Mapping[str, Any],
    registry_lineage: Sequence[Mapping[str, Any]],
    evidence_payload: Mapping[str, Any],
    integrity_result: Mapping[str, Any],
    sequential_result: Mapping[str, Any],
    family_report: Mapping[str, Any],
    primary_metric: str,
    source_references: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    plan = ExperimentPlan.from_dict(plan_payload)
    lineage = _verify_lineage(registry_lineage)
    latest = lineage[-1]

    if latest.experiment_id != plan.experiment_id:
        raise ExperimentAuditError(
            "registry experiment does not match plan"
        )
    if latest.sequential_policy["plan_digest"] != plan_digest(plan):
        raise ExperimentAuditError("registry plan digest mismatch")

    observations = evidence_payload.get("observations")
    if not isinstance(observations, list):
        raise ExperimentAuditError(
            "experiment observations missing"
        )
    parsed_evidence = parse_experiment_evidence(
        plan, evidence_payload
    )
    canonical_observations = _canonical_observations(observations)
    evidence_digest = hashlib.sha256(
        parsed_evidence.to_json().encode("utf-8")
    ).hexdigest()
    if sequential_result.get("evidence_digest") != evidence_digest:
        raise ExperimentAuditError(
            "sequential evidence digest mismatch"
        )

    assignment_digest = randomization_digest(
        plan, canonical_observations
    )
    corpus_digest = observation_corpus_digest(
        canonical_observations,
        window=latest.window,
    )

    bindings = integrity_result.get("bindings")
    if not isinstance(bindings, Mapping):
        raise ExperimentAuditError(
            "integrity bindings missing"
        )
    if bindings.get("plan_digest") != plan_digest(plan):
        raise ExperimentAuditError(
            "integrity plan digest mismatch"
        )
    if bindings.get("randomization_sha256") != assignment_digest:
        raise ExperimentAuditError(
            "integrity randomization digest mismatch"
        )

    _check_sequential_policy(latest, sequential_result)
    sequential_digest = sha256_json(sequential_result)
    if (
        bindings.get("sequential_report_sha256")
        != sequential_digest
    ):
        raise ExperimentAuditError(
            "integrity/sequential component digest mismatch"
        )

    if family_report.get("family_id") != latest.family_id:
        raise ExperimentAuditError(
            "wrong multiplicity family report"
        )

    family_row, exploratory = _family_rows(
        family_report,
        experiment_id=latest.experiment_id,
        metric=primary_metric,
    )
    if primary_metric not in latest.primary_metrics and not exploratory:
        raise ExperimentAuditError(
            "non-preregistered metric lacks exploratory family evidence"
        )

    if family_row is not None:
        if family_row.get("registry_revision") != latest.revision:
            raise ExperimentAuditError(
                "family registry revision mismatch"
            )
        if family_row.get("registry_freeze_hash") != latest.freeze_hash:
            raise ExperimentAuditError(
                "family registry freeze mismatch"
            )
        integrity_reference = family_row.get(
            "integrity_reference"
        )
        if not isinstance(integrity_reference, Mapping):
            raise ExperimentAuditError(
                "family integrity reference missing"
            )
        if (
            integrity_reference.get("status")
            != integrity_result.get("status")
        ):
            raise ExperimentAuditError(
                "family/integrity status conflict"
            )

    required_references = set(CANONICAL_FIXTURES)
    if set(source_references) != required_references:
        raise ExperimentAuditError(
            "audit source references must be exact"
        )
    references: dict[str, dict[str, str]] = {}
    for name in sorted(required_references):
        reference = source_references[name]
        if (
            not isinstance(reference, Mapping)
            or set(reference) != {"path", "sha256"}
        ):
            raise ExperimentAuditError(
                f"invalid source reference: {name}"
            )
        path = reference["path"]
        digest = reference["sha256"]
        if not isinstance(path, str) or not path:
            raise ExperimentAuditError(
                f"missing source path: {name}"
            )
        if not isinstance(digest, str) or len(digest) != 64:
            raise ExperimentAuditError(
                f"missing source digest: {name}"
            )
        references[name] = {
            "path": path,
            "sha256": digest,
        }

    classification, classification_reason = classify_conclusion(
        entry=latest,
        metric=primary_metric,
        integrity_result=integrity_result,
        sequential_result=sequential_result,
        family_report=family_report,
    )
    if classification not in CLASSIFICATIONS:
        raise ExperimentAuditError(
            "invalid audit classification"
        )

    comparisons = sequential_result.get("comparisons")
    comparison = (
        json.loads(canonical_json(comparisons[0]))
        if isinstance(comparisons, list) and comparisons
        else None
    )
    lineage_summary = _lineage_summary(lineage)

    material = {
        "audit_bundle_version": AUDIT_BUNDLE_VERSION,
        "subject": {
            "experiment_id": latest.experiment_id,
            "registry_revision": latest.revision,
            "primary_metric": primary_metric,
        },
        "classification": classification,
        "classification_reason": classification_reason,
        "registry": {
            "lineage": lineage_summary,
            "lineage_digest": sha256_json(lineage_summary),
            "amendment_count": max(0, len(lineage_summary) - 1),
            "latest_freeze_hash": latest.freeze_hash,
        },
        "assignment": {
            "randomization_digest": assignment_digest,
            "assignment_unit": latest.population[
                "assignment_unit"
            ],
        },
        "event_window_corpus": {
            "window": dict(latest.window),
            "corpus_digest": corpus_digest,
            "observation_count": len(canonical_observations),
            "evidence_digest": evidence_digest,
        },
        "integrity": {
            "source_sha256": references["integrity"]["sha256"],
            "result_digest": sha256_json(integrity_result),
            "status": integrity_result.get("status"),
            "reasons": json.loads(canonical_json(
                integrity_result.get("reasons", {})
            )),
            "bindings": json.loads(canonical_json(bindings)),
        },
        "sequential": {
            "source_sha256": references["sequential"]["sha256"],
            "result_digest": sequential_digest,
            "state": sequential_result.get("state"),
            "final_look_number": sequential_result.get(
                "final_look_number"
            ),
            "final_look_sample_size": sequential_result.get(
                "final_look_sample_size"
            ),
            "sample_counts": json.loads(canonical_json(
                sequential_result.get("sample_counts", {})
            )),
            "method": json.loads(canonical_json(
                sequential_result.get("method", {})
            )),
            "comparison": comparison,
        },
        "multiplicity_family": {
            "source_sha256": references["family"]["sha256"],
            "report_digest": sha256_json(family_report),
            "family_id": family_report.get("family_id"),
            "multiplicity": json.loads(canonical_json(
                family_report.get("multiplicity", {})
            )),
            "confirmatory": (
                json.loads(canonical_json(family_row))
                if family_row is not None
                else None
            ),
            "exploratory": [
                json.loads(canonical_json(row))
                for row in exploratory
            ],
        },
        "primary_metric_definition": {
            "definition_version":
                PRIMARY_METRIC_DEFINITION_VERSION,
            "name": primary_metric,
            "unit":
                "binary_success_per_stable_assignment_unit",
            "definition": (
                "Binary synthetic primary-success rate over "
                "the stable randomized assignment unit; "
                "confirmatory only when preregistered."
            ),
            "confirmatory_preregistered":
                primary_metric in latest.primary_metrics,
        },
        "raw_evidence": {
            "source_kind": evidence_payload.get("source_kind"),
            "evidence_digest": evidence_digest,
            "sample_counts": json.loads(canonical_json(
                sequential_result.get("sample_counts", {})
            )),
            "comparison": comparison,
            "family_confirmatory": (
                json.loads(canonical_json(family_row))
                if family_row is not None
                else None
            ),
            "family_exploratory": [
                json.loads(canonical_json(row))
                for row in exploratory
            ],
            "preserved_when_blocked": True,
        },
        "source_references": references,
        "auto_publish": False,
        "external_mutation": False,
    }
    material["bundle_digest"] = sha256_json(material)
    return json.loads(canonical_json(material))


def _fixture_references(
    root: Path,
) -> dict[str, dict[str, str]]:
    references = {}
    for name, relative in CANONICAL_FIXTURES.items():
        path = root / relative
        if not path.is_file():
            raise ExperimentAuditError(
                f"missing durable fixture: {relative}"
            )
        references[name] = {
            "path": relative,
            "sha256": sha256_file(path),
        }
    return references


def rebuild_canonical_audit_bundle(
    root: str | Path,
) -> dict[str, Any]:
    root = Path(root)
    plan_payload = _load(root / CANONICAL_FIXTURES["plan"])
    registry_corpus = _load(
        root / CANONICAL_FIXTURES["registry"]
    )
    evidence_corpus = _load(
        root / CANONICAL_FIXTURES["evidence"]
    )
    integrity_report = _load(
        root / CANONICAL_FIXTURES["integrity"]
    )
    sequential_report = _load(
        root / CANONICAL_FIXTURES["sequential"]
    )
    family_bundle = _load(
        root / CANONICAL_FIXTURES["family"]
    )

    try:
        lineage = registry_corpus[
            "valid_preregistered"
        ]["entries"]
        evidence = evidence_corpus["cases"]["null_effect"]
        integrity_result = integrity_report[
            "cases"
        ]["valid_null_aa"]
        sequential_result = sequential_report[
            "cases"
        ]["null_effect"]
        family_report = family_bundle[
            "cases"
        ]["valid_preregistered"]
        primary_metric = plan_payload["primary_metric"]
    except (KeyError, TypeError) as exc:
        raise ExperimentAuditError(
            "canonical durable fixtures are incomplete"
        ) from exc

    references = _fixture_references(root)
    bundle = build_audit_bundle(
        plan_payload=plan_payload,
        registry_lineage=lineage,
        evidence_payload=evidence,
        integrity_result=integrity_result,
        sequential_result=sequential_result,
        family_report=family_report,
        primary_metric=primary_metric,
        source_references=references,
    )

    family_reference = bundle[
        "multiplicity_family"
    ]["confirmatory"]["integrity_reference"]
    if (
        family_reference["report_sha256"]
        != references["integrity"]["sha256"]
    ):
        raise ExperimentAuditError(
            "family integrity file reference mismatch"
        )
    return bundle


def _safe_source_path(
    root: Path,
    relative: str,
) -> Path:
    candidate = (root / relative).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ExperimentAuditError(
            "source reference escapes fixture root"
        ) from exc
    return candidate


def verify_audit_bundle(
    root: str | Path,
    bundle: Mapping[str, Any],
) -> dict[str, Any]:
    root = Path(root)
    expected_fields = {
        "audit_bundle_version",
        "subject",
        "classification",
        "classification_reason",
        "registry",
        "assignment",
        "event_window_corpus",
        "integrity",
        "sequential",
        "multiplicity_family",
        "primary_metric_definition",
        "raw_evidence",
        "source_references",
        "auto_publish",
        "external_mutation",
        "bundle_digest",
    }
    if (
        not isinstance(bundle, Mapping)
        or set(bundle) != expected_fields
    ):
        raise ExperimentAuditError(
            "audit bundle fields must match v1 exactly"
        )
    if bundle["audit_bundle_version"] != AUDIT_BUNDLE_VERSION:
        raise ExperimentAuditError(
            "unsupported audit bundle version"
        )
    if bundle["classification"] not in CLASSIFICATIONS:
        raise ExperimentAuditError(
            "unknown audit classification"
        )
    if (
        bundle["auto_publish"] is not False
        or bundle["external_mutation"] is not False
    ):
        raise ExperimentAuditError(
            "audit bundle must be read-only"
        )

    assignment = bundle["assignment"]
    if (
        not isinstance(assignment, Mapping)
        or set(assignment)
        != {"randomization_digest", "assignment_unit"}
    ):
        raise ExperimentAuditError(
            "assignment component fields conflict"
        )
    digest = assignment["randomization_digest"]
    if not isinstance(digest, str) or len(digest) != 64:
        raise ExperimentAuditError(
            "assignment randomization digest missing"
        )

    provided_digest = bundle["bundle_digest"]
    if (
        not isinstance(provided_digest, str)
        or len(provided_digest) != 64
    ):
        raise ExperimentAuditError("bundle digest missing")
    material = dict(bundle)
    material.pop("bundle_digest")
    if sha256_json(material) != provided_digest:
        raise ExperimentAuditError(
            "audit bundle digest mismatch"
        )

    references = bundle["source_references"]
    if (
        not isinstance(references, Mapping)
        or set(references) != set(CANONICAL_FIXTURES)
    ):
        raise ExperimentAuditError(
            "audit source references conflict"
        )

    verified_hashes = {}
    for name in sorted(CANONICAL_FIXTURES):
        reference = references[name]
        if (
            not isinstance(reference, Mapping)
            or set(reference) != {"path", "sha256"}
        ):
            raise ExperimentAuditError(
                f"invalid source reference: {name}"
            )
        path = _safe_source_path(
            root, reference["path"]
        )
        if not path.is_file():
            raise ExperimentAuditError(
                f"missing source component: {name}"
            )
        actual = sha256_file(path)
        if actual != reference["sha256"]:
            raise ExperimentAuditError(
                f"tampered source component: {name}"
            )
        verified_hashes[name] = actual

    rebuilt = rebuild_canonical_audit_bundle(root)
    if rebuilt["bundle_digest"] != provided_digest:
        raise ExperimentAuditError(
            "bundle does not reconstruct from durable fixtures"
        )

    return {
        "verification_version": VERIFICATION_VERSION,
        "status": "verified",
        "bundle_digest": provided_digest,
        "classification": bundle["classification"],
        "classification_reason":
            bundle["classification_reason"],
        "experiment_id": bundle["subject"]["experiment_id"],
        "registry_freeze_hash":
            bundle["registry"]["latest_freeze_hash"],
        "randomization_digest":
            bundle["assignment"]["randomization_digest"],
        "event_window_corpus_digest":
            bundle["event_window_corpus"]["corpus_digest"],
        "integrity_result_digest":
            bundle["integrity"]["result_digest"],
        "sequential_result_digest":
            bundle["sequential"]["result_digest"],
        "family_report_digest":
            bundle["multiplicity_family"]["report_digest"],
        "source_hashes": verified_hashes,
        "raw_evidence_preserved": bool(
            bundle["raw_evidence"][
                "preserved_when_blocked"
            ]
        ),
        "auto_publish": False,
        "external_mutation": False,
    }


def write_canonical_bundle(
    root: str | Path,
    output: str | Path,
) -> dict[str, Any]:
    bundle = rebuild_canonical_audit_bundle(root)
    output_path = Path(output)
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    output_path.write_text(
        json.dumps(
            bundle,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return bundle


def _cli() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m growth_analytics.experiment_audit"
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    rebuild = subparsers.add_parser("rebuild")
    rebuild.add_argument("--root", default=".")
    rebuild.add_argument(
        "--output",
        default="fixtures/experiment_audit_bundle_v1.json",
    )

    verify = subparsers.add_parser("verify")
    verify.add_argument("--root", default=".")
    verify.add_argument(
        "--bundle",
        default="fixtures/experiment_audit_bundle_v1.json",
    )
    verify.add_argument("--report", default=None)

    args = parser.parse_args()
    if args.command == "rebuild":
        bundle = write_canonical_bundle(
            args.root,
            args.output,
        )
        print(json.dumps({
            "status": "rebuilt",
            "bundle_digest": bundle["bundle_digest"],
            "classification": bundle["classification"],
            "output": args.output,
        }, sort_keys=True))
        return 0

    bundle = _load(Path(args.root) / args.bundle)
    report = verify_audit_bundle(
        args.root,
        bundle,
    )
    if args.report:
        report_path = Path(args.root) / args.report
        report_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        report_path.write_text(
            json.dumps(
                report,
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
