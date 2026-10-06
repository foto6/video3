from __future__ import annotations

import argparse
import copy
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import canonical_json, sha256_json
from .event_stream import parse_timestamp
from . import local_rehearsal_evidence_r36 as r36

CONTRACT_VERSION = "growth.local_pc_selection_adapter.r37.v1"
AUTHORITY_VERSION = "growth.local_pc_selection_authority.r37.v1"
POLICY_VERSION = "growth.local_pc_selection_policy.r37.v1"
INPUT_VERSION = "growth.local_pc_media_evidence.r37.v1"
NORMALIZATION_VERSION = "growth.local_pc_selection_normalization.r37.v1"
DECISION_VERSION = "growth.local_pc_selection_decision.r37.v1"
ADVISORY_VERSION = "growth.local_pc_selection_advisory.r37.v1"
REPORT_VERSION = "growth.local_pc_selection_adapter.r37.report.v1"
MEDIA_CONTRACT_ID = "media.local_render_runner.r26.v1"

WAITING = "WAITING_MEDIA_R26_AUTHORITY"
EVIDENCE_INVALID = "EVIDENCE_INVALID"
KEEP_BASELINE = "KEEP_BASELINE"
SELECT_CANDIDATE = "SELECT_CANDIDATE"
TARGETED_REEDIT = "TARGETED_REEDIT_RECOMMENDED"
HUMAN_REVIEW = "HUMAN_REVIEW_REQUIRED"

R36_SHA = "a53f9deb180bb256d193f0422c6ffd7a5923d97a"
R36_CI = 37403145649
R36_AUTHORITY_BLOB = "a44963e13c10fd607cf4259e355dc990a297ecd9"
R36_POLICY_BLOB = "e6f6dd25b70f1e1ec4031d7eb2d0e8bb7ceb1b82"
R36_CONTRACT_BLOB = "3e0cd5baa85b4c59eb75386766aed0300a91b16d"
R36_IMPLEMENTATION_BLOB = "3eeb7bbacb423c3ed4dcd66391147d0e9fd58c37"
R36_VERIFIER_BLOB = "a35051e47a6289f1cc04c3ae3b73516dbc45378e"


class R37Error(ValueError):
    pass


class AuthorityDrift(R37Error):
    pass


class PolicyDrift(R37Error):
    pass


class EvidenceInvalid(R37Error):
    pass


def _clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceInvalid(f"{field} must be non-empty string")
    return value


def _sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise EvidenceInvalid(f"{field} must be lowercase SHA-256")
    return value


def _git_sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise EvidenceInvalid(f"{field} must be exact Git SHA")
    return value


def _artifact_digest(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or len(value) != 71
    ):
        raise EvidenceInvalid(f"{field} must be sha256:<hex>")
    _sha256(value.removeprefix("sha256:"), field)
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EvidenceInvalid(f"{field} must be positive integer")
    return value


def _score(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceInvalid(f"{field} must be numeric")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise EvidenceInvalid(f"{field} must be within [0,1]")
    return result


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise EvidenceInvalid(f"{field} must be timestamp string")
    try:
        return parse_timestamp(value)
    except Exception as exc:
        raise EvidenceInvalid(f"{field} invalid timestamp") from exc


def _expected_r36_parent() -> dict[str, Any]:
    return {
        "repository": "foto6/video3",
        "producer_sha": R36_SHA,
        "ci_run_id": R36_CI,
        "ci_conclusion": "SUCCESS",
        "contract": "growth.local_rehearsal_evidence.r36.v1",
        "authority_blob": R36_AUTHORITY_BLOB,
        "policy_blob": R36_POLICY_BLOB,
        "contract_blob": R36_CONTRACT_BLOB,
        "implementation_blob": R36_IMPLEMENTATION_BLOB,
        "verifier_blob": R36_VERIFIER_BLOB,
    }


def _expected_boundary() -> dict[str, Any]:
    return {
        "local_only": True,
        "network_required": False,
        "browser_call": False,
        "provider_call": False,
        "provider_mutation": False,
        "creator_mutation": False,
        "live_authorization": False,
        "social_publish": False,
        "credential_access": False,
        "merge": False,
    }


def default_authority() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.local_pc_selection_adapter.r37.v1"
        / "authority.json"
    )


def default_policy() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.local_pc_selection_adapter.r37.v1"
        / "policy.json"
    )


def validate_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "contract_version",
        "growth_r36_parent",
        "media_r26_authority",
        "boundary",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise AuthorityDrift("R37 authority fields invalid")
    if value["contract_version"] != AUTHORITY_VERSION:
        raise AuthorityDrift("R37 authority contract mismatch")
    if value["growth_r36_parent"] != _expected_r36_parent():
        raise AuthorityDrift("exact Growth R36 parent authority drift")
    if value["boundary"] != _expected_boundary():
        raise AuthorityDrift("R37 no-side-effect boundary drift")

    media = value["media_r26_authority"]
    if not isinstance(media, Mapping) or set(media) != {
        "state",
        "required_contract_id",
        "repository",
        "producer_sha",
        "ci_run_id",
        "ci_conclusion",
        "artifact",
        "independent_qa",
    }:
        raise AuthorityDrift("Media R26 authority fields invalid")
    if (
        media["required_contract_id"] != MEDIA_CONTRACT_ID
        or media["repository"] != "foto6/video2"
    ):
        raise AuthorityDrift("Media R26 contract/repository authority drift")
    artifact = media["artifact"]
    if not isinstance(artifact, Mapping) or set(artifact) != {
        "availability",
        "id",
        "name",
        "digest",
    }:
        raise AuthorityDrift("Media R26 artifact authority fields invalid")
    qa = media["independent_qa"]
    if not isinstance(qa, Mapping) or set(qa) != {
        "disposition",
        "producer_sha",
        "ci_run_id",
        "artifact_id",
        "artifact_digest",
        "matrix_digest",
    }:
        raise AuthorityDrift("Media R26 QA authority fields invalid")

    if media["state"] == "UNRESOLVED":
        if any(
            media[field] is not None
            for field in ("producer_sha", "ci_run_id", "ci_conclusion")
        ):
            raise AuthorityDrift("unresolved Media authority cannot carry producer pins")
        if artifact != {
            "availability": "UNRESOLVED",
            "id": None,
            "name": None,
            "digest": None,
        }:
            raise AuthorityDrift("unresolved Media artifact authority must remain null")
        if qa != {
            "disposition": "UNRESOLVED",
            "producer_sha": None,
            "ci_run_id": None,
            "artifact_id": None,
            "artifact_digest": None,
            "matrix_digest": None,
        }:
            raise AuthorityDrift("unresolved Media QA authority must remain null")
    elif media["state"] == "ACCEPTED":
        try:
            _git_sha(media["producer_sha"], "media_r26_authority.producer_sha")
            _positive_int(media["ci_run_id"], "media_r26_authority.ci_run_id")
        except EvidenceInvalid as exc:
            raise AuthorityDrift(str(exc)) from exc
        if media["ci_conclusion"] != "SUCCESS":
            raise AuthorityDrift("accepted Media R26 CI conclusion must be SUCCESS")
        if artifact["availability"] == "AVAILABLE":
            try:
                _positive_int(artifact["id"], "media_r26_authority.artifact.id")
                _nonempty(artifact["name"], "media_r26_authority.artifact.name")
                _artifact_digest(
                    artifact["digest"],
                    "media_r26_authority.artifact.digest",
                )
            except EvidenceInvalid as exc:
                raise AuthorityDrift(str(exc)) from exc
        elif artifact["availability"] == "UNAVAILABLE":
            if any(artifact[field] is not None for field in ("id", "name", "digest")):
                raise AuthorityDrift("unavailable Media artifact must carry null tuple")
        else:
            raise AuthorityDrift("accepted Media artifact availability invalid")
        if qa["disposition"] != "ACCEPTED":
            raise AuthorityDrift("accepted Media authority requires independent QA")
        try:
            _git_sha(qa["producer_sha"], "media_r26_authority.qa.producer_sha")
            _positive_int(qa["ci_run_id"], "media_r26_authority.qa.ci_run_id")
            _positive_int(qa["artifact_id"], "media_r26_authority.qa.artifact_id")
            _artifact_digest(
                qa["artifact_digest"],
                "media_r26_authority.qa.artifact_digest",
            )
            _sha256(qa["matrix_digest"], "media_r26_authority.qa.matrix_digest")
        except EvidenceInvalid as exc:
            raise AuthorityDrift(str(exc)) from exc
    else:
        raise AuthorityDrift("Media R26 authority state must be UNRESOLVED or ACCEPTED")

    # Preserve R36's checked-in anti-leakage/no-live-action boundary.
    r36_authority = r36.default_authority()
    r36.validate_authority(r36_authority)
    if r36_authority["boundary"]["live_authorization"] is not False:
        raise AuthorityDrift("R36 parent live authorization boundary drift")
    return _clone(value)


