from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import canonical_json, sha256_json
from .web_video_critic import (
    WEB_VIDEO_ATTACHED_MODE,
    WEB_VIDEO_FIXTURE_MODE,
    parse_web_video_critic_input,
    parse_web_video_critic_output,
    parse_web_video_pairwise_input,
    parse_web_video_pairwise_output,
)

CRITIC_REEDIT_ADAPTER_VERSION = "growth.web_video_critic_reedit_adapter.v1"
CREATOR_REEDIT_HANDOFF_VERSION = "growth.creator_reedit_handoff.v1"
CRITIC_REEDIT_LEDGER_VERSION = "growth.web_video_critic_reedit_ledger.v1"
MAX_REEDIT_ROUNDS = 2

GROWTH_R22_SHA = "0f6824d7c3962ccee572b21a4e1a343e6470c1a9"

BRIDGE_R26_REPOSITORY = "foto6/WebAIBridge"
BRIDGE_R26_BRANCH = "agent/bridge-r26-file-attachment-rehearsal-20261002"
BRIDGE_R26_SHA = "73c13f9eed2a2cbcea881dd8c5452d054bfef940"
BRIDGE_R26_ATTACHMENT_CONTRACT = "bridge.chat_file_attachment.v1"
BRIDGE_R26_REHEARSAL_REQUEST_CONTRACT = "bridge.chat_file_attachment_rehearsal_request.v1"
BRIDGE_R26_REHEARSAL_RESULT_CONTRACT = "bridge.chat_file_attachment_rehearsal_result.v1"
BRIDGE_R26_DOM_PROBE_CONTRACT = "bridge.chat_file_attachment_dom_probe.v1"
BRIDGE_R26_OPERATOR_EVIDENCE_CONTRACT = "bridge.chat_file_attachment_operator_evidence.v1"
BRIDGE_R26_MAX_FILE_BYTES = 500_000_000
BRIDGE_R26_DISPOSITION = "READY_FOR_EXPLICIT_LIVE_REHEARSAL"
BRIDGE_R26_LIVE_PASS = False

MEDIA_R18_REPOSITORY = "foto6/video2"
MEDIA_R18_BRANCH = "agent/media-r18-direct-model-review-package-20261002"
MEDIA_R18_SHA = "2c41f084e000eca5efd9a51d2d3752bec1bd1311"
MEDIA_R18_CI_RUN_ID = 36967381891
MEDIA_R18_CONTRACT = "media.direct_model_review_package.v1"

DECISION_STATES = (
    "winner",
    "targeted_reedit",
    "tie",
    "insufficient_evidence",
    "human_review",
)

SUPPORTED_EDIT_OPERATIONS = (
    "trim",
    "cut",
    "crop_scale_reframe",
    "speed_change",
    "fade_transition",
    "text_overlay",
    "subtitles_captions",
    "audio_duck_mix",
    "intro_outro_cta",
)

DEFECT_TO_OPERATION = {
    "hook_clarity": "text_overlay",
    "pacing_coherence": "trim",
    "semantic_cut_correctness": "cut",
    "subject_framing_crop_quality": "crop_scale_reframe",
    "broll_relevance": "cut",
    "caption_readability_emphasis_relevance": "subtitles_captions",
    "visual_continuity": "fade_transition",
    "motion_zoom_appropriateness": "crop_scale_reframe",
    "audio_voice_music_balance": "audio_duck_mix",
    "payoff_cta_loop_coherence": "intro_outro_cta",
    "awkward_dead_moment": "trim",
}

BINDING_KEYS = (
    "source_id",
    "source_sha256",
    "source_size",
    "media_repository",
    "media_producer_sha",
    "candidate_id",
    "render_sha256",
    "render_size",
    "render_export_sha256",
    "attachment_sha256",
    "attachment_size",
    "attachment_identity",
    "review_bundle_digest",
    "critic_input_digest",
    "critic_output_digest",
)


