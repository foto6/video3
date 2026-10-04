from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import canonical_json, sha256_json
from . import multiround_session_ingest_r28 as r28

AUTHORITY_VERSION = "growth.exact_session_authorities.r29.v1"
ROUND_RESULT_VERSION = "growth.exact_session_round_result.r29.v1"
SELECTED_RESULT_VERSION = "growth.creator_r30_selected_review_result.r29.v1"
LEDGER_VERSION = "growth.exact_session_ledger.r29.v1"
REPORT_VERSION = "growth.exact_session_ingest.r29.readiness.v1"

R28_SHA = "629a2b9ddf59b84eee4e87b257c161dad42831dc"
R28_CI = 37006476121
MEDIA_R23_SHA = "78c6982a91d7e3e8c037cd9ce740ee077babdccc"
MEDIA_R23_CI = 37007419237
MEDIA_R23_ARTIFACT_ID = 11226183002
MEDIA_R23_ARTIFACT_DIGEST = (
    "sha256:5170ada97f8c86421f4bee34c97fbfa5701bef74ef406f18889a1a790ae3ac66"
)
BRIDGE_R32_SHA = "805bf628d3d2844549b54db1112736fae0200fc7"
BRIDGE_R32_CI = 37006524677
CREATOR_R30_SHA = "50c17852a910c57f0894dcdb356d4d4923edb62b"
CREATOR_R30_CI = 37006988818

MEDIA_R23_PACKAGE = "media.review_session_package.r23.v1.json"
MEDIA_R23_REQUEST = "media.review_session_request.r23.v1.json"
MEDIA_R23_EVIDENCE = "media.review_session_package.r23.evidence.json"
R32_ROUND_CONTRACT = "bridge.r32_live_review_session_round_result.v1"
R32_FIXTURE_CONTRACT = "bridge.r32_multiround_fake_cdp_rehearsal.v1"


class R29Error(ValueError):
    pass


class AuthorityDrift(R29Error):
    pass


class PackageDrift(R29Error):
    pass


class SessionConflict(R29Error):
    pass


class RoundSequenceError(R29Error):
    pass


class NonLiveCapture(R29Error):
    pass


class MalformedModelResponse(R29Error):
    pass


class ReconciliationRequired(R29Error):
    pass


def _clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise R29Error(f"{field} must be lowercase SHA-256")
    return value


