from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import canonical_json, sha256_json

EXTERNAL_CONTRACT_VERSION = "growth.consensus_review.r30.v1"
INTERNAL_ORACLE_CONTRACT_VERSION = "growth.consensus_review_oracle.r30.v1"
AUTHORITY_VERSION = "growth.consensus_authorities.r30.v1"
POLICY_VERSION = "growth.consensus_aggregation_policy.r30.v1"
REVIEW_VERSION = "growth.verified_r29_r34_model_review.r30.v1"
VOTE_VERSION = "growth.model_review_vote.r30.v1"
CONSENSUS_VERSION = "growth.multi_review_consensus.r30.v1"
CREATOR_HANDOFF_VERSION = "growth.consensus_creator_handoff.r30.v1"
LEDGER_VERSION = "growth.consensus_ledger.r30.v1"
REPORT_VERSION = "growth.consensus_review_oracle.r30.readiness.v1"

R29_SHA = "3e4a8ad6d73b058c953abeadba7a60abe567adbc"
R29_CI = 37195373334
R29_ARTIFACT_ID = 11301225456
R29_ARTIFACT_DIGEST = (
    "sha256:40fb5e5c7ab7ccf7a02514f9b5a40eb8fc3207373c63391a17606fb12a6d0e55"
)
MEDIA_R24_SHA = "244acdf154741e669991b17df3ef2a47e2dfdfa9"
MEDIA_R24_CI = 37195239582
MEDIA_R24_ARTIFACT_ID = 11301055747
MEDIA_R24_ARTIFACT_DIGEST = (
    "sha256:fc5c9b9635d49b643e66efafe602d21ce1ef695a553f81a797bdf16d7b8cf228"
)
BRIDGE_R34_SHA = "4e2a37545cc0cdd940cf6e86d40e0d620d6c94ae"
BRIDGE_R34_CI = 37196102639

R24_EXPORT_FILE = "media.canonical_live_review_export.r24.v1.json"
R24_MANIFEST_FILE = "media.canonical_live_review_package_manifest.r24.v1.json"
R24_AUTHORITY_FILE = "media.canonical_live_review_authority.r24.v1.json"
R24_HANDOFF_FILE = "media.bridge_live_review_handoff.r24.v1.json"
R24_MAPPING_FILE = "machine/r21/media.review_round_sealed_mapping.r21.v1.json"
R24_PROMPT_FILE = "model-facing-prompt.txt"

INNER_ENVELOPE_VERSION = "growth.dynamic_creator_external_review_envelope.r26.v1"
R29_ROUND_RESULT_VERSION = "growth.exact_session_round_result.r29.v1"
BRIDGE_CAPTURE_CONTRACT = "bridge.existing_chat_video_review_capture.v1"
BRIDGE_RESULT_CONTRACT = "bridge.existing_chat_video_review_result.v1"

WINNERS = {"A", "B", "tie", "insufficient_evidence"}
DIRECTIONS = {"increase", "decrease", "keep", "remove", "replace"}
SEVERITIES = {"low", "medium", "high", "critical"}


class R30Error(ValueError):
    pass


class AuthorityDrift(R30Error):
    pass


class PackageDrift(R30Error):
    pass


class ReviewConflict(R30Error):
    pass


class IndependenceError(R30Error):
    pass


class MalformedReview(R30Error):
    pass


def _clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _file_size(path: Path) -> int:
    return Path(path).stat().st_size


def _sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise R30Error(f"{field} must be lowercase SHA-256")
    return value


def _git_sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise R30Error(f"{field} must be exact Git SHA")
    return value


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise R30Error(f"{field} must be non-empty string")
    return value


def _positive(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise R30Error(f"{field} must be positive integer")
    return value


def _confidence(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MalformedReview(f"{field} must be number 0..1")
    value = float(value)
    if value < 0 or value > 1:
        raise MalformedReview(f"{field} must be number 0..1")
    return value


def _artifact_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise AuthorityDrift(f"{field} must be sha256:<digest>")
    _sha(value[7:], field)
    return value


def _safe_child(root: Path, relative: str, field: str) -> Path:
    rel = _nonempty(relative, field)
    candidate = (Path(root) / rel).resolve()
    try:
        candidate.relative_to(Path(root).resolve())
    except ValueError as exc:
        raise PackageDrift(f"{field} escapes package root") from exc
    return candidate


def default_authority_profile() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.consensus_review_oracle.r30.v1"
        / "authority-profiles.json"
    )


def default_policy() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.consensus_review_oracle.r30.v1"
        / "aggregation-policy.json"
    )


def default_external_contract() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.consensus_review.r30.v1"
        / "contract.json"
    )


def validate_external_contract(
    value: Mapping[str, Any],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "contract_version",
        "purpose",
        "internal_oracle",
        "accepted_authorities",
        "independence_rules",
        "disagreement_and_human_review_gates",
        "evidence_boundary",
    }:
        raise AuthorityDrift("R30 external umbrella fields invalid")
    if value["contract_version"] != EXTERNAL_CONTRACT_VERSION:
        raise AuthorityDrift("R30 external umbrella contract mismatch")

    root = (
        Path(__file__).resolve().parents[1]
        if root is None
        else Path(root).resolve()
    )
    internal = value["internal_oracle"]
    expected_internal_keys = {
        "contract_version",
        "contract_file",
        "contract_file_sha256",
        "aggregation_policy",
        "verified_review_schema",
        "consensus_schema",
        "authority_profile",
        "creator_handoff_contract",
        "canonical_inner_creator_envelope",
    }
    if not isinstance(internal, Mapping) or set(internal) != expected_internal_keys:
        raise AuthorityDrift("R30 external internal-oracle mapping invalid")
    if (
        internal["contract_version"] != INTERNAL_ORACLE_CONTRACT_VERSION
        or internal["creator_handoff_contract"] != CREATOR_HANDOFF_VERSION
        or internal["canonical_inner_creator_envelope"] != INNER_ENVELOPE_VERSION
    ):
        raise AuthorityDrift("R30 external internal contract mapping drift")

    bindings = {
        "contract": (
            internal["contract_file"],
            internal["contract_file_sha256"],
            "eee6baccb9466b89d77cfbd50ff3a8861d70a372f08d1314e6da90c678a1d3f4",
        ),
        "policy": (
            internal["aggregation_policy"]["file"],
            internal["aggregation_policy"]["file_sha256"],
            "8915db1d3415f8f2aff717004d738370ad11aad7b8392ca054f853099fb915b3",
        ),
        "review_schema": (
            internal["verified_review_schema"]["file"],
            internal["verified_review_schema"]["file_sha256"],
            "fac193e0627aae6c1acef4b1af431d82d9c55617a1ad4a99c7c38ee3d091ac47",
        ),
        "consensus_schema": (
            internal["consensus_schema"]["file"],
            internal["consensus_schema"]["file_sha256"],
            "b59d5d2d58b739b79146b5b11701118de8a8cd8faa4d05d6944bd60a6a143630",
        ),
        "authority_profile": (
            internal["authority_profile"]["file"],
            internal["authority_profile"]["file_sha256"],
            "95b278cbdc7efe63330c852294b17f2f28003f50c747610ea7153b07fee02267",
        ),
    }
    loaded: dict[str, Any] = {}
    for name, (relative, pinned, expected) in bindings.items():
        if pinned != expected:
            raise AuthorityDrift(f"R30 external {name} pinned hash drift")
        path = _safe_child(root, relative, f"external.{name}.file")
        if not path.is_file() or _file_sha(path) != expected:
            raise AuthorityDrift(f"R30 external {name} file hash mismatch")
        loaded[name] = _load(path)

    if (
        loaded["contract"].get("contract_version")
        != INTERNAL_ORACLE_CONTRACT_VERSION
        or loaded["contract"].get("policy_contract") != POLICY_VERSION
        or loaded["contract"].get("verified_review_contract") != REVIEW_VERSION
        or loaded["contract"].get("consensus_contract") != CONSENSUS_VERSION
        or loaded["contract"].get("creator_handoff_contract")
        != CREATOR_HANDOFF_VERSION
    ):
        raise AuthorityDrift("R30 external -> internal oracle contract mapping drift")
    if (
        internal["aggregation_policy"].get("contract_version") != POLICY_VERSION
        or internal["aggregation_policy"].get("semantic_digest")
        != "f77bc62011f5963d223d4de7ea0e5d56ece2495ae8267d376491e13421d44213"
        or policy_digest(loaded["policy"])
        != internal["aggregation_policy"]["semantic_digest"]
    ):
        raise AuthorityDrift("R30 external aggregation policy mapping drift")
    if (
        internal["verified_review_schema"].get("schema_id") != REVIEW_VERSION
        or loaded["review_schema"].get("$id") != REVIEW_VERSION
        or internal["consensus_schema"].get("schema_id") != CONSENSUS_VERSION
        or loaded["consensus_schema"].get("$id") != CONSENSUS_VERSION
    ):
        raise AuthorityDrift("R30 external schema ID mapping drift")
    if (
        internal["authority_profile"].get("contract_version") != AUTHORITY_VERSION
        or internal["authority_profile"].get("semantic_digest")
        != "8ceb1fb2fd8b2a3317fe110068e60ff4ab8716574e5ae549840b6b9b687cbbe3"
    ):
        raise AuthorityDrift("R30 external authority-profile mapping drift")
    profile = validate_authority_profile(loaded["authority_profile"])
    if authority_digest(profile) != internal["authority_profile"]["semantic_digest"]:
        raise AuthorityDrift("R30 external authority-profile semantic digest drift")

    accepted = value["accepted_authorities"]
    if accepted != {
        "growth_r29": {
            "repository": "foto6/video3",
            "producer_sha": R29_SHA,
            "ci_run_id": R29_CI,
            "artifact_id": R29_ARTIFACT_ID,
            "artifact_digest": R29_ARTIFACT_DIGEST,
        },
        "media_r24": {
            "repository": "foto6/video2",
            "producer_sha": MEDIA_R24_SHA,
            "ci_run_id": MEDIA_R24_CI,
            "artifact_id": MEDIA_R24_ARTIFACT_ID,
            "artifact_digest": MEDIA_R24_ARTIFACT_DIGEST,
        },
        "bridge_r34": {
            "repository": "foto6/WebAIBridge",
            "producer_sha": BRIDGE_R34_SHA,
            "ci_run_id": BRIDGE_R34_CI,
            "routing_authority": "providerConversationId",
            "live_cutover": False,
            "artifacts": profile["bridge_r34"]["artifacts"],
        },
    }:
        raise AuthorityDrift("R30 external accepted authority pins drift")

    policy = validate_policy(loaded["policy"])
    if value["independence_rules"] != {
        "reviewer_count": 3,
        "distinct_review_conversation_ids": 3,
        "duplicate_capture_digest_allowed": False,
        "duplicate_response_digest_allowed": False,
        "same_media_package_required": True,
        "same_review_round_required": True,
        "same_prompt_digest_required": True,
        "same_attachment_identity_required": True,
        "same_sealed_mapping_digest_required": True,
        "sealed_mapping_visible_to_reviewer": False,
        "canonical_reviewer_order": "conversation_id_ascending",
    }:
        raise AuthorityDrift("R30 external independence rules drift")
    if value["disagreement_and_human_review_gates"] != {
        "threshold_source": "internal_oracle.aggregation_policy",
        "threshold_tuning_allowed_by_umbrella": False,
        "human_review_required_for": policy["human_review_required"],
        "dissent_must_remain_in_audit": True,
        "creator_executable_handoff_only_when_internal_consensus_gate_passes": True,
    }:
        raise AuthorityDrift("R30 external disagreement/human-review gate drift")
    if value["evidence_boundary"] != {
        "model_consensus_is_human_ground_truth": False,
        "human_ground_truth": False,
        "human_rating_evidence": False,
        "human_parity_inferred": False,
        "browser_mutation": False,
        "model_call": False,
        "provider_publish": False,
        "merge": False,
    }:
        raise AuthorityDrift("R30 external evidence boundary drift")
    return _clone(value)