class CriticReeditAdapterError(ValueError):
    pass


class CriticReeditLineageError(CriticReeditAdapterError):
    pass


class CriticReeditBoundaryError(CriticReeditAdapterError):
    pass


class CriticReeditUnsupportedEdit(CriticReeditAdapterError):
    pass


class CriticReeditReplayError(CriticReeditAdapterError):
    pass


def bridge_r26_authority() -> dict[str, Any]:
    return {
        "repository": BRIDGE_R26_REPOSITORY,
        "branch": BRIDGE_R26_BRANCH,
        "source_sha": BRIDGE_R26_SHA,
        "attachment_contract": BRIDGE_R26_ATTACHMENT_CONTRACT,
        "rehearsal_request_contract": BRIDGE_R26_REHEARSAL_REQUEST_CONTRACT,
        "rehearsal_result_contract": BRIDGE_R26_REHEARSAL_RESULT_CONTRACT,
        "dom_probe_contract": BRIDGE_R26_DOM_PROBE_CONTRACT,
        "operator_evidence_contract": BRIDGE_R26_OPERATOR_EVIDENCE_CONTRACT,
        "max_file_bytes": BRIDGE_R26_MAX_FILE_BYTES,
        "disposition": BRIDGE_R26_DISPOSITION,
        "live_pass": BRIDGE_R26_LIVE_PASS,
        "real_upload_proven": False,
        "real_prompt_send_proven": False,
        "no_live_deploy": True,
        "no_cutover": True,
    }


def media_r18_authority() -> dict[str, Any]:
    return {
        "repository": MEDIA_R18_REPOSITORY,
        "branch": MEDIA_R18_BRANCH,
        "source_sha": MEDIA_R18_SHA,
        "ci_run_id": MEDIA_R18_CI_RUN_ID,
        "contract": MEDIA_R18_CONTRACT,
        "max_file_bytes": BRIDGE_R26_MAX_FILE_BYTES,
        "live_upload_performed": False,
        "model_judgment_performed": False,
    }


def _exact_binding(
    critic_input: Mapping[str, Any],
    critic_output: Mapping[str, Any],
    *,
    verified_transport_evidence_digest: str | None = None,
) -> dict[str, Any]:
    i = parse_web_video_critic_input(critic_input)
    o = parse_web_video_critic_output(
        critic_output,
        critic_input=i,
        verified_transport_evidence_digest=verified_transport_evidence_digest,
    )
    binding = {
        "source_id": i["source"]["source_id"],
        "source_sha256": i["source"]["sha256"],
        "source_size": i["source"]["size"],
        "media_repository": i["media"]["repository"],
        "media_producer_sha": i["media"]["producer_sha"],
        "candidate_id": i["candidate"]["candidate_id"],
        "render_sha256": i["candidate"]["render_sha256"],
        "render_size": i["candidate"]["render_size"],
        "render_export_sha256": i["candidate"]["render_export_sha256"],
        "attachment_sha256": i["attachment"]["sha256"],
        "attachment_size": i["attachment"]["size"],
        "attachment_identity": i["attachment"]["attachment_identity"],
        "review_bundle_digest": i["review_bundle"]["digest"],
        "critic_input_digest": i["input_digest"],
        "critic_output_digest": o["output_digest"],
    }
    if (
        binding["render_sha256"] != binding["attachment_sha256"]
        or binding["render_size"] != binding["attachment_size"]
    ):
        raise CriticReeditLineageError("render/attachment identity mismatch")
    if binding["attachment_size"] > BRIDGE_R26_MAX_FILE_BYTES:
        raise CriticReeditBoundaryError("attachment exceeds Bridge R26 file cap")
    return binding


def expected_binding_from(
    critic_input: Mapping[str, Any],
    critic_output: Mapping[str, Any],
    *,
    verified_transport_evidence_digest: str | None = None,
) -> dict[str, Any]:
    return _exact_binding(
        critic_input,
        critic_output,
        verified_transport_evidence_digest=verified_transport_evidence_digest,
    )