def _sha1(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise R29Error(f"{field} must be lowercase Git SHA")
    return value


def _positive(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise R29Error(f"{field} must be positive integer")
    return value


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise R29Error(f"{field} must be non-empty string")
    return value


def _artifact_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise AuthorityDrift(f"{field} must use sha256:")
    _sha(value[7:], field)
    return value


def _r28_profile() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(
        root
        / "conformance"
        / "growth.multiround_session_ingest.r28.v1"
        / "authority-profiles.json"
    )


def validate_authority_profile(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "contract_version",
        "growth_r28",
        "media_r23",
        "bridge_r32",
        "creator_r30",
        "future_authorities",
        "evidence_boundary",
    }:
        raise AuthorityDrift("R29 authority profile fields invalid")
    if value["contract_version"] != AUTHORITY_VERSION:
        raise AuthorityDrift("R29 authority contract mismatch")

    growth = value["growth_r28"]
    if (
        growth.get("repository") != "foto6/video3"
        or growth.get("producer_sha") != R28_SHA
        or growth.get("ci_run_id") != R28_CI
        or growth.get("session_contract")
        != "growth.multiround_session_ingest.r28.v1"
        or growth.get("blobs")
        != {
            "implementation": "8d75e086c5db161124d487a885b95999081e2521",
            "authority_profile": "5799d71528ec4e94f922b43cfb2db70b54e00206",
            "contract": "d7ec52f25fa1ff12e633f2ac3ba042265f93fe51",
            "round_result_schema": "8eb405c42a4fea6a354cf7e3afcbd1774b542e75",
            "tests": "0d38e864f0d8e1d319c09f442c938cc51a930751",
            "workflow": "fb0ec3c321ad86fd403e601758ee14063c239f41",
        }
    ):
        raise AuthorityDrift("exact Growth R28 authority drift")

    media = value["media_r23"]
    if (
        media.get("repository") != "foto6/video2"
        or media.get("producer_sha") != MEDIA_R23_SHA
        or media.get("ci_run_id") != MEDIA_R23_CI
        or media.get("contract") != "media.review_session_package.r23.v1"
        or media.get("request_contract") != "media.review_session_request.r23.v1"
        or media.get("evidence_contract")
        != "media.review_session_package.r23.evidence.v1"
        or media.get("state") != "REVIEW_SESSION_PACKAGE_READY"
        or media.get("artifact")
        != {
            "id": MEDIA_R23_ARTIFACT_ID,
            "name": "media-r23-review-session-package",
            "digest": MEDIA_R23_ARTIFACT_DIGEST,
        }
    ):
        raise AuthorityDrift("exact Media R23 producer/contract authority drift")
    expected_media_blobs = {
        "contract": "63f863493054dea4ad0e2ebf3a996137602f8940",
        "manifest": "09564e2d530a7315f58856c55b33bb52534e05c1",
        "schema": "92470d29b4a524ee6d797194f023a20c5b0dbaa6",
        "implementation": "442ca6a46cbf107d29e6cd320fbb1a4cb38951b0",
        "exporter": "4799338cf58edee89bebd773eb544719cdb7f56d",
        "demo": "7390cf348cc8d522a895fbb355a82c4bf030b58d",
        "tests": "dea1f51fbd0719614f23e8562c6e9a4f8c0626f9",
        "package": "0da628a9f8a88aa5d8828203ac7fbe296f98c832",
        "workflow": "398b9b8a7a7a40161fd50f4c831531a848195c9a",
    }
    if media.get("blobs") != expected_media_blobs:
        raise AuthorityDrift("Media R23 contract/schema/implementation blob drift")
    for key, digest in expected_media_blobs.items():
        _sha1(digest, f"media_r23.blobs.{key}")
    _artifact_digest(media["artifact"]["digest"], "media_r23.artifact.digest")
    if media.get("nested_r21") != {
        "producer_sha": r28.r27.MEDIA_R21_SHA,
        "ci_run_id": r28.r27.MEDIA_R21_CI,
        "contract": "media.review_round_bundle.r21.v1",
        "implementation_blob": "c6f556b8a177b6182d787356625094cdcad5a58e",
    }:
        raise AuthorityDrift("Media R23 nested R21 authority drift")
    if media.get("nested_r22") != {
        "producer_sha": r28.MEDIA_R22_SHA,
        "ci_run_id": r28.MEDIA_R22_CI,
        "artifact_id": r28.MEDIA_R22_ARTIFACT_ID,
        "artifact_digest": r28.MEDIA_R22_ARTIFACT_DIGEST,
        "implementation_blob": "31845333a6919364d81a7c2bc52aad1cb3ae82ed",
    }:
        raise AuthorityDrift("Media R23 nested R22 authority drift")
    rounds = media.get("exact_ci_rounds")
    if not isinstance(rounds, Mapping) or set(rounds) != {"0", "1", "2"}:
        raise AuthorityDrift("Media R23 exact CI round freeze invalid")
    for round_key, row in rounds.items():
        if not isinstance(row, Mapping):
            raise AuthorityDrift("Media R23 exact CI round row invalid")
        for key in row:
            if key.endswith("sha256") or key.endswith("digest") or key == "session_identity":
                _sha(row[key], f"media_r23.exact_ci_rounds.{round_key}.{key}")

    bridge = value["bridge_r32"]
    if (
        bridge.get("repository") != "foto6/WebAIBridge"
        or bridge.get("producer_sha") != BRIDGE_R32_SHA
        or bridge.get("ci_run_id") != BRIDGE_R32_CI
        or bridge.get("request_contract")
        != "bridge.r32_multiround_live_review_session_request.v1"
        or bridge.get("journal_contract")
        != "bridge.r32_multiround_live_review_session_journal.v1"
        or bridge.get("round_result_contract") != R32_ROUND_CONTRACT
        or bridge.get("result_contract")
        != "bridge.r32_multiround_live_review_session_result.v1"
        or bridge.get("ci_live_session_evidence") is not False
    ):
        raise AuthorityDrift("exact Bridge R32 authority drift")
    expected_bridge_blobs = {
        "request_schema": "3d6943806d7542eddb59813459e34136d5187050",
        "journal_schema": "3b39faa9e8a1de5baf297f85640d36907aa06db1",
        "round_result_schema": "bff0985b243650ad5f40954234646da9c2d9c074",
        "result_schema": "152fd55f783a0798eab040bfb4fc84b2d5e01d01",
        "session_implementation": "91cf49ce6e856c5d975a4c7c9daef88b13165fa1",
        "driver_implementation": "df2b27b1bc2240fc2ba651404b13ca202d0f5ed7",
        "powershell_operator": "934e0b5cdd952ca24ed4cfd987c175b1f891cd3f",
        "readiness": "386922a2625e0ab7b5a8aa957cf5e4a8df494154",
        "rehearsal": "6d9af437956496b79f249f970eb1a838f9dad53c",
        "contract_tests": "8643f40c0c30fab8bf78443f9d4c57d2bed77cab",
        "session_tests": "c5549b46f7820b652cd878edc9669dccc5e1f8ea",
        "fixture": "e052c0ba8ea26cec7a26d8ab9340d744e5d69fbe",
    }
    if bridge.get("blobs") != expected_bridge_blobs:
        raise AuthorityDrift("Bridge R32 schema/implementation blob drift")
    expected_artifacts = [
        {
            "platform": "ubuntu-latest",
            "id": 11225129739,
            "digest":
                "sha256:e0fea734df884bdaf39e1a5f2ed961a39bee89e2d219c744720da49581d0d5b4",
        },
        {
            "platform": "windows-latest",
            "id": 11225962270,
            "digest":
                "sha256:189358b73d7a95a934219bb226451579fb402bd7ca615b91667f3e817b045e2d",
        },
    ]
    if bridge.get("artifacts") != expected_artifacts:
        raise AuthorityDrift("Bridge R32 Actions artifact authority drift")
    for row in bridge["artifacts"]:
        _artifact_digest(row["digest"], "bridge_r32.artifact.digest")

    creator = value["creator_r30"]
    if (
        creator.get("repository") != "foto6/video1"
        or creator.get("producer_sha") != CREATOR_R30_SHA
        or creator.get("ci_run_id") != CREATOR_R30_CI
        or creator.get("manifest_contract")
        != "creator.coordinator_continuation.r30.v1"
        or creator.get("result_contract")
        != "creator.coordinator_continuation_result.r30.v1"
        or creator.get("journal_contract")
        != "creator.coordinator_continuation_journal.r30.v1"
        or creator.get("canonical_inner_envelope_contract")
        != r28.r27.CREATOR_ENVELOPE_VERSION
        or creator.get("blobs")
        != {
            "manifest": "fc50ec52706ee9861b73f92dbe2efbcc965e6a93",
            "schema": "39afedb5fc89e179f7fbd4ea56a652800304b77b",
            "implementation": "1d8869ef479549347b2ce8f7c6944fdeeba6e7e6",
            "workflow": "ebf7301db4b6e764b198b50c2784d80cacfdbb25",
        }
        or creator.get("artifact")
        != {
            "id": 11226825363,
            "name":
                "creator-r30-coordinator-source-ready-50c17852a910c57f0894dcdb356d4d4923edb62b",
            "digest":
                "sha256:50d04e86ddf29ecf7553c025bd9a6e3717bc861ddef0448fdafe7b8b1536bdb0",
        }
    ):
        raise AuthorityDrift("exact Creator R30 authority drift")
    _artifact_digest(creator["artifact"]["digest"], "creator_r30.artifact.digest")

    future = value["future_authorities"]
    if (
        future.get("media_r24_exact_green_available") is not False
        or future.get("bridge_r33_exact_green_available") is not False
    ):
        raise AuthorityDrift("unfrozen future authority must remain disabled")
    boundary = value["evidence_boundary"]
    if boundary != {
        "fixture_or_fake_cdp_live": False,
        "human_ground_truth": False,
        "human_rating_evidence": False,
        "human_parity_inferred": False,
        "browser_mutation": False,
        "provider_mutation": False,
        "merge": False,
    }:
        raise AuthorityDrift("R29 evidence boundary drift")
    return _clone(value)


def authority_profile_digest(value: Mapping[str, Any]) -> str:
    return sha256_json(validate_authority_profile(value))


def _candidate_summary(candidate: Mapping[str, Any]) -> dict[str, Any]:
    application = candidate.get("editorialApplication")
    return {
        "candidateId": candidate["candidateId"],
        "roundNumber": candidate["roundNumber"],
        "render": {
            "sha256": candidate["render"]["sha256"],
            "size": candidate["render"]["size"],
        },
        "renderExport": {
            "digest": candidate["renderExport"]["digest"],
            "fileSha256": candidate["renderExport"]["fileSha256"],
        },
        "renderProducerSha": candidate["renderProducerSha"],
        "editorialApplication": (
            None
            if application is None
            else {
                "digest": application["digest"],
                "fileSha256": application["fileSha256"],
            }
        ),
    }


def _session_identity_from_request(request: Mapping[str, Any]) -> str:
    mode = request.get("mode")
    body = {
        "sessionId": request["sessionId"],
        "reviewRound": request["reviewRound"],
        "mode": mode,
        "source": request["source"],
        "briefLineageDigest": request["briefLineageDigest"],
        "growthSelectedEnvelopeDigest": request["growthSelectedEnvelopeDigest"],
        "growthHandoffDigest": request["growthHandoffDigest"],
    }
    if mode == "initial":
        body["baseline"] = _candidate_summary(request["initial"]["left"])
        body["challenger"] = _candidate_summary(request["initial"]["right"])
    elif mode == "targeted_reedit":
        body["baseline"] = {
            **_candidate_summary(request["baseline"]["candidate"]),
            "priorReviewPackageDigest": request["baseline"][
                "priorReviewPackageDigest"
            ],
            "priorSealedMappingDigest": request["baseline"][
                "priorSealedMappingDigest"
            ],
        }
        body["challenger"] = _candidate_summary(request["challenger"])
    else:
        raise PackageDrift("Media R23 request mode invalid")
    return sha256_json(body)


def _r32_package_identity(
    *,
    operator_dir: Path,
    nested: Mapping[str, Any],
) -> dict[str, Any]:
    root = Path(operator_dir)
    manifest = _load(root / r28.OPERATOR_MANIFEST)
    source = manifest["sourceLineage"]["source"]
    attachments = [
        {
            "blindLabel": row["blind_label"],
            "name": row["path"],
            "size": row["size"],
            "sha256": row["sha256"],
            "mime": "video/mp4",
        }
        for row in nested["normalized_media_package"]["authority"]["attachments"]
    ]
    identity = {
        "family": "R22",
        "authority": {
            "repository": "foto6/video2",
            "producerSha": r28.MEDIA_R22_SHA,
            "ciRunId": r28.MEDIA_R22_CI,
            "ciConclusion": "success",
            "operatorContract": "media.live_review_operator_manifest.r22.v1",
        },
        "operatorManifestSha256": nested["operator_manifest_sha256"],
        "authorityProfileSha256": nested["authority_profile_sha256"],
        "archiveSha256": nested["archive_sha256"],
        "packageManifestSha256": nested["package_manifest_sha256"],
        "packageDigest": nested["package_digest"],
        "promptDigest": nested["prompt_digest"],
        "sealedMappingDigest": nested["sealed_mapping_digest"],
        "round": nested["review_round"],
        "sourceFingerprint": sha256_json(source),
        "briefLineageDigest": nested["brief_lineage_digest"],
        "roundLineageFingerprint": sha256_json(
            manifest["sourceLineage"].get("roundLineage")
        ),
        "attachments": attachments,
    }
    return {
        **identity,
        "sessionPackageDigest": sha256_json(identity),
    }


def validate_media_session_dir(
    session_dir: Path,
    *,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    profile = validate_authority_profile(profile)
    root = Path(session_dir).resolve()
    if not root.is_dir():
        raise PackageDrift("Media R23/R24 session directory missing")
    package_path = root / MEDIA_R23_PACKAGE
    request_path = root / MEDIA_R23_REQUEST
    evidence_path = root / MEDIA_R23_EVIDENCE
    operator_dir = root / "operator"
    if not package_path.is_file():
        if any(root.glob("media.review_session_package.r24*.json")):
            raise AuthorityDrift(
                "Media R24 package present but no exact R24 authority is frozen"
            )
        raise PackageDrift("Media R23 session package missing")
    if not request_path.is_file() or not evidence_path.is_file():
        raise PackageDrift("Media R23 request/evidence files missing")

    package = _load(package_path)
    request = _load(request_path)
    evidence = _load(evidence_path)
    media = profile["media_r23"]
    if (
        package.get("contractVersion") != media["contract"]
        or package.get("state") != media["state"]
        or package.get("producer")
        != {
            "repository": "foto6/video2",
            "sha": MEDIA_R23_SHA,
            "ciRunId": MEDIA_R23_CI,
        }
    ):
        raise AuthorityDrift("Media R23 exact producer/contract drift")
    if request.get("contractVersion") != media["request_contract"]:
        raise PackageDrift("Media R23 request contract drift")
    if (
        evidence.get("evidenceVersion") != media["evidence_contract"]
        or evidence.get("producer") != package["producer"]
        or evidence.get("nestedR22Verified") is not True
        or evidence.get("modelReviewPerformed") is not False
        or evidence.get("liveModelReviewed") is not False
        or evidence.get("providerPublish") is not False
        or evidence.get("humanQuality") is not False
    ):
        raise PackageDrift("Media R23 evidence boundary/provenance drift")

    package_sha = _file_sha(package_path)
    request_sha = _file_sha(request_path)
    evidence_sha = _file_sha(evidence_path)
    if (
        evidence.get("sessionPackageSha256") != package_sha
        or evidence.get("requestFileSha256") != request_sha
    ):
        raise PackageDrift("Media R23 package/request byte identity drift")

    if package.get("sessionIdentity") != _session_identity_from_request(request):
        raise PackageDrift("Media R23 session identity drift")
    for key in (
        "sessionId",
        "mode",
        "reviewRound",
        "source",
        "briefLineageDigest",
        "growthSelectedEnvelopeDigest",
        "growthHandoffDigest",
    ):
        if package.get(key) != request.get(key):
            raise PackageDrift(f"Media R23 request/package drift: {key}")
    if package["reviewRound"] not in (0, 1, 2):
        raise RoundSequenceError("Media R23 review round outside 0..2")

    r21 = package.get("r21")
    r22 = package.get("r22")
    if not isinstance(r21, Mapping) or not isinstance(r22, Mapping):
        raise PackageDrift("Media R23 nested R21/R22 lineage missing")
    if r21.get("authority") != {
        "repository": "foto6/video2",
        "producerSha": r28.r27.MEDIA_R21_SHA,
        "ciRunId": r28.r27.MEDIA_R21_CI,
        "contractVersion": "media.review_round_bundle.r21.v1",
        "implementationBlob": "c6f556b8a177b6182d787356625094cdcad5a58e",
    }:
        raise AuthorityDrift("Media R23 nested R21 authority drift")
    if r22.get("authority") != {
        "repository": "foto6/video2",
        "producerSha": r28.MEDIA_R22_SHA,
        "ciRunId": r28.MEDIA_R22_CI,
        "ciConclusion": "success",
        "artifactId": r28.MEDIA_R22_ARTIFACT_ID,
        "artifactName": "media-r22-live-review-operator-bundle",
        "artifactDigest": r28.MEDIA_R22_ARTIFACT_DIGEST,
        "implementationBlob": "31845333a6919364d81a7c2bc52aad1cb3ae82ed",
        "materializerBlob": "ade9e7181ecccf8e78e7dd966240618c872c9b26",
        "verifierBlob": "86b0a53eed6959af805fd0c492620eb037071e25",
        "extractorBlob": "dc45ee753480fc62249bd69ca209b994d680d08a",
        "contractBlob": "d6e494950b5ab54383733db69912c2384cce188f",
        "operatorManifestSchemaBlob": "7e78d7be9fffbbda6ee86cf39c4db8e59a635a3d",
    }:
        raise AuthorityDrift("Media R23 nested R22 authority drift")

    override = {
        "operator_manifest_sha256": r22["operatorManifestSha256"],
        "archive_sha256": r22["archiveSha256"],
        "archive_size": r22["archiveSize"],
        "authority_profile_sha256": _file_sha(
            operator_dir / r28.PAYLOAD_DIR / r28.AUTHORITY_PROFILE
        ),
        "package_manifest_sha256": r22["packageManifestSha256"],
        "r21_package_digest": r21["packageDigest"],
        "sealed_mapping_digest": package["sealedMappingDigest"],
        "prompt_digest": package["promptDigest"],
        "mode": package["mode"],
        "review_round": package["reviewRound"],
    }
    nested = r28.validate_operator_package(
        operator_dir,
        profile=_r28_profile(),
        exact_package_override=override,
    )

    source_expected = {
        "source_id": package["source"]["sourceId"],
        "sha256": package["source"]["sha256"],
        "size": package["source"]["size"],
    }
    if (
        nested["review_round"] != package["reviewRound"]
        or nested["mode"] != package["mode"]
        or nested["source"] != source_expected
        or nested["brief_lineage_digest"] != package["briefLineageDigest"]
        or nested["package_digest"] != r21["packageDigest"]
        or nested["sealed_mapping_digest"] != package["sealedMappingDigest"]
        or nested["prompt_digest"] != package["promptDigest"]
        or nested["archive_sha256"] != r22["archiveSha256"]
        or nested["operator_manifest_sha256"] != r22["operatorManifestSha256"]
        or nested["package_manifest_sha256"] != r22["packageManifestSha256"]
    ):
        raise PackageDrift("Media R23 wrapper does not bind exact nested R21/R22 bytes")

    expected_attachments = sorted(
        [
            {
                "blindLabel": row["blind_label"],
                "name": row["path"],
                "sha256": row["sha256"],
                "size": row["size"],
                "mime": "video/mp4",
            }
            for row in nested["normalized_media_package"]["authority"]["attachments"]
        ],
        key=lambda row: row["blindLabel"],
    )
    if sorted(package["attachments"], key=lambda row: row["blindLabel"]) != expected_attachments:
        raise PackageDrift("Media R23 wrapper attachment identity drift")

    mapped = {
        row["candidate_id"]: row
        for row in nested["normalized_media_package"]["mapping_by_label"].values()
    }
    for key in ("baseline", "challenger"):
        summary = package[key]
        candidate_id = summary["candidateId"]
        if candidate_id not in mapped:
            raise PackageDrift(f"Media R23 {key} candidate missing from sealed mapping")
        row = mapped[candidate_id]
        if (
            summary["roundNumber"] != row["candidate_round"]
            or summary["render"]["sha256"] != row["render"]["sha256"]
            or summary["render"]["size"] != row["render"]["size"]
            or summary["renderExport"]["digest"] != row["render_export"]["digest"]
            or summary["renderExport"]["fileSha256"]
            != row["render_export"]["file_sha256"]
        ):
            raise PackageDrift(f"Media R23 {key} candidate/render lineage drift")

    directory_files = evidence.get("operatorDirectoryFiles")
    if not isinstance(directory_files, list) or not directory_files:
        raise PackageDrift("Media R23 operator directory evidence missing")
    for row in directory_files:
        rel = _nonempty(row.get("path"), "operatorDirectoryFiles.path")
        path = (operator_dir / rel).resolve()
        try:
            path.relative_to(operator_dir.resolve())
        except ValueError as exc:
            raise PackageDrift("Media R23 operator evidence path escape") from exc
        if (
            not path.is_file()
            or _file_sha(path) != row.get("sha256")
            or path.stat().st_size != row.get("size")
        ):
            raise PackageDrift(f"Media R23 operator directory byte drift: {rel}")
    if evidence.get("operatorDirectoryDigest") != r22["directoryDigest"]:
        raise PackageDrift("Media R23 R22 directory digest drift")

    r32_identity = _r32_package_identity(operator_dir=operator_dir, nested=nested)
    demo = media["exact_ci_rounds"].get(str(package["reviewRound"]))
    if package.get("sessionId") == "r23-real-session" and demo is not None:
        observed_demo = {
            "session_package_sha256": package_sha,
            "evidence_sha256": evidence_sha,
            "session_identity": package["sessionIdentity"],
            "r21_package_digest": r21["packageDigest"],
            "r22_directory_digest": r22["directoryDigest"],
            "r22_archive_sha256": r22["archiveSha256"],
            "operator_manifest_sha256": r22["operatorManifestSha256"],
            "sealed_mapping_digest": package["sealedMappingDigest"],
            "r32_session_package_digest": r32_identity["sessionPackageDigest"],
        }
        if observed_demo != demo:
            raise PackageDrift("exact Media R23 CI rehearsal round identity drift")

    return _clone(
        {
            "session_dir": str(root),
            "operator_dir": str(operator_dir),
            "session_id": package["sessionId"],
            "session_identity": package["sessionIdentity"],
            "session_package_sha256": package_sha,
            "request_sha256": request_sha,
            "evidence_sha256": evidence_sha,
            "review_round": package["reviewRound"],
            "mode": package["mode"],
            "source": nested["source"],
            "brief_lineage_digest": package["briefLineageDigest"],
            "growth_selected_envelope_digest": package[
                "growthSelectedEnvelopeDigest"
            ],
            "growth_handoff_digest": package["growthHandoffDigest"],
            "nested": nested,
            "r32_identity": r32_identity,
            "media_r23": {
                "producer_sha": MEDIA_R23_SHA,
                "ci_run_id": MEDIA_R23_CI,
                "artifact_id": MEDIA_R23_ARTIFACT_ID,
                "artifact_digest": MEDIA_R23_ARTIFACT_DIGEST,
            },
        }
    )


def _conversation(value: Any) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        raise SessionConflict("Bridge R32 conversation missing")
    conversation_id = _nonempty(value.get("conversationId"), "conversationId")
    canonical = _nonempty(value.get("canonicalUrl"), "canonicalUrl")
    if canonical != f"https://chatgpt.com/c/{conversation_id}":
        raise SessionConflict("Bridge R32 canonical conversation drift")
    return conversation_id, canonical


def load_bridge_round(
    round_result_path: Path,
    *,
    media: Mapping[str, Any],
    profile: Mapping[str, Any],
    conversation_id: str,
    r31_result_path: Path | None = None,
) -> dict[str, Any]:
    validate_authority_profile(profile)
    path = Path(round_result_path).resolve()
    result = _load(path)
    if not isinstance(result, Mapping):
        raise NonLiveCapture("Bridge R32 round result must be object")
    contract = result.get("contract")
    if contract != R32_ROUND_CONTRACT:
        if isinstance(contract, str) and contract.startswith("bridge.r33"):
            raise AuthorityDrift(
                "Bridge R33 result supplied but no exact R33 authority is frozen"
            )
        raise AuthorityDrift("exact Bridge R32 round-result contract required")
    state = result.get("state")
    if state == "MALFORMED_MODEL_RESPONSE":
        raise MalformedModelResponse("Bridge R32 round is MALFORMED_MODEL_RESPONSE")
    if state == "RECONCILIATION_REQUIRED":
        raise ReconciliationRequired(
            "Bridge R32 round is RECONCILIATION_REQUIRED"
        )
    if state != "LIVE_REVIEW_PASS":
        raise NonLiveCapture(f"Bridge R32 round is not genuine live evidence: {state}")
    if result.get("fixtureSimulatedTerminalState") not in (None, ""):
        raise NonLiveCapture("fixture/simulated Bridge R32 round cannot become live")
    if (
        result.get("model_evidence") is not True
        or result.get("human_ground_truth") is not False
        or result.get("retryUploadAuthorized") is not False
        or result.get("retrySendAuthorized") is not False
    ):
        raise NonLiveCapture("Bridge R32 live evidence boundary invalid")

    expected_round = media["review_round"]
    if result.get("round") != expected_round:
        raise SessionConflict("Bridge R32 wrong review round")
    if result.get("sessionId") != media["session_id"]:
        raise SessionConflict("Bridge R32/Media R23 session ID mismatch")
    observed_conversation, canonical = _conversation(result.get("conversation"))
    if observed_conversation != conversation_id:
        raise SessionConflict("Bridge R32 dedicated conversation mismatch")
    r32 = media["r32_identity"]
    if (
        result.get("sourceFingerprint") != r32["sourceFingerprint"]
        or result.get("briefLineageDigest") != media["brief_lineage_digest"]
        or result.get("packageDigest") != r32["packageDigest"]
        or result.get("sessionPackageDigest") != r32["sessionPackageDigest"]
        or result.get("promptDigest") != r32["promptDigest"]
        or result.get("attachments") != r32["attachments"]
    ):
        raise PackageDrift("Bridge R32 exact package/prompt/attachment/session binding drift")
    request_id = _nonempty(result.get("requestId"), "r32.requestId")
    operation_id = _nonempty(result.get("operationId"), "r32.operationId")
    response_digest = _sha(result.get("responseDigest"), "r32.responseDigest")
    capture_digest = _sha(result.get("captureDigest"), "r32.captureDigest")

    if r31_result_path is None:
        r31_result_path = (
            path.parent
            / "round-sidecars"
            / f"round-{expected_round}"
            / "r31-live-result.json"
        )
    r31_result_path = Path(r31_result_path).resolve()
    if not r31_result_path.is_file():
        raise NonLiveCapture(
            "genuine R32 round requires exact sibling R31 terminal result/capture/response"
        )
    try:
        capture, r31_wrapper = r28.r27.load_bridge_capture_input(
            r31_result_path,
            profile=r28.r27.validate_authority_profile(
                _load(
                    Path(__file__).resolve().parents[1]
                    / "conformance"
                    / "growth.live_ingest_operator.r27.v1"
                    / "authority-profiles.json"
                )
            ),
        )
    except r28.r27.MalformedModelResponse as exc:
        raise MalformedModelResponse(str(exc)) from exc
    except r28.r27.NonLiveCapture as exc:
        raise NonLiveCapture(str(exc)) from exc

    if r31_wrapper is None:
        raise NonLiveCapture("Bridge R32 must bind an exact Bridge R31 result wrapper")
    if (
        r31_wrapper["request_id"] != request_id
        or r31_wrapper["operation_id"] != operation_id
        or r31_wrapper["response_digest"] != response_digest
        or r31_wrapper["capture_digest"] != capture_digest
        or capture.get("requestId") != request_id
        or capture.get("operationId") != operation_id
        or capture.get("responseDigest") != response_digest
        or capture.get("conversationId") != conversation_id
        or capture.get("conversationUrl") != canonical
    ):
        raise SessionConflict(
            "Bridge R32/R31 request/operation/response/capture/conversation digest drift"
        )

    validated = r28.validate_capture_against_package(
        capture,
        package=media["nested"],
    )
    return _clone(
        {
            "round_result": result,
            "round_result_sha256": _file_sha(path),
            "r31_result_path": str(r31_result_path),
            "r31_wrapper": r31_wrapper,
            "capture": capture,
            "parsed_capture": validated["parsed_capture"],
            "normalized_media_package": validated["normalized_media_package"],
            "request_id": request_id,
            "operation_id": operation_id,
            "response_digest": response_digest,
            "capture_digest": capture_digest,
        }
    )


def validate_r32_fixture_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("contract") != R32_FIXTURE_CONTRACT:
        raise R29Error("Bridge R32 fixture contract mismatch")
    if (
        value.get("state") != "SOURCE_READY"
        or value.get("liveSessionEvidence") is not False
        or value.get("realPromptSent") is not False
        or value.get("realUploadPerformed") is not False
        or value.get("browserMutationPerformedByR32") is not False
    ):
        raise NonLiveCapture("Bridge R32 fixture evidence boundary drift")
    rounds = value.get("rounds")
    if not isinstance(rounds, list) or [row.get("round") for row in rounds] != [0, 1, 2]:
        raise RoundSequenceError("Bridge R32 fixture must prove 0 -> 1 -> 2")
    for row in rounds:
        if (
            row.get("state") != "SOURCE_READY"
            or row.get("model_evidence") is not False
            or row.get("human_ground_truth") is not False
        ):
            raise NonLiveCapture("Bridge R32 fixture round cannot become live")
    return _clone(value)


def _session_identity(
    *,
    media: Mapping[str, Any],
    conversation_id: str,
    authority_digest: str,
) -> tuple[str, dict[str, Any]]:
    identity = {
        "media_session_id": media["session_id"],
        "source": media["source"],
        "brief_lineage_digest": media["brief_lineage_digest"],
        "conversation_id": conversation_id,
        "authority_profile_digest": authority_digest,
        "authorities": {
            "growth_r28_sha": R28_SHA,
            "media_r23_sha": MEDIA_R23_SHA,
            "bridge_r32_sha": BRIDGE_R32_SHA,
            "creator_r30_sha": CREATOR_R30_SHA,
        },
    }
    return "gr29s1:" + sha256_json(identity), identity


class SessionLedger:
    def __init__(
        self,
        directory: Path,
        *,
        session_id: str,
        identity: Mapping[str, Any],
    ) -> None:
        self.directory = Path(directory).resolve()
        self.path = self.directory / "growth-r29-session-ledger.json"
        self.session_id = session_id
        self.identity = _clone(identity)
        self.rounds: dict[str, Any] = {}
        self.requests: dict[str, str] = {}
        self.closed = False
        if self.path.exists():
            raw = _load(self.path)
            if not isinstance(raw, Mapping) or set(raw) != {
                "version",
                "session_id",
                "identity",
                "rounds",
                "requests",
                "closed",
            }:
                raise SessionConflict("R29 session ledger fields invalid")
            if raw["version"] != LEDGER_VERSION:
                raise SessionConflict("R29 session ledger version mismatch")
            if raw["session_id"] != session_id or raw["identity"] != self.identity:
                raise SessionConflict("R29 session identity/authority drift")
            self.rounds = dict(raw["rounds"])
            self.requests = dict(raw["requests"])
            self.closed = bool(raw["closed"])

    @property
    def next_round(self) -> int:
        if self.closed:
            return 3
        if not self.rounds:
            return 0
        return max(int(key) for key in self.rounds) + 1

    def apply(
        self,
        *,
        round_result: Mapping[str, Any],
        fingerprint: str,
    ) -> bool:
        round_no = round_result["review_round"]
        if round_no not in (0, 1, 2):
            raise RoundSequenceError("review round outside 0 -> 1 -> 2")
        key = str(round_no)
        request_key = (
            round_result["conversation_id"]
            + "\n"
            + round_result["request_id"]
            + "\n"
            + str(round_no)
        )
        prior = self.rounds.get(key)
        if prior is not None:
            if prior["fingerprint"] != fingerprint:
                raise SessionConflict(
                    "same session/round changed capture/response/package/mapping bytes"
                )
            if prior["round_result_digest"] != round_result["round_result_digest"]:
                raise SessionConflict("exact replay changed R29 round result")
            return False
        if self.closed:
            raise RoundSequenceError("terminal winner already closed R29 session")
        if round_no != self.next_round:
            raise RoundSequenceError(
                f"review round sequence violation: expected {self.next_round}, got {round_no}"
            )
        prior_request = self.requests.get(request_key)
        if prior_request is not None and prior_request != fingerprint:
            raise SessionConflict("same request identity changed bytes")
        self.rounds[key] = {
            "fingerprint": fingerprint,
            "round_result_digest": round_result["round_result_digest"],
            "package_digest": round_result["package_digest"],
            "mapping_digest": round_result["sealed_mapping_digest"],
            "response_digest": round_result["response_digest"],
            "capture_digest": round_result["capture_digest"],
            "selected_candidate_id": round_result["selected_candidate_id"],
            "round_result": _clone(round_result),
        }
        self.requests[request_key] = fingerprint
        if round_result["terminal_winner"]:
            self.closed = True
        self.directory.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(
                {
                    "version": LEDGER_VERSION,
                    "session_id": self.session_id,
                    "identity": self.identity,
                    "rounds": self.rounds,
                    "requests": self.requests,
                    "closed": self.closed,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temp, self.path)
        return True


def _selected_outer(
    *,
    selected: Mapping[str, Any] | None,
    growth_sha: str,
    growth_ci_run_id: int,
    authority_digest: str,
) -> dict[str, Any] | None:
    if selected is None:
        return None
    outer = {
        "contract_version": SELECTED_RESULT_VERSION,
        "result_id": "",
        "result_digest": "",
        "inner_contract": selected["contract_version"],
        "inner_result_digest": selected["result_digest"],
        "candidate_id": selected["candidate_id"],
        "candidate_round": selected["candidate_round"],
        "state": selected["state"],
        "render_sha256": selected["render_sha256"],
        "attachment_sha256": selected["attachment_sha256"],
        "handoff_digest": selected["handoff_digest"],
        "envelope_digest": selected["envelope_digest"],
        "review_round": selected["review_round"],
        "growth_r29": {
            "repository": "foto6/video3",
            "producer_sha": _sha1(growth_sha, "growth_sha"),
            "ci_run_id": _positive(growth_ci_run_id, "growth_ci_run_id"),
            "authority_profile_digest": authority_digest,
        },
        "creator_r30_authority": {
            "repository": "foto6/video1",
            "producer_sha": CREATOR_R30_SHA,
            "ci_run_id": CREATOR_R30_CI,
            "result_contract": "creator.coordinator_continuation_result.r30.v1",
        },
        "publish_authorized": False,
        "human_ground_truth": False,
        "human_rating_evidence": False,
        "human_parity_inferred": False,
    }
    outer["result_id"] = "gr29sel1:" + sha256_json(
        {
            "inner_result_digest": selected["result_digest"],
            "growth_sha": outer["growth_r29"]["producer_sha"],
            "authority_profile_digest": authority_digest,
        }
    )
    material = copy.deepcopy(outer)
    material["result_digest"] = ""
    outer["result_digest"] = sha256_json(material)
    return outer


def ingest_round(
    *,
    media_session_dir: Path,
    bridge_round_result: Path,
    authority_profile: Mapping[str, Any],
    session_dir: Path,
    out_dir: Path,
    conversation_id: str,
    growth_sha: str,
    growth_ci_run_id: int,
    r31_result: Path | None = None,
    terminal_winner: bool = False,
) -> dict[str, Any]:
    profile = validate_authority_profile(authority_profile)
    media = validate_media_session_dir(media_session_dir, profile=profile)
    bridge = load_bridge_round(
        bridge_round_result,
        media=media,
        profile=profile,
        conversation_id=conversation_id,
        r31_result_path=r31_result,
    )
    ingest = r28.convert_dynamic_capture(
        media_package=bridge["normalized_media_package"],
        parsed_capture=bridge["parsed_capture"],
    )
    if ingest.get("evidence_state") != "LIVE_REVIEW_INGESTED":
        raise NonLiveCapture("verified R32/R31 capture did not become live ingest")

    envelopes = {
        candidate_id: r28.build_creator_envelope(
            ingest_result=ingest,
            candidate_id=candidate_id,
            growth_producer_sha=r28.r27.CREATOR_R29_GROWTH_R26_SHA,
            growth_ci_run_id=r28.r27.CREATOR_R29_GROWTH_R26_CI,
        )
        for candidate_id in sorted(ingest["dynamic_handoffs"])
    }
    selected_inner = r28._selected_result(ingest=ingest, envelopes=envelopes)
    authority_digest = authority_profile_digest(profile)
    selected = _selected_outer(
        selected=selected_inner,
        growth_sha=growth_sha,
        growth_ci_run_id=growth_ci_run_id,
        authority_digest=authority_digest,
    )
    session_id, identity = _session_identity(
        media=media,
        conversation_id=conversation_id,
        authority_digest=authority_digest,
    )
    ledger = SessionLedger(session_dir, session_id=session_id, identity=identity)

    selection = ingest["unblinding"]["model_facing_selection"]
    selected_candidate_id = ingest["unblinding"]["selected_candidate_id"]
    outcome = (
        selection
        if selection in {"tie", "insufficient_evidence"}
        else ("winner" if selected_candidate_id is not None else "human_review")
    )
    round_result = {
        "contract_version": ROUND_RESULT_VERSION,
        "round_result_id": "",
        "round_result_digest": "",
        "session_id": session_id,
        "media_session_id": media["session_id"],
        "review_round": media["review_round"],
        "conversation_id": conversation_id,
        "request_id": bridge["request_id"],
        "operation_id": bridge["operation_id"],
        "response_digest": bridge["response_digest"],
        "capture_digest": bridge["capture_digest"],
        "bridge_round_result_sha256": bridge["round_result_sha256"],
        "session_package_sha256": media["session_package_sha256"],
        "package_digest": media["nested"]["package_digest"],
        "sealed_mapping_digest": media["nested"]["sealed_mapping_digest"],
        "prompt_digest": media["nested"]["prompt_digest"],
        "r32_session_package_digest": media["r32_identity"][
            "sessionPackageDigest"
        ],
        "pairwise": {
            "selection": selection,
            "selected_candidate_id": selected_candidate_id,
            "output_digest": ingest["pairwise_output"]["output_digest"],
            "rationale": ingest["pairwise_output"]["rationale"],
            "confidence": ingest["pairwise_output"]["confidence"],
            "uncertainty": ingest["pairwise_output"]["uncertainty"],
        },
        "outcome": outcome,
        "selected_candidate_id": selected_candidate_id,
        "selected_result": selected,
        "candidate_envelopes": {
            candidate_id: {
                "contract_version": envelope["contract_version"],
                "envelope_digest": envelope["envelope_digest"],
                "handoff_digest": envelope["candidate"]["handoff_digest"],
                "candidate_round": envelope["candidate"]["candidate_round"],
                "state": envelope["candidate"]["state"],
                "render_sha256": envelope["candidate"]["render_sha256"],
                "attachment_sha256": envelope["candidate"]["attachment_sha256"],
            }
            for candidate_id, envelope in envelopes.items()
        },
        "terminal_winner": bool(terminal_winner and outcome == "winner"),
        "creator_executable_handoff_emitted": True,
        "growth_r29": {
            "producer_sha": _sha1(growth_sha, "growth_sha"),
            "ci_run_id": _positive(growth_ci_run_id, "growth_ci_run_id"),
            "authority_profile_digest": authority_digest,
        },
        "authorities": {
            "media_r23_sha": MEDIA_R23_SHA,
            "bridge_r32_sha": BRIDGE_R32_SHA,
            "creator_r30_sha": CREATOR_R30_SHA,
            "canonical_inner_envelope_contract":
                r28.r27.CREATOR_ENVELOPE_VERSION,
        },
        "evidence_boundary": {
            "model_evidence": True,
            "human_ground_truth": False,
            "human_rating_evidence": False,
            "human_parity_inferred": False,
            "browser_mutation": False,
            "provider_mutation": False,
        },
    }
    round_result["round_result_id"] = "gr29rr1:" + sha256_json(
        {
            "session_id": session_id,
            "review_round": media["review_round"],
            "capture_digest": bridge["capture_digest"],
            "package_digest": media["nested"]["package_digest"],
        }
    )
    material = copy.deepcopy(round_result)
    material["round_result_digest"] = ""
    round_result["round_result_digest"] = sha256_json(material)
    fingerprint = sha256_json(
        {
            "media_session_package_sha256": media["session_package_sha256"],
            "media_evidence_sha256": media["evidence_sha256"],
            "operator_manifest_sha256": media["nested"][
                "operator_manifest_sha256"
            ],
            "mapping_file_sha256": media["nested"][
                "raw_mapping_file_sha256"
            ],
            "bridge_round_result_sha256": bridge["round_result_sha256"],
            "r31_result": bridge["r31_wrapper"],
            "capture_payload": bridge["capture"],
            "response_digest": bridge["response_digest"],
            "authority_profile_digest": authority_digest,
        }
    )
    effect = ledger.apply(round_result=round_result, fingerprint=fingerprint)

    out = Path(out_dir)
    round_dir = out / f"round-{media['review_round']}"
    if effect:
        round_dir.mkdir(parents=True, exist_ok=True)
        for candidate_id, envelope in envelopes.items():
            slug = hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()[:16]
            (round_dir / f"creator-envelope-{slug}.json").write_text(
                json.dumps(envelope, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        (round_dir / "round-result.json").write_text(
            json.dumps(round_result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        prior = round_dir / "round-result.json"
        if (
            not prior.is_file()
            or _load(prior).get("round_result_digest")
            != round_result["round_result_digest"]
        ):
            raise SessionConflict("exact replay output is missing or changed")

    report = {
        "report_version": REPORT_VERSION,
        "state": "LIVE_REVIEW_INGESTED",
        "session_id": session_id,
        "media_session_id": media["session_id"],
        "review_round": media["review_round"],
        "next_review_round": ledger.next_round,
        "session_closed": ledger.closed,
        "new_ingest_effect": effect,
        "round_result_digest": round_result["round_result_digest"],
        "selected_result": selected,
        "pairwise": round_result["pairwise"],
        "candidate_envelope_count": 2,
        "creator_executable_handoff_emitted": True,
        "fixture_promoted": False,
        "human_ground_truth": False,
        "human_rating_evidence": False,
        "human_parity_inferred": False,
        "browser_mutation": False,
        "provider_mutation": False,
    }
    report["report_digest"] = sha256_json(report)
    out.mkdir(parents=True, exist_ok=True)
    (out / "session-readiness.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return _clone(report)


def source_ready(
    *,
    media_session_dir: Path,
    authority_profile: Mapping[str, Any],
    out_dir: Path,
    conversation_id: str,
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    profile = validate_authority_profile(authority_profile)
    media = validate_media_session_dir(media_session_dir, profile=profile)
    authority_digest = authority_profile_digest(profile)
    session_id, _ = _session_identity(
        media=media,
        conversation_id=conversation_id,
        authority_digest=authority_digest,
    )
    report = {
        "report_version": REPORT_VERSION,
        "state": "SOURCE_READY",
        "live_capture_gate": "BLOCKED_WAITING_GENUINE_R32_ROUND",
        "session_id": session_id,
        "media_session_id": media["session_id"],
        "review_round": media["review_round"],
        "session_package_sha256": media["session_package_sha256"],
        "r21_package_digest": media["nested"]["package_digest"],
        "r22_archive_sha256": media["nested"]["archive_sha256"],
        "sealed_mapping_digest": media["nested"]["sealed_mapping_digest"],
        "prompt_digest": media["nested"]["prompt_digest"],
        "r32_session_package_digest": media["r32_identity"][
            "sessionPackageDigest"
        ],
        "growth_r29": {
            "producer_sha": _sha1(growth_sha, "growth_sha"),
            "ci_run_id": _positive(growth_ci_run_id, "growth_ci_run_id"),
            "authority_profile_digest": authority_digest,
        },
        "live_review_ingested": False,
        "creator_executable_handoff_emitted": False,
        "fixture_promoted": False,
        "human_ground_truth": False,
        "human_rating_evidence": False,
        "human_parity_inferred": False,
        "browser_mutation": False,
        "provider_mutation": False,
    }
    report["report_digest"] = sha256_json(report)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "session-readiness.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return _clone(report)


def fixture_readiness(
    *,
    fixture_path: Path,
    authority_profile: Mapping[str, Any],
    out_dir: Path,
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    profile = validate_authority_profile(authority_profile)
    fixture = validate_r32_fixture_evidence(_load(fixture_path))
    report = {
        "report_version": REPORT_VERSION,
        "state": "SOURCE_READY",
        "fixture_contract": fixture["contract"],
        "round_sequence_proven": [0, 1, 2],
        "fixture_only": True,
        "live_review_ingested": False,
        "creator_executable_handoff_emitted": False,
        "fixture_promoted": False,
        "growth_r29": {
            "producer_sha": _sha1(growth_sha, "growth_sha"),
            "ci_run_id": _positive(growth_ci_run_id, "growth_ci_run_id"),
            "authority_profile_digest": authority_profile_digest(profile),
        },
        "human_ground_truth": False,
        "human_rating_evidence": False,
        "human_parity_inferred": False,
        "browser_mutation": False,
        "provider_mutation": False,
    }
    report["report_digest"] = sha256_json(report)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "fixture-readiness.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return _clone(report)


def _write_blocked(
    *,
    out_dir: Path,
    state: str,
    exc: Exception,
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    report = {
        "report_version": REPORT_VERSION,
        "state": state,
        "reason": type(exc).__name__,
        "detail": str(exc),
        "growth_r29": {
            "producer_sha": _sha1(growth_sha, "growth_sha"),
            "ci_run_id": _positive(growth_ci_run_id, "growth_ci_run_id"),
        },
        "live_review_ingested": False,
        "creator_executable_handoff_emitted": False,
        "fixture_promoted": False,
        "human_ground_truth": False,
        "human_rating_evidence": False,
        "human_parity_inferred": False,
        "browser_mutation": False,
        "provider_mutation": False,
    }
    report["report_digest"] = sha256_json(report)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "session-readiness.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="growth-session-ingest-r29")
    sub = parser.add_subparsers(dest="command", required=True)

    ready = sub.add_parser("source-ready")
    ready.add_argument("--media-session-dir", required=True)
    ready.add_argument("--authority-profile", required=True)
    ready.add_argument("--out-dir", required=True)
    ready.add_argument("--conversation-id", required=True)
    ready.add_argument("--growth-sha", required=True)
    ready.add_argument("--growth-ci-run-id", type=int, required=True)

    ingest = sub.add_parser("ingest")
    ingest.add_argument("--media-session-dir", required=True)
    ingest.add_argument("--bridge-round-result", required=True)
    ingest.add_argument("--r31-result")
    ingest.add_argument("--authority-profile", required=True)
    ingest.add_argument("--session-dir", required=True)
    ingest.add_argument("--out-dir", required=True)
    ingest.add_argument("--conversation-id", required=True)
    ingest.add_argument("--growth-sha", required=True)
    ingest.add_argument("--growth-ci-run-id", type=int, required=True)
    ingest.add_argument("--terminal-winner", action="store_true")

    fixture = sub.add_parser("fixture-readiness")
    fixture.add_argument("--bridge-fixture", required=True)
    fixture.add_argument("--authority-profile", required=True)
    fixture.add_argument("--out-dir", required=True)
    fixture.add_argument("--growth-sha", required=True)
    fixture.add_argument("--growth-ci-run-id", type=int, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    profile = _load(Path(args.authority_profile))
    try:
        if args.command == "source-ready":
            report = source_ready(
                media_session_dir=Path(args.media_session_dir),
                authority_profile=profile,
                out_dir=Path(args.out_dir),
                conversation_id=args.conversation_id,
                growth_sha=args.growth_sha,
                growth_ci_run_id=args.growth_ci_run_id,
            )
        elif args.command == "fixture-readiness":
            report = fixture_readiness(
                fixture_path=Path(args.bridge_fixture),
                authority_profile=profile,
                out_dir=Path(args.out_dir),
                growth_sha=args.growth_sha,
                growth_ci_run_id=args.growth_ci_run_id,
            )
        else:
            report = ingest_round(
                media_session_dir=Path(args.media_session_dir),
                bridge_round_result=Path(args.bridge_round_result),
                authority_profile=profile,
                session_dir=Path(args.session_dir),
                out_dir=Path(args.out_dir),
                conversation_id=args.conversation_id,
                growth_sha=args.growth_sha,
                growth_ci_run_id=args.growth_ci_run_id,
                r31_result=(
                    None if not args.r31_result else Path(args.r31_result)
                ),
                terminal_winner=bool(args.terminal_winner),
            )
        print(json.dumps(report, sort_keys=True))
        return 0
    except MalformedModelResponse as exc:
        report = _write_blocked(
            out_dir=Path(args.out_dir),
            state="MALFORMED_MODEL_RESPONSE",
            exc=exc,
            growth_sha=args.growth_sha,
            growth_ci_run_id=args.growth_ci_run_id,
        )
        print(json.dumps(report, sort_keys=True))
        return 3
    except ReconciliationRequired as exc:
        report = _write_blocked(
            out_dir=Path(args.out_dir),
            state="RECONCILIATION_REQUIRED",
            exc=exc,
            growth_sha=args.growth_sha,
            growth_ci_run_id=args.growth_ci_run_id,
        )
        print(json.dumps(report, sort_keys=True))
        return 4
    except Exception as exc:
        report = _write_blocked(
            out_dir=Path(args.out_dir),
            state="BLOCKED_WAITING_GENUINE_R32_ROUND",
            exc=exc,
            growth_sha=args.growth_sha,
            growth_ci_run_id=args.growth_ci_run_id,
        )
        print(json.dumps(report, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