def media_authority_resolved(value: Mapping[str, Any]) -> bool:
    return validate_authority(value)["media_r26_authority"]["state"] == "ACCEPTED"


def authority_digest(value: Mapping[str, Any]) -> str:
    return sha256_json(validate_authority(value))


def validate_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "contract_version",
        "policy_version",
        "parent_contract",
        "selection",
        "evidence",
        "runtime",
        "output",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PolicyDrift("R37 policy fields invalid")
    if (
        value["contract_version"] != POLICY_VERSION
        or value["policy_version"] != 1
        or value["parent_contract"] != "growth.local_rehearsal_evidence.r36.v1"
    ):
        raise PolicyDrift("R37 policy identity drift")
    if value["selection"] != {
        "minimum_candidates": 2,
        "minimum_reviews_per_candidate": 2,
        "tie_margin": 0.02,
        "select_margin_over_baseline": 0.05,
        "maximum_review_score_spread": 0.25,
        "targeted_reedit_margin": 0.03,
    }:
        raise PolicyDrift("R37 selection thresholds drift")
    if value["evidence"] != {
        "allowed_classes": ["FIXTURE", "OFFLINE_MODEL", "GENUINE_REVIEW"],
        "positive_decision_classes": ["OFFLINE_MODEL", "GENUINE_REVIEW"],
        "fixture_positive_decision_allowed": False,
        "human_ground_truth": False,
        "live_metrics": False,
    }:
        raise PolicyDrift("R37 evidence policy drift")
    if value["runtime"] != {
        "required_execution_context": "LOCAL_WINDOWS",
        "hosted_ci_evidence_allowed": False,
        "provider_network_allowed": False,
        "browser_allowed": False,
    }:
        raise PolicyDrift("R37 runtime policy drift")
    if value["output"] != {
        "recommendations": [
            KEEP_BASELINE,
            SELECT_CANDIDATE,
            TARGETED_REEDIT,
            HUMAN_REVIEW,
            WAITING,
            EVIDENCE_INVALID,
        ],
        "live_authorization": False,
        "provider_mutation_allowed": False,
        "creator_mutation_allowed": False,
        "publish_allowed": False,
    }:
        raise PolicyDrift("R37 output boundary drift")
    return _clone(value)


def policy_digest(value: Mapping[str, Any]) -> str:
    return sha256_json(validate_policy(value))


def _validate_artifact_claim(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "availability",
        "id",
        "name",
        "digest",
    }:
        raise EvidenceInvalid("media claim artifact fields invalid")
    if value["availability"] == "AVAILABLE":
        _positive_int(value["id"], "media_claim.artifact.id")
        _nonempty(value["name"], "media_claim.artifact.name")
        _artifact_digest(value["digest"], "media_claim.artifact.digest")
    elif value["availability"] == "UNAVAILABLE":
        if any(value[field] is not None for field in ("id", "name", "digest")):
            raise EvidenceInvalid("unavailable media artifact claim must carry null tuple")
    else:
        raise EvidenceInvalid("media claim artifact availability invalid")
    return _clone(value)


def _validate_runtime(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"ffmpeg", "ffprobe"}:
        raise EvidenceInvalid("runtime fields invalid")
    out: dict[str, Any] = {}
    for key in ("ffmpeg", "ffprobe"):
        row = value[key]
        if not isinstance(row, Mapping) or set(row) != {
            "sha256",
            "version_digest",
        }:
            raise EvidenceInvalid(f"runtime.{key} fields invalid")
        out[key] = {
            "sha256": _sha256(row["sha256"], f"runtime.{key}.sha256"),
            "version_digest": _sha256(
                row["version_digest"],
                f"runtime.{key}.version_digest",
            ),
        }
    return out


def _validate_input_video(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"sha256", "size"}:
        raise EvidenceInvalid("input_video fields invalid")
    return {
        "sha256": _sha256(value["sha256"], "input_video.sha256"),
        "size": _positive_int(value["size"], "input_video.size"),
    }


def _validate_review(
    value: Mapping[str, Any],
    *,
    subject_id: str,
    subject_sha256: str,
    execution_started: datetime,
    execution_completed: datetime,
    rendered_at: datetime,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "review_id",
        "candidate_id",
        "evidence_class",
        "score",
        "evidence_digest",
        "review_policy_contract",
        "review_policy_version",
        "captured_at",
        "source_render_sha256",
        "human_ground_truth",
    }:
        raise EvidenceInvalid("review evidence fields invalid")
    if value["candidate_id"] != subject_id:
        raise EvidenceInvalid("review candidate identity mismatch")
    if value["source_render_sha256"] != subject_sha256:
        raise EvidenceInvalid("review source render hash mismatch")
    evidence_class = value["evidence_class"]
    if evidence_class not in policy["evidence"]["allowed_classes"]:
        raise EvidenceInvalid("review evidence class invalid")
    if value["review_policy_contract"] != POLICY_VERSION:
        raise EvidenceInvalid("stale review policy contract")
    if value["review_policy_version"] != policy["policy_version"]:
        raise EvidenceInvalid("stale review policy version")
    if value["human_ground_truth"] is not False:
        raise EvidenceInvalid("model/local review may not claim human ground truth")
    captured = _timestamp(value["captured_at"], "review.captured_at")
    if captured < rendered_at:
        raise EvidenceInvalid("stale review predates rendered candidate bytes")
    if captured < execution_started or captured > execution_completed:
        raise EvidenceInvalid("review timestamp outside local execution window")
    return {
        "review_id": _nonempty(value["review_id"], "review.review_id"),
        "candidate_id": subject_id,
        "evidence_class": evidence_class,
        "score": _score(value["score"], "review.score"),
        "evidence_digest": _sha256(value["evidence_digest"], "review.evidence_digest"),
        "review_policy_contract": POLICY_VERSION,
        "review_policy_version": policy["policy_version"],
        "captured_at": value["captured_at"],
        "source_render_sha256": subject_sha256,
        "human_ground_truth": False,
    }


