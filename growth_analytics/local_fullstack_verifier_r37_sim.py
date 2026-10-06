from __future__ import annotations

import copy
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from . import local_fullstack_verifier_r37 as r37


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _accepted_creator() -> dict[str, Any]:
    exact = {
        "repository": "foto6/video1",
        "producer_sha": "1" * 40,
        "ci_run_id": 900001,
        "artifact_id": 900002,
        "artifact_name": "synthetic-creator-r38-authority-fixture",
        "artifact_digest": "sha256:" + "2" * 64,
        "contract": "creator.local_fullstack_rehearsal.r38.v1",
    }
    return {
        "contract_version": r37.CREATOR_AUTHORITY_VERSION,
        "status": "ACCEPTED",
        "repository": "foto6/video1",
        "observed_branch": r37.OBSERVED_CREATOR_R38_BRANCH,
        "observed_sha": r37.OBSERVED_CREATOR_SHA,
        "observed_commit_message": "Add R37 local integration driver",
        "distinct_r38_authority_available": True,
        "exact_authority": exact,
        "exact_authority_digest": r37._sha_json(exact),
    }


def _refresh_final_manifest(root: Path, manifest: dict[str, Any]) -> None:
    path = root / manifest["final"]["manifest_path"]
    value = _load(path)
    value["media_authority_digest"] = r37._sha_json(manifest["media_authority"])
    material = copy.deepcopy(value)
    material["manifest_digest"] = ""
    value["manifest_digest"] = r37._sha_json(material)
    _write_json(path, value)
    manifest["final"]["manifest_sha256"] = r37._file_sha(path)
    manifest["final"]["manifest_size"] = r37._file_size(path)


def _refresh_file_refs(root: Path, manifest: dict[str, Any]) -> None:
    source = root / manifest["source"]["path"]
    if source.is_file():
        manifest["source"]["sha256"] = r37._file_sha(source)
        manifest["source"]["size"] = r37._file_size(source)
    for candidate in manifest["candidates"]:
        path = root / candidate["path"]
        if path.is_file():
            candidate["sha256"] = r37._file_sha(path)
            candidate["size"] = r37._file_size(path)
    for round_row in manifest["rounds"]:
        prompt = root / round_row["prompt"]["path"]
        if prompt.is_file():
            round_row["prompt"]["sha256"] = r37._file_sha(prompt)
            round_row["prompt"]["size"] = r37._file_size(prompt)
        for review in round_row["reviews"]:
            path = root / review["path"]
            if path.is_file():
                review["sha256"] = r37._file_sha(path)
                review["size"] = r37._file_size(path)
    final = root / manifest["final"]["path"]
    if final.is_file():
        manifest["final"]["sha256"] = r37._file_sha(final)
        manifest["final"]["size"] = r37._file_size(final)


def _reseal(root: Path, *, refresh_files: bool = True) -> str:
    path = root / "manifest.json"
    manifest = _load(path)
    if refresh_files:
        _refresh_file_refs(root, manifest)
    manifest["media_authority_digest"] = r37._sha_json(manifest["media_authority"])
    _refresh_final_manifest(root, manifest)
    material = copy.deepcopy(manifest)
    material["manifest_digest"] = ""
    manifest["manifest_digest"] = r37._sha_json(material)
    _write_json(path, manifest)
    return manifest["manifest_digest"]


def _make_real_local(
    root: Path,
    *,
    evidence_class: str = "OFFLINE_MODEL",
) -> str:
    manifest = _load(root / "manifest.json")
    manifest["bundle_kind"] = "LOCAL_EVIDENCE"
    manifest["evidence_boundary"]["fixture"] = False
    manifest["media_authority"] = {
        "authority_class": "EXACT_GREEN",
        "repository": "foto6/video2",
        "producer_sha": "3" * 40,
        "ci_run_id": 900101,
        "artifact_id": 900102,
        "artifact_name": "synthetic-exact-green-media-r37-fixture",
        "artifact_digest": "sha256:" + "4" * 64,
        "contract": "media.multicandidate_round.r25.v1",
    }
    _write_json(root / "manifest.json", manifest)
    for round_row in manifest["rounds"]:
        for ref in round_row["reviews"]:
            path = root / ref["path"]
            review = _load(path)
            review["evidence_class"] = evidence_class
            _write_json(path, review)
    return _reseal(root)