def external_contract_digest(
    value: Mapping[str, Any],
    *,
    root: Path | None = None,
) -> str:
    return sha256_json(validate_external_contract(value, root=root))


def validate_authority_profile(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "contract_version",
        "growth_r29",
        "media_r24",
        "bridge_r34",
        "evidence_boundary",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise AuthorityDrift("R30 authority profile fields invalid")
    if value["contract_version"] != AUTHORITY_VERSION:
        raise AuthorityDrift("R30 authority profile contract mismatch")

    growth = value["growth_r29"]
    if (
        growth.get("repository") != "foto6/video3"
        or growth.get("producer_sha") != R29_SHA
        or growth.get("ci_run_id") != R29_CI
        or growth.get("artifact")
        != {
            "id": R29_ARTIFACT_ID,
            "name": "growth-r29-exact-session-ingest",
            "digest": R29_ARTIFACT_DIGEST,
        }
        or growth.get("contracts")
        != {
            "round_result": R29_ROUND_RESULT_VERSION,
            "selected_result": "growth.creator_r30_selected_review_result.r29.v1",
            "inner_creator_envelope": INNER_ENVELOPE_VERSION,
        }
        or growth.get("blobs")
        != {
            "implementation": "cfd3a3e8788775b754a2a94af37baa88e7957dc8",
            "authority_profile": "5f59d52d9768b0489ff7655bf90fe0982d7523d8",
            "contract": "402ca7aed9b2d5a17ac9a1e79e751d3ccb376b5c",
            "tests": "f7dd43d5d8e0dd08b67aa0f907d9f4fac51a05bb",
            "workflow": "3f7e77508183b56a450244c6f1207549fd1551a5",
        }
    ):
        raise AuthorityDrift("exact Growth R29 authority drift")
    _artifact_digest(growth["artifact"]["digest"], "growth_r29.artifact.digest")

    media = value["media_r24"]
    if (
        media.get("repository") != "foto6/video2"
        or media.get("producer_sha") != MEDIA_R24_SHA
        or media.get("ci_run_id") != MEDIA_R24_CI
        or media.get("artifact")
        != {
            "id": MEDIA_R24_ARTIFACT_ID,
            "name": "media-r24-canonical-live-review-export",
            "digest": MEDIA_R24_ARTIFACT_DIGEST,
        }
        or media.get("contracts")
        != {
            "export": "media.canonical_live_review_export.r24.v1",
            "package_manifest": "media.canonical_live_review_package_manifest.r24.v1",
            "authority": "media.canonical_live_review_authority.r24.v1",
            "bridge_handoff": "media.bridge_live_review_handoff.r24.v1",
            "verification": "media.canonical_live_review_verification.r24.v1",
        }
    ):
        raise AuthorityDrift("exact Media R24 authority drift")
    expected_media_blobs = {
        "contract": "e63a7d712f85ad5335e89c9a23c617d178baf2ea",
        "manifest": "d2d22ca2fb9300053fe58fe5a7c56a517bd18aaa",
        "schema": "79ab876b27143fe9939c90ea7e075b42243285ba",
        "implementation": "6ac715d57cb5ec37792a84190ec481c0f7194a77",
        "exporter": "13552bc37ce752b3980396f49ed503c0c744c587",
        "verifier": "b01cfab99ffb6a39a6edcc3a29af491d7fb42e9c",
        "upstream_verifier": "3fa2c45764e5c3baad4627143b0548980dfe44d9",
        "demo": "8af588531c6dc31541e32e6ba64af5679d8e1df4",
        "actions_readiness": "240e98ad363e04a4386d3fba2621fc27d53ae4bb",
        "tests": "97b8539633fdc773744c29cb651ceefbdfe1a2d7",
        "workflow": "830cf4ac5976460c7217574860ebb4ef06287c99",
    }
    if media.get("blobs") != expected_media_blobs:
        raise AuthorityDrift("Media R24 contract/schema/implementation blob drift")
    for key, digest in expected_media_blobs.items():
        _git_sha(digest, f"media_r24.blobs.{key}")
    _artifact_digest(media["artifact"]["digest"], "media_r24.artifact.digest")
    known = media.get("known_artifact_rounds")
    if not isinstance(known, Mapping) or set(known) != {"0", "1"}:
        raise AuthorityDrift("Media R24 known artifact round freeze invalid")
    for round_key, row in known.items():
        for field in (
            "session_identity",
            "package_digest",
            "export_index_sha256",
            "payload_directory_digest",
            "archive_sha256",
            "prompt_digest",
            "sealed_mapping_digest",
            "r23_session_package_sha256",
            "r29_package_digest",
        ):
            _sha(row.get(field), f"media_r24.rounds.{round_key}.{field}")

    bridge = value["bridge_r34"]
    if (
        bridge.get("repository") != "foto6/WebAIBridge"
        or bridge.get("producer_sha") != BRIDGE_R34_SHA
        or bridge.get("ci_run_id") != BRIDGE_R34_CI
        or bridge.get("contracts")
        != {
            "review_request": "bridge.existing_chat_video_review_request.v1",
            "review_result": BRIDGE_RESULT_CONTRACT,
            "capture": BRIDGE_CAPTURE_CONTRACT,
            "readiness": "bridge.r34_sticky_chat_exact_id_readiness.v1",
            "rehearsal": "bridge.r34_sticky_chat_exact_id_rehearsal.v1",
        }
        or bridge.get("routing_authority") != "providerConversationId"
        or bridge.get("title_routing_allowed") is not False
        or bridge.get("last_open_routing_allowed") is not False
        or bridge.get("only_open_routing_allowed") is not False
        or bridge.get("tab_id_authoritative") is not False
        or bridge.get("project_slug_authoritative") is not False
        or bridge.get("live_cutover") is not False
    ):
        raise AuthorityDrift("exact Bridge R34 authority/routing drift")
    expected_bridge_blobs = {
        "sticky_chat_routing": "0e72ccb822f4e5fd9f29bc8ef17ccec70aeb78da",
        "chat_file_attachment": "3807a788e1a2113dc2d1a7e573a7bb1e065e8840",
        "existing_chat_video_review": "51df839d829028da754ce93e2640c9fc76c2412a",
        "server": "a2bb2502dc071b8a6530214e07eb1895c5c5be7b",
        "attachment_rehearsal": "132fcf25d5fdccc6801459be95b57a1e1ee24879",
        "readiness": "e4cf0ba13e9b51a978f61ac9bac33b9d08ee3bcc",
        "tests": "726eb6b6a3284666293dcde4458ee65e7fc6ea26",
        "rehearsal": "9a8cad3627a3d0d429897cf09d263dae97e8209e",
        "package": "b13e7e3aca12b22f7b3b5af140a92dd0a7ccb998",
        "workflow": "1677f17e9c55be18b2b1bc8c1d4eb25431ecd906",
    }
    if bridge.get("blobs") != expected_bridge_blobs:
        raise AuthorityDrift("Bridge R34 implementation blob drift")
    expected_artifacts = [
        {
            "platform": "ubuntu-latest",
            "id": 11300833232,
            "name": "r34-sticky-chat-ubuntu-latest",
            "digest":
                "sha256:feab9b876c609e3fcc6987ae92278ea61c2fed3e13aaad79cb5e68256fd9ef93",
        },
        {
            "platform": "windows-latest",
            "id": 11301086988,
            "name": "r34-sticky-chat-windows-latest",
            "digest":
                "sha256:41febb909113a8d522be40388e1c90351815ac9e211f09a2933adf74e47731f6",
        },
    ]
    if bridge.get("artifacts") != expected_artifacts:
        raise AuthorityDrift("Bridge R34 Actions artifact authority drift")
    for row in bridge["artifacts"]:
        _artifact_digest(row["digest"], "bridge_r34.artifact.digest")

    boundary = value["evidence_boundary"]
    if boundary != {
        "model_consensus_is_human_ground_truth": False,
        "human_ground_truth": False,
        "human_rating_evidence": False,
        "human_parity_inferred": False,
        "browser_mutation": False,
        "model_call": False,
        "provider_publish": False,
        "merge": False,
    }:
        raise AuthorityDrift("R30 evidence boundary drift")
    return _clone(value)


def validate_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("contract_version") != POLICY_VERSION:
        raise R30Error("aggregation policy contract mismatch")
    if value.get("reviewer_count") != 3:
        raise R30Error("aggregation policy reviewer_count must be 3")
    thresholds = value.get("thresholds")
    if thresholds != {
        "high_confidence_min": 0.82,
        "high_confidence_mean": 0.86,
        "majority_dissent_confidence_max": 0.45,
        "majority_disagreement_score_max": 0.30,
    }:
        raise R30Error("aggregation thresholds drift")
    if value.get("canonical_reviewer_order") != "conversation_id_ascending":
        raise R30Error("canonical reviewer ordering drift")
    return _clone(value)


def authority_digest(profile: Mapping[str, Any]) -> str:
    return sha256_json(validate_authority_profile(profile))


def policy_digest(policy: Mapping[str, Any]) -> str:
    return sha256_json(validate_policy(policy))


def _verify_file_manifest(payload: Path, manifest: Mapping[str, Any]) -> dict[str, str]:
    if (
        manifest.get("contractVersion")
        != "media.canonical_live_review_package_manifest.r24.v1"
        or manifest.get("state") != "CANONICAL_LIVE_REVIEW_ARTIFACT_READY"
    ):
        raise PackageDrift("Media R24 package-manifest contract/state drift")
    rows = manifest.get("files")
    if not isinstance(rows, list) or not rows:
        raise PackageDrift("Media R24 package-manifest files missing")
    if sha256_json(rows) != manifest.get("fileSetDigest"):
        raise PackageDrift("Media R24 package-manifest fileSetDigest drift")
    hashes: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "mime", "path", "sha256", "size"
        }:
            raise PackageDrift("Media R24 package-manifest row invalid")
        rel = _nonempty(row["path"], "package_manifest.path")
        file = _safe_child(payload, rel, "package_manifest.path")
        if not file.is_file():
            raise PackageDrift(f"Media R24 payload file missing: {rel}")
        digest = _file_sha(file)
        if digest != _sha(row["sha256"], f"package_manifest.{rel}.sha256"):
            raise PackageDrift(f"Media R24 payload hash drift: {rel}")
        if _file_size(file) != row["size"]:
            raise PackageDrift(f"Media R24 payload size drift: {rel}")
        hashes[rel] = digest
    return hashes