def _validate_phase_timings(
    phases: Any,
    *,
    execution_started: datetime,
    execution_completed: datetime,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not isinstance(phases, list) or not phases:
        raise EvidenceInvalid("phase_timings must be non-empty list")
    out = []
    by_id = {}
    operation_ids = set()
    for raw in phases:
        if not isinstance(raw, Mapping) or set(raw) != {
            "phase_id",
            "operation_id",
            "started_at",
            "ended_at",
            "duration_ms",
            "resumed",
            "checkpoint_digest",
        }:
            raise EvidenceInvalid("phase timing fields invalid")
        phase_id = _nonempty(raw["phase_id"], "phase.phase_id")
        operation_id = _nonempty(raw["operation_id"], "phase.operation_id")
        if phase_id in by_id:
            raise EvidenceInvalid("duplicate phase_id")
        if operation_id in operation_ids:
            raise EvidenceInvalid("duplicate operation IDs")
        operation_ids.add(operation_id)
        start = _timestamp(raw["started_at"], "phase.started_at")
        end = _timestamp(raw["ended_at"], "phase.ended_at")
        if start < execution_started or end > execution_completed or end < start:
            raise EvidenceInvalid("phase timestamp outside local run or reversed")
        duration_ms = int((end - start).total_seconds() * 1000)
        if raw["duration_ms"] != duration_ms:
            raise EvidenceInvalid("phase duration/timestamp mismatch")
        resumed = raw["resumed"]
        if not isinstance(resumed, bool):
            raise EvidenceInvalid("phase.resumed must be boolean")
        checkpoint = raw["checkpoint_digest"]
        if resumed:
            _sha256(checkpoint, "phase.checkpoint_digest")
        elif checkpoint is not None:
            raise EvidenceInvalid("non-resumed phase cannot carry checkpoint")
        row = {
            "phase_id": phase_id,
            "operation_id": operation_id,
            "started_at": raw["started_at"],
            "ended_at": raw["ended_at"],
            "duration_ms": raw["duration_ms"],
            "resumed": resumed,
            "checkpoint_digest": checkpoint,
        }
        out.append(row)
        by_id[phase_id] = row
    out.sort(key=lambda row: row["phase_id"])
    return out, by_id


def _validate_resume_events(
    events: Any,
    *,
    phases_by_id: Mapping[str, Mapping[str, Any]],
    execution_started: datetime,
    execution_completed: datetime,
) -> list[dict[str, Any]]:
    if not isinstance(events, list):
        raise EvidenceInvalid("resume_events must be list")
    out = []
    ids = set()
    for raw in events:
        if not isinstance(raw, Mapping) or set(raw) != {
            "resume_event_id",
            "phase_id",
            "operation_id",
            "checkpoint_digest",
            "resumed_at",
            "event_digest",
        }:
            raise EvidenceInvalid("resume event fields invalid")
        event_id = _nonempty(raw["resume_event_id"], "resume_event_id")
        if event_id in ids:
            raise EvidenceInvalid("duplicate resume event identity")
        ids.add(event_id)
        phase_id = _nonempty(raw["phase_id"], "resume.phase_id")
        if phase_id not in phases_by_id:
            raise EvidenceInvalid("resume event references missing phase")
        phase = phases_by_id[phase_id]
        if phase["resumed"] is not True:
            raise EvidenceInvalid("resume event references non-resumed phase")
        if raw["operation_id"] != phase["operation_id"]:
            raise EvidenceInvalid("resume event operation mismatch")
        checkpoint = _sha256(
            raw["checkpoint_digest"],
            "resume.checkpoint_digest",
        )
        if checkpoint != phase["checkpoint_digest"]:
            raise EvidenceInvalid("resume checkpoint mismatch")
        resumed_at = _timestamp(raw["resumed_at"], "resume.resumed_at")
        if resumed_at < execution_started or resumed_at > execution_completed:
            raise EvidenceInvalid("resume timestamp outside local run")
        event_material = {
            "resume_event_id": event_id,
            "phase_id": phase_id,
            "operation_id": raw["operation_id"],
            "checkpoint_digest": checkpoint,
            "resumed_at": raw["resumed_at"],
        }
        if raw["event_digest"] != sha256_json(event_material):
            raise EvidenceInvalid("resume event digest mismatch")
        out.append({**event_material, "event_digest": raw["event_digest"]})
    out.sort(key=lambda row: row["resume_event_id"])

    resumptions = {
        (row["phase_id"], row["checkpoint_digest"]) for row in out
    }
    for phase_id, phase in phases_by_id.items():
        if phase["resumed"] and (
            phase_id,
            phase["checkpoint_digest"],
        ) not in resumptions:
            raise EvidenceInvalid("resumed phase missing checkpoint evidence")
    return out


def _phase_for_operation(
    operation_id: str,
    phase_id: str,
    phases_by_id: Mapping[str, Mapping[str, Any]],
    field: str,
) -> Mapping[str, Any]:
    phase = phases_by_id.get(phase_id)
    if phase is None:
        raise EvidenceInvalid(f"{field} render phase missing")
    if phase["operation_id"] != operation_id:
        raise EvidenceInvalid(f"{field} operation/phase mismatch")
    return phase


def validate_bundle(
    value: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    policy = validate_policy(policy)
    expected = {
        "contract_version",
        "media_contract_id",
        "bundle_id",
        "media_claim",
        "execution_context",
        "local_run_manifest",
        "local_run_manifest_digest",
        "input_video",
        "runtime",
        "candidates",
        "targeted_reedit",
        "final_artifact",
        "phase_timings",
        "resume_events",
        "boundary",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise EvidenceInvalid("local Media evidence bundle fields invalid")
    if value["contract_version"] != INPUT_VERSION:
        raise EvidenceInvalid("local Media evidence bundle contract mismatch")
    if value["media_contract_id"] != MEDIA_CONTRACT_ID:
        raise EvidenceInvalid("Media contract ID must equal media.local_render_runner.r26.v1")
    bundle_id = _nonempty(value["bundle_id"], "bundle_id")

    media_claim = value["media_claim"]
    if not isinstance(media_claim, Mapping) or set(media_claim) != {
        "repository",
        "producer_sha",
        "ci_run_id",
        "ci_conclusion",
        "artifact",
    }:
        raise EvidenceInvalid("media_claim fields invalid")
    if media_claim["repository"] != "foto6/video2":
        raise EvidenceInvalid("Media claim repository mismatch")
    media_claim_norm = {
        "repository": "foto6/video2",
        "producer_sha": _git_sha(media_claim["producer_sha"], "media_claim.producer_sha"),
        "ci_run_id": _positive_int(media_claim["ci_run_id"], "media_claim.ci_run_id"),
        "ci_conclusion": media_claim["ci_conclusion"],
        "artifact": _validate_artifact_claim(media_claim["artifact"]),
    }
    if media_claim_norm["ci_conclusion"] != "SUCCESS":
        raise EvidenceInvalid("Media local evidence requires successful source CI claim")

    execution = value["execution_context"]
    if not isinstance(execution, Mapping) or set(execution) != {
        "mode",
        "evidence_origin",
        "hosted_ci",
        "local_run",
        "provider_network_used",
        "browser_used",
        "machine_pseudonym",
        "started_at",
        "completed_at",
    }:
        raise EvidenceInvalid("execution_context fields invalid")
    if execution["mode"] != policy["runtime"]["required_execution_context"]:
        raise EvidenceInvalid("local evidence must use LOCAL_WINDOWS execution context")
    if execution["evidence_origin"] != "LOCAL_PC":
        raise EvidenceInvalid("local-run evidence origin must be LOCAL_PC")
    if execution["hosted_ci"] is not False:
        raise EvidenceInvalid("local-run evidence may not pretend hosted CI evidence")
    if execution["local_run"] is not True:
        raise EvidenceInvalid("local-run marker must be true")
    if execution["provider_network_used"] is not False:
        raise EvidenceInvalid("provider network use forbidden")
    if execution["browser_used"] is not False:
        raise EvidenceInvalid("browser use forbidden")
    _nonempty(execution["machine_pseudonym"], "execution_context.machine_pseudonym")
    execution_started = _timestamp(execution["started_at"], "execution.started_at")
    execution_completed = _timestamp(execution["completed_at"], "execution.completed_at")
    if execution_completed < execution_started:
        raise EvidenceInvalid("execution timestamps reversed")
    execution_norm = _clone(execution)

    input_video = _validate_input_video(value["input_video"])
    runtime = _validate_runtime(value["runtime"])
    phases, phases_by_id = _validate_phase_timings(
        value["phase_timings"],
        execution_started=execution_started,
        execution_completed=execution_completed,
    )
    resume_events = _validate_resume_events(
        value["resume_events"],
        phases_by_id=phases_by_id,
        execution_started=execution_started,
        execution_completed=execution_completed,
    )

    raw_candidates = value["candidates"]
    if (
        not isinstance(raw_candidates, list)
        or len(raw_candidates) < policy["selection"]["minimum_candidates"]
    ):
        raise EvidenceInvalid("partial candidate set")
    candidates = []
    candidate_ids = set()
    render_hashes = set()
    operation_ids = set()
    review_ids = set()
    baseline_ids = []
    for raw in raw_candidates:
        if not isinstance(raw, Mapping) or set(raw) != {
            "candidate_id",
            "role",
            "operation_id",
            "render_phase_id",
            "render_sha256",
            "render_size",
            "reviews",
        }:
            raise EvidenceInvalid("candidate fields invalid")
        candidate_id = _nonempty(raw["candidate_id"], "candidate.candidate_id")
        if candidate_id in candidate_ids:
            raise EvidenceInvalid("duplicate candidate identity")
        candidate_ids.add(candidate_id)
        if raw["role"] not in {"BASELINE", "CANDIDATE"}:
            raise EvidenceInvalid("candidate role invalid")
        if raw["role"] == "BASELINE":
            baseline_ids.append(candidate_id)
        operation_id = _nonempty(raw["operation_id"], "candidate.operation_id")
        if operation_id in operation_ids:
            raise EvidenceInvalid("duplicate operation IDs")
        operation_ids.add(operation_id)
        render_hash = _sha256(raw["render_sha256"], "candidate.render_sha256")
        if render_hash in render_hashes:
            raise EvidenceInvalid("duplicate candidate rendered bytes")
        render_hashes.add(render_hash)
        render_size = _positive_int(raw["render_size"], "candidate.render_size")
        phase = _phase_for_operation(
            operation_id,
            _nonempty(raw["render_phase_id"], "candidate.render_phase_id"),
            phases_by_id,
            "candidate",
        )
        reviews_raw = raw["reviews"]
        if not isinstance(reviews_raw, list):
            raise EvidenceInvalid("candidate reviews must be list")
        reviews = []
        rendered_at = _timestamp(phase["ended_at"], "candidate.rendered_at")
        for review_raw in reviews_raw:
            review = _validate_review(
                review_raw,
                subject_id=candidate_id,
                subject_sha256=render_hash,
                execution_started=execution_started,
                execution_completed=execution_completed,
                rendered_at=rendered_at,
                policy=policy,
            )
            if review["review_id"] in review_ids:
                raise EvidenceInvalid("duplicate review identity")
            review_ids.add(review["review_id"])
            reviews.append(review)
        reviews.sort(key=lambda row: row["review_id"])
        candidates.append(
            {
                "candidate_id": candidate_id,
                "role": raw["role"],
                "operation_id": operation_id,
                "render_phase_id": raw["render_phase_id"],
                "render_sha256": render_hash,
                "render_size": render_size,
                "reviews": reviews,
            }
        )
    if len(baseline_ids) != 1:
        raise EvidenceInvalid("exactly one baseline candidate required")
    candidates.sort(key=lambda row: row["candidate_id"])
    by_candidate = {row["candidate_id"]: row for row in candidates}

    target = value["targeted_reedit"]
    if not isinstance(target, Mapping) or set(target) != {
        "targeted_reedit_id",
        "operation_id",
        "render_phase_id",
        "derived_from_candidate_id",
        "derived_from_render_sha256",
        "artifact_sha256",
        "artifact_size",
        "operation_graph_digest",
        "reviews",
    }:
        raise EvidenceInvalid("targeted_reedit fields invalid")
    target_id = _nonempty(target["targeted_reedit_id"], "targeted_reedit_id")
    if target_id in candidate_ids:
        raise EvidenceInvalid("targeted re-edit identity collides with candidate")
    target_operation = _nonempty(target["operation_id"], "targeted_reedit.operation_id")
    if target_operation in operation_ids:
        raise EvidenceInvalid("duplicate operation IDs")
    operation_ids.add(target_operation)
    source_candidate_id = _nonempty(
        target["derived_from_candidate_id"],
        "targeted_reedit.derived_from_candidate_id",
    )
    if source_candidate_id not in by_candidate:
        raise EvidenceInvalid("targeted re-edit source candidate missing")
    source_candidate = by_candidate[source_candidate_id]
    if target["derived_from_render_sha256"] != source_candidate["render_sha256"]:
        raise EvidenceInvalid("targeted re-edit source render hash mismatch")
    target_hash = _sha256(target["artifact_sha256"], "targeted_reedit.artifact_sha256")
    if target_hash == source_candidate["render_sha256"]:
        raise EvidenceInvalid("targeted re-edit bytes identical to source candidate")
    target_size = _positive_int(target["artifact_size"], "targeted_reedit.artifact_size")
    target_graph = _sha256(
        target["operation_graph_digest"],
        "targeted_reedit.operation_graph_digest",
    )
    target_phase = _phase_for_operation(
        target_operation,
        _nonempty(target["render_phase_id"], "targeted_reedit.render_phase_id"),
        phases_by_id,
        "targeted_reedit",
    )
    target_reviews_raw = target["reviews"]
    if not isinstance(target_reviews_raw, list):
        raise EvidenceInvalid("targeted re-edit reviews must be list")
    target_reviews = []
    target_rendered_at = _timestamp(
        target_phase["ended_at"],
        "targeted_reedit.rendered_at",
    )
    for review_raw in target_reviews_raw:
        review = _validate_review(
            review_raw,
            subject_id=target_id,
            subject_sha256=target_hash,
            execution_started=execution_started,
            execution_completed=execution_completed,
            rendered_at=target_rendered_at,
            policy=policy,
        )
        if review["review_id"] in review_ids:
            raise EvidenceInvalid("duplicate review identity")
        review_ids.add(review["review_id"])
        target_reviews.append(review)
    target_reviews.sort(key=lambda row: row["review_id"])
    targeted_reedit = {
        "targeted_reedit_id": target_id,
        "operation_id": target_operation,
        "render_phase_id": target["render_phase_id"],
        "derived_from_candidate_id": source_candidate_id,
        "derived_from_render_sha256": source_candidate["render_sha256"],
        "artifact_sha256": target_hash,
        "artifact_size": target_size,
        "operation_graph_digest": target_graph,
        "reviews": target_reviews,
    }

    final = value["final_artifact"]
    if not isinstance(final, Mapping) or set(final) != {
        "operation_id",
        "render_phase_id",
        "sha256",
        "size",
        "lineage_kind",
        "lineage_id",
        "lineage_sha256",
    }:
        raise EvidenceInvalid("final_artifact fields invalid")
    final_operation = _nonempty(final["operation_id"], "final_artifact.operation_id")
    if final_operation in operation_ids:
        raise EvidenceInvalid("duplicate operation IDs")
    operation_ids.add(final_operation)
    _phase_for_operation(
        final_operation,
        _nonempty(final["render_phase_id"], "final_artifact.render_phase_id"),
        phases_by_id,
        "final_artifact",
    )
    final_hash = _sha256(final["sha256"], "final_artifact.sha256")
    final_size = _positive_int(final["size"], "final_artifact.size")
    lineage_kind = final["lineage_kind"]
    lineage_id = _nonempty(final["lineage_id"], "final_artifact.lineage_id")
    lineage_sha = _sha256(final["lineage_sha256"], "final_artifact.lineage_sha256")
    if lineage_kind == "CANDIDATE":
        if lineage_id not in by_candidate:
            raise EvidenceInvalid("final candidate lineage identity missing")
        expected_hash = by_candidate[lineage_id]["render_sha256"]
    elif lineage_kind == "TARGETED_REEDIT":
        if lineage_id != target_id:
            raise EvidenceInvalid("final targeted re-edit lineage identity mismatch")
        expected_hash = target_hash
    else:
        raise EvidenceInvalid("final artifact lineage kind invalid")
    if lineage_sha != expected_hash or final_hash != expected_hash:
        raise EvidenceInvalid("final artifact hash does not match selected lineage bytes")
    final_artifact = {
        "operation_id": final_operation,
        "render_phase_id": final["render_phase_id"],
        "sha256": final_hash,
        "size": final_size,
        "lineage_kind": lineage_kind,
        "lineage_id": lineage_id,
        "lineage_sha256": lineage_sha,
    }

    boundary = value["boundary"]
    if not isinstance(boundary, Mapping) or boundary != {
        "local_pc_evidence": True,
        "hosted_ci_evidence": False,
        "network_call": False,
        "browser_call": False,
        "provider_call": False,
        "live_metrics": False,
        "human_ground_truth": False,
        "provider_mutation": False,
        "creator_mutation": False,
        "social_publish": False,
        "credential_access": False,
    }:
        raise EvidenceInvalid("local evidence boundary drift")

    manifest = value["local_run_manifest"]
    if not isinstance(manifest, Mapping) or set(manifest) != {
        "manifest_version",
        "input_video",
        "runtime_identity_digest",
        "candidate_hashes",
        "targeted_reedit_sha256",
        "final_artifact_sha256",
        "phase_timings_digest",
        "resume_events_digest",
    }:
        raise EvidenceInvalid("local_run_manifest fields invalid")
    if manifest["manifest_version"] != "media.local_render_runner.r26.local_run_manifest.v1":
        raise EvidenceInvalid("local-run manifest version invalid")
    manifest_digest = _sha256(
        value["local_run_manifest_digest"],
        "local_run_manifest_digest",
    )
    if manifest_digest != sha256_json(manifest):
        raise EvidenceInvalid("final/local-run manifest tamper")
    if manifest["input_video"] != input_video:
        raise EvidenceInvalid("input video hash mismatch with local-run manifest")
    if manifest["runtime_identity_digest"] != sha256_json(runtime):
        raise EvidenceInvalid("runtime ffmpeg/ffprobe identity mismatch")
    expected_candidate_hashes = {
        row["candidate_id"]: row["render_sha256"] for row in candidates
    }
    if manifest["candidate_hashes"] != expected_candidate_hashes:
        raise EvidenceInvalid("candidate hash mapping mismatch with local-run manifest")
    if manifest["targeted_reedit_sha256"] != target_hash:
        raise EvidenceInvalid("targeted re-edit hash mismatch with local-run manifest")
    if manifest["final_artifact_sha256"] != final_hash:
        raise EvidenceInvalid("final artifact hash mismatch with local-run manifest")
    if manifest["phase_timings_digest"] != sha256_json(phases):
        raise EvidenceInvalid("phase timing digest mismatch with local-run manifest")
    if manifest["resume_events_digest"] != sha256_json(resume_events):
        raise EvidenceInvalid("resume event digest mismatch with local-run manifest")

    normalized = {
        "contract_version": INPUT_VERSION,
        "media_contract_id": MEDIA_CONTRACT_ID,
        "bundle_id": bundle_id,
        "media_claim": media_claim_norm,
        "execution_context": execution_norm,
        "local_run_manifest": _clone(manifest),
        "local_run_manifest_digest": manifest_digest,
        "input_video": input_video,
        "runtime": runtime,
        "candidates": candidates,
        "targeted_reedit": targeted_reedit,
        "final_artifact": final_artifact,
        "phase_timings": phases,
        "resume_events": resume_events,
        "boundary": _clone(boundary),
    }
    return _clone(normalized)


def _match_media_authority(
    bundle: Mapping[str, Any],
    authority: Mapping[str, Any],
) -> None:
    media = authority["media_r26_authority"]
    if media["state"] != "ACCEPTED":
        return
    claim = bundle["media_claim"]
    if (
        claim["producer_sha"] != media["producer_sha"]
        or claim["ci_run_id"] != media["ci_run_id"]
        or claim["ci_conclusion"] != media["ci_conclusion"]
    ):
        raise EvidenceInvalid("Media authority drift")
    accepted_artifact = media["artifact"]
    claimed_artifact = claim["artifact"]
    if accepted_artifact != claimed_artifact:
        raise EvidenceInvalid("Media artifact authority drift")


def _evidence_summary(reviews: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(review["evidence_class"] for review in reviews)
    return {
        "review_count": len(reviews),
        "classes": {key: counts[key] for key in sorted(counts)},
        "human_ground_truth": False,
    }


def _score_subject(
    reviews: Sequence[Mapping[str, Any]],
    *,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    positive_classes = set(policy["evidence"]["positive_decision_classes"])
    eligible = [review for review in reviews if review["evidence_class"] in positive_classes]
    scores = [float(review["score"]) for review in eligible]
    result = {
        "all_evidence": _evidence_summary(reviews),
        "eligible_review_count": len(eligible),
        "mean_score": None,
        "score_spread": None,
        "conflicting_scores": False,
        "missing_review_evidence": len(eligible)
        < policy["selection"]["minimum_reviews_per_candidate"],
    }
    if scores:
        result["mean_score"] = round(sum(scores) / len(scores), 12)
        result["score_spread"] = round(max(scores) - min(scores), 12)
        result["conflicting_scores"] = (
            result["score_spread"]
            > policy["selection"]["maximum_review_score_spread"]
        )
    return result


def _build_r36_projection(
    bundle: Mapping[str, Any],
    score_table: Mapping[str, Any],
) -> dict[str, Any]:
    reviews = []
    for candidate in bundle["candidates"]:
        for review in candidate["reviews"]:
            reviews.append(
                {
                    "review_id": review["review_id"],
                    "candidate_id": candidate["candidate_id"],
                    "evidence_class": review["evidence_class"],
                    "score": review["score"],
                    "evidence_digest": review["evidence_digest"],
                    "captured_at": review["captured_at"],
                    "human_ground_truth": False,
                }
            )
    for review in bundle["targeted_reedit"]["reviews"]:
        reviews.append(
            {
                "review_id": review["review_id"],
                "candidate_id": bundle["targeted_reedit"]["targeted_reedit_id"],
                "evidence_class": review["evidence_class"],
                "score": review["score"],
                "evidence_digest": review["evidence_digest"],
                "captured_at": review["captured_at"],
                "human_ground_truth": False,
            }
        )
    reviews.sort(key=lambda row: row["review_id"])
    return {
        "parent_contract": "growth.local_rehearsal_evidence.r36.v1",
        "parent_normalization_contract": "growth.local_review_normalization.r36.v1",
        "projection_contract": "growth.local_pc_r36_projection.r37.v1",
        "source": {
            "source_id": "media-r26-local-input",
            "source_sha256": bundle["input_video"]["sha256"],
            "source_size": bundle["input_video"]["size"],
        },
        "candidates": [
            {
                "candidate_id": row["candidate_id"],
                "role": row["role"],
                "render_sha256": row["render_sha256"],
                "render_size": row["render_size"],
                "operation_id": row["operation_id"],
                "score": score_table[row["candidate_id"]],
            }
            for row in bundle["candidates"]
        ],
        "reviews": reviews,
        "targeted_reedit": {
            "targeted_reedit_id": bundle["targeted_reedit"]["targeted_reedit_id"],
            "derived_from_candidate_id": bundle["targeted_reedit"][
                "derived_from_candidate_id"
            ],
            "derived_from_render_sha256": bundle["targeted_reedit"][
                "derived_from_render_sha256"
            ],
            "artifact_sha256": bundle["targeted_reedit"]["artifact_sha256"],
            "operation_graph_digest": bundle["targeted_reedit"][
                "operation_graph_digest"
            ],
            "score": score_table[bundle["targeted_reedit"]["targeted_reedit_id"]],
        },
        "final_artifact": bundle["final_artifact"],
        "media_r26_lineage": {
            "media_contract_id": bundle["media_contract_id"],
            "media_claim": bundle["media_claim"],
            "local_run_manifest_digest": bundle["local_run_manifest_digest"],
            "runtime_identity_digest": sha256_json(bundle["runtime"]),
            "phase_timings_digest": sha256_json(bundle["phase_timings"]),
            "resume_events_digest": sha256_json(bundle["resume_events"]),
        },
        "boundary": {
            "offline_only": True,
            "live_metrics": False,
            "human_ground_truth": False,
            "live_authorization": False,
            "provider_mutation": False,
            "creator_mutation": False,
            "social_publish": False,
        },
    }


def normalize_bundle(
    bundle: Mapping[str, Any],
    *,
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    authority = validate_authority(authority)
    policy = validate_policy(policy)
    parsed = validate_bundle(bundle, policy=policy)
    _match_media_authority(parsed, authority)

    score_table: dict[str, Any] = {}
    for candidate in parsed["candidates"]:
        score_table[candidate["candidate_id"]] = _score_subject(
            candidate["reviews"],
            policy=policy,
        )
    target_id = parsed["targeted_reedit"]["targeted_reedit_id"]
    score_table[target_id] = _score_subject(
        parsed["targeted_reedit"]["reviews"],
        policy=policy,
    )
    class_counts = Counter()
    for candidate in parsed["candidates"]:
        for review in candidate["reviews"]:
            class_counts[review["evidence_class"]] += 1
    for review in parsed["targeted_reedit"]["reviews"]:
        class_counts[review["evidence_class"]] += 1

    normalization = {
        "contract_version": NORMALIZATION_VERSION,
        "normalization_digest": "",
        "bundle_id": parsed["bundle_id"],
        "media_contract_id": MEDIA_CONTRACT_ID,
        "media_authority_state": authority["media_r26_authority"]["state"],
        "media_claim": parsed["media_claim"],
        "local_run_manifest_digest": parsed["local_run_manifest_digest"],
        "input_video": parsed["input_video"],
        "runtime": parsed["runtime"],
        "candidates": parsed["candidates"],
        "targeted_reedit": parsed["targeted_reedit"],
        "final_artifact": parsed["final_artifact"],
        "phase_timings": parsed["phase_timings"],
        "resume_events": parsed["resume_events"],
        "score_table": {key: score_table[key] for key in sorted(score_table)},
        "evidence_class_counts": {
            key: class_counts[key] for key in sorted(class_counts)
        },
        "r36_projection": _build_r36_projection(parsed, score_table),
        "authority": {
            "growth_r36": _expected_r36_parent(),
            "media_r26": authority["media_r26_authority"],
            "authority_digest": authority_digest(authority),
            "policy_digest": policy_digest(policy),
        },
        "boundary": {
            "offline_only": True,
            "network_call": False,
            "browser_call": False,
            "provider_call": False,
            "live_metrics": False,
            "human_ground_truth": False,
            "live_authorization": False,
            "provider_mutation": False,
            "creator_mutation": False,
            "social_publish": False,
            "credential_access": False,
        },
    }
    material = copy.deepcopy(normalization)
    material["normalization_digest"] = ""
    normalization["normalization_digest"] = sha256_json(material)
    return _clone(normalization)


def _decision_from_normalization(
    normalization: Mapping[str, Any],
    *,
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    media_state = authority["media_r26_authority"]["state"]
    if media_state != "ACCEPTED":
        return {
            "recommendation": WAITING,
            "selected_candidate_id": None,
            "selected_targeted_reedit_id": None,
            "reason_codes": ["MEDIA_R26_AUTHORITY_UNRESOLVED"],
            "scores": normalization["score_table"],
        }

    score_table = normalization["score_table"]
    candidates = normalization["candidates"]
    baseline = next(row for row in candidates if row["role"] == "BASELINE")
    target = normalization["targeted_reedit"]

    for row in candidates:
        quality = score_table[row["candidate_id"]]
        if quality["missing_review_evidence"]:
            return {
                "recommendation": HUMAN_REVIEW,
                "selected_candidate_id": None,
                "selected_targeted_reedit_id": None,
                "reason_codes": ["MISSING_REVIEW_EVIDENCE"],
                "scores": score_table,
            }
        if quality["conflicting_scores"]:
            return {
                "recommendation": HUMAN_REVIEW,
                "selected_candidate_id": None,
                "selected_targeted_reedit_id": None,
                "reason_codes": ["CONFLICTING_CANDIDATE_SCORES"],
                "scores": score_table,
            }
    target_quality = score_table[target["targeted_reedit_id"]]
    if target_quality["missing_review_evidence"]:
        return {
            "recommendation": HUMAN_REVIEW,
            "selected_candidate_id": None,
            "selected_targeted_reedit_id": None,
            "reason_codes": ["MISSING_TARGETED_REEDIT_REVIEW_EVIDENCE"],
            "scores": score_table,
        }
    if target_quality["conflicting_scores"]:
        return {
            "recommendation": HUMAN_REVIEW,
            "selected_candidate_id": None,
            "selected_targeted_reedit_id": None,
            "reason_codes": ["CONFLICTING_TARGETED_REEDIT_SCORES"],
            "scores": score_table,
        }

    scored = sorted(
        candidates,
        key=lambda row: (
            -float(score_table[row["candidate_id"]]["mean_score"]),
            row["candidate_id"],
        ),
    )
    top = scored[0]
    top_score = float(score_table[top["candidate_id"]]["mean_score"])
    second_score = float(score_table[scored[1]["candidate_id"]]["mean_score"])
    baseline_score = float(score_table[baseline["candidate_id"]]["mean_score"])

    tied_ids = [
        row["candidate_id"]
        for row in candidates
        if top_score - float(score_table[row["candidate_id"]]["mean_score"])
        <= policy["selection"]["tie_margin"]
    ]
    if len(tied_ids) > 1:
        if baseline["candidate_id"] in tied_ids:
            recommendation = KEEP_BASELINE
            selected_candidate = baseline["candidate_id"]
            reason_codes = ["TIE_WITH_BASELINE_CONSERVATIVE_KEEP"]
        else:
            return {
                "recommendation": HUMAN_REVIEW,
                "selected_candidate_id": None,
                "selected_targeted_reedit_id": None,
                "reason_codes": ["TOP_CANDIDATE_TIE_WITHOUT_BASELINE"],
                "scores": score_table,
            }
    elif top["candidate_id"] == baseline["candidate_id"]:
        recommendation = KEEP_BASELINE
        selected_candidate = baseline["candidate_id"]
        reason_codes = ["BASELINE_HIGHEST_SCORE"]
    elif top_score - baseline_score < policy["selection"]["select_margin_over_baseline"]:
        recommendation = KEEP_BASELINE
        selected_candidate = baseline["candidate_id"]
        reason_codes = ["CANDIDATE_MARGIN_BELOW_SELECTION_THRESHOLD"]
    else:
        if target["derived_from_candidate_id"] != top["candidate_id"]:
            raise EvidenceInvalid(
                "targeted re-edit not derived from selected source candidate"
            )
        target_score = float(target_quality["mean_score"])
        if target_score - top_score >= policy["selection"]["targeted_reedit_margin"]:
            recommendation = TARGETED_REEDIT
            selected_candidate = top["candidate_id"]
            reason_codes = ["TARGETED_REEDIT_CLEARS_MARGIN"]
        else:
            recommendation = SELECT_CANDIDATE
            selected_candidate = top["candidate_id"]
            reason_codes = ["CANDIDATE_CLEARS_BASELINE_MARGIN"]

    final = normalization["final_artifact"]
    if recommendation == KEEP_BASELINE:
        if not (
            final["lineage_kind"] == "CANDIDATE"
            and final["lineage_id"] == baseline["candidate_id"]
        ):
            raise EvidenceInvalid("final artifact lineage disagrees with KEEP_BASELINE")
        selected_reedit = None
    elif recommendation == SELECT_CANDIDATE:
        if not (
            final["lineage_kind"] == "CANDIDATE"
            and final["lineage_id"] == selected_candidate
        ):
            raise EvidenceInvalid("final artifact lineage disagrees with selected candidate")
        selected_reedit = None
    elif recommendation == TARGETED_REEDIT:
        if not (
            final["lineage_kind"] == "TARGETED_REEDIT"
            and final["lineage_id"] == target["targeted_reedit_id"]
        ):
            raise EvidenceInvalid("final artifact lineage disagrees with targeted re-edit")
        selected_reedit = target["targeted_reedit_id"]
    else:
        selected_reedit = None

    return {
        "recommendation": recommendation,
        "selected_candidate_id": selected_candidate,
        "selected_targeted_reedit_id": selected_reedit,
        "reason_codes": reason_codes,
        "scores": score_table,
        "diagnostic_top_score": top_score,
        "diagnostic_second_score": second_score,
        "diagnostic_baseline_score": baseline_score,
    }


def _base_advisory(
    *,
    recommendation: str,
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
    normalization: Mapping[str, Any] | None,
    decision_digest: str | None,
    reason_codes: Sequence[str],
) -> dict[str, Any]:
    return {
        "contract_version": ADVISORY_VERSION,
        "advisory_digest": "",
        "disposition": "ADVISORY_ONLY",
        "recommendation": recommendation,
        "reason_codes": sorted(set(reason_codes)),
        "decision_digest": decision_digest,
        "normalization_digest": (
            None if normalization is None else normalization["normalization_digest"]
        ),
        "media_r26_authority_state": authority["media_r26_authority"]["state"],
        "media_contract_id": MEDIA_CONTRACT_ID,
        "growth_r36_parent": _expected_r36_parent(),
        "authority_digest": authority_digest(authority),
        "policy_digest": policy_digest(policy),
        "input_video_sha256": (
            None if normalization is None else normalization["input_video"]["sha256"]
        ),
        "candidate_hashes": (
            {}
            if normalization is None
            else {
                row["candidate_id"]: row["render_sha256"]
                for row in normalization["candidates"]
            }
        ),
        "targeted_reedit_sha256": (
            None
            if normalization is None
            else normalization["targeted_reedit"]["artifact_sha256"]
        ),
        "final_artifact_sha256": (
            None
            if normalization is None
            else normalization["final_artifact"]["sha256"]
        ),
        "local_run_manifest_digest": (
            None
            if normalization is None
            else normalization["local_run_manifest_digest"]
        ),
        "runtime_identity_digest": (
            None if normalization is None else sha256_json(normalization["runtime"])
        ),
        "phase_timings_digest": (
            None
            if normalization is None
            else sha256_json(normalization["phase_timings"])
        ),
        "resume_events_digest": (
            None
            if normalization is None
            else sha256_json(normalization["resume_events"])
        ),
        "evidence_class_counts": (
            {} if normalization is None else normalization["evidence_class_counts"]
        ),
        "live_authorization": False,
        "provider_mutation_allowed": False,
        "creator_mutation_allowed": False,
        "browser_call_allowed": False,
        "provider_call_allowed": False,
        "social_publish_allowed": False,
        "credential_access_allowed": False,
        "human_ground_truth": False,
    }


def evaluate(
    *,
    bundle: Mapping[str, Any],
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    authority = validate_authority(authority)
    policy = validate_policy(policy)
    _git_sha(growth_sha, "growth_sha")
    _positive_int(growth_ci_run_id, "growth_ci_run_id")

    try:
        normalization = normalize_bundle(
            bundle,
            authority=authority,
            policy=policy,
        )
        decision_core = _decision_from_normalization(
            normalization,
            authority=authority,
            policy=policy,
        )
        recommendation = decision_core["recommendation"]
        decision = {
            "contract_version": DECISION_VERSION,
            "decision_digest": "",
            "recommendation": recommendation,
            "reason_codes": decision_core["reason_codes"],
            "selected_candidate_id": decision_core.get("selected_candidate_id"),
            "selected_targeted_reedit_id": decision_core.get(
                "selected_targeted_reedit_id"
            ),
            "scores": decision_core["scores"],
            "normalization_digest": normalization["normalization_digest"],
            "media_r26_authority_state": authority["media_r26_authority"]["state"],
            "growth_r37": {
                "producer_sha": growth_sha,
                "ci_run_id": growth_ci_run_id,
                "contract": CONTRACT_VERSION,
            },
            "growth_r36_parent": _expected_r36_parent(),
            "authority_digest": authority_digest(authority),
            "policy_digest": policy_digest(policy),
            "boundary": {
                "offline_only": True,
                "live_authorization": False,
                "provider_mutation": False,
                "creator_mutation": False,
                "browser_call": False,
                "provider_call": False,
                "social_publish": False,
                "credential_access": False,
                "human_ground_truth": False,
            },
        }
        material = copy.deepcopy(decision)
        material["decision_digest"] = ""
        decision["decision_digest"] = sha256_json(material)
        advisory = _base_advisory(
            recommendation=recommendation,
            authority=authority,
            policy=policy,
            normalization=normalization,
            decision_digest=decision["decision_digest"],
            reason_codes=decision["reason_codes"],
        )
        advisory_material = copy.deepcopy(advisory)
        advisory_material["advisory_digest"] = ""
        advisory["advisory_digest"] = sha256_json(advisory_material)
        result = {
            "contract_version": CONTRACT_VERSION,
            "status": recommendation,
            "disposition": "ADVISORY_ONLY",
            "normalization": normalization,
            "decision": decision,
            "creator_advisory": advisory,
            "result_digest": "",
        }
    except (EvidenceInvalid, AuthorityDrift, PolicyDrift) as exc:
        recommendation = EVIDENCE_INVALID
        reason_codes = [type(exc).__name__, str(exc)]
        advisory = _base_advisory(
            recommendation=recommendation,
            authority=authority,
            policy=policy,
            normalization=None,
            decision_digest=None,
            reason_codes=reason_codes,
        )
        advisory_material = copy.deepcopy(advisory)
        advisory_material["advisory_digest"] = ""
        advisory["advisory_digest"] = sha256_json(advisory_material)
        result = {
            "contract_version": CONTRACT_VERSION,
            "status": recommendation,
            "disposition": "ADVISORY_ONLY",
            "normalization": None,
            "decision": None,
            "creator_advisory": advisory,
            "invalid_evidence": {
                "error_type": type(exc).__name__,
                "detail": str(exc),
            },
            "result_digest": "",
        }
    material = copy.deepcopy(result)
    material["result_digest"] = ""
    result["result_digest"] = sha256_json(material)
    return _clone(result)


def write_outputs(result: Mapping[str, Any], out_dir: Path) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write(out / "result.json", result)
    _write(out / "creator-advisory.json", result["creator_advisory"])
    if result.get("normalization") is not None:
        _write(out / "normalization.json", result["normalization"])
    if result.get("decision") is not None:
        _write(out / "decision.json", result["decision"])
    report = {
        "report_version": REPORT_VERSION,
        "status": result["status"],
        "disposition": result["disposition"],
        "result_digest": result["result_digest"],
        "normalization_digest": (
            None
            if result.get("normalization") is None
            else result["normalization"]["normalization_digest"]
        ),
        "decision_digest": (
            None
            if result.get("decision") is None
            else result["decision"]["decision_digest"]
        ),
        "advisory_digest": result["creator_advisory"]["advisory_digest"],
        "media_r26_authority_state": result["creator_advisory"][
            "media_r26_authority_state"
        ],
        "live_authorization": False,
        "provider_mutation_allowed": False,
        "creator_mutation_allowed": False,
        "browser_call_allowed": False,
        "provider_call_allowed": False,
        "social_publish_allowed": False,
        "credential_access_allowed": False,
        "human_ground_truth": False,
        "report_digest": "",
    }
    material = copy.deepcopy(report)
    material["report_digest"] = ""
    report["report_digest"] = sha256_json(material)
    _write(out / "readiness-report.json", report)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="growth-r37-local-pc-selection")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run")
    run.add_argument("--bundle", required=True)
    run.add_argument("--authority", required=True)
    run.add_argument("--policy", required=True)
    run.add_argument("--out-dir", required=True)
    run.add_argument("--growth-sha", required=True)
    run.add_argument("--growth-ci-run-id", type=int, required=True)

    rehearse = sub.add_parser("rehearse-fixtures")
    rehearse.add_argument("--authority", required=True)
    rehearse.add_argument("--policy", required=True)
    rehearse.add_argument("--out-dir", required=True)
    rehearse.add_argument("--growth-sha", required=True)
    rehearse.add_argument("--growth-ci-run-id", type=int, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    authority = _load(Path(args.authority))
    policy = _load(Path(args.policy))
    out = Path(args.out_dir)
    if args.command == "rehearse-fixtures":
        from .local_pc_selection_adapter_r37_sim import build_rehearsal

        report = build_rehearsal(
            authority=authority,
            policy=policy,
            growth_sha=args.growth_sha,
            growth_ci_run_id=args.growth_ci_run_id,
        )
        out.mkdir(parents=True, exist_ok=True)
        _write(out / "adversarial-report.json", report)
        _write(out / "source-ready-bundle.json", report["source_ready_bundle"])
        _write(out / "source-ready-result.json", report["source_ready_result"])
        _write(
            out / "source-ready-creator-advisory.json",
            report["source_ready_result"]["creator_advisory"],
        )
        print(json.dumps({
            "scenario_count": report["scenario_count"],
            "all_expected_dispositions_stable": report[
                "all_expected_dispositions_stable"
            ],
            "source_ready_status": report["source_ready_result"]["status"],
            "report_digest": report["report_digest"],
        }, sort_keys=True))
        return 0

    result = evaluate(
        bundle=_load(Path(args.bundle)),
        authority=authority,
        policy=policy,
        growth_sha=args.growth_sha,
        growth_ci_run_id=args.growth_ci_run_id,
    )
    write_outputs(result, out)
    print(json.dumps({
        "status": result["status"],
        "result_digest": result["result_digest"],
        "advisory_digest": result["creator_advisory"]["advisory_digest"],
        "media_r26_authority_state": result["creator_advisory"][
            "media_r26_authority_state"
        ],
    }, sort_keys=True))
    if result["status"] == WAITING:
        return 3
    if result["status"] == EVIDENCE_INVALID:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
