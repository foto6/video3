from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from growth_analytics.autonomous_reels import canonical_json
from growth_analytics.visual_critic import (
    CALIBRATION_VERSION,
    HUMAN_BENCHMARK_CORPUS_READY,
    HUMAN_LEVEL_UNPROVEN,
    PAIRWISE_CRITIC_VERSION,
    VISUAL_CRITIC_VERSION,
    VLM_OBSERVATION_VERSION,
    StructuralCriticPolicy,
    VisualCriticError,
    VisualCriticProviderError,
    build_calibration_report,
    build_human_pairwise_label,
    build_visual_critic_input,
    build_vlm_observation,
    compare_candidates,
    critique_candidate,
    parse_human_pairwise_label,
    parse_pairwise_comparison,
    parse_visual_critic_input,
    parse_visual_critic_report,
)


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


class FixtureVLM:
    provider_name = "fixture-vlm"
    model_name = "fixture-model-r15"

    def observe(self, critic_input):
        return [
            build_vlm_observation(
                input_digest=critic_input["input_digest"],
                provider_name=self.provider_name,
                model_name=self.model_name,
                dimension="broll_relevance",
                time=11.0,
                judgment="negative",
                confidence=0.71,
                note="The B-roll appears weakly related to the active semantic beat.",
                request_digest=digest(
                    "fixture-vlm-request:"
                    + critic_input["input_digest"]
                ),
            )
        ]


class SecretVLM:
    provider_name = "secret-vlm"
    model_name = "fixture-model"

    def observe(self, critic_input):
        return [{
            "observation_version": VLM_OBSERVATION_VERSION,
            "observation_id": "gvo1:" + "0" * 64,
            "input_digest": critic_input["input_digest"],
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "dimension": "hook_clarity_first_1_3s",
            "time": 1.0,
            "judgment": "negative",
            "confidence": 0.8,
            "note": "secret should be rejected",
            "request_digest": digest("secret-request"),
            "token": "Bearer forbidden",
        }]