def _validate_expected_binding(
    actual: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> None:
    if not isinstance(expected, Mapping) or set(expected) != set(BINDING_KEYS):
        raise CriticReeditLineageError("expected binding must contain exact R23 binding fields")
    if any(actual[k] != expected[k] for k in BINDING_KEYS):
        raise CriticReeditLineageError("stale hash or provenance drift")


def _covered(start_ms: int, end_ms: int, ranges: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        isinstance(r, Mapping)
        and isinstance(r.get("start_ms"), int)
        and isinstance(r.get("end_ms"), int)
        and r["start_ms"] <= start_ms
        and end_ms <= r["end_ms"]
        for r in ranges
    )


def _validate_timestamp_evidence(
    observation: Mapping[str, Any],
    *,
    coverage: Mapping[str, Any],
    execution_mode: str,
) -> None:
    if observation["scope"] != "local":
        return
    start = observation["start_ms"]
    end = observation["end_ms"]
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start < 0
        or end <= start
    ):
        raise CriticReeditBoundaryError("contradictory defect timestamps")
    if execution_mode == WEB_VIDEO_ATTACHED_MODE and not _covered(
        start, end, coverage["inspected_ranges"]
    ):
        raise CriticReeditBoundaryError(
            "timestamped defect is outside reported inspected coverage"
        )


def _directive(
    observation: Mapping[str, Any],
    *,
    binding: Mapping[str, Any],
    coverage: Mapping[str, Any],
    execution_mode: str,
) -> dict[str, Any] | None:
    if observation["scope"] != "local" or observation["severity"] == "info":
        return None
    _validate_timestamp_evidence(
        observation, coverage=coverage, execution_mode=execution_mode
    )
    operation = DEFECT_TO_OPERATION.get(observation["defect_category"])
    if operation not in SUPPORTED_EDIT_OPERATIONS:
        raise CriticReeditUnsupportedEdit(
            f"unsupported edit mapping for {observation['defect_category']}"
        )
    body = {
        "operation": operation,
        "start_ms": observation["start_ms"],
        "end_ms": observation["end_ms"],
        "defect_category": observation["defect_category"],
        "severity": observation["severity"],
        "source_observation_id": observation["observation_id"],
        "evidence": observation["evidence"],
        "confidence": observation["confidence"],
        "uncertainty": observation["uncertainty"],
        "upstream_proposed_edit": observation["proposed_edit"],
        "upstream_proposed_edit_executable": False,
        "binding": dict(binding),
    }
    body["directive_id"] = "gcrd1:" + sha256_json(body)
    return body


def _state_for(
    *,
    execution_mode: str,
    assessment: str,
    directive_count: int,
    reedit_round: int,
    current_candidate_id: str,
    pairwise_selection: str | None = None,
    pairwise_mapped_candidate_id: str | None = None,
) -> str:
    if execution_mode != WEB_VIDEO_ATTACHED_MODE:
        return "insufficient_evidence"
    if pairwise_selection == "tie":
        return "tie"
    if pairwise_selection == "insufficient_evidence":
        return "insufficient_evidence"
    if pairwise_selection in {"A", "B"}:
        if pairwise_mapped_candidate_id == current_candidate_id:
            return "winner"
        if directive_count and reedit_round < MAX_REEDIT_ROUNDS:
            return "targeted_reedit"
        return "human_review"
    if assessment == "insufficient_evidence":
        return "insufficient_evidence"
    if directive_count:
        return "targeted_reedit" if reedit_round < MAX_REEDIT_ROUNDS else "human_review"
    if assessment == "no_material_defect_observed":
        return "winner"
    return "human_review"


