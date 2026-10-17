"""Bind the accepted Media certificate without promoting source tests to reviews."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from . import real_local_bundle_verifier_r38 as r38

VERSION = "growth.media_qa_bind.r39.v1"
CERTIFICATE = Path(__file__).resolve().parents[1] / "conformance" / VERSION / "media-qa.json"
CERTIFICATE_BLOB = "52241a8629aa9b958cd549e837da80ae21abe6be"
SOURCE_SHA = "06ade2b1e8d40c4ab6559f3ffe1d69b0ee36fd78"
CREATOR_SHA = "e1ac861826662eb1e7155db455970f5902ea49b8"
QA_PRODUCER = "6ab529846e99e572d77006dee48d6c4b01caea12"
QA_CI = 37452884976
QA_ARTIFACT = 11407720740
QA_DIGEST = "sha256:910326676dd9371745cb70099ec3db76a21ad7f0d5e171bcd59bddfa091bd7d8"
MATRIX_DIGEST = "831804bedce4652156ac052e1c50f1e6f3642cd36dadbc58a00907804e646a54"


def validate_certificate(value):
    checked = r38.validate_media_qa(value, r38.default_authority(), allow_fixture=False)
    if checked is None or checked.get("fixture_only") is not False:
        raise r38.AuthorityDrift("An actual independent QA certificate is required")
    observed = (checked["producer_sha"], checked["ci_run_id"], checked["artifact_id"],
                checked["artifact_digest"], checked["matrix_digest"])
    if observed != (QA_PRODUCER, QA_CI, QA_ARTIFACT, QA_DIGEST, MATRIX_DIGEST):
        raise r38.AuthorityDrift("Stale or mismatched independent QA authority")
    return checked


def load_certificate():
    raw = CERTIFICATE.read_bytes()
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if blob != CERTIFICATE_BLOB:
        raise r38.AuthorityDrift("Certificate bytes differ from the pinned Boss source")
    return validate_certificate(json.loads(raw))


def diagnose():
    certificate = load_certificate()
    return {
        "contract_version": VERSION,
        "state": "SOURCE_READY_FOR_REAL_BUNDLE",
        "media_qa": certificate,
        "certificate_source_sha": SOURCE_SHA,
        "creator_source": {"producer_sha": CREATOR_SHA, "contract": "creator.autonomous_reels_local.r40.v1"},
        "real_bundle_verified": False,
        "real_quality_review_verified": False,
        "ci_authority": None,
        "publish_authorized": False,
        "live_authorization": False,
    }


def verify_bundle(bundle_dir, review=None):
    result = diagnose()
    parsed = r38.validate_bundle(Path(bundle_dir), r38.validate_authority(r38.default_authority()))
    # A sealed hash can also describe a synthetic fixture. Inspect and decode the
    # actual bytes before claiming that a bundle contains real encoded video.
    for row in parsed["files"]:
        if not row["path"].lower().endswith(".mp4"):
            continue
        path = r38._safe(Path(bundle_dir), row["path"], "encoded media")
        try:
            data = json.loads(subprocess.check_output([
                "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                "-of", "json", str(path)], stderr=subprocess.PIPE, timeout=30))
            if not any(s.get("codec_type") == "video" and s.get("width", 0) > 0 and s.get("height", 0) > 0
                       for s in data.get("streams", [])) or float(data.get("format", {}).get("duration", 0)) <= 0:
                raise ValueError("No decodable video stream")
            subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=120)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise r38.EvidenceInvalid(f"Bundle video is not a valid encoded MP4: {row['path']}") from exc
    policy = r38.validate_policy(r38.default_policy())
    validated_review = r38.validate_review(review, parsed, policy)
    consensus = r38.consensus(validated_review, policy)
    result.update({
        "state": "REAL_BUNDLE_VERIFIED_REVIEW_REQUIRED",
        "real_bundle_verified": True,
        "manifest_digest": parsed["manifest_digest"],
        "input_sha256": parsed["input_sha256"],
        "final_sha256": parsed["final_sha256"],
        "candidate_hashes": parsed["candidate_hashes"],
        "evidence_class": validated_review["evidence_class"],
        "review_consensus": consensus,
    })
    # The R38 policy owns the distinction between fixture and genuine reviews.
    if validated_review["evidence_class"] == "GENUINE_REVIEW" and consensus["state"] == "ACCEPTED":
        result["review_digest"] = validated_review["review_digest"]
        result["real_quality_review_verified"] = True
        result["state"] = "REAL_BUNDLE_AND_REVIEW_VERIFIED" if consensus["winner"] == "TARGETED_REEDIT" else "REAL_REVIEW_NEEDS_REEDIT"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("diagnose", "verify"))
    parser.add_argument("--bundle-dir")
    parser.add_argument("--review")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            if not args.bundle_dir: raise ValueError("--bundle-dir is required")
            review = json.loads(Path(args.review).read_text(encoding="utf-8")) if args.review else None
            result = verify_bundle(args.bundle_dir, review)
        else: result = diagnose()
        if args.out:
            output = Path(args.out); output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({"state": "BLOCKED", "error": str(exc), "publish_authorized": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
