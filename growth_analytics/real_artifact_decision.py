from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import canonical_json, sha256_json
from .candidate_decision import (
    CANDIDATE_DECISION_VERSION,
    CandidateDecisionError,
    build_candidate_decision,
    parse_candidate_decision,
)
from .closed_loop_feedback import (
    CLOSED_LOOP_FEEDBACK_VERSION,
    ClosedLoopFeedbackError,
    build_closed_loop_feedback,
    parse_closed_loop_feedback,
)
from .critic_export import (
    CRITIC_EXPORT_VERSION,
    CriticExportError,
    build_critic_export,
    validate_critic_export,
)
from .visual_critic import (
    CRITIC_DIMENSIONS,
    VISUAL_CRITIC_VERSION,
    VisualCriticError,
    parse_visual_critic_report,
)


REAL_ARTIFACT_DECISION_PACK_VERSION = (
    "growth.real_artifact_decision_pack.v1"
)
REAL_ARTIFACT_VERIFICATION_VERSION = (
    "growth.real_artifact_verification.r21.v1"
)
REAL_ARTIFACT_LEDGER_VERSION = (
    "growth.real_artifact_decision_ledger.r21.v1"
)

MEDIA_REPOSITORY = "foto6/video2"
MEDIA_PRODUCER_SHA = (
    "231a0680c8939cfec77aaa283e507e93f383ad73"
)
MEDIA_RUN_ID = 36865890506
MEDIA_ARTIFACT_ID = 11163920921
MEDIA_ARTIFACT_NAME = "media-r16-candidate-batch-demo"
MEDIA_ARCHIVE_SHA256 = (
    "d929b592c76ec93e41b54701376f4b02366a3bdbd127d3472d73ae277d450d0f"
)
MEDIA_BATCH_CONTRACT = "media.candidate_batch.v1"
MEDIA_RENDER_EXPORT_CONTRACT = "media.render_export.v1"
MEDIA_BATCH_ID = "r16-demo-batch"
MEDIA_SOURCE_ID = "r16-demo-source"
MEDIA_SOURCE_SHA256 = (
    "7b484abef5de1569e1b7f91a5d780f17c6d687ef68b3c42c9e54375f4e5e434b"
)
MEDIA_SOURCE_SIZE = 763377
MEDIA_CYCLE_REVISION = 16

_EXPECTED_CANDIDATES = (
    {
        "candidate_id": "candidate-1",
        "order": 0,
        "render_sha256":
            "3bd12d999264cb932cb3ef15dfbd96e002ede517f2273795b593553b9642d864",
        "render_size": 575465,
        "render_export_sha256":
            "2b5177c00bb054184a239c7eddb1383ad253922e8eb686f5aa2e56c91a1335ac",
    },
    {
        "candidate_id": "candidate-2",
        "order": 1,
        "render_sha256":
            "cdae63d5ccce67332e607af7fd87b18c31da8dc77c2f0ce5182550321767286c",
        "render_size": 576763,
        "render_export_sha256":
            "b349b897f8f86bc9257e2622f564d6430f3864ff605ae4b82ba0cb09c59298f6",
    },
    {
        "candidate_id": "candidate-3",
        "order": 2,
        "render_sha256":
            "3bd12d999264cb932cb3ef15dfbd96e002ede517f2273795b593553b9642d864",
        "render_size": 575465,
        "render_export_sha256":
            "26fe61f0644e263ce047869334ba65c5973eba809ff45f76286490f573d61b8e",
    },
)

_SHA256_HEX = set("0123456789abcdef")


class RealArtifactDecisionError(ValueError):
    pass


class RealArtifactTamperError(RealArtifactDecisionError):
    pass


class RealArtifactDecisionConflictError(
    RealArtifactDecisionError
):
    pass


class RealArtifactDecisionOutOfOrder(
    RealArtifactDecisionError
):
    pass


@dataclass(frozen=True)
class MediaArtifactAuthority:
    repository: str = MEDIA_REPOSITORY
    producer_sha: str = MEDIA_PRODUCER_SHA
    run_id: int = MEDIA_RUN_ID
    artifact_id: int = MEDIA_ARTIFACT_ID
    artifact_name: str = MEDIA_ARTIFACT_NAME
    archive_sha256: str = MEDIA_ARCHIVE_SHA256
    batch_id: str = MEDIA_BATCH_ID
    source_id: str = MEDIA_SOURCE_ID
    source_sha256: str = MEDIA_SOURCE_SHA256
    source_size: int = MEDIA_SOURCE_SIZE
    expected_candidates: tuple[Mapping[str, Any], ...] = (
        _EXPECTED_CANDIDATES
    )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_hex(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in _SHA256_HEX for ch in value)
    ):
        raise RealArtifactDecisionError(
            f"{field} must be lowercase SHA-256"
        )
    return value


def _sha1(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(ch not in _SHA256_HEX for ch in value)
    ):
        raise RealArtifactDecisionError(
            f"{field} must be lowercase Git SHA-1"
        )
    return value


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RealArtifactDecisionError(
            f"{field} must be non-empty string"
        )
    return value


def _positive_int(value: Any, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
    ):
        raise RealArtifactDecisionError(
            f"{field} must be integer >= 1"
        )
    return value


def _json_bytes(data: bytes, field: str) -> dict[str, Any]:
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RealArtifactTamperError(
            f"{field} must be UTF-8 JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise RealArtifactTamperError(
            f"{field} must be JSON object"
        )
    return parsed


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return _sha256_bytes(
        canonical_json(value).encode("utf-8")
    )


def _read_member(
    archive: zipfile.ZipFile,
    name: str,
) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise RealArtifactTamperError(
            f"archive member missing: {name}"
        ) from exc
    if info.is_dir():
        raise RealArtifactTamperError(
            f"archive member unexpectedly directory: {name}"
        )
    if (
        name.startswith("/")
        or ".." in Path(name).parts
    ):
        raise RealArtifactTamperError(
            "unsafe archive member path"
        )
    return archive.read(info)