class GrowthR15VisualCriticTests(unittest.TestCase):
    def frame(
        self,
        *,
        candidate,
        time,
        shot,
        role="primary",
        subject_visible=True,
        bbox=None,
        semantic_match=None,
        motion=0.35,
        zoom=1.0,
        continuity_break=False,
    ):
        if bbox is None and subject_visible:
            bbox = {"x": 0.22, "y": 0.12, "w": 0.56, "h": 0.58}
        return {
            "time": time,
            "frame_sha256": digest(
                f"{candidate}:frame:{time}:{shot}"
            ),
            "shot_id": shot,
            "subject_bbox": bbox,
            "subject_visible": subject_visible,
            "frame_role": role,
            "semantic_match": semantic_match,
            "motion_score": motion,
            "zoom_factor": zoom,
            "continuity_break": continuity_break,
        }

    def candidate_input(
        self,
        candidate,
        *,
        quality="good",
    ):
        artifact_digest = digest(f"render:{candidate}")
        duration = 15.0

        if quality == "insufficient":
            frames = [
                self.frame(
                    candidate=candidate,
                    time=1.0,
                    shot="s1",
                    subject_visible=False,
                    bbox=None,
                    motion=0.2,
                )
            ]
            captions = []
            overlays = []
            cuts = []
            semantic = None
            audio = {
                "integrated_lufs": None,
                "true_peak_dbfs": None,
                "voice_lufs": None,
                "music_lufs": None,
                "speech_coverage": None,
                "silence_intervals": [],
            }
            media_qa = {
                "contract_version": "media.qa.fixture.v1",
                "artifact_sha256": artifact_digest,
                "technical_pass": True,
                "creative_pass": None,
                "hard_failures": [],
                "warnings": ["fixture intentionally sparse"],
            }
        else:
            bad = quality == "bad"
            tie = quality == "tie"
            frames = [
                self.frame(
                    candidate=candidate,
                    time=0.4,
                    shot="s1",
                    subject_visible=not bad,
                    bbox=(
                        None
                        if bad
                        else {"x": 0.22, "y": 0.12, "w": 0.56, "h": 0.58}
                    ),
                    motion=0.35,
                ),
                self.frame(
                    candidate=candidate,
                    time=1.6,
                    shot="s1" if bad else "s2",
                    subject_visible=True,
                    bbox={"x": 0.20, "y": 0.11, "w": 0.58, "h": 0.60},
                    motion=0.4,
                ),
                self.frame(
                    candidate=candidate,
                    time=3.2,
                    shot="s2" if bad else "s3",
                    subject_visible=True,
                    bbox={"x": 0.24, "y": 0.13, "w": 0.54, "h": 0.56},
                    motion=0.45,
                ),
                self.frame(
                    candidate=candidate,
                    time=5.0,
                    shot="s2" if bad else "s4",
                    role="broll",
                    subject_visible=False,
                    bbox=None,
                    semantic_match=0.82,
                    motion=0.5,
                ),
                self.frame(
                    candidate=candidate,
                    time=7.2,
                    shot="s3" if bad else "s5",
                    subject_visible=True,
                    bbox=(
                        {"x": 0.78, "y": 0.62, "w": 0.18, "h": 0.28}
                        if bad
                        else {"x": 0.20, "y": 0.12, "w": 0.58, "h": 0.58}
                    ),
                    motion=0.42,
                    continuity_break=bad,
                ),
                self.frame(
                    candidate=candidate,
                    time=9.0,
                    shot="s3" if bad else "s6",
                    subject_visible=True,
                    bbox={"x": 0.23, "y": 0.12, "w": 0.55, "h": 0.58},
                    motion=0.38,
                    zoom=1.0,
                ),
                self.frame(
                    candidate=candidate,
                    time=11.0,
                    shot="s4" if bad else "s7",
                    role="broll",
                    subject_visible=False,
                    bbox=None,
                    semantic_match=0.12 if bad else 0.88,
                    motion=0.04 if bad else 0.48,
                    zoom=1.85 if bad else 1.08,
                ),
                self.frame(
                    candidate=candidate,
                    time=13.5,
                    shot="s4" if bad else "s8",
                    subject_visible=True,
                    bbox={"x": 0.22, "y": 0.12, "w": 0.56, "h": 0.58},
                    motion=0.05 if bad else 0.36,
                ),
            ]
            captions = [
                {
                    "start": 0.2,
                    "end": 2.0,
                    "text": (
                        "A very long weak opening caption that takes too long to parse"
                        if bad
                        else "Three edits that fix this"
                    ),
                    "chars_per_second": 31.0 if bad else 13.0,
                    "contrast_ratio": 2.1 if bad else 7.2,
                    "collision": False,
                    "emphasis_relevant": not bad,
                },
                {
                    "start": 8.7,
                    "end": 10.3,
                    "text": "Watch the framing",
                    "chars_per_second": 14.0,
                    "contrast_ratio": 6.5,
                    "collision": bad,
                    "emphasis_relevant": True,
                },
                {
                    "start": 12.2,
                    "end": 14.8,
                    "text": "Save this and loop back",
                    "chars_per_second": 11.0,
                    "contrast_ratio": 7.0,
                    "collision": False,
                    "emphasis_relevant": True,
                },
            ]
            overlays = [
                {
                    "start": 4.8,
                    "end": 6.0,
                    "kind": "callout",
                    "text": "B-roll example",
                    "semantic_relevance": 0.85,
                    "collision": False,
                }
            ]
            cuts = [
                {
                    "time": 3.0,
                    "source_boundary_distance_seconds": 0.08,
                    "semantic_safe": True,
                },
                {
                    "time": 7.0,
                    "source_boundary_distance_seconds": 0.42 if bad else 0.07,
                    "semantic_safe": False if bad else True,
                },
                {
                    "time": 10.5,
                    "source_boundary_distance_seconds": 0.05,
                    "semantic_safe": True,
                },
            ]
            semantic = [
                {
                    "start": 0.0,
                    "end": 2.8,
                    "label": "hook_result",
                    "importance": 1.0,
                    "source_ref": "source:hook",
                },
                {
                    "start": 2.8,
                    "end": 10.5,
                    "label": "explanation",
                    "importance": 0.8,
                    "source_ref": "source:body",
                },
                {
                    "start": 10.5,
                    "end": 13.0,
                    "label": "payoff_result",
                    "importance": 1.0,
                    "source_ref": "source:payoff",
                },
                {
                    "start": 13.0,
                    "end": 15.0,
                    "label": "cta_loop",
                    "importance": 0.9,
                    "source_ref": "source:cta",
                },
            ]
            audio = {
                "integrated_lufs": -14.2,
                "true_peak_dbfs": -1.2,
                "voice_lufs": -15.0,
                "music_lufs": -22.0 if not bad else -15.8,
                "speech_coverage": 0.86 if not bad else 0.62,
                "silence_intervals": (
                    [{"start": 10.7, "end": 12.3}]
                    if bad
                    else []
                ),
            }
            media_qa = {
                "contract_version": "media.qa.fixture.v1",
                "artifact_sha256": artifact_digest,
                "technical_pass": True,
                "creative_pass": True if not bad else False,
                "hard_failures": [],
                "warnings": (
                    ["caption collision near 9s"] if bad else []
                ),
            }
            if tie:
                frames = copy.deepcopy(frames)
                captions = copy.deepcopy(captions)
                cuts = copy.deepcopy(cuts)
                semantic = copy.deepcopy(semantic)
                audio = copy.deepcopy(audio)

        return build_visual_critic_input(
            candidate_id=candidate,
            render={
                "artifact_id": f"artifact-{candidate}",
                "artifact_sha256": artifact_digest,
                "duration_seconds": duration,
                "width": 1080,
                "height": 1920,
                "fps": 30.0,
            },
            frames=frames,
            contact_sheet={
                "sha256": digest(f"contact:{candidate}"),
                "frame_count": len(frames),
            },
            audio_probe=audio,
            captions=captions,
            overlays=overlays,
            cuts=cuts,
            source_semantic_timeline=semantic,
            media_qa=media_qa,
        )

    def test_input_contract_binds_render_frames_audio_timelines_and_media_qa(self):
        candidate = self.candidate_input("good-a", quality="good")
        parsed = parse_visual_critic_input(candidate)
        self.assertEqual(
            parsed["input_version"],
            VISUAL_CRITIC_VERSION,
        )
        self.assertEqual(
            parsed["render"]["artifact_sha256"],
            digest("render:good-a"),
        )
        self.assertEqual(
            parsed["contact_sheet"]["frame_count"],
            len(parsed["frames"]),
        )
        changed = copy.deepcopy(candidate)
        changed["media_qa"]["artifact_sha256"] = digest(
            "another-artifact"
        )
        changed["input_digest"] = ""
        changed["input_digest"] = hashlib.sha256(
            canonical_json({
                key: value
                for key, value in changed.items()
                if key != "input_digest"
            }).encode("utf-8")
        ).hexdigest()
        with self.assertRaises(VisualCriticError):
            parse_visual_critic_input(changed)

    def test_structural_critic_localizes_deliberate_bad_variant(self):
        bad = critique_candidate(
            self.candidate_input("bad-a", quality="bad")
        )
        parse_visual_critic_report(bad)
        self.assertEqual(
            set(bad["heuristic_editorial_judgments"]),
            {
                "hook_clarity_first_1_3s",
                "pacing_coherence",
                "semantic_cut_correctness",
                "subject_framing_crop_quality",
                "broll_relevance",
                "caption_readability_emphasis_relevance",
                "visual_continuity",
                "motion_zoom_appropriateness",
                "audio_voice_music_balance",
                "payoff_cta_loop_coherence",
                "awkward_dead_moments",
            },
        )
        note_times = [
            note["time"] for note in bad["actionable_notes"]
            if note["time"] is not None
        ]
        self.assertIn(7.2, note_times)
        self.assertIn(11.0, note_times)
        self.assertTrue(
            any(
                failure["code"] == "CAPTION_COLLISION"
                and failure["time"] == 8.7
                for failure in bad["objective_hard_failures"]
            )
        )
        self.assertEqual(
            bad["vlm_provider"]["state"],
            "missing_provider",
        )
        self.assertIn(
            "does not directly inspect pixels",
            bad["vlm_provider"]["limitation"],
        )
        self.assertFalse(
            bad["authority"]["publish_authorized"]
        )

    def test_optional_vlm_observation_is_opinion_with_provenance(self):
        candidate = self.candidate_input("bad-vlm", quality="bad")
        report = critique_candidate(
            candidate,
            vlm_provider=FixtureVLM(),
        )
        self.assertEqual(
            report["vlm_provider"]["state"],
            "observations_present",
        )
        self.assertEqual(
            report["vlm_provider"]["observation_count"],
            1,
        )
        observation = report["vlm_observations"][0]
        self.assertEqual(
            observation["provider_name"],
            "fixture-vlm",
        )
        self.assertEqual(observation["confidence"], 0.71)
        self.assertTrue(
            any(
                note["source"] == "vlm_opinion"
                for note in report["actionable_notes"]
            )
        )
        self.assertIn(
            "not human preference labels",
            report["vlm_provider"]["limitation"],
        )

    def test_vlm_boundary_rejects_credential_like_material(self):
        with self.assertRaises(VisualCriticProviderError):
            critique_candidate(
                self.candidate_input(
                    "secret-vlm-input",
                    quality="good",
                ),
                vlm_provider=SecretVLM(),
            )

    def test_pairwise_prefers_good_over_bad_without_claiming_human_truth(self):
        good = critique_candidate(
            self.candidate_input("pair-good", quality="good")
        )
        bad = critique_candidate(
            self.candidate_input("pair-bad", quality="bad")
        )
        pair = compare_candidates(good, bad)
        parsed = parse_pairwise_comparison(pair)
        self.assertEqual(
            parsed["contract_version"],
            PAIRWISE_CRITIC_VERSION,
        )
        self.assertEqual(parsed["selection"], "A")
        self.assertFalse(
            parsed["uncertainty"]["human_preference_ground_truth"]
        )
        self.assertTrue(
            parsed["authority"]["tournament_selection_input"]
        )
        self.assertFalse(
            parsed["authority"]["publish_authorized"]
        )

    def test_pairwise_tie_is_explicit(self):
        left = critique_candidate(
            self.candidate_input("tie-left", quality="good")
        )
        right = critique_candidate(
            self.candidate_input("tie-right", quality="tie")
        )
        pair = compare_candidates(left, right)
        self.assertEqual(pair["selection"], "tie")
        self.assertEqual(
            pair["reason"],
            "dimension_specific_difference_within_tie_margin",
        )

    def test_pairwise_insufficient_evidence_is_explicit(self):
        left = critique_candidate(
            self.candidate_input(
                "sparse-left",
                quality="insufficient",
            )
        )
        right = critique_candidate(
            self.candidate_input(
                "sparse-right",
                quality="insufficient",
            )
        )
        pair = compare_candidates(left, right)
        self.assertEqual(
            pair["selection"],
            "insufficient_evidence",
        )
        self.assertEqual(
            pair["reason"],
            "too_few_comparable_structural_dimensions",
        )

    def test_human_benchmark_zero_labels_stays_unproven_without_metrics(self):
        good = critique_candidate(
            self.candidate_input("human-good", quality="good")
        )
        bad = critique_candidate(
            self.candidate_input("human-bad", quality="bad")
        )
        pair = compare_candidates(good, bad)
        report = build_calibration_report(
            comparisons=[pair],
            labels=[],
            min_real_labels=30,
        )
        self.assertEqual(
            report["contract_version"],
            CALIBRATION_VERSION,
        )
        self.assertEqual(
            report["human_benchmark_readiness"],
            HUMAN_LEVEL_UNPROVEN,
        )
        self.assertEqual(report["actual_human_labels"], 0)
        self.assertIsNone(report["agreement_metrics"])

    def test_calibration_accepts_only_explicit_human_labels_and_never_claims_human_level(self):
        good = critique_candidate(
            self.candidate_input("label-good", quality="good")
        )
        bad = critique_candidate(
            self.candidate_input("label-bad", quality="bad")
        )
        pair = compare_candidates(good, bad)
        label = build_human_pairwise_label(
            comparison=pair,
            preference="A",
            annotator_ref="human-study:participant-001",
            observed_at="2026-10-01T01:00:00Z",
            provenance_digest=digest(
                "external-human-study-row-001"
            ),
        )
        parsed = parse_human_pairwise_label(
            label,
            comparison=pair,
        )
        self.assertEqual(
            parsed["source_kind"],
            "human_provided",
        )
        report = build_calibration_report(
            comparisons=[pair],
            labels=[label],
            min_real_labels=30,
        )
        self.assertEqual(
            report["human_benchmark_readiness"],
            HUMAN_LEVEL_UNPROVEN,
        )
        self.assertEqual(
            report["agreement_metrics"][
                "non_abstaining_agreement"
            ],
            1.0,
        )
        changed = copy.deepcopy(label)
        changed["source_kind"] = "synthetic_fixture"
        with self.assertRaises(VisualCriticError):
            parse_human_pairwise_label(
                changed,
                comparison=pair,
            )

    def test_readiness_gate_changes_only_after_configured_real_label_minimum(self):
        comparisons = []
        labels = []
        for index in range(3):
            good = critique_candidate(
                self.candidate_input(
                    f"ready-good-{index}",
                    quality="good",
                )
            )
            bad = critique_candidate(
                self.candidate_input(
                    f"ready-bad-{index}",
                    quality="bad",
                )
            )
            pair = compare_candidates(good, bad)
            comparisons.append(pair)
            labels.append(
                build_human_pairwise_label(
                    comparison=pair,
                    preference="A",
                    annotator_ref=(
                        f"human-study:participant-{index:03d}"
                    ),
                    observed_at=(
                        f"2026-10-01T01:0{index}:00Z"
                    ),
                    provenance_digest=digest(
                        f"external-human-study-row-{index:03d}"
                    ),
                )
            )
        report = build_calibration_report(
            comparisons=comparisons,
            labels=labels,
            min_real_labels=3,
        )
        self.assertEqual(
            report["human_benchmark_readiness"],
            HUMAN_BENCHMARK_CORPUS_READY,
        )
        self.assertEqual(report["actual_human_labels"], 3)
        self.assertIn(
            "do not establish",
            report["interpretation"],
        )

    def test_fixture_pack_is_deterministic_and_contains_no_human_labels(self):
        good_input = self.candidate_input(
            "fixture-good",
            quality="good",
        )
        bad_input = self.candidate_input(
            "fixture-bad",
            quality="bad",
        )
        tie_a_input = self.candidate_input(
            "fixture-tie-a",
            quality="good",
        )
        tie_b_input = self.candidate_input(
            "fixture-tie-b",
            quality="tie",
        )
        sparse_a_input = self.candidate_input(
            "fixture-sparse-a",
            quality="insufficient",
        )
        sparse_b_input = self.candidate_input(
            "fixture-sparse-b",
            quality="insufficient",
        )
        reports = {
            key: critique_candidate(value)
            for key, value in {
                "good": good_input,
                "bad": bad_input,
                "tie_a": tie_a_input,
                "tie_b": tie_b_input,
                "sparse_a": sparse_a_input,
                "sparse_b": sparse_b_input,
            }.items()
        }
        pack = {
            "fixture_version":
                "growth.visual_critic_fixture_pack.r15.v1",
            "inputs": {
                "good": good_input,
                "bad": bad_input,
                "tie_a": tie_a_input,
                "tie_b": tie_b_input,
                "sparse_a": sparse_a_input,
                "sparse_b": sparse_b_input,
            },
            "reports": reports,
            "pairwise": {
                "good_vs_bad": compare_candidates(
                    reports["good"],
                    reports["bad"],
                ),
                "tie": compare_candidates(
                    reports["tie_a"],
                    reports["tie_b"],
                ),
                "insufficient": compare_candidates(
                    reports["sparse_a"],
                    reports["sparse_b"],
                ),
            },
            "human_labels": [],
            "human_benchmark": build_calibration_report(
                comparisons=[],
                labels=[],
                min_real_labels=30,
            ),
        }
        self.assertEqual(
            pack["pairwise"]["good_vs_bad"]["selection"],
            "A",
        )
        self.assertEqual(
            pack["pairwise"]["tie"]["selection"],
            "tie",
        )
        self.assertEqual(
            pack["pairwise"]["insufficient"]["selection"],
            "insufficient_evidence",
        )
        self.assertEqual(pack["human_labels"], [])
        self.assertEqual(
            pack["human_benchmark"]["human_benchmark_readiness"],
            HUMAN_LEVEL_UNPROVEN,
        )
        root = Path(__file__).resolve().parents[1]
        fixture_path = (
            root / "fixtures" / "visual_critic_r15"
            / "fixture_pack.json"
        )
        self.assertTrue(fixture_path.exists())
        self.assertEqual(
            fixture_path.read_bytes(),
            (canonical_json(pack) + "\n").encode("utf-8"),
        )

    def test_replay_report_declares_human_level_unproven(self):
        root = Path(__file__).resolve().parents[1]
        report_path = (
            root / "fixtures" / "visual_critic_r15"
            / "replay_report.json"
        )
        self.assertTrue(report_path.exists())
        report = json.loads(
            report_path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            report["report_version"],
            "growth.visual_critic_replay.r15.v1",
        )
        self.assertEqual(
            report["human_benchmark"]["readiness"],
            HUMAN_LEVEL_UNPROVEN,
        )
        self.assertEqual(
            report["human_benchmark"]["actual_labels"],
            0,
        )
        self.assertIsNone(
            report["human_benchmark"]["agreement_metrics"]
        )
        self.assertFalse(report["authority"]["publish"])
        self.assertFalse(
            report["authority"]["media_mutation"]
        )
        self.assertFalse(
            report["authority"]["creator_mutation"]
        )


if __name__ == "__main__":
    unittest.main()
