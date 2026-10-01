from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from growth_analytics.autonomous_reels import canonical_json
from growth_analytics.gemini_pairwise import (
    GEMINI_NATIVE_VIDEO_PAIRWISE_VERSION,
    GeminiNativeVideoPairwiseEvaluator,
)
from growth_analytics.gemini_transport import (
    GEMINI_PROVIDER_NAME,
    GeminiNativeVideoConfig,
    GeminiNativeVideoError,
    GeminiNativeVideoMalformedOutput,
    GeminiNativeVideoTransportError,
)
from growth_analytics.gemini_visual_critic import (
    GEMINI_NATIVE_VIDEO_CRITIC_VERSION,
    GeminiNativeVideoCriticAdapter,
    critique_candidate_with_gemini_native_video,
    gemini_provider_from_environment,
)
from growth_analytics.visual_critic import (
    CRITIC_DIMENSIONS,
    HUMAN_LEVEL_UNPROVEN,
    build_visual_critic_input,
    compare_candidates,
    critique_candidate,
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeGeminiTransport:
    is_fake = True

    def __init__(self, responses):
        self.responses = list(responses)
        self.uploads = []
        self.interactions = []
        self.deletes = []
        self.gets = []
        self.fail_interact_once = False

    def upload_video(
        self,
        path,
        *,
        api_key,
        timeout_seconds,
    ):
        index = len(self.uploads) + 1
        self.uploads.append({
            "path": str(path),
            "api_key": api_key,
            "timeout_seconds": timeout_seconds,
        })
        return {
            "name": f"files/fake-{index}",
            "uri": f"gemini://video/{index}",
            "mimeType": "video/mp4",
            "state": "ACTIVE",
        }

    def get_file(
        self,
        name,
        *,
        api_key,
        timeout_seconds,
    ):
        self.gets.append(name)
        return {
            "name": name,
            "uri": "gemini://video/active",
            "mimeType": "video/mp4",
            "state": "ACTIVE",
        }

    def interact(
        self,
        *,
        model,
        input_parts,
        api_key,
        timeout_seconds,
    ):
        self.interactions.append({
            "model": model,
            "input_parts": copy.deepcopy(list(input_parts)),
            "api_key": api_key,
            "timeout_seconds": timeout_seconds,
        })
        if self.fail_interact_once:
            self.fail_interact_once = False
            raise GeminiNativeVideoTransportError(
                "fixture transient interaction failure"
            )
        if not self.responses:
            raise AssertionError("fake Gemini response queue exhausted")
        return self.responses.pop(0)

    def delete_file(
        self,
        name,
        *,
        api_key,
        timeout_seconds,
    ):
        self.deletes.append({
            "name": name,
            "api_key": api_key,
            "timeout_seconds": timeout_seconds,
        })


def fake_probe(_path: Path, _timeout: float) -> float:
    return 15.0


def single_response(
    *,
    timestamp_override=None,
    missing_dimension=None,
):
    observations = []
    for index, dimension in enumerate(CRITIC_DIMENSIONS):
        if dimension == missing_dimension:
            continue
        observations.append({
            "dimension": dimension,
            "timestamp_seconds": (
                timestamp_override
                if timestamp_override is not None
                else min(14.0, 0.5 + index)
            ),
            "judgment": (
                "negative"
                if dimension in {
                    "broll_relevance",
                    "subject_framing_crop_quality",
                }
                else "positive"
            ),
            "confidence": 0.82,
            "note": (
                f"Evidence-backed native-video observation for {dimension}."
            ),
        })
    return canonical_json({"observations": observations})


def pair_response(
    *,
    selection,
    confidence,
    with_evidence=True,
):
    evidence = []
    if with_evidence:
        evidence = [{
            "dimension": "caption_readability_emphasis_relevance",
            "candidate_1_time_seconds": 8.5,
            "candidate_2_time_seconds": 8.5,
            "note": (
                "candidate_1 keeps the caption clear while candidate_2 "
                "visibly collides with the subject."
            ),
        }]
    return canonical_json({
        "selection": selection,
        "confidence": confidence,
        "reason": "Native video shows a localized caption/framing difference.",
        "evidence": evidence,
    })


class GrowthR15BGeminiNativeVideoTests(unittest.TestCase):
    def frame(self, *, candidate, time, shot, motion=0.35):
        return {
            "time": time,
            "frame_sha256": hashlib.sha256(
                f"{candidate}:{time}:{shot}".encode("utf-8")
            ).hexdigest(),
            "shot_id": shot,
            "subject_bbox": {
                "x": 0.22,
                "y": 0.12,
                "w": 0.56,
                "h": 0.58,
            },
            "subject_visible": True,
            "frame_role": "primary",
            "semantic_match": None,
            "motion_score": motion,
            "zoom_factor": 1.0,
            "continuity_break": False,
        }

    def critic_input(
        self,
        *,
        candidate,
        video_sha256,
        sparse=False,
    ):
        if sparse:
            frames = [
                self.frame(
                    candidate=candidate,
                    time=1.0,
                    shot="s1",
                    motion=0.2,
                )
            ]
            captions = []
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
        else:
            frames = [
                self.frame(
                    candidate=candidate,
                    time=0.5,
                    shot="s1",
                ),
                self.frame(
                    candidate=candidate,
                    time=1.8,
                    shot="s2",
                ),
                self.frame(
                    candidate=candidate,
                    time=3.3,
                    shot="s3",
                ),
                self.frame(
                    candidate=candidate,
                    time=5.0,
                    shot="s4",
                ),
                self.frame(
                    candidate=candidate,
                    time=7.0,
                    shot="s5",
                ),
                self.frame(
                    candidate=candidate,
                    time=9.0,
                    shot="s6",
                ),
                self.frame(
                    candidate=candidate,
                    time=11.0,
                    shot="s7",
                ),
                self.frame(
                    candidate=candidate,
                    time=13.5,
                    shot="s8",
                ),
            ]
            captions = [
                {
                    "start": 0.2,
                    "end": 2.0,
                    "text": "Three edits that fix this",
                    "chars_per_second": 13.0,
                    "contrast_ratio": 7.2,
                    "collision": False,
                    "emphasis_relevant": True,
                },
                {
                    "start": 12.0,
                    "end": 14.8,
                    "text": "Save this and loop back",
                    "chars_per_second": 11.0,
                    "contrast_ratio": 7.0,
                    "collision": False,
                    "emphasis_relevant": True,
                },
            ]
            cuts = [
                {
                    "time": 3.0,
                    "source_boundary_distance_seconds": 0.08,
                    "semantic_safe": True,
                },
                {
                    "time": 7.0,
                    "source_boundary_distance_seconds": 0.07,
                    "semantic_safe": True,
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
                "music_lufs": -22.0,
                "speech_coverage": 0.86,
                "silence_intervals": [],
            }
        return build_visual_critic_input(
            candidate_id=candidate,
            render={
                "artifact_id": f"artifact-{candidate}",
                "artifact_sha256": video_sha256,
                "duration_seconds": 15.0,
                "width": 1080,
                "height": 1920,
                "fps": 30.0,
            },
            frames=frames,
            contact_sheet={
                "sha256": hashlib.sha256(
                    f"contact:{candidate}".encode("utf-8")
                ).hexdigest(),
                "frame_count": len(frames),
            },
            audio_probe=audio,
            captions=captions,
            overlays=[],
            cuts=cuts,
            source_semantic_timeline=semantic,
            media_qa={
                "contract_version": "media.qa.fixture.v1",
                "artifact_sha256": video_sha256,
                "technical_pass": True,
                "creative_pass": True,
                "hard_failures": [],
                "warnings": [],
            },
        )

    def write_video(self, root: Path, name: str):
        data = (
            b"\x00\x00\x00\x18ftypmp42"
            + b"fixture-native-video:"
            + name.encode("utf-8")
        )
        path = root / f"{name}.mp4"
        path.write_bytes(data)
        return path, sha256_bytes(data)

    def test_provider_disabled_by_default_and_structural_fallback_remains_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            path, video_sha = self.write_video(
                Path(temp),
                "disabled",
            )
            critic_input = self.critic_input(
                candidate="disabled",
                video_sha256=video_sha,
            )
            provider = gemini_provider_from_environment(
                video_path=path,
                env={},
                transport=FakeGeminiTransport([]),
                probe_runner=fake_probe,
            )
            self.assertIsNone(provider)
            report = critique_candidate(critic_input)
            self.assertEqual(
                report["vlm_provider"]["state"],
                "missing_provider",
            )
            self.assertFalse(
                report["authority"]["publish_authorized"]
            )

    def test_explicit_key_and_model_enable_native_video_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            path, video_sha = self.write_video(
                Path(temp),
                "enabled",
            )
            critic_input = self.critic_input(
                candidate="enabled",
                video_sha256=video_sha,
            )
            transport = FakeGeminiTransport([
                single_response()
            ])
            provider = GeminiNativeVideoCriticAdapter.from_environment(
                video_path=path,
                env={
                    "GEMINI_API_KEY": "fixture-key-not-secret",
                    "GEMINI_MODEL": "gemini-configurable-fixture",
                },
                transport=transport,
                probe_runner=fake_probe,
            )
            self.assertIsNotNone(provider)
            report = critique_candidate_with_gemini_native_video(
                critic_input,
                adapter=provider,
            )
            self.assertEqual(
                report["contract_version"],
                GEMINI_NATIVE_VIDEO_CRITIC_VERSION,
            )
            evidence = report["provider_evidence"]
            self.assertEqual(
                evidence["provider_name"],
                GEMINI_PROVIDER_NAME,
            )
            self.assertEqual(
                evidence["model_name"],
                "gemini-configurable-fixture",
            )
            self.assertEqual(evidence["mode"], "native_video")
            self.assertEqual(evidence["observation_count"], 11)
            self.assertFalse(evidence["human_ground_truth"])
            serialized = canonical_json(report)
            self.assertNotIn("fixture-key-not-secret", serialized)
            self.assertEqual(
                len(
                    report["base_critic_report"][
                        "vlm_observations"
                    ]
                ),
                len(CRITIC_DIMENSIONS),
            )
            self.assertEqual(len(transport.uploads), 1)
            self.assertEqual(len(transport.deletes), 1)
            parts = transport.interactions[0]["input_parts"]
            self.assertEqual(parts[0]["type"], "video")
            self.assertEqual(
                parts[0]["mime_type"],
                "video/mp4",
            )
            self.assertIn("processing", parts[0])
            self.assertEqual(parts[-1]["type"], "text")
            prompt = parts[-1]["text"]
            self.assertNotIn("contact_sheet", prompt)
            self.assertNotIn('"frames"', prompt)
            self.assertIn("source_semantic_timeline", prompt)
            self.assertIn("media_qa", prompt)

    def test_fake_transport_never_requires_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            path, video_sha = self.write_video(
                Path(temp),
                "fake-no-key",
            )
            transport = FakeGeminiTransport([
                single_response()
            ])
            provider = GeminiNativeVideoCriticAdapter.for_fake_transport(
                video_path=path,
                transport=transport,
                probe_runner=fake_probe,
            )
            report = critique_candidate_with_gemini_native_video(
                self.critic_input(
                    candidate="fake-no-key",
                    video_sha256=video_sha,
                ),
                adapter=provider,
            )
            self.assertEqual(
                report["provider_evidence"]["mode"],
                "native_video",
            )
            self.assertIsNone(
                transport.uploads[0]["api_key"]
            )
            self.assertIsNone(
                transport.interactions[0]["api_key"]
            )
            self.assertIsNone(
                transport.deletes[0]["api_key"]
            )

    def test_all_dimensions_are_required_and_timestamps_validate_against_probe_duration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, video_sha = self.write_video(root, "strict")
            for response in (
                single_response(
                    missing_dimension="pacing_coherence"
                ),
                single_response(timestamp_override=16.0),
            ):
                transport = FakeGeminiTransport([response])
                provider = GeminiNativeVideoCriticAdapter.for_fake_transport(
                    video_path=path,
                    transport=transport,
                    probe_runner=fake_probe,
                )
                with self.assertRaises(
                    GeminiNativeVideoMalformedOutput
                ):
                    critique_candidate_with_gemini_native_video(
                        self.critic_input(
                            candidate="strict",
                            video_sha256=video_sha,
                        ),
                        adapter=provider,
                    )
                self.assertEqual(
                    len(transport.deletes),
                    1,
                )

    def test_render_digest_and_ffprobe_duration_fail_closed_before_upload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, video_sha = self.write_video(root, "bound")
            transport = FakeGeminiTransport([
                single_response()
            ])
            provider = GeminiNativeVideoCriticAdapter.for_fake_transport(
                video_path=path,
                transport=transport,
                probe_runner=fake_probe,
            )
            wrong_input = self.critic_input(
                candidate="bound",
                video_sha256="0" * 64,
            )
            with self.assertRaises(GeminiNativeVideoError):
                provider.observe(wrong_input)
            self.assertEqual(transport.uploads, [])

            provider_duration = (
                GeminiNativeVideoCriticAdapter.for_fake_transport(
                    video_path=path,
                    transport=transport,
                    probe_runner=lambda _path, _timeout: 18.0,
                )
            )
            with self.assertRaises(GeminiNativeVideoError):
                provider_duration.observe(
                    self.critic_input(
                        candidate="bound",
                        video_sha256=video_sha,
                    )
                )
            self.assertEqual(transport.uploads, [])

    def test_bounded_retry_then_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            path, video_sha = self.write_video(
                Path(temp),
                "retry",
            )
            transport = FakeGeminiTransport([
                single_response()
            ])
            transport.fail_interact_once = True
            provider = GeminiNativeVideoCriticAdapter.for_fake_transport(
                video_path=path,
                transport=transport,
                probe_runner=fake_probe,
            )
            critique_candidate_with_gemini_native_video(
                self.critic_input(
                    candidate="retry",
                    video_sha256=video_sha,
                ),
                adapter=provider,
            )
            self.assertEqual(len(transport.interactions), 2)
            self.assertEqual(len(transport.deletes), 1)

    def test_pairwise_blind_order_is_deterministic_and_maps_only_after_scoring(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path_a, sha_a = self.write_video(root, "pair-a")
            path_b, sha_b = self.write_video(root, "pair-b")
            input_a = self.critic_input(
                candidate="original-A-secret-label",
                video_sha256=sha_a,
            )
            input_b = self.critic_input(
                candidate="original-B-secret-label",
                video_sha256=sha_b,
            )
            structural = compare_candidates(
                critique_candidate(input_a),
                critique_candidate(input_b),
            )
            self.assertEqual(structural["selection"], "tie")
            transport = FakeGeminiTransport([
                pair_response(
                    selection="candidate_1",
                    confidence=0.91,
                )
            ])
            evaluator = (
                GeminiNativeVideoPairwiseEvaluator.for_fake_transport(
                    transport=transport,
                    probe_runner=fake_probe,
                )
            )
            result = evaluator.compare(
                critic_input_a=input_a,
                video_path_a=path_a,
                critic_input_b=input_b,
                video_path_b=path_b,
            )
            self.assertEqual(
                result["contract_version"],
                GEMINI_NATIVE_VIDEO_PAIRWISE_VERSION,
            )
            self.assertEqual(
                result["structural_comparison"]["selection"],
                "tie",
            )
            self.assertIn(
                result["final_selection"],
                {"A", "B"},
            )
            self.assertEqual(
                result["final_selection"],
                result["gemini_opinion"]["mapped_selection"],
            )
            self.assertEqual(
                result["final_reason"],
                "gemini_native_video_pairwise_opinion",
            )
            self.assertTrue(
                result["presentation"]["blinded"]
            )
            self.assertTrue(
                result["presentation"]["mapped_after_scoring"]
            )
            request_text = canonical_json(
                transport.interactions[0]["input_parts"]
            )
            self.assertNotIn(
                "original-A-secret-label",
                request_text,
            )
            self.assertNotIn(
                "original-B-secret-label",
                request_text,
            )
            self.assertNotIn(sha_a, request_text)
            self.assertNotIn(sha_b, request_text)
            self.assertFalse(
                result["gemini_opinion"]["human_ground_truth"]
            )
            self.assertEqual(
                result["human_benchmark_readiness"],
                HUMAN_LEVEL_UNPROVEN,
            )
            self.assertEqual(len(transport.deletes), 2)

    def test_pairwise_order_reproducible_from_structural_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path_a, sha_a = self.write_video(root, "repro-a")
            path_b, sha_b = self.write_video(root, "repro-b")
            input_a = self.critic_input(
                candidate="repro-a",
                video_sha256=sha_a,
            )
            input_b = self.critic_input(
                candidate="repro-b",
                video_sha256=sha_b,
            )
            outputs = []
            for _ in range(2):
                transport = FakeGeminiTransport([
                    pair_response(
                        selection="candidate_2",
                        confidence=0.88,
                    )
                ])
                evaluator = (
                    GeminiNativeVideoPairwiseEvaluator.for_fake_transport(
                        transport=transport,
                        probe_runner=fake_probe,
                    )
                )
                outputs.append(
                    evaluator.compare(
                        critic_input_a=input_a,
                        video_path_a=path_a,
                        critic_input_b=input_b,
                        video_path_b=path_b,
                    )
                )
            self.assertEqual(
                outputs[0]["presentation"],
                outputs[1]["presentation"],
            )
            self.assertEqual(
                outputs[0]["gemini_opinion"]["mapped_selection"],
                outputs[1]["gemini_opinion"]["mapped_selection"],
            )

    def test_weak_gemini_does_not_override_structural_insufficient_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path_a, sha_a = self.write_video(root, "weak-a")
            path_b, sha_b = self.write_video(root, "weak-b")
            input_a = self.critic_input(
                candidate="weak-a",
                video_sha256=sha_a,
                sparse=True,
            )
            input_b = self.critic_input(
                candidate="weak-b",
                video_sha256=sha_b,
                sparse=True,
            )
            transport = FakeGeminiTransport([
                pair_response(
                    selection="candidate_1",
                    confidence=0.2,
                )
            ])
            evaluator = (
                GeminiNativeVideoPairwiseEvaluator.for_fake_transport(
                    transport=transport,
                    probe_runner=fake_probe,
                )
            )
            result = evaluator.compare(
                critic_input_a=input_a,
                video_path_a=path_a,
                critic_input_b=input_b,
                video_path_b=path_b,
            )
            self.assertEqual(
                result["structural_comparison"]["selection"],
                "insufficient_evidence",
            )
            self.assertEqual(
                result["gemini_opinion"]["evidence_strength"],
                "weak",
            )
            self.assertEqual(
                result["final_selection"],
                "insufficient_evidence",
            )
            self.assertEqual(
                result["final_reason"],
                "structural_result_preserved_weak_gemini_evidence",
            )

    def test_pairwise_malformed_timestamp_fails_closed_and_cleans_both_uploads(self):
        malformed = canonical_json({
            "selection": "candidate_1",
            "confidence": 0.9,
            "reason": "bad timestamp fixture",
            "evidence": [{
                "dimension": "broll_relevance",
                "candidate_1_time_seconds": 99.0,
                "candidate_2_time_seconds": 2.0,
                "note": "out of bounds",
            }],
        })
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path_a, sha_a = self.write_video(root, "bad-ts-a")
            path_b, sha_b = self.write_video(root, "bad-ts-b")
            transport = FakeGeminiTransport([malformed])
            evaluator = (
                GeminiNativeVideoPairwiseEvaluator.for_fake_transport(
                    transport=transport,
                    probe_runner=fake_probe,
                )
            )
            with self.assertRaises(
                GeminiNativeVideoMalformedOutput
            ):
                evaluator.compare(
                    critic_input_a=self.critic_input(
                        candidate="bad-ts-a",
                        video_sha256=sha_a,
                    ),
                    video_path_a=path_a,
                    critic_input_b=self.critic_input(
                        candidate="bad-ts-b",
                        video_sha256=sha_b,
                    ),
                    video_path_b=path_b,
                )
            self.assertEqual(len(transport.deletes), 2)

    def test_pairwise_fixture_is_deterministic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tie_a_path, tie_a_sha = self.write_video(
                root,
                "fixture-tie-a",
            )
            tie_b_path, tie_b_sha = self.write_video(
                root,
                "fixture-tie-b",
            )
            tie_a = self.critic_input(
                candidate="fixture-tie-a",
                video_sha256=tie_a_sha,
            )
            tie_b = self.critic_input(
                candidate="fixture-tie-b",
                video_sha256=tie_b_sha,
            )
            strong_transport = FakeGeminiTransport([
                pair_response(
                    selection="candidate_1",
                    confidence=0.91,
                )
            ])
            strong = (
                GeminiNativeVideoPairwiseEvaluator.for_fake_transport(
                    transport=strong_transport,
                    probe_runner=fake_probe,
                ).compare(
                    critic_input_a=tie_a,
                    video_path_a=tie_a_path,
                    critic_input_b=tie_b,
                    video_path_b=tie_b_path,
                )
            )

            weak_a_path, weak_a_sha = self.write_video(
                root,
                "fixture-weak-a",
            )
            weak_b_path, weak_b_sha = self.write_video(
                root,
                "fixture-weak-b",
            )
            weak_transport = FakeGeminiTransport([
                pair_response(
                    selection="candidate_2",
                    confidence=0.2,
                )
            ])
            weak = (
                GeminiNativeVideoPairwiseEvaluator.for_fake_transport(
                    transport=weak_transport,
                    probe_runner=fake_probe,
                ).compare(
                    critic_input_a=self.critic_input(
                        candidate="fixture-weak-a",
                        video_sha256=weak_a_sha,
                        sparse=True,
                    ),
                    video_path_a=weak_a_path,
                    critic_input_b=self.critic_input(
                        candidate="fixture-weak-b",
                        video_sha256=weak_b_sha,
                        sparse=True,
                    ),
                    video_path_b=weak_b_path,
                )
            )
            pack = {
                "fixture_version":
                    "growth.visual_critic_gemini_fixture.r15b.v1",
                "strong_tie_resolution": strong,
                "weak_insufficient": weak,
                "human_benchmark_readiness":
                    HUMAN_LEVEL_UNPROVEN,
                "human_labels": [],
                "credentials": None,
            }
            root_repo = Path(__file__).resolve().parents[1]
            fixture_path = (
                root_repo
                / "fixtures"
                / "visual_critic_r15b"
                / "pairwise_fixture.json"
            )
            if fixture_path.exists():
                self.assertEqual(
                    fixture_path.read_bytes(),
                    (canonical_json(pack) + "\n").encode("utf-8"),
                )
            else:
                print(
                    "R15B_FIXTURE_JSON="
                    + canonical_json(pack)
                )

    def test_fixture_descriptor_has_no_credentials_or_human_claims(self):
        root = Path(__file__).resolve().parents[1]
        replay = (
            root
            / "fixtures"
            / "visual_critic_r15b"
            / "replay_report.json"
        )
        if not replay.exists():
            self.skipTest("R15B replay pinned in final commit")
        data = json.loads(replay.read_text(encoding="utf-8"))
        self.assertEqual(
            data["human_benchmark_readiness"],
            HUMAN_LEVEL_UNPROVEN,
        )
        self.assertEqual(data["human_labels"], 0)
        self.assertFalse(data["credentials_committed"])
        self.assertFalse(data["authority"]["publish"])
        self.assertFalse(data["authority"]["media_mutation"])
        self.assertFalse(data["authority"]["creator_mutation"])


if __name__ == "__main__":
    unittest.main()