def _pairwise_result(
    pairwise_input: Mapping[str, Any] | None,
    pairwise_output: Mapping[str, Any] | None,
) -> tuple[str | None, str | None, str | None]:
    if pairwise_input is None and pairwise_output is None:
        return None, None, None
    if pairwise_input is None or pairwise_output is None:
        raise CriticReeditLineageError("pairwise input/output must be supplied together")
    p = parse_web_video_pairwise_input(pairwise_input)
    o = parse_web_video_pairwise_output(pairwise_output, pairwise_input=p)
    return o["selection"], o["mapped_candidate_id"], o["output_digest"]


def build_creator_reedit_handoff(
    *,
    critic_input: Mapping[str, Any],
    critic_output: Mapping[str, Any],
    expected_binding: Mapping[str, Any],
    reedit_round: int,
    pairwise_input: Mapping[str, Any] | None = None,
    pairwise_output: Mapping[str, Any] | None = None,
    verified_transport_evidence_digest: str | None = None,
) -> dict[str, Any]:
    if isinstance(reedit_round, bool) or not isinstance(reedit_round, int):
        raise CriticReeditBoundaryError("reedit_round must be integer")
    if reedit_round < 0 or reedit_round > MAX_REEDIT_ROUNDS:
        raise CriticReeditBoundaryError("maximum two re-edit rounds")
    i = parse_web_video_critic_input(critic_input)
    o = parse_web_video_critic_output(
        critic_output,
        critic_input=i,
        verified_transport_evidence_digest=verified_transport_evidence_digest,
    )
    binding = _exact_binding(
        i,
        o,
        verified_transport_evidence_digest=verified_transport_evidence_digest,
    )
    _validate_expected_binding(binding, expected_binding)

    provenance = o["review_provenance"]
    mode = provenance["execution_mode"]
    coverage = o["coverage"]
    all_directives = []
    for obs in o["observations"]:
        directive = _directive(
            obs, binding=binding, coverage=coverage, execution_mode=mode
        )
        if directive is not None:
            all_directives.append(directive)

    selection, mapped_candidate, pairwise_digest = _pairwise_result(
        pairwise_input, pairwise_output
    )
    state = _state_for(
        execution_mode=mode,
        assessment=o["whole_video_summary"]["assessment"],
        directive_count=len(all_directives),
        reedit_round=reedit_round,
        current_candidate_id=binding["candidate_id"],
        pairwise_selection=selection,
        pairwise_mapped_candidate_id=mapped_candidate,
    )
    directives = all_directives if state == "targeted_reedit" else []
    handoff = {
        "contract_version": CREATOR_REEDIT_HANDOFF_VERSION,
        "adapter_version": CRITIC_REEDIT_ADAPTER_VERSION,
        "handoff_id": "",
        "handoff_digest": "",
        "state": state,
        "reedit_round": reedit_round,
        "max_reedit_rounds": MAX_REEDIT_ROUNDS,
        "binding": binding,
        "pairwise": {
            "selection": selection,
            "mapped_candidate_id": mapped_candidate,
            "output_digest": pairwise_digest,
        },
        "coverage": {
            "method": coverage["method"],
            "inspected_ranges": coverage["inspected_ranges"],
            "uninspected_possible": coverage["uninspected_possible"],
            "every_frame_inspected": coverage["every_frame_inspected"],
            "notes": coverage["notes"],
            "coverage_uncertainty_preserved": True,
        },
        "summary_uncertainty": o["whole_video_summary"]["uncertainty"],
        "directives": directives,
        "bridge_r26_authority": bridge_r26_authority(),
        "media_r18_authority": media_r18_authority(),
        "evidence_boundary": {
            "model_review_only": True,
            "human_ground_truth": False,
            "human_label": False,
            "live_platform_evidence": False,
            "live_video_review_fabricated": False,
        },
        "authority": {
            "advisory_only": True,
            "creator_mutation": False,
            "media_mutation": False,
            "provider_mutation": False,
            "upload_performed": False,
            "publish_authorized": False,
            "release_authorized": False,
        },
    }
    handoff["handoff_id"] = "gcrh1:" + sha256_json({
        "critic_output_digest": binding["critic_output_digest"],
        "pairwise_output_digest": pairwise_digest,
        "reedit_round": reedit_round,
    })
    material = dict(handoff)
    material["handoff_digest"] = ""
    handoff["handoff_digest"] = sha256_json(material)
    return parse_creator_reedit_handoff(handoff)