def validate_media_r24(
    media_dir: Path,
    *,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    profile = validate_authority_profile(profile)
    root = Path(media_dir).resolve()
    payload = root / "payload"
    index_path = root / R24_EXPORT_FILE
    manifest_path = payload / R24_MANIFEST_FILE
    authority_path = payload / R24_AUTHORITY_FILE
    handoff_path = payload / R24_HANDOFF_FILE
    prompt_path = payload / R24_PROMPT_FILE
    mapping_path = payload / R24_MAPPING_FILE
    for path in (
        index_path, manifest_path, authority_path, handoff_path,
        prompt_path, mapping_path,
    ):
        if not path.is_file():
            raise PackageDrift(f"Media R24 required file missing: {path.name}")

    index = _load(index_path)
    if (
        index.get("contractVersion") != "media.canonical_live_review_export.r24.v1"
        or index.get("state") != "CANONICAL_LIVE_REVIEW_ARTIFACT_READY"
        or index.get("producer")
        != {
            "repository": "foto6/video2",
            "sha": MEDIA_R24_SHA,
            "ciRunId": MEDIA_R24_CI,
        }
    ):
        raise AuthorityDrift("Media R24 exact producer/contract drift")
    for field in (
        "nonHumanGroundTruthDemo", "modelReviewPerformed", "liveModelReviewed",
        "browserMutationPerformed", "providerPublish", "humanQuality",
    ):
        expected = True if field == "nonHumanGroundTruthDemo" else False
        if index.get(field) is not expected:
            raise PackageDrift(f"Media R24 evidence boundary drift: {field}")

    manifest = _load(manifest_path)
    hashes = _verify_file_manifest(payload, manifest)
    authority = _load(authority_path)
    handoff = _load(handoff_path)
    mapping = _load(mapping_path)

    if (
        index.get("packageManifest", {}).get("sha256") != _file_sha(manifest_path)
        or index.get("authorityProfile", {}).get("sha256") != _file_sha(authority_path)
        or index.get("bridgeHandoff", {}).get("sha256") != _file_sha(handoff_path)
    ):
        raise PackageDrift("Media R24 derived manifest byte identity drift")
    if (
        manifest.get("authorityProfileSha256") != _file_sha(authority_path)
        or manifest.get("bridgeHandoffSha256") != _file_sha(handoff_path)
    ):
        raise PackageDrift("Media R24 package manifest linkage drift")
    if (
        authority.get("contractVersion") != "media.canonical_live_review_authority.r24.v1"
        or authority.get("state") != "CANONICAL_LIVE_REVIEW_ARTIFACT_READY"
        or authority.get("producerR24", {}).get("repository") != "foto6/video2"
        or authority.get("producerR24", {}).get("producerSha") != MEDIA_R24_SHA
        or authority.get("producerR24", {}).get("ciRunId") != MEDIA_R24_CI
    ):
        raise AuthorityDrift("Media R24 embedded authority profile drift")
    accepted = authority.get("acceptedR23", {})
    if (
        accepted.get("producerSha")
        != "78c6982a91d7e3e8c037cd9ce740ee077babdccc"
        or accepted.get("ciRunId") != 37007419237
        or accepted.get("contractVersion") != "media.review_session_package.r23.v1"
    ):
        raise AuthorityDrift("Media R24 nested R23 authority drift")
    if (
        handoff.get("contractVersion") != "media.bridge_live_review_handoff.r24.v1"
        or handoff.get("state") != "CANONICAL_LIVE_REVIEW_ARTIFACT_READY"
        or handoff.get("modelReviewPerformed") is not False
        or handoff.get("liveModelReviewed") is not False
        or handoff.get("browserMutationPerformed") is not False
        or handoff.get("providerPublish") is not False
        or handoff.get("humanQuality") is not False
        or handoff.get("sealedMapping", {}).get("contentsModelFacing") is not False
    ):
        raise PackageDrift("Media R24 Bridge handoff/evidence boundary drift")

    prompt_digest = _file_sha(prompt_path)
    if (
        index.get("promptDigest") != prompt_digest
        or manifest.get("promptDigest") != prompt_digest
        or handoff.get("prompt", {}).get("sha256") != prompt_digest
        or handoff.get("prompt", {}).get("size") != _file_size(prompt_path)
    ):
        raise PackageDrift("Media R24 prompt bytes/hash drift")

    if not isinstance(mapping, Mapping) or set(mapping) != {"digest", "entries"}:
        raise PackageDrift("Media R24 sealed mapping shape invalid")
    mapping_digest = sha256_json(mapping["entries"])
    if (
        mapping.get("digest") != mapping_digest
        or index.get("sealedMappingDigest") != mapping_digest
        or manifest.get("sealedMappingDigest") != mapping_digest
        or handoff.get("sealedMapping", {}).get("digest") != mapping_digest
    ):
        raise PackageDrift("Media R24 sealed mapping digest/content drift")

    attachments = sorted(index.get("attachments") or [], key=lambda x: x.get("blindLabel", ""))
    if len(attachments) != 2 or [x.get("blindLabel") for x in attachments] != ["A", "B"]:
        raise PackageDrift("Media R24 exact blinded A/B attachments required")
    handoff_attachments = sorted(
        handoff.get("attachments") or [], key=lambda x: x.get("blindLabel", "")
    )
    if handoff_attachments != attachments:
        raise PackageDrift("Media R24 handoff attachment identity drift")
    for row in attachments:
        file = payload / row["relativePath"]
        if (
            not file.is_file()
            or _file_sha(file) != row["sha256"]
            or _file_size(file) != row["size"]
            or row.get("mime") != "video/mp4"
        ):
            raise PackageDrift(
                f"Media R24 attachment bytes drift: {row.get('blindLabel')}"
            )

    entries = mapping.get("entries")
    if not isinstance(entries, list) or len(entries) != 2:
        raise PackageDrift("Media R24 sealed mapping must contain two entries")
    mapping_by_label: dict[str, Any] = {}
    attachment_by_label = {row["blindLabel"]: row for row in attachments}
    for row in entries:
        label = row.get("blindLabel")
        if label not in {"A", "B"} or label in mapping_by_label:
            raise PackageDrift("Media R24 sealed mapping labels invalid")
        attachment = attachment_by_label[label]
        if (
            row.get("genericFileName") != attachment["relativePath"]
            or row.get("attachment", {}).get("sha256") != attachment["sha256"]
            or row.get("attachment", {}).get("size") != attachment["size"]
            or row.get("attachment", {}).get("mimeType") != "video/mp4"
        ):
            raise PackageDrift("Media R24 sealed mapping attachment identity drift")
        mapping_by_label[label] = row

    nested_r23 = index.get("nestedR23")
    if not isinstance(nested_r23, Mapping) or set(nested_r23) != {
        "sessionEvidenceRelativePath",
        "sessionEvidenceSha256",
        "sessionPackageRelativePath",
        "sessionPackageSha256",
    }:
        raise PackageDrift("Media R24 nested R23 linkage missing")
    r23_package_path = _safe_child(
        root,
        nested_r23["sessionPackageRelativePath"],
        "nestedR23.sessionPackageRelativePath",
    )
    if (
        not r23_package_path.is_file()
        or _file_sha(r23_package_path)
        != _sha(
            nested_r23["sessionPackageSha256"],
            "nestedR23.sessionPackageSha256",
        )
    ):
        raise PackageDrift("Media R24 nested R23 package byte identity drift")
    r23_manifest_key = str(
        r23_package_path.relative_to(payload)
    ).replace("\\", "/")
    if hashes.get(r23_manifest_key) != nested_r23["sessionPackageSha256"]:
        raise PackageDrift("Media R24 package manifest does not bind nested R23")
    r23_package = _load(r23_package_path)
    if (
        r23_package.get("contractVersion")
        != "media.review_session_package.r23.v1"
        or r23_package.get("state") != "REVIEW_SESSION_PACKAGE_READY"
        or r23_package.get("producer")
        != {
            "repository": "foto6/video2",
            "sha": "78c6982a91d7e3e8c037cd9ce740ee077babdccc",
            "ciRunId": 37007419237,
        }
    ):
        raise PackageDrift("Media R24 nested R23 producer/contract drift")
    r29_package_digest = _sha(
        r23_package.get("r21", {}).get("packageDigest"),
        "nestedR23.r21.packageDigest",
    )

    source_lineage = index.get("sourceLineage")
    if not isinstance(source_lineage, Mapping):
        raise PackageDrift("Media R24 source lineage missing")
    for key in ("sessionId", "sessionIdentity", "reviewRound", "briefLineageDigest"):
        if handoff.get("sourceLineage", {}).get(key) != source_lineage.get(key):
            raise PackageDrift(f"Media R24 source lineage drift: {key}")
        r23_key = {
            "sessionId": "sessionId",
            "sessionIdentity": "sessionIdentity",
            "reviewRound": "reviewRound",
            "briefLineageDigest": "briefLineageDigest",
        }[key]
        if r23_package.get(r23_key) != source_lineage.get(key):
            raise PackageDrift(f"Media R24/R23 source lineage drift: {key}")
    if (
        r23_package.get("promptDigest") != prompt_digest
        or r23_package.get("sealedMappingDigest") != mapping_digest
    ):
        raise PackageDrift("Media R24/R23 prompt or sealed mapping lineage drift")
    r23_attachments = sorted(
        r23_package.get("attachments") or [],
        key=lambda row: row.get("blindLabel", ""),
    )
    expected_r23_attachments = [
        {
            "blindLabel": row["blindLabel"],
            "name": row["relativePath"],
            "sha256": row["sha256"],
            "size": row["size"],
            "mime": row["mime"],
        }
        for row in attachments
    ]
    if r23_attachments != expected_r23_attachments:
        raise PackageDrift("Media R24/R23 attachment lineage drift")
    review_round = source_lineage.get("reviewRound")
    if review_round not in (0, 1, 2):
        raise PackageDrift("Media R24 review round outside 0..2")

    known = profile["media_r24"]["known_artifact_rounds"].get(str(review_round))
    index_sha = _file_sha(index_path)
    if (
        source_lineage.get("sessionId") == "r23-real-session"
        and known is not None
    ):
        observed = {
            "session_id": source_lineage["sessionId"],
            "session_identity": source_lineage["sessionIdentity"],
            "package_digest": index["packageDigest"],
            "export_index_sha256": index_sha,
            "payload_directory_digest": index["payloadDirectory"]["digest"],
            "archive_sha256": index["archive"]["sha256"],
            "prompt_digest": prompt_digest,
            "sealed_mapping_digest": mapping_digest,
            "r23_session_package_sha256": nested_r23["sessionPackageSha256"],
            "r29_package_digest": r29_package_digest,
            "attachments": attachments,
        }
        if observed != known:
            raise PackageDrift(f"exact Media R24 artifact round-{review_round} drift")

    return _clone(
        {
            "root": str(root),
            "session_id": source_lineage["sessionId"],
            "session_identity": source_lineage["sessionIdentity"],
            "review_round": review_round,
            "mode": source_lineage.get("mode"),
            "package_digest": _sha(index["packageDigest"], "media.packageDigest"),
            "r29_package_digest": r29_package_digest,
            "r23_session_package_sha256": nested_r23["sessionPackageSha256"],
            "export_index_sha256": index_sha,
            "payload_directory_digest": _sha(
                index["payloadDirectory"]["digest"],
                "media.payloadDirectory.digest",
            ),
            "prompt_digest": prompt_digest,
            "prompt_size": _file_size(prompt_path),
            "sealed_mapping_digest": mapping_digest,
            "sealed_mapping_file_sha256": hashes[R24_MAPPING_FILE],
            "attachments": attachments,
            "source": source_lineage["source"],
            "brief_lineage_digest": source_lineage["briefLineageDigest"],
            "mapping_by_label": mapping_by_label,
            "model_facing_files": handoff["modelFacingFiles"],
        }
    )


def _forbidden_mapping_key(value: Any) -> bool:
    forbidden = {
        "candidateId", "candidate_id", "baselineCandidateId",
        "challengerCandidateId", "sealedMapping", "sealed_mapping",
        "mapping_by_label", "renderProducerSha", "role",
    }
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in forbidden:
                return True
            if _forbidden_mapping_key(child):
                return True
    elif isinstance(value, list):
        return any(_forbidden_mapping_key(x) for x in value)
    return False


def parse_vote_bytes(raw: bytes) -> dict[str, Any]:
    digest = hashlib.sha256(raw).hexdigest()
    try:
        decoded = raw.decode("utf-8")
        value = json.loads(decoded)
    except Exception as exc:
        raise MalformedReview("model response is not UTF-8 strict JSON") from exc
    if not isinstance(value, Mapping) or set(value) != {
        "contract_version",
        "winner",
        "confidence",
        "defects",
        "policy_valid",
        "format_valid",
        "uncertainty",
    }:
        raise MalformedReview("model response fields mismatch")
    if value["contract_version"] != VOTE_VERSION:
        raise MalformedReview("model vote contract mismatch")
    if value["winner"] not in WINNERS:
        raise MalformedReview("winner must be A/B/tie/insufficient_evidence")
    confidence = _confidence(value["confidence"], "vote.confidence")
    if not isinstance(value["policy_valid"], bool) or not isinstance(
        value["format_valid"], bool
    ):
        raise MalformedReview("policy_valid/format_valid must be booleans")
    uncertainty = value["uncertainty"]
    if not isinstance(uncertainty, str):
        raise MalformedReview("uncertainty must be string")
    defects_raw = value["defects"]
    if not isinstance(defects_raw, list):
        raise MalformedReview("defects must be array")
    defects = []
    for index, row in enumerate(defects_raw):
        if not isinstance(row, Mapping) or set(row) != {
            "attachment_label",
            "start_ms",
            "end_ms",
            "defect_category",
            "severity",
            "evidence",
            "requested_edit",
            "direction",
            "confidence",
            "uncertainty",
        }:
            raise MalformedReview(f"defects[{index}] fields mismatch")
        label = row["attachment_label"]
        if label not in {"A", "B"}:
            raise MalformedReview("defect attachment_label must be A/B")
        start = row["start_ms"]
        end = row["end_ms"]
        if (
            isinstance(start, bool) or isinstance(end, bool)
            or not isinstance(start, int) or not isinstance(end, int)
            or start < 0 or end <= start
        ):
            raise MalformedReview("defect timestamp interval invalid")
        category = _nonempty(row["defect_category"], "defect_category")
        severity = row["severity"]
        if severity not in SEVERITIES:
            raise MalformedReview("defect severity invalid")
        direction = row["direction"]
        if direction not in DIRECTIONS:
            raise MalformedReview("defect direction invalid")
        evidence = _nonempty(row["evidence"], "defect.evidence")
        requested_edit = _nonempty(row["requested_edit"], "defect.requested_edit")
        defect_uncertainty = row["uncertainty"]
        if not isinstance(defect_uncertainty, str):
            raise MalformedReview("defect uncertainty must be string")
        defects.append(
            {
                "attachment_label": label,
                "start_ms": start,
                "end_ms": end,
                "defect_category": category,
                "severity": severity,
                "evidence": evidence,
                "requested_edit": requested_edit,
                "direction": direction,
                "confidence": _confidence(
                    row["confidence"], f"defects[{index}].confidence"
                ),
                "uncertainty": defect_uncertainty,
            }
        )
    if _forbidden_mapping_key(value):
        raise MalformedReview("model response contains sealed role/candidate mapping")
    return _clone(
        {
            "raw_sha256": digest,
            "winner": value["winner"],
            "confidence": confidence,
            "defects": defects,
            "policy_valid": value["policy_valid"],
            "format_valid": value["format_valid"],
            "uncertainty": uncertainty,
        }
    )


def _validate_inner_envelope(
    envelope: Mapping[str, Any],
    *,
    media: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(envelope, Mapping):
        raise ReviewConflict("inner Creator envelope must be object")
    if envelope.get("contract_version") != INNER_ENVELOPE_VERSION:
        raise ReviewConflict("canonical inner Creator envelope contract drift")
    digest = _sha(envelope.get("envelope_digest"), "inner.envelope_digest")
    material = copy.deepcopy(dict(envelope))
    material["envelope_digest"] = ""
    if sha256_json(material) != digest:
        raise ReviewConflict("inner Creator envelope digest drift")
    candidate = envelope.get("candidate")
    review = envelope.get("review")
    boundary = envelope.get("evidence_boundary")
    if not isinstance(candidate, Mapping) or not isinstance(review, Mapping):
        raise ReviewConflict("inner Creator envelope candidate/review missing")
    if (
        review.get("package_digest") != media["r29_package_digest"]
        or review.get("sealed_mapping_digest") != media["sealed_mapping_digest"]
        or review.get("review_round") != media["review_round"]
    ):
        raise ReviewConflict("inner Creator envelope Media lineage drift")
    candidate_id = _nonempty(candidate.get("candidate_id"), "candidate_id")
    mapping_row = None
    for row in media["mapping_by_label"].values():
        if row["candidateId"] == candidate_id:
            mapping_row = row
            break
    if mapping_row is None:
        raise ReviewConflict("inner Creator envelope candidate not in sealed mapping")
    if (
        candidate.get("render_sha256") != mapping_row["render"]["sha256"]
        or candidate.get("render_size") != mapping_row["render"]["size"]
        or candidate.get("attachment_sha256")
        != mapping_row["attachment"]["sha256"]
        or candidate.get("attachment_size") != mapping_row["attachment"]["size"]
        or candidate.get("attachment_mime_type") != "video/mp4"
    ):
        raise ReviewConflict("inner Creator envelope candidate bytes drift")
    if boundary != {
        "model_evidence": True,
        "human_ground_truth": False,
        "human_rating_evidence": False,
        "human_parity_inferred": False,
        "provider_mutation": False,
    }:
        raise ReviewConflict("inner Creator envelope evidence boundary drift")
    return _clone(envelope)


def _critical_r29_result(
    result: Mapping[str, Any],
    *,
    media: Mapping[str, Any],
    review: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(result, Mapping) or result.get(
        "contract_version"
    ) != R29_ROUND_RESULT_VERSION:
        raise ReviewConflict("exact R29 round-result contract required")
    digest = _sha(result.get("round_result_digest"), "r29.round_result_digest")
    material = copy.deepcopy(dict(result))
    material["round_result_digest"] = ""
    if sha256_json(material) != digest:
        raise ReviewConflict("R29 round-result semantic digest drift")
    if result.get("growth_r29") != {
        "producer_sha": R29_SHA,
        "ci_run_id": R29_CI,
        "authority_profile_digest": result.get("growth_r29", {}).get(
            "authority_profile_digest"
        ),
    }:
        raise AuthorityDrift("R29 round-result producer authority drift")
    _sha(
        result["growth_r29"]["authority_profile_digest"],
        "r29.growth_r29.authority_profile_digest",
    )
    critical = {
        "session_id": result.get("session_id"),
        "media_session_id": result.get("media_session_id"),
        "review_round": result.get("review_round"),
        "conversation_id": result.get("conversation_id"),
        "request_id": result.get("request_id"),
        "operation_id": result.get("operation_id"),
        "response_digest": result.get("response_digest"),
        "capture_digest": result.get("capture_digest"),
        "package_digest": result.get("package_digest"),
        "sealed_mapping_digest": result.get("sealed_mapping_digest"),
        "prompt_digest": result.get("prompt_digest"),
    }
    expected = {
        "session_id": review["r29"]["session_id"],
        "media_session_id": media["session_id"],
        "review_round": media["review_round"],
        "conversation_id": review["bridge_r34"]["conversation_id"],
        "request_id": review["bridge_r34"]["request_id"],
        "operation_id": review["bridge_r34"]["operation_id"],
        "response_digest": review["bridge_r34"]["response_digest"],
        "capture_digest": review["bridge_r34"]["capture_digest"],
        "package_digest": media["r29_package_digest"],
        "sealed_mapping_digest": media["sealed_mapping_digest"],
        "prompt_digest": media["prompt_digest"],
    }
    if critical != expected:
        raise ReviewConflict("R29 result critical lineage/digest drift")
    if result.get("session_package_sha256") != media["r23_session_package_sha256"]:
        raise ReviewConflict("R29 result nested R23 session package drift")
    if result.get("authorities") != {
        "media_r23_sha": "78c6982a91d7e3e8c037cd9ce740ee077babdccc",
        "bridge_r32_sha": "805bf628d3d2844549b54db1112736fae0200fc7",
        "creator_r30_sha": "50c17852a910c57f0894dcdb356d4d4923edb62b",
        "canonical_inner_envelope_contract": INNER_ENVELOPE_VERSION,
    }:
        raise AuthorityDrift("R29 round-result preserved authority set drift")
    if result.get("evidence_boundary") != {
        "model_evidence": True,
        "human_ground_truth": False,
        "human_rating_evidence": False,
        "human_parity_inferred": False,
        "browser_mutation": False,
        "provider_mutation": False,
    }:
        raise ReviewConflict("R29 result evidence boundary drift")
    return critical


def load_review(
    review_path: Path,
    *,
    media: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    profile = validate_authority_profile(profile)
    path = Path(review_path).resolve()
    root = path.parent
    review = _load(path)
    if not isinstance(review, Mapping) or set(review) != {
        "contract_version",
        "evidence_state",
        "review_id",
        "r29",
        "media_r24",
        "bridge_r34",
        "model_metadata",
        "inner_envelopes",
        "evidence_boundary",
    }:
        raise ReviewConflict("verified review manifest fields invalid")
    if review["contract_version"] != REVIEW_VERSION:
        raise ReviewConflict("verified review contract mismatch")
    if review["evidence_state"] not in {"LIVE_REVIEW_INGESTED", "FIXTURE_VERIFIED"}:
        raise ReviewConflict("verified review evidence_state invalid")
    _nonempty(review["review_id"], "review_id")

    r29 = review["r29"]
    if not isinstance(r29, Mapping) or set(r29) != {
        "producer_sha",
        "ci_run_id",
        "artifact_id",
        "artifact_digest",
        "round_result_contract",
        "round_result_file",
        "round_result_file_sha256",
        "session_id",
    }:
        raise ReviewConflict("R29 review authority fields invalid")
    if (
        r29["producer_sha"] != R29_SHA
        or r29["ci_run_id"] != R29_CI
        or r29["artifact_id"] != R29_ARTIFACT_ID
        or r29["artifact_digest"] != R29_ARTIFACT_DIGEST
        or r29["round_result_contract"] != R29_ROUND_RESULT_VERSION
    ):
        raise AuthorityDrift("review R29 exact authority drift")

    media_ref = review["media_r24"]
    if not isinstance(media_ref, Mapping) or media_ref != {
        "producer_sha": MEDIA_R24_SHA,
        "ci_run_id": MEDIA_R24_CI,
        "artifact_id": MEDIA_R24_ARTIFACT_ID,
        "artifact_digest": MEDIA_R24_ARTIFACT_DIGEST,
        "session_id": media["session_id"],
        "session_identity": media["session_identity"],
        "package_digest": media["package_digest"],
        "r29_package_digest": media["r29_package_digest"],
        "review_round": media["review_round"],
        "prompt_digest": media["prompt_digest"],
        "prompt_size": media["prompt_size"],
        "sealed_mapping_digest": media["sealed_mapping_digest"],
        "attachments": media["attachments"],
    }:
        raise PackageDrift("review Media R24 exact package/round identity drift")

    bridge = review["bridge_r34"]
    if not isinstance(bridge, Mapping) or set(bridge) != {
        "producer_sha",
        "ci_run_id",
        "capture_contract",
        "result_contract",
        "routing_authority",
        "provider_conversation_id",
        "conversation_id",
        "canonical_url",
        "request_id",
        "operation_id",
        "capture_digest",
        "response_digest",
        "response_file",
        "response_file_sha256",
    }:
        raise ReviewConflict("Bridge R34 review fields invalid")
    if (
        bridge["producer_sha"] != BRIDGE_R34_SHA
        or bridge["ci_run_id"] != BRIDGE_R34_CI
        or bridge["capture_contract"] != BRIDGE_CAPTURE_CONTRACT
        or bridge["result_contract"] != BRIDGE_RESULT_CONTRACT
        or bridge["routing_authority"] != "providerConversationId"
    ):
        raise AuthorityDrift("review Bridge R34 exact authority/contract drift")
    conversation_id = _nonempty(bridge["conversation_id"], "conversation_id")
    if (
        bridge["provider_conversation_id"] != conversation_id
        or bridge["canonical_url"] != f"https://chatgpt.com/c/{conversation_id}"
    ):
        raise ReviewConflict("Bridge R34 exact conversation identity drift")
    _nonempty(bridge["request_id"], "request_id")
    _nonempty(bridge["operation_id"], "operation_id")
    capture_digest = _sha(bridge["capture_digest"], "capture_digest")
    response_digest = _sha(bridge["response_digest"], "response_digest")

    response_path = _safe_child(root, bridge["response_file"], "response_file")
    if not response_path.is_file():
        raise ReviewConflict("model response file missing")
    raw = response_path.read_bytes()
    raw_sha = hashlib.sha256(raw).hexdigest()
    if (
        raw_sha != bridge["response_file_sha256"]
        or raw_sha != response_digest
    ):
        raise ReviewConflict("exact model response bytes/hash drift")

    round_path = _safe_child(root, r29["round_result_file"], "round_result_file")
    if not round_path.is_file():
        raise ReviewConflict("R29 round result file missing")
    if _file_sha(round_path) != r29["round_result_file_sha256"]:
        raise ReviewConflict("R29 round-result bytes/hash drift")
    round_result = _load(round_path)
    _critical_r29_result(round_result, media=media, review=review)

    model = review["model_metadata"]
    if not isinstance(model, Mapping) or set(model) != {
        "provider", "model", "metadata"
    }:
        raise ReviewConflict("model metadata fields invalid")
    if model["provider"] is not None and not isinstance(model["provider"], str):
        raise ReviewConflict("model provider metadata invalid")
    if model["model"] is not None and not isinstance(model["model"], str):
        raise ReviewConflict("model model metadata invalid")
    if not isinstance(model["metadata"], Mapping):
        raise ReviewConflict("model metadata payload invalid")

    boundary = review["evidence_boundary"]
    if boundary != {
        "sealed_mapping_visible_to_reviewer": False,
        "model_evidence": True,
        "human_ground_truth": False,
        "human_rating_evidence": False,
        "human_parity_inferred": False,
        "browser_mutation_by_growth": False,
        "provider_publish_by_growth": False,
    }:
        raise ReviewConflict("review evidence boundary drift")

    envelope_refs = review["inner_envelopes"]
    if not isinstance(envelope_refs, Mapping) or set(envelope_refs) != {"A", "B"}:
        raise ReviewConflict("review must bind exact A/B inner envelopes")
    envelopes: dict[str, Any] = {}
    for label in ("A", "B"):
        ref = envelope_refs[label]
        if not isinstance(ref, Mapping) or set(ref) != {
            "relative_path", "sha256", "envelope_digest"
        }:
            raise ReviewConflict("inner envelope reference fields invalid")
        envelope_path = _safe_child(root, ref["relative_path"], "inner envelope")
        if not envelope_path.is_file():
            raise ReviewConflict("inner Creator envelope file missing")
        if _file_sha(envelope_path) != ref["sha256"]:
            raise ReviewConflict("inner Creator envelope file hash drift")
        envelope = _validate_inner_envelope(_load(envelope_path), media=media)
        if envelope["envelope_digest"] != ref["envelope_digest"]:
            raise ReviewConflict("inner Creator envelope reference digest drift")
        candidate_id = media["mapping_by_label"][label]["candidateId"]
        if envelope["candidate"]["candidate_id"] != candidate_id:
            raise ReviewConflict("inner Creator envelope blind-label mapping drift")
        envelopes[label] = envelope

    malformed = None
    vote = None
    try:
        vote = parse_vote_bytes(raw)
    except MalformedReview as exc:
        malformed = str(exc)
    return _clone(
        {
            "review_id": review["review_id"],
            "evidence_state": review["evidence_state"],
            "conversation_id": conversation_id,
            "request_id": bridge["request_id"],
            "operation_id": bridge["operation_id"],
            "capture_digest": capture_digest,
            "response_digest": response_digest,
            "response_file_sha256": raw_sha,
            "r29_round_result_file_sha256": r29["round_result_file_sha256"],
            "model_metadata": model,
            "vote": vote,
            "malformed_reason": malformed,
            "inner_envelopes": envelopes,
        }
    )


def _overlap(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return a["start_ms"] < b["end_ms"] and b["start_ms"] < a["end_ms"]


def _directive_conflicts(reviews: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for review in reviews:
        if review["vote"] is None:
            continue
        for defect in review["vote"]["defects"]:
            rows.append(
                {
                    "conversation_id": review["conversation_id"],
                    **defect,
                }
            )
    conflicts = []
    for i, left in enumerate(rows):
        for right in rows[i + 1:]:
            if left["conversation_id"] == right["conversation_id"]:
                continue
            if (
                left["attachment_label"] == right["attachment_label"]
                and left["defect_category"] == right["defect_category"]
                and _overlap(left, right)
                and left["direction"] != right["direction"]
            ):
                conflicts.append(
                    {
                        "attachment_label": left["attachment_label"],
                        "defect_category": left["defect_category"],
                        "overlap": [
                            max(left["start_ms"], right["start_ms"]),
                            min(left["end_ms"], right["end_ms"]),
                        ],
                        "left": {
                            "conversation_id": left["conversation_id"],
                            "direction": left["direction"],
                            "requested_edit": left["requested_edit"],
                        },
                        "right": {
                            "conversation_id": right["conversation_id"],
                            "direction": right["direction"],
                            "requested_edit": right["requested_edit"],
                        },
                    }
                )
    return conflicts


def _disagreement(
    reviews: Sequence[Mapping[str, Any]],
    *,
    conflicts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    valid = [row for row in reviews if row["vote"] is not None]
    invalid_fraction = (3 - len(valid)) / 3
    if valid:
        confidences = [row["vote"]["confidence"] for row in valid]
        confidence_spread = max(confidences) - min(confidences)
        counts = Counter(row["vote"]["winner"] for row in valid)
        modal, modal_count = sorted(
            counts.items(), key=lambda item: (-item[1], item[0])
        )[0]
        categorical = 1 - modal_count / 3
        if modal in {"A", "B"}:
            dissent = [
                row["vote"]["confidence"]
                for row in valid
                if row["vote"]["winner"] != modal
            ]
            dissent_strength = max(dissent) if dissent else 0.0
        else:
            dissent_strength = max(confidences)
    else:
        modal = None
        modal_count = 0
        categorical = 1.0
        confidence_spread = 1.0
        dissent_strength = 1.0
    directive = 1.0 if conflicts else 0.0
    score = min(
        1.0,
        0.35 * categorical
        + 0.15 * confidence_spread
        + 0.25 * dissent_strength
        + 0.15 * directive
        + 0.10 * invalid_fraction,
    )
    return {
        "score": round(score, 6),
        "categorical_disagreement": round(categorical, 6),
        "confidence_spread": round(confidence_spread, 6),
        "dissent_strength": round(dissent_strength, 6),
        "directive_conflict": bool(conflicts),
        "invalid_fraction": round(invalid_fraction, 6),
        "modal_vote": modal,
        "modal_vote_count": modal_count,
    }


def aggregate_reviews(
    reviews: Sequence[Mapping[str, Any]],
    *,
    media: Mapping[str, Any],
    policy: Mapping[str, Any],
    profile: Mapping[str, Any],
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    profile = validate_authority_profile(profile)
    policy = validate_policy(policy)
    if len(reviews) != 3:
        raise IndependenceError("exactly three reviews required")
    ordered = sorted(reviews, key=lambda row: row["conversation_id"])
    conversations = [row["conversation_id"] for row in ordered]
    captures = [row["capture_digest"] for row in ordered]
    responses = [row["response_digest"] for row in ordered]
    if len(set(conversations)) != 3:
        raise IndependenceError("three distinct review conversation IDs required")
    if len(set(captures)) != 3:
        raise IndependenceError("duplicate capture digest violates independence")
    if len(set(responses)) != 3:
        raise IndependenceError("duplicate response digest violates independence")

    conflicts = _directive_conflicts(ordered)
    disagreement = _disagreement(ordered, conflicts=conflicts)
    reasons: list[str] = []
    valid = [row for row in ordered if row["vote"] is not None]
    if len(valid) != 3:
        reasons.append("malformed_review")
    if any(row["vote"] and not row["vote"]["policy_valid"] for row in ordered):
        reasons.append("policy_invalid")
    if any(row["vote"] and not row["vote"]["format_valid"] for row in ordered):
        reasons.append("format_invalid")
    if conflicts:
        reasons.append("conflicting_defect_direction")

    votes = [row["vote"]["winner"] for row in valid]
    counts = Counter(votes)
    winner = None
    rule = "none"
    majority_rows: list[Mapping[str, Any]] = []
    dissent_rows: list[Mapping[str, Any]] = []
    if len(valid) == 3:
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        modal, count = ranked[0]
        if modal in {"A", "B"} and count == 3:
            majority_rows = valid
            mean = sum(x["vote"]["confidence"] for x in majority_rows) / 3
            if min(x["vote"]["confidence"] for x in majority_rows) < 0.82 or mean < 0.86:
                reasons.append("unanimous_low_confidence")
            elif not reasons:
                winner = modal
                rule = "unanimous_high_confidence_winner"
        elif modal in {"A", "B"} and count == 2:
            majority_rows = [x for x in valid if x["vote"]["winner"] == modal]
            dissent_rows = [x for x in valid if x["vote"]["winner"] != modal]
            majority_mean = sum(x["vote"]["confidence"] for x in majority_rows) / 2
            if min(x["vote"]["confidence"] for x in majority_rows) < 0.82 or majority_mean < 0.86:
                reasons.append("majority_low_confidence")
            if max(x["vote"]["confidence"] for x in dissent_rows) > 0.45:
                reasons.append("dissent_confidence_too_high")
            if disagreement["score"] > 0.30:
                reasons.append("excessive_disagreement")
            if not reasons:
                winner = modal
                rule = "two_of_three_majority"
        elif modal in {"tie", "insufficient_evidence"}:
            reasons.append("tie_or_insufficient_modal_outcome")
        else:
            reasons.append("no_two_vote_winner")
    if len(valid) < 3 and "malformed_review" not in reasons:
        reasons.append("malformed_review")

    reasons = sorted(set(reasons))
    state = "CONSENSUS_ACCEPTED" if winner is not None and not reasons else "HUMAN_REVIEW_REQUIRED"
    selected_candidate_id = (
        None if winner is None else media["mapping_by_label"][winner]["candidateId"]
    )
    all_live = all(row["evidence_state"] == "LIVE_REVIEW_INGESTED" for row in ordered)

    selected_inner = None
    selected_from_conversation = None
    if state == "CONSENSUS_ACCEPTED":
        agreeing = [row for row in ordered if row["vote"]["winner"] == winner]
        canonical = agreeing[0]
        selected_inner = canonical["inner_envelopes"][winner]
        selected_from_conversation = canonical["conversation_id"]

    audit_reference_winner = winner
    if (
        audit_reference_winner is None
        and disagreement["modal_vote"] in {"A", "B"}
        and disagreement["modal_vote_count"] >= 2
    ):
        audit_reference_winner = disagreement["modal_vote"]

    audit = {
        "canonical_reviewer_order": conversations,
        "reviews": [
            {
                "conversation_id": row["conversation_id"],
                "review_id": row["review_id"],
                "capture_digest": row["capture_digest"],
                "response_digest": row["response_digest"],
                "evidence_state": row["evidence_state"],
                "model_metadata": row["model_metadata"],
                "malformed_reason": row["malformed_reason"],
                "vote": row["vote"],
                "dissent": bool(
                    audit_reference_winner is not None
                    and row["vote"] is not None
                    and row["vote"]["winner"] != audit_reference_winner
                ),
            }
            for row in ordered
        ],
        "directive_conflicts": conflicts,
        "disagreement": disagreement,
        "acceptance_rule": rule,
        "rejection_reasons": reasons,
        "selected_inner_envelope_from_conversation": selected_from_conversation,
        "dissenting_reviews": [
            row["conversation_id"]
            for row in ordered
            if audit_reference_winner is not None
            and row["vote"] is not None
            and row["vote"]["winner"] != audit_reference_winner
        ],
    }
    result = {
        "contract_version": CONSENSUS_VERSION,
        "state": state,
        "consensus_id": "",
        "consensus_digest": "",
        "media": {
            "producer_sha": MEDIA_R24_SHA,
            "ci_run_id": MEDIA_R24_CI,
            "session_id": media["session_id"],
            "session_identity": media["session_identity"],
            "package_digest": media["package_digest"],
            "r29_package_digest": media["r29_package_digest"],
            "review_round": media["review_round"],
            "prompt_digest": media["prompt_digest"],
            "sealed_mapping_digest": media["sealed_mapping_digest"],
            "attachments": media["attachments"],
        },
        "winner_blind_label": winner,
        "selected_candidate_id": selected_candidate_id,
        "disagreement_score": disagreement["score"],
        "audit": audit,
        "creator_executable_handoff_emitted": bool(
            state == "CONSENSUS_ACCEPTED" and all_live
        ),
        "creator_handoff": None,
        "fixture_or_nonlive_input": not all_live,
        "growth_r30": {
            "repository": "foto6/video3",
            "producer_sha": _git_sha(growth_sha, "growth_sha"),
            "ci_run_id": _positive(growth_ci_run_id, "growth_ci_run_id"),
            "starting_r29_sha": R29_SHA,
            "authority_profile_digest": authority_digest(profile),
            "aggregation_policy_digest": policy_digest(policy),
        },
        "authorities": {
            "growth_r29_sha": R29_SHA,
            "media_r24_sha": MEDIA_R24_SHA,
            "bridge_r34_sha": BRIDGE_R34_SHA,
            "inner_creator_envelope_contract": INNER_ENVELOPE_VERSION,
        },
        "evidence_boundary": {
            "model_consensus": True,
            "model_consensus_is_human_ground_truth": False,
            "human_ground_truth": False,
            "human_rating_evidence": False,
            "human_parity_inferred": False,
            "browser_mutation": False,
            "model_call": False,
            "provider_publish": False,
        },
    }
    result["consensus_id"] = "gr30c1:" + sha256_json(
        {
            "package_digest": media["package_digest"],
            "review_round": media["review_round"],
            "reviewers": [
                {
                    "conversation_id": row["conversation_id"],
                    "capture_digest": row["capture_digest"],
                    "response_digest": row["response_digest"],
                }
                for row in ordered
            ],
        }
    )
    if result["creator_executable_handoff_emitted"]:
        result["creator_handoff"] = _creator_handoff(
            consensus=result,
            inner=selected_inner,
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
            profile=profile,
            policy=policy,
        )
    material = copy.deepcopy(result)
    material["consensus_digest"] = ""
    result["consensus_digest"] = sha256_json(material)
    return _clone(result)


def _creator_handoff(
    *,
    consensus: Mapping[str, Any],
    inner: Mapping[str, Any],
    growth_sha: str,
    growth_ci_run_id: int,
    profile: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    handoff = {
        "contract_version": CREATOR_HANDOFF_VERSION,
        "handoff_id": "",
        "handoff_digest": "",
        "state": "CONSENSUS_ACCEPTED",
        "selected_candidate_id": consensus["selected_candidate_id"],
        "selected_blind_label": consensus["winner_blind_label"],
        "inner_contract": INNER_ENVELOPE_VERSION,
        "inner_envelope": _clone(inner),
        "inner_envelope_digest": inner["envelope_digest"],
        "consensus_id": consensus["consensus_id"],
        "review_round": consensus["media"]["review_round"],
        "package_digest": consensus["media"]["package_digest"],
        "growth_r30": {
            "repository": "foto6/video3",
            "producer_sha": _git_sha(growth_sha, "growth_sha"),
            "ci_run_id": _positive(growth_ci_run_id, "growth_ci_run_id"),
            "authority_profile_digest": authority_digest(profile),
            "aggregation_policy_digest": policy_digest(policy),
        },
        "evidence_boundary": {
            "model_consensus": True,
            "human_ground_truth": False,
            "human_rating_evidence": False,
            "human_parity_inferred": False,
            "provider_publish": False,
        },
    }
    handoff["handoff_id"] = "gr30ch1:" + sha256_json(
        {
            "consensus_id": consensus["consensus_id"],
            "inner_envelope_digest": inner["envelope_digest"],
            "selected_candidate_id": consensus["selected_candidate_id"],
        }
    )
    material = copy.deepcopy(handoff)
    material["handoff_digest"] = ""
    handoff["handoff_digest"] = sha256_json(material)
    return _clone(handoff)


class ConsensusLedger:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory).resolve()
        self.path = self.directory / "growth-r30-consensus-ledger.json"
        self.reviewers: dict[str, str] = {}
        self.sets: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            raw = _load(self.path)
            if not isinstance(raw, Mapping) or set(raw) != {
                "version", "reviewers", "sets"
            }:
                raise ReviewConflict("R30 ledger fields invalid")
            if raw["version"] != LEDGER_VERSION:
                raise ReviewConflict("R30 ledger version drift")
            self.reviewers = dict(raw["reviewers"])
            self.sets = dict(raw["sets"])

    def _persist(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(
                {
                    "version": LEDGER_VERSION,
                    "reviewers": self.reviewers,
                    "sets": self.sets,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temp, self.path)

    def apply(
        self,
        *,
        reviews: Sequence[Mapping[str, Any]],
        result: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        ordered = sorted(reviews, key=lambda row: row["conversation_id"])
        fingerprints = []
        for row in ordered:
            identity = (
                result["media"]["session_id"]
                + "\n"
                + str(result["media"]["review_round"])
                + "\n"
                + row["conversation_id"]
            )
            fingerprint = sha256_json(
                {
                    "capture_digest": row["capture_digest"],
                    "response_digest": row["response_digest"],
                    "r29_round_result_file_sha256":
                        row["r29_round_result_file_sha256"],
                    "response_file_sha256": row["response_file_sha256"],
                }
            )
            prior = self.reviewers.get(identity)
            if prior is not None and prior != fingerprint:
                raise ReviewConflict(
                    "same reviewer/session identity changed capture or response bytes"
                )
            fingerprints.append(
                {
                    "identity": identity,
                    "fingerprint": fingerprint,
                }
            )
        set_key = sha256_json(
            {
                "session_id": result["media"]["session_id"],
                "package_digest": result["media"]["package_digest"],
                "review_round": result["media"]["review_round"],
                "conversation_ids": [row["conversation_id"] for row in ordered],
            }
        )
        set_fingerprint = sha256_json(fingerprints)
        prior_set = self.sets.get(set_key)
        if prior_set is not None:
            if prior_set["fingerprint"] != set_fingerprint:
                raise ReviewConflict("same consensus reviewer set changed bytes")
            if prior_set["result"]["consensus_digest"] != result["consensus_digest"]:
                raise ReviewConflict("exact consensus replay changed result bytes")
            return _clone(prior_set["result"]), False
        for row in fingerprints:
            self.reviewers[row["identity"]] = row["fingerprint"]
        self.sets[set_key] = {
            "fingerprint": set_fingerprint,
            "result": _clone(result),
        }
        self._persist()
        return _clone(result), True


def _review_paths(directory: Path) -> list[Path]:
    root = Path(directory).resolve()
    paths = sorted(root.glob("review-*.json"))
    if len(paths) != 3:
        raise IndependenceError(
            "reviews directory must contain exactly three review-*.json manifests"
        )
    return paths


def consensus_directory(
    *,
    media_dir: Path,
    reviews_dir: Path,
    profile: Mapping[str, Any],
    policy: Mapping[str, Any],
    ledger_dir: Path,
    output_dir: Path,
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    media = validate_media_r24(media_dir, profile=profile)
    reviews = [
        load_review(path, media=media, profile=profile)
        for path in _review_paths(reviews_dir)
    ]
    result = aggregate_reviews(
        reviews,
        media=media,
        policy=policy,
        profile=profile,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
    )
    ledger = ConsensusLedger(ledger_dir)
    canonical, _ = ledger.apply(reviews=reviews, result=result)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "consensus-result.json").write_text(
        json.dumps(canonical, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = {
        "report_version": REPORT_VERSION,
        "state": canonical["state"],
        "consensus_digest": canonical["consensus_digest"],
        "disagreement_score": canonical["disagreement_score"],
        "creator_executable_handoff_emitted":
            canonical["creator_executable_handoff_emitted"],
        "fixture_or_nonlive_input": canonical["fixture_or_nonlive_input"],
        "rejection_reasons": canonical["audit"]["rejection_reasons"],
        "model_consensus_is_human_ground_truth": False,
        "human_ground_truth": False,
        "human_rating_evidence": False,
        "human_parity_inferred": False,
        "browser_mutation": False,
        "model_call": False,
        "provider_publish": False,
    }
    report["report_digest"] = sha256_json(report)
    (out / "readiness.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return _clone(canonical)


def _mutate_json(path: Path, fn) -> None:
    value = _load(path)
    fn(value)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _refresh_review_hashes(review_path: Path) -> None:
    review = _load(review_path)
    root = review_path.parent
    response = root / review["bridge_r34"]["response_file"]
    review["bridge_r34"]["response_file_sha256"] = _file_sha(response)
    review["bridge_r34"]["response_digest"] = _file_sha(response)
    round_path = root / review["r29"]["round_result_file"]
    round_result = _load(round_path)
    round_result["response_digest"] = review["bridge_r34"]["response_digest"]
    material = copy.deepcopy(round_result)
    material["round_result_digest"] = ""
    round_result["round_result_digest"] = sha256_json(material)
    round_path.write_text(
        json.dumps(round_result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    review["r29"]["round_result_file_sha256"] = _file_sha(round_path)
    review_path.write_text(
        json.dumps(review, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def rehearse_fixtures(
    *,
    media_dir: Path,
    accepted_reviews_dir: Path,
    profile: Mapping[str, Any],
    policy: Mapping[str, Any],
    output_dir: Path,
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    cases: dict[str, Any] = {}

    def run_case(name: str, mutator=None, expected=None) -> None:
        with tempfile.TemporaryDirectory() as temp:
            copied = Path(temp) / "reviews"
            shutil.copytree(accepted_reviews_dir, copied)
            if mutator:
                mutator(copied)
            try:
                result = consensus_directory(
                    media_dir=media_dir,
                    reviews_dir=copied,
                    profile=profile,
                    policy=policy,
                    ledger_dir=Path(temp) / "ledger",
                    output_dir=Path(temp) / "out",
                    growth_sha=growth_sha,
                    growth_ci_run_id=growth_ci_run_id,
                )
                cases[name] = {
                    "state": result["state"],
                    "consensus_digest": result["consensus_digest"],
                    "reasons": result["audit"]["rejection_reasons"],
                    "creator_executable_handoff_emitted":
                        result["creator_executable_handoff_emitted"],
                }
            except R30Error as exc:
                cases[name] = {
                    "state": "BLOCKED_INVALID_EVIDENCE",
                    "reason": type(exc).__name__,
                    "detail": str(exc),
                    "creator_executable_handoff_emitted": False,
                }
            if expected and cases[name]["state"] != expected:
                raise AssertionError(
                    f"fixture {name}: expected {expected}, got {cases[name]}"
                )

    run_case("accepted_unanimous", expected="CONSENSUS_ACCEPTED")

    def high_disagreement(root: Path) -> None:
        response = root / "responses" / "reviewer-c.json"
        _mutate_json(
            response,
            lambda x: (
                x.__setitem__("winner", "B"),
                x.__setitem__("confidence", 0.99),
            ),
        )
        _refresh_review_hashes(root / "review-c.json")
    run_case(
        "two_one_high_disagreement",
        high_disagreement,
        "HUMAN_REVIEW_REQUIRED",
    )

    def low_confidence(root: Path) -> None:
        for name in ("a", "b", "c"):
            response = root / "responses" / f"reviewer-{name}.json"
            _mutate_json(response, lambda x: x.__setitem__("confidence", 0.55))
            _refresh_review_hashes(root / f"review-{name}.json")
    run_case("unanimous_low_confidence", low_confidence, "HUMAN_REVIEW_REQUIRED")

    def contradictory(root: Path) -> None:
        response = root / "responses" / "reviewer-c.json"
        def change(x):
            x["defects"][0]["start_ms"] = 1000
            x["defects"][0]["end_ms"] = 1600
            x["defects"][0]["direction"] = "increase"
            x["defects"][0]["requested_edit"] = "Increase the same pacing interval."
        _mutate_json(response, change)
        _refresh_review_hashes(root / "review-c.json")
    run_case(
        "contradictory_directives",
        contradictory,
        "HUMAN_REVIEW_REQUIRED",
    )

    def duplicate_conversation(root: Path) -> None:
        b = root / "review-b.json"
        value = _load(b)
        a = _load(root / "review-a.json")
        value["bridge_r34"]["conversation_id"] = a["bridge_r34"]["conversation_id"]
        value["bridge_r34"]["provider_conversation_id"] = a["bridge_r34"]["conversation_id"]
        value["bridge_r34"]["canonical_url"] = a["bridge_r34"]["canonical_url"]
        round_path = root / value["r29"]["round_result_file"]
        result = _load(round_path)
        result["conversation_id"] = a["bridge_r34"]["conversation_id"]
        material = copy.deepcopy(result)
        material["round_result_digest"] = ""
        result["round_result_digest"] = sha256_json(material)
        round_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        value["r29"]["round_result_file_sha256"] = _file_sha(round_path)
        b.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    run_case(
        "duplicate_conversation",
        duplicate_conversation,
        "BLOCKED_INVALID_EVIDENCE",
    )

    report = {
        "contract_version": "growth.consensus_rehearsal.r30.v1",
        "cases": cases,
        "accepted_case": "accepted_unanimous",
        "rejected_cases": sorted(
            name for name, row in cases.items()
            if row["state"] != "CONSENSUS_ACCEPTED"
        ),
        "model_consensus_is_human_ground_truth": False,
        "human_ground_truth": False,
        "human_rating_evidence": False,
        "human_parity_inferred": False,
        "browser_mutation": False,
        "model_call": False,
        "provider_publish": False,
    }
    report["report_digest"] = sha256_json(report)
    (output / "rehearsal.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return _clone(report)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="growth-consensus-review-r30")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("consensus", "rehearse-fixtures"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--media-dir", required=True)
        cmd.add_argument("--reviews-dir", required=True)
        cmd.add_argument("--authority-profile", required=True)
        cmd.add_argument("--policy", required=True)
        cmd.add_argument("--out-dir", required=True)
        cmd.add_argument("--growth-sha", required=True)
        cmd.add_argument("--growth-ci-run-id", type=int, required=True)
        if name == "consensus":
            cmd.add_argument("--ledger-dir", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    profile = _load(Path(args.authority_profile))
    policy = _load(Path(args.policy))
    try:
        if args.command == "consensus":
            result = consensus_directory(
                media_dir=Path(args.media_dir),
                reviews_dir=Path(args.reviews_dir),
                profile=profile,
                policy=policy,
                ledger_dir=Path(args.ledger_dir),
                output_dir=Path(args.out_dir),
                growth_sha=args.growth_sha,
                growth_ci_run_id=args.growth_ci_run_id,
            )
        else:
            result = rehearse_fixtures(
                media_dir=Path(args.media_dir),
                accepted_reviews_dir=Path(args.reviews_dir),
                profile=profile,
                policy=policy,
                output_dir=Path(args.out_dir),
                growth_sha=args.growth_sha,
                growth_ci_run_id=args.growth_ci_run_id,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except R30Error as exc:
        report = {
            "report_version": REPORT_VERSION,
            "state": "BLOCKED_INVALID_EVIDENCE",
            "reason": type(exc).__name__,
            "detail": str(exc),
            "creator_executable_handoff_emitted": False,
            "model_consensus_is_human_ground_truth": False,
            "human_ground_truth": False,
            "human_rating_evidence": False,
            "human_parity_inferred": False,
            "browser_mutation": False,
            "model_call": False,
            "provider_publish": False,
        }
        report["report_digest"] = sha256_json(report)
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "readiness.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
