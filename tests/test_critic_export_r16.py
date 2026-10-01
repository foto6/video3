from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from growth_analytics.critic_export import (
    BENCHMARK_AUTHORITY_COMMIT,
    BENCHMARK_AUTHORITY_REPOSITORY,
    BENCHMARK_DIMENSIONS,
    BENCHMARK_PROTOCOL,
    CRITIC_EXPORT_FILENAME,
    CRITIC_EXPORT_VERSION,
    CriticExportError,
    CriticExportHumanBoundaryError,
    build_critic_export,
    validate_critic_export,
    write_critic_export,
)


R16_IMPLEMENTATION_SHA = (
    "225733dd8965928cc8770acba7368102de70056b"
)


class GrowthR16CriticExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.root = root
        cls.r15 = json.loads(
            (
                root
                / "fixtures"
                / "visual_critic_r15"
                / "fixture_pack.json"
            ).read_text(encoding="utf-8")
        )
        cls.r15b = json.loads(
            (
                root
                / "fixtures"
                / "visual_critic_r15b"
                / "pairwise_fixture.json"
            ).read_text(encoding="utf-8")
        )

    def export(
        self,
        report_key="bad",
        *,
        pairwise_key=None,
    ):
        pairwise = (
            None
            if pairwise_key is None
            else self.r15["pairwise"][pairwise_key]
        )
        return build_critic_export(
            critic_report=self.r15["reports"][report_key],
            repository="foto6/video3",
            commit_sha=R16_IMPLEMENTATION_SHA,
            source_id=f"benchmark-source-{report_key}",
            pairwise_if_used=pairwise,
        )

    def test_exact_benchmark_keys_and_authority_pin(self):
        export = self.export()
        self.assertEqual(
            set(export),
            {
                "contract_version",
                "repository",
                "commit_sha",
                "source_id",
                "render_sha256",
                "critic_mode",
                "model_or_rule_identity",
                "dimension_observations",
                "timecoded_evidence",
                "hard_failure_observations",
                "pairwise_if_used",
                "human_ground_truth",
            },
        )
        self.assertEqual(
            export["contract_version"],
            CRITIC_EXPORT_VERSION,
        )
        self.assertEqual(BENCHMARK_PROTOCOL, "boss.human_editing_gate.v1")
        self.assertEqual(
            BENCHMARK_AUTHORITY_REPOSITORY,
            "foto6/boss",
        )
        self.assertEqual(
            BENCHMARK_AUTHORITY_COMMIT,
            "e0763bebf2aad9402f8de8c60edb1b9eb8c4be8e",
        )
        self.assertEqual(
            export["render_sha256"],
            self.r15["reports"]["bad"]["render"]["artifact_sha256"],
        )
        self.assertEqual(
            export["commit_sha"],
            R16_IMPLEMENTATION_SHA,
        )
        self.assertFalse(export["human_ground_truth"])

    def test_maps_exact_benchmark_dimensions_without_inventing_overall_preference(self):
        export = self.export()
        self.assertEqual(
            set(export["dimension_observations"]),
            set(BENCHMARK_DIMENSIONS),
        )
        self.assertNotIn(
            "overall_preference",
            export["dimension_observations"],
        )
        self.assertEqual(
            export["dimension_observations"]["hook"][
                "source_dimension"
            ],
            "hook_clarity_first_1_3s",
        )
        self.assertEqual(
            export["dimension_observations"]["framing_crop"][
                "source_dimension"
            ],
            "subject_framing_crop_quality",
        )
        self.assertEqual(
            export["dimension_observations"]["captions"][
                "source_dimension"
            ],
            "caption_readability_emphasis_relevance",
        )

    def test_unavailable_dimensions_are_explicit_and_have_no_invented_score(self):
        export = self.export("sparse_a")
        for dimension in (
            "pacing",
            "semantic_cut_correctness",
            "framing_crop",
            "broll_relevance",
            "captions",
            "audio_balance",
            "payoff_cta_loop",
        ):
            item = export["dimension_observations"][dimension]
            self.assertFalse(
                item["rule_observation"]["available"]
            )
            self.assertIsNone(
                item["rule_observation"]["normalized_score"]
            )
            self.assertIsInstance(item["unavailable"], str)
            self.assertTrue(item["unavailable"])

    def test_preserves_timecoded_unmapped_awkward_evidence_and_hard_failures(self):
        export = self.export()
        awkward = [
            item
            for item in export["timecoded_evidence"]
            if item["source_dimension"]
            == "awkward_dead_moments"
        ]
        self.assertTrue(awkward)
        self.assertIsNone(awkward[0]["rubric_dimension"])
        self.assertEqual(awkward[0]["start_ms"], 10700)

        hard = export["hard_failure_observations"]
        self.assertTrue(
            any(
                item["code"] == "CAPTION_COLLISION"
                and item["time_ms"] == 8700
                for item in hard
            )
        )
        self.assertTrue(
            all(
                item["human_ground_truth"] is False
                for item in hard
            )
        )

    def test_rule_model_vlm_export_cannot_claim_human_ground_truth(self):
        export = self.export()
        changed = copy.deepcopy(export)
        changed["human_ground_truth"] = True
        with self.assertRaises(CriticExportHumanBoundaryError):
            validate_critic_export(changed)

        changed = copy.deepcopy(export)
        changed["dimension_observations"]["hook"][
            "rule_observation"
        ]["human_ground_truth"] = True
        with self.assertRaises(CriticExportHumanBoundaryError):
            validate_critic_export(changed)

    def test_pairwise_model_decision_is_advisory_and_not_human_label(self):
        export = self.export(
            "good",
            pairwise_key="good_vs_bad",
        )
        pair = export["pairwise_if_used"]
        self.assertIsNotNone(pair)
        self.assertTrue(pair["advisory_only"])
        self.assertFalse(pair["human_label"])
        self.assertFalse(pair["human_ground_truth"])
        self.assertIn(
            export["render_sha256"],
            {
                pair["candidate_a_render_sha256"],
                pair["candidate_b_render_sha256"],
            },
        )

        changed = copy.deepcopy(export)
        changed["pairwise_if_used"]["human_label"] = True
        with self.assertRaises(CriticExportHumanBoundaryError):
            validate_critic_export(changed)

    def test_unrelated_pairwise_comparison_is_rejected(self):
        with self.assertRaises(CriticExportError):
            build_critic_export(
                critic_report=self.r15["reports"]["bad"],
                repository="foto6/video3",
                commit_sha=R16_IMPLEMENTATION_SHA,
                source_id="wrong-pair-source",
                pairwise_if_used=self.r15["pairwise"]["tie"],
            )

    def test_writer_refuses_benchmark_owned_human_ratings_filename(self):
        export = self.export()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(
                CriticExportHumanBoundaryError
            ):
                write_critic_export(
                    export,
                    root / "human_ratings.v1.ndjson",
                )
            output = write_critic_export(
                export,
                root / CRITIC_EXPORT_FILENAME,
            )
            self.assertEqual(
                output.name,
                "growth.critic_export.v1.json",
            )
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                export,
            )

    def test_gemini_fake_pairwise_fixture_remains_nonhuman_evidence(self):
        strong = self.r15b["strong_tie_resolution"]
        weak = self.r15b["weak_insufficient"]
        self.assertFalse(
            strong["gemini_opinion"]["human_ground_truth"]
        )
        self.assertFalse(
            weak["gemini_opinion"]["human_ground_truth"]
        )
        self.assertEqual(
            self.r15b["human_benchmark_readiness"],
            "HUMAN_LEVEL_UNPROVEN",
        )
        self.assertEqual(self.r15b["human_labels"], [])
        self.assertNotEqual(
            strong["final_selection"],
            "human_preference",
        )

    def test_model_pairwise_shape_cannot_be_represented_as_human_ratings(self):
        pair = self.r15b["strong_tie_resolution"]
        serialized = json.dumps(
            pair,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.assertIn(
            '"human_ground_truth":false',
            serialized,
        )
        self.assertNotIn(
            '"source_kind":"human_provided"',
            serialized,
        )
        self.assertNotIn('"rater_id"', serialized)
        self.assertNotIn('"overall_preference"', serialized)

    def test_canonical_export_fixture_is_byte_stable(self):
        path = (
            self.root
            / "fixtures"
            / "critic_export_v1"
            / CRITIC_EXPORT_FILENAME
        )
        expected = self.export()
        self.assertTrue(path.exists())
        self.assertEqual(
            path.read_bytes(),
            (
                json.dumps(
                    expected,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8"),
        )
        self.assertEqual(
            validate_critic_export(
                json.loads(path.read_text(encoding="utf-8"))
            ),
            expected,
        )

    def test_conformance_nonhuman_proof_fixture(self):
        path = (
            self.root
            / "fixtures"
            / "critic_export_v1"
            / "nonhuman_evidence_proof.json"
        )
        self.assertTrue(path.exists())
        proof = json.loads(
            path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            proof["benchmark_protocol"],
            "boss.human_editing_gate.v1",
        )
        self.assertFalse(
            proof["r15_structural"]["human_ground_truth"]
        )
        self.assertFalse(
            proof["r15b_gemini_mock"]["human_ground_truth"]
        )
        self.assertEqual(
            proof["r15b_gemini_mock"][
                "human_benchmark_readiness"
            ],
            "HUMAN_LEVEL_UNPROVEN",
        )
        self.assertEqual(
            proof["benchmark_owned_human_ratings_written_by_growth"],
            False,
        )


if __name__ == "__main__":
    unittest.main()