def _expected_by_id(
    authority: MediaArtifactAuthority,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for raw in authority.expected_candidates:
        candidate_id = _nonempty(
            raw.get("candidate_id"),
            "expected candidate_id",
        )
        if candidate_id in result:
            raise RealArtifactDecisionError(
                "authority contains duplicate expected candidate"
            )
        result[candidate_id] = raw
    if not 2 <= len(result) <= 4:
        raise RealArtifactDecisionError(
            "authority must contain 2-4 candidates"
        )
    return result


def verify_media_candidate_batch_archive(
    archive_bytes: bytes,
    *,
    authority: MediaArtifactAuthority | None = None,
) -> dict[str, Any]:
    authority = authority or MediaArtifactAuthority()
    if not isinstance(archive_bytes, bytes) or not archive_bytes:
        raise RealArtifactDecisionError(
            "archive_bytes must be non-empty bytes"
        )
    observed_archive_sha = _sha256_bytes(
        archive_bytes
    )
    if observed_archive_sha != authority.archive_sha256:
        raise RealArtifactTamperError(
            "Media artifact archive SHA-256 mismatch"
        )
    try:
        archive = zipfile.ZipFile(
            io.BytesIO(archive_bytes),
            "r",
        )
    except zipfile.BadZipFile as exc:
        raise RealArtifactTamperError(
            "Media artifact is not valid ZIP"
        ) from exc

    manifest_raw = _read_member(
        archive,
        "batch/media.candidate_batch.v1.json",
    )
    manifest = _json_bytes(
        manifest_raw,
        "candidate batch manifest",
    )
    evidence_raw = _read_member(
        archive,
        "batch/media.candidate_batch.r16.evidence.json",
    )
    evidence = _json_bytes(
        evidence_raw,
        "candidate batch evidence",
    )
    source_bytes = _read_member(
        archive,
        "source.mp4",
    )

    if (
        manifest.get("contractVersion")
        != MEDIA_BATCH_CONTRACT
        or manifest.get("batchId")
        != authority.batch_id
        or manifest.get("status")
        != "succeeded"
    ):
        raise RealArtifactTamperError(
            "candidate batch manifest identity/status mismatch"
        )
    producer = manifest.get("producer")
    if (
        not isinstance(producer, Mapping)
        or producer.get("repository")
        != authority.repository
        or producer.get("sha")
        != authority.producer_sha
    ):
        raise RealArtifactTamperError(
            "stale or wrong Media producer pin"
        )
    evidence_producer = evidence.get(
        "producer"
    )
    if (
        not isinstance(evidence_producer, Mapping)
        or evidence_producer.get("repository")
        != authority.repository
        or evidence_producer.get("sha")
        != authority.producer_sha
    ):
        raise RealArtifactTamperError(
            "candidate batch evidence producer mismatch"
        )
    if evidence.get("manifestDigest") != (
        _canonical_sha256(manifest)
    ):
        raise RealArtifactTamperError(
            "candidate batch canonical manifest digest mismatch"
        )

    source = manifest.get("source")
    if (
        not isinstance(source, Mapping)
        or source.get("sourceId")
        != authority.source_id
        or source.get("sha256")
        != authority.source_sha256
        or source.get("size")
        != authority.source_size
    ):
        raise RealArtifactTamperError(
            "wrong source lineage in candidate batch"
        )
    if (
        _sha256_bytes(source_bytes)
        != authority.source_sha256
        or len(source_bytes)
        != authority.source_size
    ):
        raise RealArtifactTamperError(
            "source.mp4 bytes do not match source lineage"
        )

    candidates_raw = manifest.get(
        "candidates"
    )
    if not isinstance(candidates_raw, list):
        raise RealArtifactTamperError(
            "manifest candidates must be array"
        )
    expected = _expected_by_id(
        authority
    )
    if len(candidates_raw) != len(expected):
        raise RealArtifactTamperError(
            "missing or extra Media candidates"
        )

    seen: set[str] = set()
    verified_candidates: list[
        dict[str, Any]
    ] = []
    for raw in sorted(
        candidates_raw,
        key=lambda item: item.get(
            "order",
            -1,
        ),
    ):
        if not isinstance(raw, Mapping):
            raise RealArtifactTamperError(
                "candidate entry invalid"
            )
        candidate_id = raw.get(
            "candidateId"
        )
        if (
            not isinstance(candidate_id, str)
            or candidate_id not in expected
            or candidate_id in seen
        ):
            raise RealArtifactTamperError(
                "duplicate or unexpected candidate"
            )
        seen.add(candidate_id)
        exp = expected[candidate_id]
        if (
            raw.get("status") != "succeeded"
            or raw.get("failure") is not None
            or raw.get("order") != exp.get("order")
        ):
            raise RealArtifactTamperError(
                f"{candidate_id} status/order mismatch"
            )
        final = raw.get("final")
        if not isinstance(final, Mapping):
            raise RealArtifactTamperError(
                f"{candidate_id} final binding missing"
            )
        if (
            final.get("sha256")
            != exp.get("render_sha256")
            or final.get("size")
            != exp.get("render_size")
            or final.get(
                "renderExportSha256"
            )
            != exp.get(
                "render_export_sha256"
            )
        ):
            raise RealArtifactTamperError(
                f"{candidate_id} manifest final binding mismatch"
            )
        mp4_path = (
            "batch/"
            + _nonempty(
                final.get("relativePath"),
                "final.relativePath",
            )
        )
        export_path = (
            "batch/"
            + _nonempty(
                final.get(
                    "renderExportRelativePath"
                ),
                "final.renderExportRelativePath",
            )
        )
        mp4_bytes = _read_member(
            archive,
            mp4_path,
        )
        export_bytes = _read_member(
            archive,
            export_path,
        )
        observed_mp4_sha = _sha256_bytes(
            mp4_bytes
        )
        observed_export_sha = (
            _sha256_bytes(
                export_bytes
            )
        )
        if (
            observed_mp4_sha
            != final["sha256"]
            or len(mp4_bytes)
            != final["size"]
        ):
            raise RealArtifactTamperError(
                f"{candidate_id} final.mp4 hash/size mismatch"
            )
        if (
            observed_export_sha
            != final[
                "renderExportSha256"
            ]
        ):
            raise RealArtifactTamperError(
                f"{candidate_id} render-export hash mismatch"
            )
        export = _json_bytes(
            export_bytes,
            f"{candidate_id} render export",
        )
        if (
            export.get("contractVersion")
            != MEDIA_RENDER_EXPORT_CONTRACT
            or export.get("status")
            != "succeeded"
        ):
            raise RealArtifactTamperError(
                f"{candidate_id} render-export contract/status mismatch"
            )
        export_producer = export.get(
            "producer"
        )
        if (
            not isinstance(
                export_producer,
                Mapping,
            )
            or export_producer.get(
                "repository"
            )
            != authority.repository
            or export_producer.get("sha")
            != authority.producer_sha
        ):
            raise RealArtifactTamperError(
                f"{candidate_id} render-export producer mismatch"
            )
        artifact = export.get(
            "artifact"
        )
        if (
            not isinstance(artifact, Mapping)
            or artifact.get("sha256")
            != observed_mp4_sha
            or artifact.get("size")
            != len(mp4_bytes)
        ):
            raise RealArtifactTamperError(
                f"{candidate_id} render-export artifact binding mismatch"
            )
        sources = (
            export.get("evidence", {})
            .get("sources", {})
            .get("items")
        )
        if (
            not isinstance(sources, list)
            or len(sources) != 1
        ):
            raise RealArtifactTamperError(
                f"{candidate_id} source evidence invalid"
            )
        source_item = sources[0]
        if (
            not isinstance(source_item, Mapping)
            or source_item.get("sourceId")
            != authority.source_id
            or source_item.get("sha256")
            != authority.source_sha256
            or source_item.get("size")
            != authority.source_size
            or source_item.get("probeOk")
            is not True
        ):
            raise RealArtifactTamperError(
                f"{candidate_id} source lineage mismatch"
            )
        qa = export.get("qa")
        if not isinstance(qa, Mapping):
            raise RealArtifactTamperError(
                f"{candidate_id} Media QA missing"
            )
        technical = qa.get(
            "technical"
        )
        creative = qa.get(
            "creative"
        )
        if (
            not isinstance(
                technical,
                Mapping,
            )
            or not isinstance(
                creative,
                Mapping,
            )
        ):
            raise RealArtifactTamperError(
                f"{candidate_id} Media QA invalid"
            )
        verified_candidates.append({
            "candidate_id":
                candidate_id,
            "order": raw["order"],
            "plan_digest":
                raw["planDigest"],
            "cache_identity_digest":
                raw["cacheIdentityDigest"],
            "render_sha256":
                observed_mp4_sha,
            "render_size":
                len(mp4_bytes),
            "render_export_sha256":
                observed_export_sha,
            "render_export_canonical_sha256":
                _canonical_sha256(
                    export
                ),
            "render_fingerprint":
                export["job"][
                    "renderFingerprint"
                ],
            "timeline_digest":
                export[
                    "provenance"
                ]["timelineDigest"],
            "creative_plan_digest":
                export[
                    "provenance"
                ][
                    "creativePlanDigest"
                ],
            "technical_qa": {
                "passed":
                    technical[
                        "passed"
                    ],
                "sha256":
                    technical[
                        "sha256"
                    ],
            },
            "creative_qa": {
                "passed":
                    creative[
                        "passed"
                    ],
                "sha256":
                    creative[
                        "sha256"
                    ],
            },
            "benchmark_technical_dq":
                bool(
                    export.get(
                        "benchmark",
                        {},
                    ).get(
                        "technicalDq",
                        False,
                    )
                ),
            "render_export":
                export,
        })

    if seen != set(expected):
        raise RealArtifactTamperError(
            "candidate set does not equal authority"
        )

    groups: dict[
        str,
        list[str],
    ] = {}
    for candidate in verified_candidates:
        groups.setdefault(
            candidate[
                "render_sha256"
            ],
            [],
        ).append(
            candidate[
                "candidate_id"
            ]
        )
    content_groups = [
        {
            "render_sha256": render_sha,
            "candidate_ids":
                sorted(ids),
            "byte_identical":
                len(ids) > 1,
        }
        for render_sha, ids
        in sorted(groups.items())
    ]
    return {
        "verification_version":
            REAL_ARTIFACT_VERIFICATION_VERSION,
        "archive": {
            "repository":
                authority.repository,
            "producer_sha":
                authority.producer_sha,
            "run_id":
                authority.run_id,
            "artifact_id":
                authority.artifact_id,
            "artifact_name":
                authority.artifact_name,
            "archive_sha256":
                observed_archive_sha,
            "archive_size":
                len(archive_bytes),
        },
        "batch": {
            "contract_version":
                manifest[
                    "contractVersion"
                ],
            "batch_id":
                manifest["batchId"],
            "manifest_file_sha256":
                _sha256_bytes(
                    manifest_raw
                ),
            "manifest_canonical_sha256":
                _canonical_sha256(
                    manifest
                ),
            "request_digest":
                manifest[
                    "requestDigest"
                ],
        },
        "source": {
            "source_id":
                authority.source_id,
            "sha256":
                authority.source_sha256,
            "size":
                authority.source_size,
        },
        "candidates":
            verified_candidates,
        "content_equivalence_groups":
            content_groups,
        "verification": {
            "archive_digest_verified":
                True,
            "producer_pin_verified":
                True,
            "source_bytes_verified":
                True,
            "candidate_bytes_verified":
                True,
            "render_exports_verified":
                True,
            "source_lineage_verified":
                True,
        },
    }


def _media_only_critic_report(
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    render_sha = candidate[
        "render_sha256"
    ]
    evidence_refs = [
        (
            "media.render_export.v1:"
            + candidate[
                "render_export_sha256"
            ]
        ),
        (
            "media.technical_qa:"
            + candidate[
                "technical_qa"
            ]["sha256"]
        ),
        (
            "media.creative_qa:"
            + candidate[
                "creative_qa"
            ]["sha256"]
        ),
    ]
    binding = {
        "candidate_id":
            candidate["candidate_id"],
        "render_sha256":
            render_sha,
        "render_size":
            candidate[
                "render_size"
            ],
        "render_export_sha256":
            candidate[
                "render_export_sha256"
            ],
        "render_export_canonical_sha256":
            candidate[
                "render_export_canonical_sha256"
            ],
        "render_fingerprint":
            candidate[
                "render_fingerprint"
            ],
        "timeline_digest":
            candidate[
                "timeline_digest"
            ],
        "technical_qa_sha256":
            candidate[
                "technical_qa"
            ]["sha256"],
        "creative_qa_sha256":
            candidate[
                "creative_qa"
            ]["sha256"],
        "media_producer_sha":
            MEDIA_PRODUCER_SHA,
        "source_sha256":
            MEDIA_SOURCE_SHA256,
    }
    input_digest = sha256_json(
        binding
    )
    hard_failures = []
    if (
        candidate[
            "technical_qa"
        ]["passed"] is not True
        or candidate[
            "benchmark_technical_dq"
        ]
    ):
        hard_failures.append({
            "code":
                "MEDIA_TECHNICAL_QA_FAILURE",
            "time": None,
            "message": (
                "Media technical QA or benchmark technical-DQ "
                "flag rejected the real render."
            ),
            "source":
                "media.render_export.v1",
        })
    limitation = (
        "Exact Media render bytes and QA are verified, but this artifact "
        "does not contain bound frame-semantic observations, production "
        "VLM observations, or human labels for this editorial dimension."
    )
    judgments = {
        dimension: {
            "score": None,
            "confidence": 0.0,
            "evidence":
                list(evidence_refs),
            "limitation":
                limitation,
        }
        for dimension
        in CRITIC_DIMENSIONS
    }
    report = {
        "contract_version":
            VISUAL_CRITIC_VERSION,
        "critic_report_id": "",
        "critic_report_digest": "",
        "candidate_id":
            candidate[
                "candidate_id"
            ],
        "input_digest":
            input_digest,
        "render": {
            "artifact_id": (
                "media-r16:"
                + candidate[
                    "candidate_id"
                ]
            ),
            "artifact_sha256":
                render_sha,
        },
        "objective_hard_failures":
            hard_failures,
        "heuristic_editorial_judgments":
            judgments,
        "vlm_provider": {
            "state":
                "missing_provider",
            "provider_name": None,
            "model_name": None,
            "observation_count": 0,
            "limitation": (
                "No production VLM observation is bound to this exact "
                "Media R16 artifact; no model preference is inferred."
            ),
        },
        "vlm_observations": [],
        "human_labels": {
            "present": False,
            "count": 0,
            "note": (
                "Candidate critique does not contain human labels. "
                "Human preference is evaluated only by the calibration harness."
            ),
        },
        "actionable_notes": [],
        "limitations": [
            (
                "Media technical/creative QA is objective/structural "
                "evidence, not human preference."
            ),
            (
                "No production VLM observation is bound to the exact "
                "candidate bytes in this artifact."
            ),
            (
                "No human pairwise labels or live platform metrics are "
                "present in the Media artifact."
            ),
        ],
        "authority": {
            "advisory_only": True,
            "publish_authorized": False,
            "provider_mutation": False,
            "media_mutation": False,
            "creator_mutation": False,
        },
    }
    report["critic_report_id"] = (
        "gvcr15:"
        + sha256_json({
            "candidate_id":
                report[
                    "candidate_id"
                ],
            "input_digest":
                input_digest,
            "artifact_sha256":
                render_sha,
        })
    )
    digest_material = dict(
        report
    )
    digest_material[
        "critic_report_digest"
    ] = ""
    report[
        "critic_report_digest"
    ] = sha256_json(
        digest_material
    )
    try:
        return parse_visual_critic_report(
            report
        )
    except VisualCriticError as exc:
        raise RealArtifactDecisionError(
            "failed to build bound R15 critic report"
        ) from exc


def _representative_candidates(
    candidates: Sequence[
        Mapping[str, Any]
    ],
) -> tuple[
    list[Mapping[str, Any]],
    dict[str, str],
]:
    representatives: list[
        Mapping[str, Any]
    ] = []
    representative_by_render: dict[
        str,
        str,
    ] = {}
    aliases: dict[str, str] = {}
    for candidate in sorted(
        candidates,
        key=lambda row:
            row["order"],
    ):
        render = candidate[
            "render_sha256"
        ]
        representative_id = (
            representative_by_render.get(
                render
            )
        )
        if representative_id is None:
            representative_by_render[
                render
            ] = candidate[
                "candidate_id"
            ]
            representatives.append(
                candidate
            )
        else:
            aliases[
                candidate[
                    "candidate_id"
                ]
            ] = representative_id
    if not 2 <= len(representatives) <= 4:
        raise RealArtifactDecisionError(
            "R18 requires 2-4 distinct render byte candidates"
        )
    return representatives, aliases


def build_real_artifact_decision_pack(
    verification: Mapping[str, Any],
    *,
    growth_commit_sha: str,
    growth_run_id: str,
    pack_revision: int = 1,
) -> dict[str, Any]:
    growth_commit_sha = _sha1(
        growth_commit_sha,
        "growth_commit_sha",
    )
    growth_run_id = _nonempty(
        str(growth_run_id),
        "growth_run_id",
    )
    if not growth_run_id.isdigit():
        raise RealArtifactDecisionError(
            "growth_run_id must contain decimal digits"
        )
    pack_revision = _positive_int(
        pack_revision,
        "pack_revision",
    )
    if (
        verification.get(
            "verification_version"
        )
        != REAL_ARTIFACT_VERIFICATION_VERSION
    ):
        raise RealArtifactDecisionError(
            "verification contract mismatch"
        )
    archive = verification[
        "archive"
    ]
    if (
        archive[
            "producer_sha"
        ] != MEDIA_PRODUCER_SHA
        or archive[
            "archive_sha256"
        ] != MEDIA_ARCHIVE_SHA256
        or archive[
            "artifact_id"
        ] != MEDIA_ARTIFACT_ID
    ):
        raise RealArtifactDecisionError(
            "verification is not pinned to Media R16 authority"
        )
    source = verification[
        "source"
    ]
    if (
        source[
            "source_id"
        ] != MEDIA_SOURCE_ID
        or source[
            "sha256"
        ] != MEDIA_SOURCE_SHA256
        or source[
            "size"
        ] != MEDIA_SOURCE_SIZE
    ):
        raise RealArtifactDecisionError(
            "verification source mismatch"
        )

    candidates = verification[
        "candidates"
    ]
    if (
        not isinstance(candidates, list)
        or len(candidates)
        != len(_EXPECTED_CANDIDATES)
    ):
        raise RealArtifactDecisionError(
            "verified candidate set invalid"
        )
    critic_reports: dict[
        str,
        dict[str, Any],
    ] = {}
    critic_exports: dict[
        str,
        dict[str, Any],
    ] = {}
    for candidate in candidates:
        report = _media_only_critic_report(
            candidate
        )
        try:
            export = build_critic_export(
                critic_report=report,
                repository="foto6/video3",
                commit_sha=growth_commit_sha,
                source_id=
                    source["source_id"],
            )
        except (
            CriticExportError,
            VisualCriticError,
        ) as exc:
            raise RealArtifactDecisionError(
                "failed to build R16 canonical critic export"
            ) from exc
        critic_reports[
            candidate["candidate_id"]
        ] = report
        critic_exports[
            candidate["candidate_id"]
        ] = export

    representatives, aliases = (
        _representative_candidates(
            candidates
        )
    )
    r18_candidates = []
    for candidate in representatives:
        candidate_id = candidate[
            "candidate_id"
        ]
        r18_candidates.append({
            "candidate_id":
                candidate_id,
            "source_id":
                source[
                    "source_id"
                ],
            "source_sha256":
                source["sha256"],
            "render_sha256":
                candidate[
                    "render_sha256"
                ],
            "critic_export":
                critic_exports[
                    candidate_id
                ],
        })
    expected_ids = [
        row["candidate_id"]
        for row in representatives
    ]
    try:
        decision = (
            build_candidate_decision(
                campaign_id=(
                    "human-gate-v4:"
                    + verification[
                        "batch"
                    ]["batch_id"]
                ),
                source_id=
                    source["source_id"],
                source_sha256=
                    source["sha256"],
                cycle_revision=
                    MEDIA_CYCLE_REVISION,
                decision_revision=1,
                expected_candidate_ids=
                    expected_ids,
                candidates=r18_candidates,
                live_metric_evidence=(),
                human_pairwise_evidence=(),
                historical_metrics=(),
            )
        )
    except CandidateDecisionError as exc:
        raise RealArtifactDecisionError(
            "R18 candidate decision failed"
        ) from exc
    try:
        feedback = build_closed_loop_feedback(
            candidate_decision=
                decision,
            post_publish_learning=None,
            bundle_revision=1,
            next_cycle_id=(
                "human-gate-v4:"
                + verification[
                    "batch"
                ]["batch_id"]
                + ":next"
            ),
        )
    except ClosedLoopFeedbackError as exc:
        raise RealArtifactDecisionError(
            "R20 feedback construction failed"
        ) from exc

    candidate_rows = []
    for candidate in sorted(
        candidates,
        key=lambda row:
            row["order"],
    ):
        cid = candidate[
            "candidate_id"
        ]
        export = critic_exports[cid]
        candidate_rows.append({
            key: candidate[key]
            for key in (
                "candidate_id",
                "order",
                "plan_digest",
                "cache_identity_digest",
                "render_sha256",
                "render_size",
                "render_export_sha256",
                "render_export_canonical_sha256",
                "render_fingerprint",
                "timeline_digest",
                "creative_plan_digest",
                "technical_qa",
                "creative_qa",
                "benchmark_technical_dq",
            )
        } | {
            "r18_representative":
                cid in expected_ids,
            "duplicate_of_candidate_id":
                aliases.get(cid),
            "critic_report_digest":
                critic_reports[cid][
                    "critic_report_digest"
                ],
            "critic_export_digest":
                sha256_json(
                    export
                ),
            "critic_export":
                export,
        })

    pack = {
        "contract_version":
            REAL_ARTIFACT_DECISION_PACK_VERSION,
        "pack_id": "",
        "pack_digest": "",
        "pack_revision":
            pack_revision,
        "growth_authority": {
            "repository":
                "foto6/video3",
            "commit_sha":
                growth_commit_sha,
            "run_id":
                growth_run_id,
        },
        "media_authority": {
            key: archive[key]
            for key in (
                "repository",
                "producer_sha",
                "run_id",
                "artifact_id",
                "artifact_name",
                "archive_sha256",
                "archive_size",
            )
        },
        "batch": dict(
            verification["batch"]
        ),
        "source": dict(source),
        "candidates":
            candidate_rows,
        "content_equivalence_groups":
            verification[
                "content_equivalence_groups"
            ],
        "r18_distinct_render_candidate_ids":
            expected_ids,
        "r18_duplicate_aliases":
            aliases,
        "r18_decision":
            decision,
        "r20_feedback":
            feedback,
        "evidence_classes": {
            "objective_qa": {
                "present": True,
                "source":
                    "media.render_export.v1",
                "all_technical_pass":
                    all(
                        row[
                            "technical_qa"
                        ]["passed"]
                        for row in
                        candidate_rows
                    ),
                "all_creative_pass":
                    all(
                        row[
                            "creative_qa"
                        ]["passed"]
                        for row in
                        candidate_rows
                    ),
            },
            "model_aesthetic_judgment": {
                "present": False,
                "reason": (
                    "No production VLM observation is bound to these exact "
                    "candidate bytes; no model preference is synthesized."
                ),
                "human_ground_truth":
                    False,
            },
            "human_evidence": {
                "present": False,
                "human_ground_truth":
                    False,
                "reason": (
                    "No real human pairwise label was supplied in the "
                    "Media artifact or Growth input."
                ),
            },
            "live_platform_metrics": {
                "present": False,
                "synthetic_as_live":
                    False,
                "reason": (
                    "No R17 platform metrics are part of this pre-publish "
                    "real-artifact decision."
                ),
            },
        },
        "targeted_reedit_guidance":
            list(
                decision[
                    "reedit_guidance"
                ]
            ),
        "unavailable_evidence": {
            "production_vlm":
                "No production/non-synthetic VLM observation bound to exact artifact hashes.",
            "human_labels":
                "No human ratings were supplied; HUMAN_LEVEL remains unproven.",
            "live_platform_metrics":
                "No live post-publish metrics exist for this decision pack.",
            "duplicate_render_candidate": (
                "candidate-3 is byte-identical to candidate-1 and is "
                "retained as an alias; R18 compares distinct render hashes."
            ),
        },
        "human_ground_truth":
            False,
        "synthetic_metrics_as_live":
            False,
        "authority": {
            "advisory_only": True,
            "provider_mutation": False,
            "publish_authorized": False,
            "creator_mutation": False,
            "media_mutation": False,
            "release_authorized": False,
        },
        "interpretation": (
            "This pack is produced from the exact Media R16 artifact bytes. "
            "Media objective QA passes, but no bound production VLM or human "
            "preference evidence satisfies R18 winner policy; the decision "
            "therefore remains tie/insufficient according to R18 rather than "
            "inventing a winner."
        ),
    }
    pack["pack_id"] = (
        "gradp21:"
        + sha256_json({
            "growth_commit_sha":
                growth_commit_sha,
            "growth_run_id":
                growth_run_id,
            "archive_sha256":
                archive[
                    "archive_sha256"
                ],
            "source_sha256":
                source["sha256"],
            "decision_digest":
                decision[
                    "decision_digest"
                ],
            "feedback_digest":
                feedback[
                    "bundle_digest"
                ],
            "pack_revision":
                pack_revision,
        })
    )
    digest_material = dict(pack)
    digest_material["pack_digest"] = ""
    pack["pack_digest"] = sha256_json(
        digest_material
    )
    return parse_real_artifact_decision_pack(
        pack
    )


def parse_real_artifact_decision_pack(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    required = {
        "contract_version",
        "pack_id",
        "pack_digest",
        "pack_revision",
        "growth_authority",
        "media_authority",
        "batch",
        "source",
        "candidates",
        "content_equivalence_groups",
        "r18_distinct_render_candidate_ids",
        "r18_duplicate_aliases",
        "r18_decision",
        "r20_feedback",
        "evidence_classes",
        "targeted_reedit_guidance",
        "unavailable_evidence",
        "human_ground_truth",
        "synthetic_metrics_as_live",
        "authority",
        "interpretation",
    }
    if (
        not isinstance(payload, Mapping)
        or set(payload) != required
    ):
        raise RealArtifactDecisionError(
            "real-artifact pack fields must match R21 exactly"
        )
    if (
        payload["contract_version"]
        != REAL_ARTIFACT_DECISION_PACK_VERSION
    ):
        raise RealArtifactDecisionError(
            "unsupported R21 decision pack version"
        )
    revision = _positive_int(
        payload["pack_revision"],
        "pack_revision",
    )
    growth = payload[
        "growth_authority"
    ]
    if (
        not isinstance(growth, Mapping)
        or set(growth)
        != {
            "repository",
            "commit_sha",
            "run_id",
        }
        or growth["repository"]
        != "foto6/video3"
    ):
        raise RealArtifactDecisionError(
            "Growth authority invalid"
        )
    _sha1(
        growth["commit_sha"],
        "growth commit SHA",
    )
    run_id = _nonempty(
        growth["run_id"],
        "growth run id",
    )
    if not run_id.isdigit():
        raise RealArtifactDecisionError(
            "growth run id invalid"
        )

    media = payload[
        "media_authority"
    ]
    expected_media = {
        "repository":
            MEDIA_REPOSITORY,
        "producer_sha":
            MEDIA_PRODUCER_SHA,
        "run_id":
            MEDIA_RUN_ID,
        "artifact_id":
            MEDIA_ARTIFACT_ID,
        "artifact_name":
            MEDIA_ARTIFACT_NAME,
        "archive_sha256":
            MEDIA_ARCHIVE_SHA256,
    }
    if (
        not isinstance(media, Mapping)
        or any(
            media.get(key) != value
            for key, value
            in expected_media.items()
        )
    ):
        raise RealArtifactDecisionError(
            "Media authority pin mismatch"
        )
    _positive_int(
        media.get("archive_size"),
        "media archive size",
    )
    source = payload["source"]
    if source != {
        "source_id":
            MEDIA_SOURCE_ID,
        "sha256":
            MEDIA_SOURCE_SHA256,
        "size":
            MEDIA_SOURCE_SIZE,
    }:
        raise RealArtifactDecisionError(
            "R21 source lineage mismatch"
        )

    candidates = payload[
        "candidates"
    ]
    if (
        not isinstance(candidates, list)
        or len(candidates)
        != 3
    ):
        raise RealArtifactDecisionError(
            "R21 candidate set must contain exact Media batch candidates"
        )
    ids: set[str] = set()
    render_to_ids: dict[
        str,
        list[str],
    ] = {}
    for row in candidates:
        if not isinstance(row, Mapping):
            raise RealArtifactDecisionError(
                "R21 candidate entry invalid"
            )
        cid = _nonempty(
            row.get("candidate_id"),
            "candidate_id",
        )
        if cid in ids:
            raise RealArtifactDecisionError(
                "duplicate R21 candidate id"
            )
        ids.add(cid)
        render_sha = _sha256_hex(
            row.get(
                "render_sha256"
            ),
            "candidate render SHA",
        )
        _positive_int(
            row.get("render_size"),
            "candidate render size",
        )
        _sha256_hex(
            row.get(
                "render_export_sha256"
            ),
            "render-export SHA",
        )
        export = row.get(
            "critic_export"
        )
        try:
            parsed_export = (
                validate_critic_export(
                    export
                )
            )
        except CriticExportError as exc:
            raise RealArtifactDecisionError(
                "invalid candidate critic export"
            ) from exc
        if (
            parsed_export[
                "render_sha256"
            ]
            != render_sha
            or parsed_export[
                "human_ground_truth"
            ]
            is not False
        ):
            raise RealArtifactDecisionError(
                "critic export/render or human boundary mismatch"
            )
        if (
            sha256_json(
                parsed_export
            )
            != row.get(
                "critic_export_digest"
            )
        ):
            raise RealArtifactDecisionError(
                "critic export digest mismatch"
            )
        render_to_ids.setdefault(
            render_sha,
            [],
        ).append(cid)
    if ids != {
        "candidate-1",
        "candidate-2",
        "candidate-3",
    }:
        raise RealArtifactDecisionError(
            "R21 candidate IDs mismatch exact Media artifact"
        )
    if sorted(
        render_to_ids.get(
            _EXPECTED_CANDIDATES[0][
                "render_sha256"
            ],
            [],
        )
    ) != [
        "candidate-1",
        "candidate-3",
    ]:
        raise RealArtifactDecisionError(
            "byte-identical candidate alias evidence mismatch"
        )

    try:
        decision = parse_candidate_decision(
            payload["r18_decision"]
        )
    except CandidateDecisionError as exc:
        raise RealArtifactDecisionError(
            "invalid embedded R18 decision"
        ) from exc
    if (
        decision["source_id"]
        != MEDIA_SOURCE_ID
        or decision[
            "source_sha256"
        ] != MEDIA_SOURCE_SHA256
        or decision[
            "cycle_revision"
        ] != MEDIA_CYCLE_REVISION
    ):
        raise RealArtifactDecisionError(
            "R18 source/cycle mismatch"
        )
    reps = payload[
        "r18_distinct_render_candidate_ids"
    ]
    if (
        reps
        != [
            "candidate-1",
            "candidate-2",
        ]
        or decision[
            "expected_candidate_ids"
        ] != reps
    ):
        raise RealArtifactDecisionError(
            "R18 distinct-render representatives mismatch"
        )
    if payload[
        "r18_duplicate_aliases"
    ] != {
        "candidate-3":
            "candidate-1"
    }:
        raise RealArtifactDecisionError(
            "R18 duplicate alias mapping mismatch"
        )
    if decision["decision"] == "winner":
        raise RealArtifactDecisionError(
            "R21 cannot claim winner without sufficient real evidence"
        )

    try:
        feedback = (
            parse_closed_loop_feedback(
                payload[
                    "r20_feedback"
                ]
            )
        )
    except ClosedLoopFeedbackError as exc:
        raise RealArtifactDecisionError(
            "invalid embedded R20 feedback"
        ) from exc
    if (
        feedback["lineage"]["r18"][
            "decision_digest"
        ]
        != decision[
            "decision_digest"
        ]
        or feedback["lineage"][
            "r19"
        ] is not None
    ):
        raise RealArtifactDecisionError(
            "R20 feedback lineage mismatch"
        )

    evidence = payload[
        "evidence_classes"
    ]
    if (
        not isinstance(evidence, Mapping)
        or evidence[
            "model_aesthetic_judgment"
        ].get(
            "human_ground_truth"
        ) is not False
        or evidence[
            "human_evidence"
        ].get(
            "human_ground_truth"
        ) is not False
        or evidence[
            "live_platform_metrics"
        ].get(
            "synthetic_as_live"
        ) is not False
        or evidence[
            "live_platform_metrics"
        ].get("present")
        is not False
    ):
        raise RealArtifactDecisionError(
            "R21 evidence-class separation violated"
        )
    if (
        payload[
            "human_ground_truth"
        ] is not False
        or payload[
            "synthetic_metrics_as_live"
        ] is not False
    ):
        raise RealArtifactDecisionError(
            "R21 human/live evidence boundary violated"
        )
    if (
        payload[
            "targeted_reedit_guidance"
        ]
        != decision[
            "reedit_guidance"
        ]
    ):
        raise RealArtifactDecisionError(
            "R21 re-edit guidance must come only from R18 evidence"
        )
    if payload["authority"] != {
        "advisory_only": True,
        "provider_mutation": False,
        "publish_authorized": False,
        "creator_mutation": False,
        "media_mutation": False,
        "release_authorized": False,
    }:
        raise RealArtifactDecisionError(
            "R21 authority boundary invalid"
        )

    expected_id = (
        "gradp21:"
        + sha256_json({
            "growth_commit_sha":
                growth["commit_sha"],
            "growth_run_id":
                growth["run_id"],
            "archive_sha256":
                media[
                    "archive_sha256"
                ],
            "source_sha256":
                source["sha256"],
            "decision_digest":
                decision[
                    "decision_digest"
                ],
            "feedback_digest":
                feedback[
                    "bundle_digest"
                ],
            "pack_revision":
                revision,
        })
    )
    if payload["pack_id"] != expected_id:
        raise RealArtifactDecisionError(
            "R21 pack identity mismatch"
        )
    _sha256_hex(
        payload["pack_digest"],
        "R21 pack digest",
    )
    material = dict(payload)
    material["pack_digest"] = ""
    if (
        sha256_json(material)
        != payload["pack_digest"]
    ):
        raise RealArtifactDecisionError(
            "R21 pack digest mismatch"
        )
    return json.loads(
        canonical_json(dict(payload))
    )


class RealArtifactDecisionLedger:
    def __init__(
        self,
        path: str | Path,
    ) -> None:
        self.path = Path(path)
        self._rows: list[
            dict[str, Any]
        ] = []
        self._latest: dict[
            str,
            dict[str, Any],
        ] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line_no, line in enumerate(
            self.path.read_text(
                encoding="utf-8"
            ).splitlines(),
            1,
        ):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RealArtifactDecisionConflictError(
                    f"invalid R21 ledger line {line_no}"
                ) from exc
            if (
                not isinstance(row, Mapping)
                or set(row)
                != {
                    "ledger_version",
                    "sequence",
                    "pack",
                }
                or row[
                    "ledger_version"
                ]
                != REAL_ARTIFACT_LEDGER_VERSION
                or row["sequence"]
                != len(self._rows) + 1
            ):
                raise RealArtifactDecisionConflictError(
                    "R21 ledger row invalid"
                )
            pack = (
                parse_real_artifact_decision_pack(
                    row["pack"]
                )
            )
            key = pack[
                "media_authority"
            ]["archive_sha256"]
            previous = self._latest.get(
                key
            )
            if (
                previous is not None
                and pack[
                    "pack_revision"
                ]
                <= previous[
                    "pack_revision"
                ]
            ):
                raise RealArtifactDecisionConflictError(
                    "R21 ledger revisions not strictly increasing"
                )
            self._rows.append(dict(row))
            self._latest[key] = pack

    def record(
        self,
        pack: Mapping[str, Any],
    ) -> str:
        parsed = (
            parse_real_artifact_decision_pack(
                pack
            )
        )
        key = parsed[
            "media_authority"
        ]["archive_sha256"]
        previous = self._latest.get(key)
        if previous is not None:
            revision = parsed[
                "pack_revision"
            ]
            previous_revision = previous[
                "pack_revision"
            ]
            if revision < previous_revision:
                raise RealArtifactDecisionOutOfOrder(
                    "R21 pack revision older than durable state"
                )
            if revision == previous_revision:
                if (
                    parsed["pack_digest"]
                    != previous[
                        "pack_digest"
                    ]
                ):
                    raise RealArtifactDecisionConflictError(
                        "same R21 revision changed payload"
                    )
                return "duplicate"
        row = {
            "ledger_version":
                REAL_ARTIFACT_LEDGER_VERSION,
            "sequence":
                len(self._rows) + 1,
            "pack": parsed,
        }
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        with self.path.open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            handle.write(
                canonical_json(row)
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        self._rows.append(row)
        self._latest[key] = parsed
        return "accepted"


def produce_real_artifact_decision_pack(
    *,
    artifact_zip: str | Path,
    output_dir: str | Path,
    growth_commit_sha: str,
    growth_run_id: str,
    pack_revision: int = 1,
    authority: MediaArtifactAuthority | None = None,
) -> dict[str, Any]:
    artifact_path = Path(
        artifact_zip
    )
    archive_bytes = (
        artifact_path.read_bytes()
    )
    verification = (
        verify_media_candidate_batch_archive(
            archive_bytes,
            authority=authority,
        )
    )
    pack = build_real_artifact_decision_pack(
        verification,
        growth_commit_sha=
            growth_commit_sha,
        growth_run_id=
            growth_run_id,
        pack_revision=
            pack_revision,
    )
    output = Path(output_dir)
    output.mkdir(
        parents=True,
        exist_ok=True,
    )
    files: dict[str, bytes] = {
        "growth.real_artifact_decision_pack.v1.json":
            (
                canonical_json(pack)
                + "\n"
            ).encode("utf-8"),
        "growth.real_artifact_verification.r21.v1.json":
            (
                canonical_json({
                    key: value
                    for key, value
                    in verification.items()
                    if key != "candidates"
                } | {
                    "candidates": [
                        {
                            key: value
                            for key, value
                            in candidate.items()
                            if key
                            != "render_export"
                        }
                        for candidate
                        in verification[
                            "candidates"
                        ]
                    ]
                })
                + "\n"
            ).encode("utf-8"),
        "growth.candidate_decision.v1.json":
            (
                canonical_json(
                    pack[
                        "r18_decision"
                    ]
                )
                + "\n"
            ).encode("utf-8"),
        "growth.closed_loop_feedback.v1.json":
            (
                canonical_json(
                    pack[
                        "r20_feedback"
                    ]
                )
                + "\n"
            ).encode("utf-8"),
    }
    for candidate in pack[
        "candidates"
    ]:
        name = (
            "critic/"
            + candidate[
                "candidate_id"
            ]
            + "/growth.critic_export.v1.json"
        )
        files[name] = (
            canonical_json(
                candidate[
                    "critic_export"
                ]
            )
            + "\n"
        ).encode("utf-8")
    output_manifest = {
        "manifest_version":
            "growth.real_artifact_decision_output_manifest.r21.v1",
        "growth_commit_sha":
            growth_commit_sha,
        "growth_run_id":
            str(growth_run_id),
        "media_archive_sha256":
            verification[
                "archive"
            ]["archive_sha256"],
        "pack_id":
            pack["pack_id"],
        "pack_digest":
            pack["pack_digest"],
        "files": {},
    }
    for name, data in sorted(
        files.items()
    ):
        destination = output / name
        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        destination.write_bytes(data)
        output_manifest[
            "files"
        ][name] = {
            "sha256":
                _sha256_bytes(data),
            "size":
                len(data),
        }
    manifest_bytes = (
        canonical_json(
            output_manifest
        )
        + "\n"
    ).encode("utf-8")
    (
        output
        / "growth.real_artifact_decision_output_manifest.r21.v1.json"
    ).write_bytes(
        manifest_bytes
    )
    return {
        "pack": pack,
        "verification":
            verification,
        "output_manifest":
            output_manifest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify exact Media R16 candidate-batch artifact bytes and "
            "produce the Growth R21 Human Gate V4 decision pack."
        )
    )
    parser.add_argument(
        "--artifact-zip",
        required=True,
    )
    parser.add_argument(
        "--output-dir",
        required=True,
    )
    parser.add_argument(
        "--growth-sha",
        required=True,
    )
    parser.add_argument(
        "--growth-run-id",
        required=True,
    )
    parser.add_argument(
        "--pack-revision",
        type=int,
        default=1,
    )
    args = parser.parse_args()
    result = (
        produce_real_artifact_decision_pack(
            artifact_zip=
                args.artifact_zip,
            output_dir=
                args.output_dir,
            growth_commit_sha=
                args.growth_sha,
            growth_run_id=
                args.growth_run_id,
            pack_revision=
                args.pack_revision,
        )
    )
    pack = result["pack"]
    print(canonical_json({
        "contract_version":
            pack[
                "contract_version"
            ],
        "pack_id":
            pack["pack_id"],
        "pack_digest":
            pack["pack_digest"],
        "decision":
            pack[
                "r18_decision"
            ]["decision"],
        "decision_reason":
            pack[
                "r18_decision"
            ]["reason"],
        "winner_candidate_id":
            pack[
                "r18_decision"
            ][
                "winner_candidate_id"
            ],
        "candidate_refs": [
            {
                "candidate_id":
                    row[
                        "candidate_id"
                    ],
                "render_sha256":
                    row[
                        "render_sha256"
                    ],
                "render_size":
                    row[
                        "render_size"
                    ],
                "render_export_sha256":
                    row[
                        "render_export_sha256"
                    ],
                "r18_representative":
                    row[
                        "r18_representative"
                    ],
                "duplicate_of_candidate_id":
                    row[
                        "duplicate_of_candidate_id"
                    ],
            }
            for row in
            pack["candidates"]
        ],
        "media_archive_sha256":
            pack[
                "media_authority"
            ][
                "archive_sha256"
            ],
        "media_producer_sha":
            pack[
                "media_authority"
            ]["producer_sha"],
        "growth_commit_sha":
            pack[
                "growth_authority"
            ]["commit_sha"],
        "growth_run_id":
            pack[
                "growth_authority"
            ]["run_id"],
        "human_ground_truth":
            pack[
                "human_ground_truth"
            ],
        "synthetic_metrics_as_live":
            pack[
                "synthetic_metrics_as_live"
            ],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