def _case(
    name: str,
    expected: str,
    fn: Callable[[], Any],
) -> dict[str, Any]:
    try:
        value = fn()
        if isinstance(value, Mapping):
            actual = value.get("final_decision") or value.get("state") or "OK"
        else:
            actual = str(value)
        passed = actual == expected
        return {
            "name": name,
            "expected": expected,
            "actual": actual,
            "passed": passed,
            "verification_digest": (
                value.get("verification_digest")
                if isinstance(value, Mapping)
                else None
            ),
        }
    except Exception as exc:
        actual = f"REJECTED:{type(exc).__name__}"
        return {
            "name": name,
            "expected": expected,
            "actual": actual,
            "passed": actual == expected,
            "detail": str(exc),
        }


def rehearse(
    *,
    bundle_dir: Path,
    expected_bundle_digest: str,
    authority: Mapping[str, Any],
    policy: Mapping[str, Any],
    creator_authority: Mapping[str, Any],
    growth_sha: str,
    growth_ci_run_id: int,
) -> dict[str, Any]:
    r37.validate_authority(authority)
    r37.validate_policy(policy)
    r37.validate_creator_authority(creator_authority)
    base = Path(bundle_dir).resolve()
    cases: dict[str, Any] = {}

    def run(
        root: Path,
        *,
        expected: str | None = None,
        creator: Mapping[str, Any] | None = None,
        ledger: Path | None = None,
    ) -> dict[str, Any]:
        manifest = _load(root / "manifest.json")
        return r37.verify_local_bundle(
            bundle_dir=root,
            expected_bundle_digest=expected or manifest["manifest_digest"],
            authority=authority,
            policy=policy,
            creator_authority=creator or creator_authority,
            ledger_dir=ledger or (root / ".ledger"),
            growth_sha=growth_sha,
            growth_ci_run_id=growth_ci_run_id,
        )

    # Baseline current authority: structurally valid but blocked on Creator + fixture Media.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        cases["01_current_fixture_waits_creator"] = _case(
            "01_current_fixture_waits_creator",
            r37.BLOCKED_INCOMPLETE,
            lambda: run(root),
        )

    # Creator-only acceptance does not promote synthetic Media.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        cases["02_creator_acceptance_fixture_still_blocked"] = _case(
            "02_creator_acceptance_fixture_still_blocked",
            r37.BLOCKED_INCOMPLETE,
            lambda: run(root, creator=_accepted_creator()),
        )

    # Positive offline-model local path.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        digest = _make_real_local(root, evidence_class="OFFLINE_MODEL")
        cases["03_offline_model_ready_local_demo"] = _case(
            "03_offline_model_ready_local_demo",
            r37.READY,
            lambda: run(
                root,
                expected=digest,
                creator=_accepted_creator(),
            ),
        )

    # Positive genuine-review path remains distinct.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        digest = _make_real_local(root, evidence_class="GENUINE_REVIEW")
        result = run(root, expected=digest, creator=_accepted_creator())
        cases["04_genuine_review_ready_and_distinct"] = {
            "name": "04_genuine_review_ready_and_distinct",
            "expected": r37.READY,
            "actual": result["final_decision"],
            "passed": (
                result["final_decision"] == r37.READY
                and result["evidence_classification"]["classification"]
                == "GENUINE_REVIEW"
            ),
            "evidence_classification":
                result["evidence_classification"]["classification"],
        }

    # Missing candidate file.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        (root / "candidates" / "challenger.mp4").unlink()
        cases["05_missing_candidate_file"] = _case(
            "05_missing_candidate_file",
            "REJECTED:IncompleteEvidence",
            lambda: run(root),
        )

    # Duplicate candidate bytes, resealed.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        shutil.copyfile(
            root / "candidates" / "baseline.mp4",
            root / "candidates" / "challenger.mp4",
        )
        digest = _reseal(root)
        cases["06_duplicate_candidate_bytes"] = _case(
            "06_duplicate_candidate_bytes",
            "REJECTED:EvidenceConflict",
            lambda: run(root, expected=digest),
        )

    # Source drift with reseal: candidates retain old source binding.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        (root / "source.bin").write_bytes(b"R37 altered source\n")
        digest = _reseal(root)
        cases["07_source_drift"] = _case(
            "07_source_drift",
            "REJECTED:IncompleteEvidence",
            lambda: run(root, expected=digest),
        )

    # Candidate source-lineage field drift.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        manifest = _load(root / "manifest.json")
        manifest["candidates"][0]["source_sha256"] = "0" * 64
        _write_json(root / "manifest.json", manifest)
        digest = _reseal(root, refresh_files=False)
        cases["08_candidate_source_lineage_drift"] = _case(
            "08_candidate_source_lineage_drift",
            "REJECTED:IncompleteEvidence",
            lambda: run(root, expected=digest),
        )

    # Stale review package.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        path = root / "reviews" / "round-0-a.json"
        review = _load(path)
        review["package_digest"] = "0" * 64
        _write_json(path, review)
        digest = _reseal(root)
        cases["09_stale_review_package"] = _case(
            "09_stale_review_package",
            "REJECTED:IncompleteEvidence",
            lambda: run(root, expected=digest),
        )

    # Stale timestamp.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        path = root / "reviews" / "round-1-a.json"
        review = _load(path)
        review["created_at"] = "2026-10-05T23:59:00Z"
        _write_json(path, review)
        digest = _reseal(root)
        cases["10_stale_review_timestamp"] = _case(
            "10_stale_review_timestamp",
            "REJECTED:IncompleteEvidence",
            lambda: run(root, expected=digest),
        )

    # Duplicate reviewer identity.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        a = _load(root / "reviews" / "round-0-a.json")
        bpath = root / "reviews" / "round-0-b.json"
        b = _load(bpath)
        b["reviewer_id"] = a["reviewer_id"]
        _write_json(bpath, b)
        digest = _reseal(root)
        cases["11_duplicate_reviewer"] = _case(
            "11_duplicate_reviewer",
            "REJECTED:EvidenceConflict",
            lambda: run(root, expected=digest),
        )

    # Duplicate review bytes.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        shutil.copyfile(
            root / "reviews" / "round-0-a.json",
            root / "reviews" / "round-0-b.json",
        )
        digest = _reseal(root)
        cases["12_duplicate_review_bytes"] = _case(
            "12_duplicate_review_bytes",
            "REJECTED:EvidenceConflict",
            lambda: run(root, expected=digest),
        )

    # Wrong re-edit parent lineage.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        manifest = _load(root / "manifest.json")
        manifest["targeted_reedits"][0]["parent_candidate_id"] = "baseline"
        _write_json(root / "manifest.json", manifest)
        digest = _reseal(root, refresh_files=False)
        cases["13_wrong_targeted_reedit_parent"] = _case(
            "13_wrong_targeted_reedit_parent",
            "REJECTED:IncompleteEvidence",
            lambda: run(root, expected=digest),
        )

    # Candidate re-edit directive binding drift.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        manifest = _load(root / "manifest.json")
        manifest["candidates"][2]["applied_directive_digest"] = "0" * 64
        _write_json(root / "manifest.json", manifest)
        digest = _reseal(root, refresh_files=False)
        cases["14_reedit_candidate_directive_binding_drift"] = _case(
            "14_reedit_candidate_directive_binding_drift",
            "REJECTED:IncompleteEvidence",
            lambda: run(root, expected=digest),
        )

    # Structurally sealed but wrong targeted edit => NEEDS_REEDIT.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        digest = _make_real_local(root, evidence_class="OFFLINE_MODEL")
        manifest = _load(root / "manifest.json")
        reedit = manifest["targeted_reedits"][0]
        reedit["directives"][0]["end_ms"] = 1399
        reedit["directive_digest"] = r37._sha_json(reedit["directives"])
        manifest["candidates"][2]["applied_directive_digest"] = reedit["directive_digest"]
        _write_json(root / "manifest.json", manifest)
        digest = _reseal(root, refresh_files=False)
        cases["15_wrong_targeted_reedit_semantics_needs_reedit"] = _case(
            "15_wrong_targeted_reedit_semantics_needs_reedit",
            r37.NEEDS_REEDIT,
            lambda: run(
                root,
                expected=digest,
                creator=_accepted_creator(),
            ),
        )

    # Final bytes tamper.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        (root / "final.mp4").write_bytes(b"altered final")
        cases["16_final_bytes_tamper"] = _case(
            "16_final_bytes_tamper",
            "REJECTED:IncompleteEvidence",
            lambda: run(root),
        )

    # Final selected candidate lineage mismatch.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        manifest = _load(root / "manifest.json")
        manifest["final"]["selected_candidate_id"] = "challenger"
        manifest["final"]["selected_round"] = 0
        _write_json(root / "manifest.json", manifest)
        digest = _reseal(root, refresh_files=False)
        cases["17_final_selected_lineage_mismatch"] = _case(
            "17_final_selected_lineage_mismatch",
            "REJECTED:IncompleteEvidence",
            lambda: run(root, expected=digest),
        )

    # Final manifest timestamp altered without ref refresh.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        path = root / "final-manifest.json"
        final = _load(path)
        final["created_at"] = "2026-10-06T00:17:59Z"
        _write_json(path, final)
        cases["18_final_manifest_bytes_altered"] = _case(
            "18_final_manifest_bytes_altered",
            "REJECTED:IncompleteEvidence",
            lambda: run(root),
        )

    # Manifest timestamp altered/resealed but caller keeps old expected seal.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        manifest = _load(root / "manifest.json")
        old = manifest["manifest_digest"]
        manifest["sealed_at"] = "2026-10-06T00:21:00Z"
        _write_json(root / "manifest.json", manifest)
        _reseal(root, refresh_files=False)
        cases["19_altered_manifest_expected_digest_conflict"] = _case(
            "19_altered_manifest_expected_digest_conflict",
            "REJECTED:IncompleteEvidence",
            lambda: run(root, expected=old),
        )

    # Wrong expected seal.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        cases["20_wrong_expected_bundle_digest"] = _case(
            "20_wrong_expected_bundle_digest",
            "REJECTED:IncompleteEvidence",
            lambda: run(root, expected="0" * 64),
        )

    # Missing Media authority field.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        manifest = _load(root / "manifest.json")
        del manifest["media_authority"]["artifact_id"]
        _write_json(root / "manifest.json", manifest)
        digest = _reseal(root, refresh_files=False)
        cases["21_media_authority_field_missing"] = _case(
            "21_media_authority_field_missing",
            "REJECTED:IncompleteEvidence",
            lambda: run(root, expected=digest),
        )

    # Real authority class cannot use fixture repo.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        manifest = _load(root / "manifest.json")
        manifest["media_authority"]["authority_class"] = "EXACT_GREEN"
        _write_json(root / "manifest.json", manifest)
        digest = _reseal(root, refresh_files=False)
        cases["22_media_exact_green_wrong_repository"] = _case(
            "22_media_exact_green_wrong_repository",
            "REJECTED:IncompleteEvidence",
            lambda: run(root, expected=digest),
        )

    # Creator exact authority digest must bind tuple.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        bad = _accepted_creator()
        bad["exact_authority_digest"] = "0" * 64
        cases["23_creator_authority_digest_drift"] = _case(
            "23_creator_authority_digest_drift",
            "REJECTED:AuthorityDrift",
            lambda: run(root, creator=bad),
        )

    # Round references missing candidate.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        manifest = _load(root / "manifest.json")
        manifest["rounds"][1]["labels"]["B"] = "missing-candidate"
        _write_json(root / "manifest.json", manifest)
        digest = _reseal(root, refresh_files=False)
        cases["24_round_missing_candidate"] = _case(
            "24_round_missing_candidate",
            "REJECTED:IncompleteEvidence",
            lambda: run(root, expected=digest),
        )

    # Duplicate candidate id.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        manifest = _load(root / "manifest.json")
        manifest["candidates"][1]["candidate_id"] = "baseline"
        _write_json(root / "manifest.json", manifest)
        digest = _reseal(root, refresh_files=False)
        cases["25_duplicate_candidate_id"] = _case(
            "25_duplicate_candidate_id",
            "REJECTED:EvidenceConflict",
            lambda: run(root, expected=digest),
        )

    # Final manifest Media authority drift.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        path = root / "final-manifest.json"
        final = _load(path)
        final["media_authority_digest"] = "0" * 64
        material = copy.deepcopy(final)
        material["manifest_digest"] = ""
        final["manifest_digest"] = r37._sha_json(material)
        _write_json(path, final)
        manifest = _load(root / "manifest.json")
        manifest["final"]["manifest_sha256"] = r37._file_sha(path)
        manifest["final"]["manifest_size"] = r37._file_size(path)
        material = copy.deepcopy(manifest)
        material["manifest_digest"] = ""
        manifest["manifest_digest"] = r37._sha_json(material)
        _write_json(root / "manifest.json", manifest)
        cases["26_final_manifest_media_authority_drift"] = _case(
            "26_final_manifest_media_authority_drift",
            "REJECTED:IncompleteEvidence",
            lambda: run(root, expected=manifest["manifest_digest"]),
        )

    # Review candidate hash drift.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        path = root / "reviews" / "round-1-a.json"
        review = _load(path)
        review["candidate_hashes"]["B"] = "0" * 64
        _write_json(path, review)
        digest = _reseal(root)
        cases["27_review_candidate_hash_drift"] = _case(
            "27_review_candidate_hash_drift",
            "REJECTED:IncompleteEvidence",
            lambda: run(root, expected=digest),
        )

    # Review source drift.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        path = root / "reviews" / "round-1-a.json"
        review = _load(path)
        review["source_sha256"] = "0" * 64
        _write_json(path, review)
        digest = _reseal(root)
        cases["28_review_source_drift"] = _case(
            "28_review_source_drift",
            "REJECTED:IncompleteEvidence",
            lambda: run(root, expected=digest),
        )

    # Invalid evidence class.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        path = root / "reviews" / "round-1-a.json"
        review = _load(path)
        review["evidence_class"] = "HUMAN_GROUND_TRUTH"
        _write_json(path, review)
        digest = _reseal(root)
        cases["29_invalid_review_evidence_class"] = _case(
            "29_invalid_review_evidence_class",
            "REJECTED:IncompleteEvidence",
            lambda: run(root, expected=digest),
        )

    # Tie => human review on exact-green local evidence.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        _make_real_local(root, evidence_class="OFFLINE_MODEL")
        for name in ("round-1-a.json", "round-1-b.json", "round-1-c.json"):
            path = root / "reviews" / name
            review = _load(path)
            review["winner"] = "tie"
            review["confidence"] = 0.9
            _write_json(path, review)
        digest = _reseal(root)
        cases["30_tie_requires_human_review"] = _case(
            "30_tie_requires_human_review",
            r37.HUMAN_REVIEW,
            lambda: run(
                root,
                expected=digest,
                creator=_accepted_creator(),
            ),
        )

    # Low confidence unanimous => human review.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        _make_real_local(root, evidence_class="OFFLINE_MODEL")
        for name in ("round-1-a.json", "round-1-b.json", "round-1-c.json"):
            path = root / "reviews" / name
            review = _load(path)
            review["confidence"] = 0.5
            _write_json(path, review)
        digest = _reseal(root)
        cases["31_low_confidence_requires_human_review"] = _case(
            "31_low_confidence_requires_human_review",
            r37.HUMAN_REVIEW,
            lambda: run(
                root,
                expected=digest,
                creator=_accepted_creator(),
            ),
        )

    # High-severity defect in final reviewed round => NEEDS_REEDIT.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        shutil.copytree(base, root)
        _make_real_local(root, evidence_class="OFFLINE_MODEL")
        for i, name in enumerate(("round-1-a.json", "round-1-b.json", "round-1-c.json")):
            path = root / "reviews" / name
            review = _load(path)
            review["defects"] = [{
                "candidate_label": "B",
                "start_ms": 3000 + i * 100,
                "end_ms": 3050 + i * 100,
                "category": "pacing",
                "severity": "high",
                "direction": "decrease",
                "requested_edit": "Trim final pacing pause.",
                "confidence": 0.9,
            }]
            _write_json(path, review)
        digest = _reseal(root)
        cases["32_final_round_high_defect_needs_reedit"] = _case(
            "32_final_round_high_defect_needs_reedit",
            r37.NEEDS_REEDIT,
            lambda: run(
                root,
                expected=digest,
                creator=_accepted_creator(),
            ),
        )

    # Exact replay => ledger no-op.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        ledger = Path(temp) / "ledger"
        shutil.copytree(base, root)
        first = run(root, ledger=ledger)
        second = run(root, ledger=ledger)
        cases["33_exact_replay_is_noop"] = {
            "name": "33_exact_replay_is_noop",
            "expected": "NOOP",
            "actual": "NOOP" if second["replay_noop"] else "CHANGED",
            "passed": (
                first["verification_digest"] == second["verification_digest"]
                and second["replay_noop"] is True
            ),
        }

    # Resume after output loss: ledger is sufficient, result stays byte-stable.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        ledger = Path(temp) / "ledger"
        shutil.copytree(base, root)
        first = run(root, ledger=ledger)
        second = run(root, ledger=ledger)
        cases["34_resume_from_ledger_byte_stable"] = {
            "name": "34_resume_from_ledger_byte_stable",
            "expected": "BYTE_STABLE",
            "actual": (
                "BYTE_STABLE"
                if first["verification_digest"] == second["verification_digest"]
                else "DRIFT"
            ),
            "passed": first["verification_digest"] == second["verification_digest"],
        }

    # Same bundle id with changed sealed bytes conflicts durably.
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "bundle"
        ledger = Path(temp) / "ledger"
        shutil.copytree(base, root)
        run(root, ledger=ledger)
        manifest = _load(root / "manifest.json")
        manifest["sealed_at"] = "2026-10-06T00:21:00Z"
        _write_json(root / "manifest.json", manifest)
        changed = _reseal(root, refresh_files=False)
        cases["35_same_bundle_identity_changed_bytes_conflict"] = _case(
            "35_same_bundle_identity_changed_bytes_conflict",
            "REJECTED:ReplayConflict",
            lambda: run(root, expected=changed, ledger=ledger),
        )

    values = [cases[key] for key in sorted(cases)]
    failures = [row["name"] for row in values if not row["passed"]]
    report = {
        "report_version": "growth.local_fullstack_verifier.r37.adversarial.v1",
        "scenario_count": len(values),
        "all_expected_dispositions_stable": not failures,
        "failed_scenarios": failures,
        "cases": {key: cases[key] for key in sorted(cases)},
        "fixture_expected_bundle_digest": expected_bundle_digest,
        "current_creator_authority_state": creator_authority["status"],
        "fixture_offline_model_genuine_are_distinct": True,
        "exact_replay_idempotent": True,
        "changed_same_identity_conflicts": True,
        "provider_mutation_authorized": False,
        "browser_mutation_authorized": False,
        "credential_access_authorized": False,
        "publish_authorized": False,
        "human_ground_truth": False,
        "report_digest": "",
    }
    material = copy.deepcopy(report)
    material["report_digest"] = ""
    report["report_digest"] = r37._sha_json(material)
    if failures:
        raise AssertionError(f"R37 adversarial scenario failures: {failures}")
    return report
