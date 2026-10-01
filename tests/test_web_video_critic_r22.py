from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from growth_analytics.web_video_critic import (
    WEB_VIDEO_ATTACHED_MODE,
    WEB_VIDEO_CRITIC_VERSION,
    WEB_VIDEO_FIXTURE_MODE,
    WEB_VIDEO_INTEGRATION_STATE,
    WEB_VIDEO_PAIRWISE_VERSION,
    WebVideoCriticBoundaryError,
    WebVideoCriticError,
    WebVideoCriticLineageError,
    build_web_video_critic_input,
    build_web_video_critic_output,
    build_web_video_observation,
    build_web_video_pairwise_input,
    build_web_video_pairwise_output,
    parse_web_video_critic_input,
    parse_web_video_critic_output,
    parse_web_video_pairwise_input,
    parse_web_video_pairwise_output,
)


class GrowthR22WebVideoCriticTests(
    unittest.TestCase
):
    @classmethod
    def setUpClass(cls):
        cls.root = (
            Path(__file__).resolve().parents[1]
        )
        cls.fixture = json.loads(
            (
                cls.root
                / "fixtures"
                / "web_video_critic_r22"
                / "fixture_spec.json"
            ).read_text(
                encoding="utf-8"
            )
        )

    def critic_input(
        self,
        candidate_index=0,
    ):
        source = self.fixture[
            "source"
        ]
        media = self.fixture[
            "media"
        ]
        candidate = self.fixture[
            "candidates"
        ][candidate_index]
        brief = self.fixture[
            "brief"
        ]
        return build_web_video_critic_input(
            source_id=
                source["source_id"],
            source_sha256=
                source["sha256"],
            source_size=
                source["size"],
            media_repository=
                media["repository"],
            media_producer_sha=
                media[
                    "producer_sha"
                ],
            candidate_id=
                candidate[
                    "candidate_id"
                ],
            render_sha256=
                candidate[
                    "render_sha256"
                ],
            render_size=
                candidate[
                    "render_size"
                ],
            render_export_sha256=
                candidate[
                    "render_export_sha256"
                ],
            review_bundle_digest=
                self.fixture[
                    "review_bundle"
                ]["digest"],
            attachment_sha256=
                candidate[
                    "render_sha256"
                ],
            attachment_size=
                candidate[
                    "render_size"
                ],
            attachment_mime_type=
                "video/mp4",
            review_goal=
                brief[
                    "review_goal"
                ],
            platform=
                brief["platform"],
            requested_focus=
                brief[
                    "requested_focus"
                ],
            constraints=
                brief[
                    "constraints"
                ],
        )

    def observations(
        self,
        critic_input,
    ):
        built = []
        for raw in self.fixture[
            "schema_only_observations"
        ]:
            built.append(
                build_web_video_observation(
                    input_digest=
                        critic_input[
                            "input_digest"
                        ],
                    scope=
                        raw["scope"],
                    start_ms=
                        raw["start_ms"],
                    end_ms=
                        raw["end_ms"],
                    defect_category=
                        raw[
                            "defect_category"
                        ],
                    severity=
                        raw["severity"],
                    evidence=
                        raw["evidence"],
                    description=
                        raw[
                            "description"
                        ],
                    proposed_edit=
                        raw[
                            "proposed_edit"
                        ],
                    confidence=
                        raw[
                            "confidence"
                        ],
                    uncertainty=
                        raw[
                            "uncertainty"
                        ],
                )
            )
        return built

    def critic_output(
        self,
        critic_input=None,
    ):
        critic_input = (
            critic_input
            or self.critic_input()
        )
        return build_web_video_critic_output(
            critic_input=critic_input,
            model_identity=
                "fixture-schema-validator",
            execution_mode=
                WEB_VIDEO_FIXTURE_MODE,
            inspected_ranges=[],
            coverage_notes=(
                "Fixture validation only. "
                "No attached MP4 was watched."
            ),
            observations=
                self.observations(
                    critic_input
                ),
            assessment=
                "insufficient_evidence",
            whole_video_summary=(
                "Fixture-only output validates schema and "
                "does not describe the real candidate video."
            ),
            summary_confidence=0.1,
            summary_uncertainty=(
                "Attachment transport is not independently green."
            ),
        )

    def test_input_binds_exact_r21_lineage_and_brief(self):
        payload = self.critic_input()
        self.assertEqual(
            payload[
                "contract_version"
            ],
            WEB_VIDEO_CRITIC_VERSION,
        )
        self.assertEqual(
            payload[
                "integration_state"
            ],
            WEB_VIDEO_INTEGRATION_STATE,
        )
        self.assertEqual(
            payload["source"][
                "sha256"
            ],
            "7b484abef5de1569e1b7f91a5d780f17c6d687ef68b3c42c9e54375f4e5e434b",
        )
        self.assertEqual(
            payload["media"][
                "producer_sha"
            ],
            "231a0680c8939cfec77aaa283e507e93f383ad73",
        )
        self.assertEqual(
            payload[
                "candidate"
            ]["render_sha256"],
            payload[
                "attachment"
            ]["sha256"],
        )
        self.assertEqual(
            payload[
                "candidate"
            ]["render_size"],
            payload[
                "attachment"
            ]["size"],
        )
        self.assertEqual(
            payload[
                "review_bundle"
            ]["digest"],
            "d415659c4a24dfd4198c6d08bb0fecf524086b0c2031d68395b739bf0dd631d3",
        )
        self.assertIn(
            "hook clarity",
            payload["brief"][
                "requested_focus"
            ][0],
        )

    def test_wrong_render_sha_or_attachment_size_fails_closed(self):
        candidate = self.fixture[
            "candidates"
        ][0]
        source = self.fixture[
            "source"
        ]
        media = self.fixture[
            "media"
        ]
        brief = self.fixture[
            "brief"
        ]
        with self.assertRaises(
            WebVideoCriticLineageError
        ):
            build_web_video_critic_input(
                source_id=
                    source[
                        "source_id"
                    ],
                source_sha256=
                    source["sha256"],
                source_size=
                    source["size"],
                media_repository=
                    media[
                        "repository"
                    ],
                media_producer_sha=
                    media[
                        "producer_sha"
                    ],
                candidate_id=
                    candidate[
                        "candidate_id"
                    ],
                render_sha256=
                    "0" * 64,
                render_size=
                    candidate[
                        "render_size"
                    ],
                render_export_sha256=
                    candidate[
                        "render_export_sha256"
                    ],
                review_bundle_digest=
                    self.fixture[
                        "review_bundle"
                    ]["digest"],
                attachment_sha256=
                    candidate[
                        "render_sha256"
                    ],
                attachment_size=
                    candidate[
                        "render_size"
                    ],
                attachment_mime_type=
                    "video/mp4",
                review_goal=
                    brief[
                        "review_goal"
                    ],
                platform=
                    brief[
                        "platform"
                    ],
                requested_focus=
                    brief[
                        "requested_focus"
                    ],
                constraints=
                    brief[
                        "constraints"
                    ],
            )

    def test_stale_source_producer_and_review_bundle_rejected(self):
        payload = self.critic_input()
        with self.assertRaises(
            WebVideoCriticLineageError
        ):
            parse_web_video_critic_input(
                payload,
                expected_source_sha256=
                    "0" * 64,
            )
        with self.assertRaises(
            WebVideoCriticLineageError
        ):
            parse_web_video_critic_input(
                payload,
                expected_media_producer_sha=
                    "0" * 40,
            )
        with self.assertRaises(
            WebVideoCriticLineageError
        ):
            parse_web_video_critic_input(
                payload,
                expected_review_bundle_digest=
                    "1" * 64,
            )

    def test_mismatched_attachment_identity_rejected(self):
        payload = copy.deepcopy(
            self.critic_input()
        )
        payload["attachment"][
            "attachment_identity"
        ] = "gvwa1:" + (
            "0" * 64
        )
        with self.assertRaises(
            WebVideoCriticLineageError
        ):
            parse_web_video_critic_input(
                payload
            )

    def test_local_defect_requires_bounded_timestamps(self):
        payload = self.critic_input()
        with self.assertRaises(
            WebVideoCriticBoundaryError
        ):
            build_web_video_observation(
                input_digest=
                    payload[
                        "input_digest"
                    ],
                scope="local",
                start_ms=None,
                end_ms=None,
                defect_category=
                    "hook_clarity",
                severity="major",
                evidence=
                    "missing timestamp fixture",
                description=
                    "invalid local defect",
                proposed_edit=
                    "trim the opening",
                confidence=0.5,
                uncertainty=
                    "fixture",
            )

    def test_output_has_actionable_creator_directives_and_explicit_uncertainty(self):
        critic_input = (
            self.critic_input()
        )
        output = self.critic_output(
            critic_input
        )
        self.assertEqual(
            len(
                output[
                    "creator_reedit_directives"
                ]
            ),
            1,
        )
        directive = output[
            "creator_reedit_directives"
        ][0]
        self.assertEqual(
            directive[
                "start_ms"
            ],
            900,
        )
        self.assertEqual(
            directive[
                "end_ms"
            ],
            1800,
        )
        self.assertEqual(
            directive[
                "defect_category"
            ],
            "hook_clarity",
        )
        self.assertTrue(
            directive[
                "directive"
            ]
        )
        self.assertTrue(
            directive[
                "uncertainty"
            ]
        )
        self.assertFalse(
            output[
                "coverage"
            ][
                "every_frame_inspected"
            ]
        )
        self.assertTrue(
            output[
                "coverage"
            ][
                "uninspected_possible"
            ]
        )

    def test_every_frame_claim_is_rejected(self):
        critic_input = (
            self.critic_input()
        )
        output = copy.deepcopy(
            self.critic_output(
                critic_input
            )
        )
        output["coverage"][
            "every_frame_inspected"
        ] = True
        with self.assertRaises(
            WebVideoCriticBoundaryError
        ):
            parse_web_video_critic_output(
                output,
                critic_input=
                    critic_input,
            )

    def test_fabricated_human_label_or_model_as_live_is_rejected(self):
        critic_input = (
            self.critic_input()
        )
        output = copy.deepcopy(
            self.critic_output(
                critic_input
            )
        )
        output[
            "evidence_boundary"
        ][
            "human_ground_truth"
        ] = True
        with self.assertRaises(
            WebVideoCriticBoundaryError
        ):
            parse_web_video_critic_output(
                output,
                critic_input=
                    critic_input,
            )

        output = copy.deepcopy(
            self.critic_output(
                critic_input
            )
        )
        output[
            "observations"
        ][0][
            "live_platform_evidence"
        ] = True
        with self.assertRaises(
            WebVideoCriticBoundaryError
        ):
            parse_web_video_critic_output(
                output,
                critic_input=
                    critic_input,
            )

    def test_fixture_mode_cannot_claim_attached_video_execution(self):
        critic_input = (
            self.critic_input()
        )
        with self.assertRaises(
            WebVideoCriticBoundaryError
        ):
            build_web_video_critic_output(
                critic_input=
                    critic_input,
                model_identity=
                    "web-chat-model",
                execution_mode=
                    WEB_VIDEO_ATTACHED_MODE,
                inspected_ranges=[
                    {
                        "start_ms": 0,
                        "end_ms": 1000,
                        "kind":
                            "continuous_review",
                    }
                ],
                coverage_notes=
                    "Would be direct-video coverage if transport were green.",
                observations=[],
                assessment=
                    "insufficient_evidence",
                whole_video_summary=
                    "No execution may be claimed.",
                summary_confidence=0.0,
                summary_uncertainty=
                    "Attachment transport unverified.",
                transport_evidence_digest=
                    "2" * 64,
            )

    def test_fixture_spec_explicitly_disclaims_live_execution(self):
        self.assertEqual(
            self.fixture[
                "integration_state"
            ],
            "WAITING_FOR_ATTACHMENT_TRANSPORT",
        )
        self.assertFalse(
            self.fixture[
                "live_web_video_execution_claimed"
            ]
        )

    def test_pairwise_input_is_deterministic_and_blinds_producer_identity(self):
        left = self.critic_input(0)
        right = self.critic_input(1)
        first = (
            build_web_video_pairwise_input(
                candidate_inputs=[
                    left,
                    right,
                ],
                comparison_goal=
                    "Choose only if direct-video evidence supports a preference.",
                producer_identity_blinded=True,
            )
        )
        reversed_input = (
            build_web_video_pairwise_input(
                candidate_inputs=[
                    right,
                    left,
                ],
                comparison_goal=
                    "Choose only if direct-video evidence supports a preference.",
                producer_identity_blinded=True,
            )
        )
        self.assertEqual(
            first,
            reversed_input,
        )
        self.assertEqual(
            first[
                "contract_version"
            ],
            WEB_VIDEO_PAIRWISE_VERSION,
        )
        self.assertTrue(
            first[
                "producer_identity_blinded"
            ]
        )
        self.assertTrue(
            all(
                row[
                    "producer_identity"
                ]
                is None
                for row
                in first[
                    "review_presentation"
                ]
            )
        )

    def test_pairwise_requires_two_distinct_render_bytes(self):
        left = self.critic_input(0)
        duplicate = self.critic_input(0)
        with self.assertRaises(
            WebVideoCriticLineageError
        ):
            build_web_video_pairwise_input(
                candidate_inputs=[
                    left,
                    duplicate,
                ],
                comparison_goal=
                    "compare",
                producer_identity_blinded=True,
            )

    def test_pairwise_tie_and_insufficient_evidence_map_to_no_candidate(self):
        pair = (
            build_web_video_pairwise_input(
                candidate_inputs=[
                    self.critic_input(0),
                    self.critic_input(1),
                ],
                comparison_goal=
                    "compare exact candidate MP4s",
                producer_identity_blinded=True,
            )
        )
        for selection in (
            "tie",
            "insufficient_evidence",
        ):
            output = (
                build_web_video_pairwise_output(
                    pairwise_input=
                        pair,
                    selection=
                        selection,
                    rationale=(
                        "Fixture-only pairwise output; "
                        "no attached-video evidence."
                    ),
                    evidence_observation_ids=[],
                    confidence=0.1,
                    uncertainty=(
                        "Attachment transport unavailable."
                    ),
                )
            )
            self.assertIsNone(
                output[
                    "mapped_candidate_id"
                ]
            )
            self.assertFalse(
                output[
                    "human_ground_truth"
                ]
            )
            self.assertFalse(
                output[
                    "human_parity_gate_eligible"
                ]
            )

    def test_pairwise_mapping_tamper_is_rejected(self):
        pair = (
            build_web_video_pairwise_input(
                candidate_inputs=[
                    self.critic_input(0),
                    self.critic_input(1),
                ],
                comparison_goal=
                    "compare exact candidate MP4s",
                producer_identity_blinded=True,
            )
        )
        output = (
            build_web_video_pairwise_output(
                pairwise_input=pair,
                selection="A",
                rationale=
                    "Schema mapping fixture.",
                evidence_observation_ids=[
                    "gvwo1:"
                    + "3" * 64
                ],
                confidence=0.6,
                uncertainty=
                    "Fixture only.",
            )
        )
        tampered = copy.deepcopy(
            output
        )
        other = {
            row["candidate_id"]
            for row in pair[
                "candidate_bindings"
            ]
            if row["candidate_id"]
            != output[
                "mapped_candidate_id"
            ]
        }.pop()
        tampered[
            "mapped_candidate_id"
        ] = other
        with self.assertRaises(
            WebVideoCriticLineageError
        ):
            parse_web_video_pairwise_output(
                tampered,
                pairwise_input=pair,
            )

    def test_pairwise_blinding_tamper_is_rejected(self):
        pair = copy.deepcopy(
            build_web_video_pairwise_input(
                candidate_inputs=[
                    self.critic_input(0),
                    self.critic_input(1),
                ],
                comparison_goal=
                    "compare exact candidate MP4s",
                producer_identity_blinded=True,
            )
        )
        pair[
            "review_presentation"
        ][0][
            "producer_identity"
        ] = (
            self.fixture[
                "media"
            ][
                "producer_sha"
            ]
        )
        with self.assertRaises(
            (
                WebVideoCriticLineageError,
                WebVideoCriticBoundaryError,
            )
        ):
            parse_web_video_pairwise_input(
                pair
            )


if __name__ == "__main__":
    unittest.main()
