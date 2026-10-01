from __future__ import annotations

import copy
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

from growth_analytics.autonomous_reels import sha256_json
from growth_analytics.real_artifact_decision import (
    MEDIA_ARCHIVE_SHA256,
    MEDIA_ARTIFACT_ID,
    MEDIA_PRODUCER_SHA,
    MEDIA_SOURCE_SHA256,
    REAL_ARTIFACT_DECISION_PACK_VERSION,
    MediaArtifactAuthority,
    RealArtifactDecisionConflictError,
    RealArtifactDecisionError,
    RealArtifactDecisionLedger,
    RealArtifactDecisionOutOfOrder,
    RealArtifactTamperError,
    build_real_artifact_decision_pack,
    parse_real_artifact_decision_pack,
    verify_media_candidate_batch_archive,
)


BASE_SHA = "65ddf2aadfdb1eb8d3d7f3590331c1a5ea0b44bd"


def _canonical_sha(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _render_export(
    *,
    producer_sha,
    source_id,
    source_sha,
    source_size,
    render_sha,
    render_size,
    suffix,
):
    return {
        "contractVersion":
            "media.render_export.v1",
        "status": "succeeded",
        "producer": {
            "repository": "foto6/video2",
            "sha": producer_sha,
        },
        "artifact": {
            "sha256": render_sha,
            "size": render_size,
        },
        "evidence": {
            "sources": {
                "items": [{
                    "sourceId": source_id,
                    "sha256": source_sha,
                    "size": source_size,
                    "probeOk": True,
                }]
            }
        },
        "qa": {
            "technical": {
                "passed": True,
                "sha256": ("a" * 63) + suffix,
            },
            "creative": {
                "passed": True,
                "sha256": ("b" * 63) + suffix,
            },
        },
        "benchmark": {
            "technicalDq": False,
        },
        "job": {
            "renderFingerprint":
                ("c" * 63) + suffix,
        },
        "provenance": {
            "timelineDigest":
                ("d" * 63) + suffix,
            "creativePlanDigest":
                ("e" * 63) + suffix,
        },
    }


def _build_test_archive(
    *,
    producer_sha="1" * 40,
    source_bytes=b"source-bytes",
    candidate_bytes=None,
    omit_candidate=None,
    duplicate_candidate=False,
):
    if candidate_bytes is None:
        candidate_bytes = {
            "candidate-1": b"candidate-one",
            "candidate-2": b"candidate-two",
        }
    source_id = "test-source"
    source_sha = hashlib.sha256(
        source_bytes
    ).hexdigest()
    candidates = []
    files = {}
    expected = []
    for index, candidate_id in enumerate(
        ("candidate-1", "candidate-2")
    ):
        if candidate_id == omit_candidate:
            continue
        data = candidate_bytes[candidate_id]
        render_sha = hashlib.sha256(
            data
        ).hexdigest()
        export = _render_export(
            producer_sha=producer_sha,
            source_id=source_id,
            source_sha=source_sha,
            source_size=len(source_bytes),
            render_sha=render_sha,
            render_size=len(data),
            suffix=str(index + 1),
        )
        export_bytes = (
            json.dumps(
                export,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        export_sha = hashlib.sha256(
            export_bytes
        ).hexdigest()
        candidate = {
            "candidateId": candidate_id,
            "order": index,
            "status": "succeeded",
            "failure": None,
            "planDigest":
                hashlib.sha256(
                    (candidate_id + ":plan").encode()
                ).hexdigest(),
            "cacheIdentityDigest":
                hashlib.sha256(
                    (candidate_id + ":cache").encode()
                ).hexdigest(),
            "final": {
                "relativePath":
                    f"candidates/{candidate_id}/final.mp4",
                "renderExportRelativePath":
                    f"candidates/{candidate_id}/media.render_export.v1.json",
                "sha256": render_sha,
                "size": len(data),
                "renderExportSha256":
                    export_sha,
            },
        }
        candidates.append(candidate)
        files[
            f"batch/candidates/{candidate_id}/final.mp4"
        ] = data
        files[
            f"batch/candidates/{candidate_id}/media.render_export.v1.json"
        ] = export_bytes
        expected.append({
            "candidate_id": candidate_id,
            "order": index,
            "render_sha256": render_sha,
            "render_size": len(data),
            "render_export_sha256":
                export_sha,
        })
    if duplicate_candidate and candidates:
        candidates.append(
            copy.deepcopy(candidates[0])
        )
    manifest = {
        "contractVersion":
            "media.candidate_batch.v1",
        "batchId": "test-batch",
        "status": "succeeded",
        "producer": {
            "repository": "foto6/video2",
            "sha": producer_sha,
        },
        "requestDigest": "f" * 64,
        "source": {
            "sourceId": source_id,
            "sha256": source_sha,
            "size": len(source_bytes),
        },
        "candidates": candidates,
    }
    evidence = {
        "producer": {
            "repository": "foto6/video2",
            "sha": producer_sha,
        },
        "manifestDigest":
            _canonical_sha(manifest),
    }
    files[
        "batch/media.candidate_batch.v1.json"
    ] = (
        json.dumps(
            manifest,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    files[
        "batch/media.candidate_batch.r16.evidence.json"
    ] = (
        json.dumps(
            evidence,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    files["source.mp4"] = source_bytes

    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for name in sorted(files):
            archive.writestr(
                name,
                files[name],
            )
    archive_bytes = buffer.getvalue()
    authority = MediaArtifactAuthority(
        repository="foto6/video2",
        producer_sha=producer_sha,
        run_id=1,
        artifact_id=2,
        artifact_name="test-artifact",
        archive_sha256=
            hashlib.sha256(
                archive_bytes
            ).hexdigest(),
        batch_id="test-batch",
        source_id=source_id,
        source_sha256=source_sha,
        source_size=len(source_bytes),
        expected_candidates=
            tuple(expected),
    )
    return archive_bytes, authority


class GrowthR21RealArtifactDecisionTests(
    unittest.TestCase
):
    @classmethod
    def setUpClass(cls):
        cls.root = (
            Path(__file__).resolve().parents[1]
        )
        cls.verification = json.loads(
            (
                cls.root
                / "fixtures"
                / "real_artifact_decision_r21"
                / "independent_media_r16_verification.json"
            ).read_text(encoding="utf-8")
        )

    def pack(self, revision=1):
        return build_real_artifact_decision_pack(
            self.verification,
            growth_commit_sha=BASE_SHA,
            growth_run_id="999001",
            pack_revision=revision,
        )

    def test_real_media_authority_and_candidate_refs_are_exact(self):
        pack = self.pack()
        self.assertEqual(
            pack["contract_version"],
            REAL_ARTIFACT_DECISION_PACK_VERSION,
        )
        self.assertEqual(
            pack["media_authority"][
                "producer_sha"
            ],
            MEDIA_PRODUCER_SHA,
        )
        self.assertEqual(
            pack["media_authority"][
                "artifact_id"
            ],
            MEDIA_ARTIFACT_ID,
        )
        self.assertEqual(
            pack["media_authority"][
                "archive_sha256"
            ],
            MEDIA_ARCHIVE_SHA256,
        )
        self.assertEqual(
            pack["source"]["sha256"],
            MEDIA_SOURCE_SHA256,
        )
        refs = {
            row["candidate_id"]: (
                row["render_sha256"],
                row["render_size"],
                row["render_export_sha256"],
            )
            for row in pack["candidates"]
        }
        self.assertEqual(
            refs,
            {
                "candidate-1": (
                    "3bd12d999264cb932cb3ef15dfbd96e002ede517f2273795b593553b9642d864",
                    575465,
                    "2b5177c00bb054184a239c7eddb1383ad253922e8eb686f5aa2e56c91a1335ac",
                ),
                "candidate-2": (
                    "cdae63d5ccce67332e607af7fd87b18c31da8dc77c2f0ce5182550321767286c",
                    576763,
                    "b349b897f8f86bc9257e2622f564d6430f3864ff605ae4b82ba0cb09c59298f6",
                ),
                "candidate-3": (
                    "3bd12d999264cb932cb3ef15dfbd96e002ede517f2273795b593553b9642d864",
                    575465,
                    "26fe61f0644e263ce047869334ba65c5973eba809ff45f76286490f573d61b8e",
                ),
            },
        )

    def test_duplicate_render_is_explicit_alias_and_r18_abstains(self):
        pack = self.pack()
        self.assertEqual(
            pack[
                "r18_distinct_render_candidate_ids"
            ],
            [
                "candidate-1",
                "candidate-2",
            ],
        )
        self.assertEqual(
            pack[
                "r18_duplicate_aliases"
            ],
            {
                "candidate-3":
                    "candidate-1"
            },
        )
        self.assertEqual(
            pack["r18_decision"][
                "decision"
            ],
            "insufficient_evidence",
        )
        self.assertIsNone(
            pack["r18_decision"][
                "winner_candidate_id"
            ]
        )
        self.assertEqual(
            pack["r18_decision"][
                "reason"
            ],
            "too_few_comparable_rule_dimensions",
        )

    def test_evidence_class_separation_and_no_fake_human_or_live(self):
        pack = self.pack()
        self.assertTrue(
            pack[
                "evidence_classes"
            ]["objective_qa"][
                "present"
            ]
        )
        self.assertFalse(
            pack[
                "evidence_classes"
            ][
                "model_aesthetic_judgment"
            ][
                "human_ground_truth"
            ]
        )
        self.assertFalse(
            pack[
                "evidence_classes"
            ]["human_evidence"][
                "present"
            ]
        )
        self.assertFalse(
            pack[
                "evidence_classes"
            ][
                "live_platform_metrics"
            ]["present"]
        )
        self.assertFalse(
            pack["human_ground_truth"]
        )
        self.assertFalse(
            pack[
                "synthetic_metrics_as_live"
            ]
        )
        self.assertEqual(
            pack[
                "targeted_reedit_guidance"
            ],
            [],
        )

    def test_model_as_human_is_rejected(self):
        pack = self.pack()
        tampered = copy.deepcopy(pack)
        tampered[
            "evidence_classes"
        ][
            "model_aesthetic_judgment"
        ][
            "human_ground_truth"
        ] = True
        tampered["pack_digest"] = ""
        tampered["pack_digest"] = (
            sha256_json(tampered)
        )
        with self.assertRaises(
            RealArtifactDecisionError
        ):
            parse_real_artifact_decision_pack(
                tampered
            )

    def test_synthetic_as_live_is_rejected(self):
        pack = self.pack()
        tampered = copy.deepcopy(pack)
        tampered[
            "evidence_classes"
        ][
            "live_platform_metrics"
        ]["present"] = True
        tampered[
            "evidence_classes"
        ][
            "live_platform_metrics"
        ]["synthetic_as_live"] = True
        tampered[
            "synthetic_metrics_as_live"
        ] = True
        tampered["pack_digest"] = ""
        tampered["pack_digest"] = (
            sha256_json(tampered)
        )
        with self.assertRaises(
            RealArtifactDecisionError
        ):
            parse_real_artifact_decision_pack(
                tampered
            )

    def test_archive_digest_mismatch_rejected(self):
        archive, authority = (
            _build_test_archive()
        )
        verified = (
            verify_media_candidate_batch_archive(
                archive,
                authority=authority,
            )
        )
        self.assertTrue(
            verified[
                "verification"
            ][
                "archive_digest_verified"
            ]
        )
        with self.assertRaises(
            RealArtifactTamperError
        ):
            verify_media_candidate_batch_archive(
                archive + b"x",
                authority=authority,
            )

    def test_candidate_hash_mismatch_rejected(self):
        archive, authority = (
            _build_test_archive()
        )
        with zipfile.ZipFile(
            io.BytesIO(archive),
            "r",
        ) as z:
            files = {
                name: z.read(name)
                for name in z.namelist()
            }
        files[
            "batch/candidates/candidate-1/final.mp4"
        ] += b"tamper"
        buffer = io.BytesIO()
        with zipfile.ZipFile(
            buffer,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as z:
            for name in sorted(files):
                z.writestr(
                    name,
                    files[name],
                )
        tampered = buffer.getvalue()
        authority = replace(
            authority,
            archive_sha256=
                hashlib.sha256(
                    tampered
                ).hexdigest(),
        )
        with self.assertRaises(
            RealArtifactTamperError
        ):
            verify_media_candidate_batch_archive(
                tampered,
                authority=authority,
            )

    def test_wrong_render_export_hash_rejected(self):
        archive, authority = (
            _build_test_archive()
        )
        with zipfile.ZipFile(
            io.BytesIO(archive),
            "r",
        ) as z:
            files = {
                name: z.read(name)
                for name in z.namelist()
            }
        files[
            "batch/candidates/candidate-2/media.render_export.v1.json"
        ] += b" "
        buffer = io.BytesIO()
        with zipfile.ZipFile(
            buffer,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as z:
            for name in sorted(files):
                z.writestr(
                    name,
                    files[name],
                )
        tampered = buffer.getvalue()
        authority = replace(
            authority,
            archive_sha256=
                hashlib.sha256(
                    tampered
                ).hexdigest(),
        )
        with self.assertRaises(
            RealArtifactTamperError
        ):
            verify_media_candidate_batch_archive(
                tampered,
                authority=authority,
            )

    def test_wrong_source_rejected(self):
        archive, authority = (
            _build_test_archive()
        )
        with zipfile.ZipFile(
            io.BytesIO(archive),
            "r",
        ) as z:
            files = {
                name: z.read(name)
                for name in z.namelist()
            }
        files["source.mp4"] = b"wrong-source"
        buffer = io.BytesIO()
        with zipfile.ZipFile(
            buffer,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as z:
            for name in sorted(files):
                z.writestr(
                    name,
                    files[name],
                )
        tampered = buffer.getvalue()
        authority = replace(
            authority,
            archive_sha256=
                hashlib.sha256(
                    tampered
                ).hexdigest(),
        )
        with self.assertRaises(
            RealArtifactTamperError
        ):
            verify_media_candidate_batch_archive(
                tampered,
                authority=authority,
            )

    def test_duplicate_or_missing_candidate_rejected(self):
        missing, missing_authority = (
            _build_test_archive(
                omit_candidate="candidate-2",
            )
        )
        expected_full = (
            _build_test_archive()[1]
            .expected_candidates
        )
        missing_authority = replace(
            missing_authority,
            expected_candidates=
                expected_full,
        )
        with self.assertRaises(
            RealArtifactTamperError
        ):
            verify_media_candidate_batch_archive(
                missing,
                authority=
                    missing_authority,
            )

        duplicate, duplicate_authority = (
            _build_test_archive(
                duplicate_candidate=True,
            )
        )
        with self.assertRaises(
            RealArtifactTamperError
        ):
            verify_media_candidate_batch_archive(
                duplicate,
                authority=
                    duplicate_authority,
            )

    def test_stale_producer_pin_rejected(self):
        archive, authority = (
            _build_test_archive(
                producer_sha="1" * 40,
            )
        )
        stale = replace(
            authority,
            producer_sha="2" * 40,
        )
        with self.assertRaises(
            RealArtifactTamperError
        ):
            verify_media_candidate_batch_archive(
                archive,
                authority=stale,
            )

    def test_ledger_duplicate_conflict_restart_and_out_of_order(self):
        first = self.pack(1)
        second = self.pack(2)
        with tempfile.TemporaryDirectory() as temp:
            path = (
                Path(temp)
                / "r21-ledger.jsonl"
            )
            ledger = RealArtifactDecisionLedger(
                path
            )
            self.assertEqual(
                ledger.record(first),
                "accepted",
            )
            self.assertEqual(
                ledger.record(first),
                "duplicate",
            )
            restarted = (
                RealArtifactDecisionLedger(
                    path
                )
            )
            self.assertEqual(
                restarted.record(second),
                "accepted",
            )
            with self.assertRaises(
                RealArtifactDecisionOutOfOrder
            ):
                restarted.record(first)

            changed = copy.deepcopy(
                second
            )
            changed[
                "unavailable_evidence"
            ]["tamper"] = "changed"
            changed["pack_digest"] = ""
            changed["pack_digest"] = (
                sha256_json(changed)
            )
            changed = (
                parse_real_artifact_decision_pack(
                    changed
                )
            )
            with self.assertRaises(
                RealArtifactDecisionConflictError
            ):
                restarted.record(changed)

    def test_readiness_and_conformance_pin_real_candidate_evidence(self):
        readiness = json.loads(
            (
                self.root
                / "fixtures"
                / "real_artifact_decision_r21"
                / "readiness_report.json"
            ).read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (
                self.root
                / "conformance"
                / "growth.real_artifact_decision_pack.v1"
                / "manifest.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            readiness["media_authority"]["archive_sha256"],
            MEDIA_ARCHIVE_SHA256,
        )
        self.assertEqual(
            readiness["validated_candidate_ci"]["run_id"],
            36876333683,
        )
        self.assertEqual(
            readiness["candidate_workflow_artifact"]["artifact_id"],
            11169406955,
        )
        self.assertEqual(
            readiness["decision"]["decision"],
            "insufficient_evidence",
        )
        self.assertIsNone(
            readiness["decision"]["winner_candidate_id"]
        )
        self.assertFalse(
            readiness["evidence_separation"]["human_ground_truth"]
        )
        self.assertFalse(
            readiness["evidence_separation"]["synthetic_metrics_as_live"]
        )
        self.assertEqual(
            manifest["media_authority"]["producer_sha"],
            MEDIA_PRODUCER_SHA,
        )
        self.assertEqual(
            manifest["exact_source"]["sha256"],
            MEDIA_SOURCE_SHA256,
        )
        self.assertEqual(
            manifest["decision_evidence"]["decision_digest"],
            "5f577349d028198d48bb74e27ea808d183d35fba5874eca9e92c845b9bb6b36e",
        )
        self.assertEqual(
            manifest["candidate_validation"]["workflow_artifact"]["zip_sha256"],
            "45a39ed43fe09f45347095134b71488299471696e57c90c8df3b67ecec8ba01e",
        )
        self.assertEqual(
            manifest["distinct_render_semantics"]["duplicate_aliases"],
            {"candidate-3": "candidate-1"},
        )


if __name__ == "__main__":
    unittest.main()