def parse_creator_reedit_handoff(payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "contract_version", "adapter_version", "handoff_id", "handoff_digest",
        "state", "reedit_round", "max_reedit_rounds", "binding", "pairwise",
        "coverage", "summary_uncertainty", "directives",
        "bridge_r26_authority", "media_r18_authority",
        "evidence_boundary", "authority",
    }
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise CriticReeditAdapterError("creator re-edit handoff fields invalid")
    if (
        payload["contract_version"] != CREATOR_REEDIT_HANDOFF_VERSION
        or payload["adapter_version"] != CRITIC_REEDIT_ADAPTER_VERSION
    ):
        raise CriticReeditAdapterError("unsupported creator re-edit handoff")
    if payload["state"] not in DECISION_STATES:
        raise CriticReeditAdapterError("invalid re-edit decision state")
    round_no = payload["reedit_round"]
    if (
        isinstance(round_no, bool)
        or not isinstance(round_no, int)
        or round_no < 0
        or round_no > MAX_REEDIT_ROUNDS
        or payload["max_reedit_rounds"] != MAX_REEDIT_ROUNDS
    ):
        raise CriticReeditBoundaryError("maximum two re-edit rounds")

    binding = payload["binding"]
    if not isinstance(binding, Mapping) or set(binding) != set(BINDING_KEYS):
        raise CriticReeditLineageError("handoff binding fields invalid")
    if (
        binding["render_sha256"] != binding["attachment_sha256"]
        or binding["render_size"] != binding["attachment_size"]
        or binding["attachment_size"] > BRIDGE_R26_MAX_FILE_BYTES
    ):
        raise CriticReeditLineageError("handoff render/attachment binding invalid")

    if payload["bridge_r26_authority"] != bridge_r26_authority():
        raise CriticReeditLineageError("Bridge R26 provenance drift")
    if payload["media_r18_authority"] != media_r18_authority():
        raise CriticReeditLineageError("Media R18 provenance drift")
    coverage = payload["coverage"]
    if (
        not isinstance(coverage, Mapping)
        or coverage.get("uninspected_possible") is not True
        or coverage.get("every_frame_inspected") is not False
        or coverage.get("coverage_uncertainty_preserved") is not True
        or not isinstance(coverage.get("inspected_ranges"), list)
        or not isinstance(coverage.get("notes"), str)
        or not coverage["notes"].strip()
    ):
        raise CriticReeditBoundaryError("coverage uncertainty was not preserved")

    directives = payload["directives"]
    if not isinstance(directives, list):
        raise CriticReeditAdapterError("directives must be array")
    if payload["state"] != "targeted_reedit" and directives:
        raise CriticReeditBoundaryError("only targeted_reedit may emit executable directives")
    if payload["state"] == "targeted_reedit" and not directives:
        raise CriticReeditBoundaryError("targeted_reedit requires bounded directives")
    seen = set()
    for row in directives:
        if not isinstance(row, Mapping):
            raise CriticReeditAdapterError("directive must be object")
        if row.get("operation") not in SUPPORTED_EDIT_OPERATIONS:
            raise CriticReeditUnsupportedEdit("unsupported edit operation")
        if row.get("upstream_proposed_edit_executable") is not False:
            raise CriticReeditBoundaryError("freeform upstream edit cannot become executable")
        if row.get("binding") != binding:
            raise CriticReeditLineageError("directive provenance drift")
        if row.get("directive_id") in seen:
            raise CriticReeditReplayError("duplicate directive")
        seen.add(row.get("directive_id"))
        if (
            not isinstance(row.get("start_ms"), int)
            or not isinstance(row.get("end_ms"), int)
            or row["end_ms"] <= row["start_ms"]
        ):
            raise CriticReeditBoundaryError("contradictory directive timestamps")

    if payload["evidence_boundary"] != {
        "model_review_only": True,
        "human_ground_truth": False,
        "human_label": False,
        "live_platform_evidence": False,
        "live_video_review_fabricated": False,
    }:
        raise CriticReeditBoundaryError("model review cannot become human/live evidence")
    if payload["authority"] != {
        "advisory_only": True,
        "creator_mutation": False,
        "media_mutation": False,
        "provider_mutation": False,
        "upload_performed": False,
        "publish_authorized": False,
        "release_authorized": False,
    }:
        raise CriticReeditBoundaryError("re-edit handoff authority invalid")
    pairwise = payload["pairwise"]
    if not isinstance(pairwise, Mapping) or set(pairwise) != {
        "selection", "mapped_candidate_id", "output_digest"
    }:
        raise CriticReeditLineageError("pairwise binding invalid")
    expected_id = "gcrh1:" + sha256_json({
        "critic_output_digest": binding["critic_output_digest"],
        "pairwise_output_digest": pairwise["output_digest"],
        "reedit_round": round_no,
    })
    if payload["handoff_id"] != expected_id:
        raise CriticReeditLineageError("handoff identity mismatch")
    material = dict(payload)
    material["handoff_digest"] = ""
    if payload["handoff_digest"] != sha256_json(material):
        raise CriticReeditLineageError("handoff digest mismatch")
    return json.loads(canonical_json(dict(payload)))


class CriticReeditReplayLedger:
    def __init__(self) -> None:
        self._accepted: dict[str, str] = {}

    def accept(self, payload: Mapping[str, Any]) -> str:
        parsed = parse_creator_reedit_handoff(payload)
        key = parsed["handoff_id"]
        digest = parsed["handoff_digest"]
        if key in self._accepted:
            if self._accepted[key] == digest:
                raise CriticReeditReplayError("duplicate replay rejected")
            raise CriticReeditLineageError("handoff id reused with different provenance")
        self._accepted[key] = digest
        return digest


def readiness_report(*, growth_sha: str, run_id: str) -> dict[str, Any]:
    return {
        "report_version": "growth.critic_reedit_adapter_r23.readiness.v1",
        "growth_repository": "foto6/video3",
        "growth_source_sha": growth_sha,
        "workflow_run_id": run_id,
        "adapter_contract": CRITIC_REEDIT_ADAPTER_VERSION,
        "creator_handoff_contract": CREATOR_REEDIT_HANDOFF_VERSION,
        "starting_r22_sha": GROWTH_R22_SHA,
        "bridge_r26_authority": bridge_r26_authority(),
        "media_r18_authority": media_r18_authority(),
        "decision_states": list(DECISION_STATES),
        "supported_edit_operations": list(SUPPORTED_EDIT_OPERATIONS),
        "max_reedit_rounds": MAX_REEDIT_ROUNDS,
        "invariants": {
            "exact_source_render_attachment_binding": True,
            "timestamped_defect_evidence_required": True,
            "coverage_uncertainty_preserved": True,
            "unsupported_edit_rejected": True,
            "duplicate_replay_rejected": True,
            "provenance_drift_rejected": True,
            "human_ground_truth": False,
            "live_review_fabrication_forbidden": True,
        },
        "live_provider_mutation": False,
        "live_upload_performed": False,
        "live_video_review_claimed": False,
        "release_gate": "NO_LIVE_PROVIDER_MUTATION",
    }


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--growth-sha", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    report = readiness_report(growth_sha=args.growth_sha, run_id=args.run_id)
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
