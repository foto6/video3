from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from .autonomous_reels import canonical_json

CONTRACT_VERSION = "growth.real_local_bundle_verifier.r38.v1"
AUTHORITY_VERSION = "growth.real_local_bundle_verifier_authority.r38.v1"
POLICY_VERSION = "growth.real_local_bundle_verifier_policy.r38.v1"
REVIEW_VERSION = "growth.real_local_review_evidence.r38.v1"
VERIFICATION_VERSION = "growth.real_local_bundle_verification.r38.v1"
LEDGER_VERSION = "growth.real_local_bundle_verifier_ledger.r38.v1"
ADVISORY_VERSION = "growth.real_local_bundle_advisory.r38.v1"

WAITING_MEDIA_QA = "WAITING_MEDIA_R27_QA"
READY = "READY_FOR_CREATOR_LOCAL_REHEARSAL"
NEEDS_REEDIT = "NEEDS_REEDIT"
HUMAN_REVIEW = "HUMAN_REVIEW_REQUIRED"
BLOCKED = "BLOCKED_INVALID_EVIDENCE"

MEDIA_SHA = "183838a24205c6885b2366ad6ffa394164283d91"
MEDIA_CI = 37451069045
MEDIA_ARTIFACT_ID = 11406357346
MEDIA_ARTIFACT_DIGEST = "sha256:9cfa2ddd358f2b25a3066ee60792c44c460c62715590210bacf5c5430d14a4b5"
MEDIA_CONTRACT = "media.real_input_local_rehearsal.r27.v1"
MEDIA_GROWTH_CONTRACT = "media.real_input_growth_bundle.r27.v1"

CREATOR_R39_SHA = "b7dd4f7297db2d90317236b4108b6d3133979d2f"
CREATOR_R39_RUNTIME_BLOB = "be96165df080a6646ac45cb9a0e1a5b4e963e4bd"
CREATOR_R39_TASK_SHA = "66efe791d816f07aca42d772e57eb53ad7ed331f"
CREATOR_R39_TASK_BLOB = "b37a8e28be305c43cd9d4f83ec6e3b1382374b2a"

EVIDENCE_CLASSES = {"FIXTURE", "OFFLINE_MODEL", "GENUINE_REVIEW"}


class R38Error(ValueError):
    pass


class AuthorityDrift(R38Error):
    pass


class EvidenceInvalid(R38Error):
    pass


class ReplayConflict(R38Error):
    pass


def _clone(v: Any) -> Any:
    return json.loads(canonical_json(v))


def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _sha_json(v: Any) -> str:
    return hashlib.sha256(canonical_json(v).encode("utf-8")).hexdigest()


def _file_id(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def _sha(v: Any, field: str) -> str:
    if not isinstance(v, str) or len(v) != 64 or any(c not in "0123456789abcdef" for c in v):
        raise EvidenceInvalid(f"{field} must be lowercase sha256")
    return v


def _git(v: Any, field: str) -> str:
    if not isinstance(v, str) or len(v) != 40 or any(c not in "0123456789abcdef" for c in v):
        raise AuthorityDrift(f"{field} must be lowercase git sha")
    return v


def _positive(v: Any, field: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise EvidenceInvalid(f"{field} must be positive integer")
    return v


def _safe(root: Path, rel: Any, field: str) -> Path:
    if not isinstance(rel, str) or not rel or rel.startswith("/") or ".." in Path(rel).parts:
        raise EvidenceInvalid(f"{field} unsafe relative path")
    p = (root / rel).resolve()
    rr = root.resolve()
    if rr not in p.parents:
        raise EvidenceInvalid(f"{field} escapes bundle root")
    return p


def default_authority() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(root / "conformance" / CONTRACT_VERSION / "authority.json")


def default_policy() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    return _load(root / "conformance" / CONTRACT_VERSION / "policy.json")


def validate_authority(v: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(v, Mapping) or set(v) != {"contract_version","growth_r37_parent","media_r27","creator_r39","boundary"}:
        raise AuthorityDrift("R38 authority shape drift")
    if v["contract_version"] != AUTHORITY_VERSION:
        raise AuthorityDrift("R38 authority version drift")
    g=v["growth_r37_parent"]
    if (g.get("producer_sha"),g.get("ci_run_id"),g.get("artifact_id"),g.get("artifact_digest"),g.get("contract")) != (
        "f3cb8ec0d8aa7155b6169d5f86828de3fbc9d3ba",37406299930,11387826461,
        "sha256:d53c063ffd68481fd88901111e1683a175e8c54fd8fc07574b6c798cd12ff463",
        "growth.local_fullstack_verifier.r37.v1"):
        raise AuthorityDrift("Growth R37 parent drift")
    m=v["media_r27"]
    if (m.get("producer_sha"),m.get("ci_run_id"),m.get("artifact_id"),m.get("artifact_digest"),m.get("contract"),m.get("growth_bundle_contract"),m.get("authority_state")) != (
        MEDIA_SHA,MEDIA_CI,MEDIA_ARTIFACT_ID,MEDIA_ARTIFACT_DIGEST,MEDIA_CONTRACT,MEDIA_GROWTH_CONTRACT,"PENDING_INDEPENDENT_QA"):
        raise AuthorityDrift("Media R27 authority drift")
    if m.get("independent_qa",{}).get("disposition") != "PENDING":
        raise AuthorityDrift("checked-in Media R27 QA state must remain PENDING")
    c=v["creator_r39"]
    if (c.get("producer_sha"),c.get("runtime_blob"),c.get("assignment_anchor_sha"),c.get("task_blob"),c.get("contract")) != (
        CREATOR_R39_SHA,CREATOR_R39_RUNTIME_BLOB,CREATOR_R39_TASK_SHA,CREATOR_R39_TASK_BLOB,
        "creator.current_authority_binder.r39.v1"):
        raise AuthorityDrift("Creator R39 source authority drift")
    if c.get("media_r27_pin",{}).get("producer_sha") != MEDIA_SHA or c.get("media_r27_pin",{}).get("qa_disposition") != "PENDING":
        raise AuthorityDrift("Creator R39 Media R27 pin drift")
    if v["boundary"] != {
        "local_only":True,"network_call":False,"browser_call":False,"provider_call":False,
        "provider_mutation":False,"creator_mutation":False,"publish_authorized":False,
        "live_authorization":False,"credential_access":False,"merge":False,"human_ground_truth":False,
    }:
        raise AuthorityDrift("R38 safety boundary drift")
    return _clone(v)


def validate_policy(v: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(v, Mapping) or v.get("contract_version") != POLICY_VERSION or v.get("policy_version") != 1:
        raise AuthorityDrift("R38 policy identity drift")
    if v.get("evidence_classes") != ["FIXTURE","OFFLINE_MODEL","GENUINE_REVIEW"]:
        raise AuthorityDrift("R38 evidence class policy drift")
    c=v.get("consensus",{})
    if c != {"minimum_reviews":3,"minimum_confidence":0.82,"minimum_mean_confidence":0.86,
             "allowed_winners":["BASELINE","CANDIDATE","TARGETED_REEDIT","tie","insufficient_evidence"]}:
        raise AuthorityDrift("R38 consensus policy drift")
    if v.get("decisions") != [WAITING_MEDIA_QA,READY,NEEDS_REEDIT,HUMAN_REVIEW,BLOCKED]:
        raise AuthorityDrift("R38 decision policy drift")
    for k in ("publish_authorized","provider_mutation_allowed","browser_mutation_allowed","creator_mutation_allowed","credential_access_allowed"):
        if v.get("boundaries",{}).get(k) is not False:
            raise AuthorityDrift(f"R38 boundary drift: {k}")
    return _clone(v)


def validate_media_qa(v: Mapping[str, Any] | None, authority: Mapping[str, Any], *, allow_fixture: bool=False) -> dict[str, Any] | None:
    if v is None:
        return None
    required={"contract_version","repository","producer_sha","ci_run_id","artifact_id","artifact_digest","matrix_digest","disposition",
              "accepted_media_sha","accepted_media_ci_run_id","accepted_media_artifact_id","accepted_media_artifact_digest",
              "accepted_media_contract","accepted_growth_bundle_contract","fixture_only"}
    if not isinstance(v, Mapping) or set(v) != required:
        raise AuthorityDrift("Media R27 QA certificate fields invalid")
    if v["contract_version"] != "growth.media_r27_independent_qa.r38.v1" or v["repository"] != "foto6/boss" or v["disposition"] != "ACCEPTED":
        raise AuthorityDrift("Media R27 QA acceptance invalid")
    if v["fixture_only"] is True and not allow_fixture:
        raise AuthorityDrift("fixture QA cannot accept real Media R27")
    _git(v["producer_sha"],"media_qa.producer_sha"); _positive(v["ci_run_id"],"media_qa.ci_run_id"); _positive(v["artifact_id"],"media_qa.artifact_id")
    _sha(v["artifact_digest"][7:] if isinstance(v["artifact_digest"],str) and v["artifact_digest"].startswith("sha256:") else None,"media_qa.artifact_digest")
    _sha(v["matrix_digest"],"media_qa.matrix_digest")
    m=authority["media_r27"]
    if (v["accepted_media_sha"],v["accepted_media_ci_run_id"],v["accepted_media_artifact_id"],v["accepted_media_artifact_digest"],
        v["accepted_media_contract"],v["accepted_growth_bundle_contract"]) != (
        m["producer_sha"],m["ci_run_id"],m["artifact_id"],m["artifact_digest"],m["contract"],m["growth_bundle_contract"]):
        raise AuthorityDrift("Media R27 QA certificate does not bind exact producer tuple")
    return _clone(v)


def _validate_manifest_digest(manifest: Mapping[str, Any]) -> str:
    if manifest.get("contractVersion") != MEDIA_GROWTH_CONTRACT:
        raise EvidenceInvalid("wrong Media R27 Growth bundle contract")
    digest=_sha(manifest.get("manifestDigest"),"manifestDigest")
    material=dict(manifest); material.pop("manifestDigest",None)
    if _sha_json(material) != digest:
        raise EvidenceInvalid("Media R27 Growth manifest digest mismatch")
    return digest


def _verify_files(root: Path, files: Any) -> list[dict[str,Any]]:
    if not isinstance(files,list) or not files:
        raise EvidenceInvalid("Growth bundle file list missing")
    out=[]; seen=set()
    for row in files:
        if not isinstance(row,Mapping) or set(row)!={"path","sha256","size"}:
            raise EvidenceInvalid("Growth bundle file row invalid")
        rel=row["path"]
        if rel in seen: raise EvidenceInvalid("duplicate bundle file path")
        seen.add(rel)
        p=_safe(root,rel,"files.path")
        if not p.is_file(): raise EvidenceInvalid(f"missing bundle file: {rel}")
        actual=_file_id(p)
        if actual != {"sha256":_sha(row["sha256"],f"{rel}.sha256"),"size":_positive(row["size"],f"{rel}.size")}:
            raise EvidenceInvalid(f"bundle file bytes drift: {rel}")
        out.append({"path":rel,**actual})
    return sorted(out,key=lambda x:x["path"])


def _summary_path(root: Path, manifest: Mapping[str,Any]) -> Path:
    matches=[x["path"] for x in manifest["files"] if x["path"]=="evidence/media.real_input_local_rehearsal.r27.evidence.json"]
    if matches != ["evidence/media.real_input_local_rehearsal.r27.evidence.json"]:
        raise EvidenceInvalid("R27 summary evidence file missing")
    return _safe(root,matches[0],"summary.path")


def validate_bundle(root: Path, authority: Mapping[str,Any]) -> dict[str,Any]:
    root=Path(root).resolve()
    manifest_path=root/"media.real_input_growth_bundle.r27.manifest.json"
    if not manifest_path.is_file():
        raise EvidenceInvalid("Media R27 Growth manifest missing")
    manifest=_load(manifest_path)
    digest=_validate_manifest_digest(manifest)
    producer=manifest.get("producer",{})
    if producer != {"repository":"foto6/video2","sha":MEDIA_SHA,"authorityState":"PENDING_INDEPENDENT_QA","acceptedByIndependentQa":False}:
        raise EvidenceInvalid("Media R27 producer lineage drift")
    if manifest.get("mediaContractId") != MEDIA_CONTRACT:
        raise EvidenceInvalid("Media R27 contract ID drift")
    _sha(manifest.get("operationBindingDigest"),"operationBindingDigest")
    inp=manifest.get("inputVideo",{}); _sha(inp.get("sha256"),"inputVideo.sha256"); _positive(inp.get("size"),"inputVideo.size")
    norm=manifest.get("normalizedSource",{}); _sha(norm.get("sha256"),"normalizedSource.sha256"); _positive(norm.get("size"),"normalizedSource.size"); _sha(norm.get("normalizationSpecDigest"),"normalizedSource.normalizationSpecDigest")
    candidates=manifest.get("candidates")
    if not isinstance(candidates,list) or len(candidates)!=4: raise EvidenceInvalid("exactly four Media R27 candidates required")
    ids=set(); hashes=set()
    for row in candidates:
        cid=row.get("candidateId")
        if not isinstance(cid,str) or not cid or cid in ids: raise EvidenceInvalid("candidate identity duplicate/missing")
        ids.add(cid); h=_sha(row.get("sha256"),f"{cid}.sha256")
        if h in hashes: raise EvidenceInvalid("duplicate candidate rendered bytes")
        hashes.add(h); _positive(row.get("size"),f"{cid}.size")
    target=manifest.get("targetedReedit",{}); target_hash=_sha(target.get("sha256"),"targetedReedit.sha256"); _positive(target.get("size"),"targetedReedit.size")
    if target.get("decisionClass") != "DETERMINISTIC_OFFLINE_FIXTURE":
        raise EvidenceInvalid("Media R27 targeted decision class drift")
    final=manifest.get("finalArtifact",{}); final_hash=_sha(final.get("sha256"),"finalArtifact.sha256"); _positive(final.get("size"),"finalArtifact.size")
    if final_hash != target_hash: raise EvidenceInvalid("final artifact does not match targeted re-edit lineage")
    boundary=manifest.get("evidenceBoundary",{})
    expected={"realInputBytes":True,"realEncodedMp4":True,"fixtureReviewDecision":True,"liveModelReview":False,
              "providerMutation":False,"browserMutation":False,"socialPublish":False,"liveAuthorization":False}
    if boundary != expected: raise EvidenceInvalid("Media R27 evidence boundary drift")
    files=_verify_files(root,manifest.get("files"))
    summary=_load(_summary_path(root,manifest))
    if summary.get("contractVersion") != MEDIA_CONTRACT or summary.get("state") != "LOCAL_REAL_INPUT_REHEARSAL_COMPLETE":
        raise EvidenceInvalid("Media R27 summary state/contract invalid")
    if summary.get("producer",{}).get("sha") != MEDIA_SHA or summary.get("producer",{}).get("acceptedByIndependentQa") is not False:
        raise EvidenceInvalid("Media R27 summary producer drift")
    if summary.get("inputVideo",{}).get("sha256") != inp["sha256"]:
        raise EvidenceInvalid("input source hash mismatch")
    if summary.get("normalization",{}).get("source",{}).get("sha256") != norm["sha256"]:
        raise EvidenceInvalid("normalized source lineage mismatch")
    runtime=summary.get("runtime",{})
    _sha(runtime.get("manifestSha256"),"runtime.manifestSha256")
    if not runtime.get("ffmpegVersion") or not runtime.get("ffprobeVersion") or not isinstance(runtime.get("files"),list) or not runtime["files"]:
        raise EvidenceInvalid("runtime ffmpeg/ffprobe identity incomplete")
    summary_candidates=summary.get("candidates",[])
    if {(x.get("candidateId"),x.get("sha256"),x.get("size")) for x in summary_candidates} != {(x["candidateId"],x["sha256"],x["size"]) for x in candidates}:
        raise EvidenceInvalid("candidate hashes disagree with summary")
    if summary.get("finalArtifact",{}).get("sha256") != final_hash:
        raise EvidenceInvalid("final hash disagrees with summary")
    controls=summary.get("controls",{})
    for key in ("providerMutation","browserMutation","socialPublish","liveAuthorization","modelReviewPerformed","humanReviewPerformed"):
        if controls.get(key) is not False: raise EvidenceInvalid(f"summary control boundary drift: {key}")
    phases=summary.get("phaseTimingsMs")
    if not isinstance(phases,Mapping) or len(phases)<16 or any(not isinstance(x,(int,float)) or x<0 for x in phases.values()):
        raise EvidenceInvalid("phase timing evidence incomplete")
    resume=summary.get("resumeRestartEvidence",{})
    if not isinstance(resume.get("invocations"),list) or not isinstance(resume.get("completedBeforeSeal"),list):
        raise EvidenceInvalid("resume/restart evidence incomplete")
    return {
        "manifest":manifest,"manifest_digest":digest,"files":files,"summary":summary,
        "input_sha256":inp["sha256"],"normalized_source_sha256":norm["sha256"],
        "candidate_hashes":{x["candidateId"]:x["sha256"] for x in sorted(candidates,key=lambda x:x["candidateId"])},
        "targeted_reedit_sha256":target_hash,"final_sha256":final_hash,
        "runtime_identity_digest":_sha_json(runtime),"phase_timings_digest":_sha_json(phases),
        "resume_evidence_digest":_sha_json(resume),
    }


def validate_review(v: Mapping[str,Any] | None, parsed: Mapping[str,Any], policy: Mapping[str,Any]) -> dict[str,Any]:
    if v is None:
        return {"contract_version":REVIEW_VERSION,"evidence_class":"FIXTURE","reviews":[],"review_digest":_sha_json({"media_fixture_decision":parsed["manifest_digest"]})}
    if not isinstance(v,Mapping) or set(v)!={"contract_version","evidence_class","manifest_digest","input_sha256","candidate_hashes","targeted_reedit_sha256","reviews","review_digest"}:
        raise EvidenceInvalid("review evidence shape invalid")
    if v["contract_version"] != REVIEW_VERSION or v["evidence_class"] not in EVIDENCE_CLASSES:
        raise EvidenceInvalid("review evidence class/contract invalid")
    material=dict(v); observed=_sha(material.pop("review_digest"),"review_digest")
    if _sha_json(material)!=observed: raise EvidenceInvalid("review evidence digest mismatch")
    if v["manifest_digest"]!=parsed["manifest_digest"] or v["input_sha256"]!=parsed["input_sha256"] or v["candidate_hashes"]!=parsed["candidate_hashes"] or v["targeted_reedit_sha256"]!=parsed["targeted_reedit_sha256"]:
        raise EvidenceInvalid("review evidence lineage mismatch")
    reviews=v["reviews"]
    if not isinstance(reviews,list): raise EvidenceInvalid("reviews must be list")
    seen=set()
    for row in reviews:
        if not isinstance(row,Mapping) or set(row)!={"reviewer_id","winner","confidence","response_digest","human_ground_truth"}:
            raise EvidenceInvalid("review row shape invalid")
        rid=row["reviewer_id"]
        if not isinstance(rid,str) or not rid or rid in seen: raise EvidenceInvalid("reviewer identity duplicate/missing")
        seen.add(rid)
        if row["winner"] not in policy["consensus"]["allowed_winners"]: raise EvidenceInvalid("review winner invalid")
        conf=row["confidence"]
        if isinstance(conf,bool) or not isinstance(conf,(int,float)) or not 0<=float(conf)<=1: raise EvidenceInvalid("review confidence invalid")
        _sha(row["response_digest"],"review.response_digest")
        if row["human_ground_truth"] is not False: raise EvidenceInvalid("review cannot claim human ground truth")
    return _clone(v)


def consensus(review: Mapping[str,Any], policy: Mapping[str,Any]) -> dict[str,Any]:
    rows=review["reviews"]
    if review["evidence_class"]=="FIXTURE":
        return {"state":"NON_EXECUTABLE_FIXTURE","winner":None,"reason_codes":["FIXTURE_EVIDENCE_NOT_POSITIVE"]}
    if len(rows)<policy["consensus"]["minimum_reviews"]:
        return {"state":"HUMAN_REVIEW","winner":None,"reason_codes":["INSUFFICIENT_REVIEW_COUNT"]}
    winners=[x["winner"] for x in rows]
    counts=Counter(winners); top,count=counts.most_common(1)[0]
    confs=[float(x["confidence"]) for x in rows if x["winner"]==top]
    if top in {"tie","insufficient_evidence"}:
        return {"state":"HUMAN_REVIEW","winner":None,"reason_codes":[top.upper()]}
    if count != len(rows):
        return {"state":"HUMAN_REVIEW","winner":None,"reason_codes":["REVIEW_DISAGREEMENT"]}
    if min(confs)<policy["consensus"]["minimum_confidence"] or sum(confs)/len(confs)<policy["consensus"]["minimum_mean_confidence"]:
        return {"state":"HUMAN_REVIEW","winner":None,"reason_codes":["LOW_CONFIDENCE"]}
    return {"state":"ACCEPTED","winner":top,"reason_codes":["UNANIMOUS_HIGH_CONFIDENCE"]}


class Ledger:
    def __init__(self, directory: Path):
        self.directory=Path(directory); self.path=self.directory/"growth-r38-real-local-ledger.json"
        self.data={"contract_version":LEDGER_VERSION,"bundles":{},"evaluations":{}}
        if self.path.is_file():
            raw=_load(self.path)
            if not isinstance(raw,Mapping) or raw.get("contract_version")!=LEDGER_VERSION: raise ReplayConflict("R38 ledger drift")
            self.data=dict(raw)
    def apply(self,bundle_id:str,bundle_digest:str,context_digest:str,result:Mapping[str,Any])->tuple[dict[str,Any],bool]:
        prior=self.data["bundles"].get(bundle_id)
        if prior is not None and prior!=bundle_digest: raise ReplayConflict("same bundle identity changed sealed bytes")
        existing=self.data["evaluations"].get(context_digest)
        if existing is not None:
            if existing["verification_digest"]!=result["verification_digest"]: raise ReplayConflict("exact replay changed result")
            return _clone(existing["result"]),False
        self.data["bundles"][bundle_id]=bundle_digest
        self.data["evaluations"][context_digest]={"verification_digest":result["verification_digest"],"result":_clone(result)}
        self.directory.mkdir(parents=True,exist_ok=True); _write(self.path,self.data)
        return _clone(result),True


def build_result(*,parsed:Mapping[str,Any],review:Mapping[str,Any],qa:Mapping[str,Any]|None,authority:Mapping[str,Any],policy:Mapping[str,Any],growth_sha:str,growth_ci_run_id:int)->dict[str,Any]:
    cons=consensus(review,policy)
    if qa is None:
        decision=WAITING_MEDIA_QA; reasons=["MEDIA_R27_INDEPENDENT_QA_PENDING"]
    elif cons["state"]=="NON_EXECUTABLE_FIXTURE":
        decision=HUMAN_REVIEW; reasons=cons["reason_codes"]
    elif cons["state"]=="HUMAN_REVIEW":
        decision=HUMAN_REVIEW; reasons=cons["reason_codes"]
    elif cons["winner"]=="TARGETED_REEDIT":
        decision=READY; reasons=cons["reason_codes"]
    else:
        decision=NEEDS_REEDIT; reasons=cons["reason_codes"]+[f"CONSENSUS_WINNER_{cons['winner']}"]
    result={
        "contract_version":VERIFICATION_VERSION,"state":"VERIFIED","final_decision":decision,"reason_codes":reasons,
        "bundle_id":parsed["manifest"].get("operationBindingDigest"),"bundle_manifest_digest":parsed["manifest_digest"],
        "input_sha256":parsed["input_sha256"],"normalized_source_sha256":parsed["normalized_source_sha256"],
        "candidate_hashes":parsed["candidate_hashes"],"targeted_reedit_sha256":parsed["targeted_reedit_sha256"],"final_sha256":parsed["final_sha256"],
        "runtime_identity_digest":parsed["runtime_identity_digest"],"phase_timings_digest":parsed["phase_timings_digest"],"resume_evidence_digest":parsed["resume_evidence_digest"],
        "evidence_class":review["evidence_class"],"review_digest":review["review_digest"],"consensus":cons,
        "media_r27_authority":authority["media_r27"],"media_r27_qa":qa,"creator_r39_authority":authority["creator_r39"],
        "growth_r37_parent":authority["growth_r37_parent"],"growth_r38":{"producer_sha":_git(growth_sha,"growth_sha"),"ci_run_id":_positive(growth_ci_run_id,"growth_ci_run_id"),"contract":CONTRACT_VERSION},
        "publish_authorized":False,"live_authorization":False,"provider_mutation_authorized":False,"browser_mutation_authorized":False,
        "creator_mutation_authorized":False,"credential_access_authorized":False,"human_ground_truth":False,"verification_digest":"",
    }
    material=copy.deepcopy(result); material["verification_digest"]=""
    result["verification_digest"]=_sha_json(material)
    return result


def verify(*,bundle_dir:Path,review_evidence:Mapping[str,Any]|None,media_qa:Mapping[str,Any]|None,authority:Mapping[str,Any],policy:Mapping[str,Any],ledger_dir:Path,growth_sha:str,growth_ci_run_id:int,allow_fixture_qa:bool=False)->dict[str,Any]:
    authority=validate_authority(authority); policy=validate_policy(policy)
    parsed=validate_bundle(Path(bundle_dir),authority)
    review=validate_review(review_evidence,parsed,policy)
    qa=validate_media_qa(media_qa,authority,allow_fixture=allow_fixture_qa)
    result=build_result(parsed=parsed,review=review,qa=qa,authority=authority,policy=policy,growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id)
    context=_sha_json({"bundle":parsed["manifest_digest"],"review":review["review_digest"],"qa":None if qa is None else _sha_json(qa),"creator":_sha_json(authority["creator_r39"])})
    ledger=Ledger(ledger_dir)
    canonical,changed=ledger.apply(str(parsed["manifest"].get("operationBindingDigest")),parsed["manifest_digest"],context,result)
    canonical["replay_noop"]=not changed; canonical["ledger_path"]=str(ledger.path)
    return canonical


def blocked(exc:Exception)->dict[str,Any]:
    out={"contract_version":VERIFICATION_VERSION,"state":"BLOCKED","final_decision":BLOCKED,"reason_codes":[type(exc).__name__],
         "detail":str(exc),"publish_authorized":False,"live_authorization":False,"provider_mutation_authorized":False,
         "browser_mutation_authorized":False,"creator_mutation_authorized":False,"credential_access_authorized":False,
         "human_ground_truth":False,"verification_digest":""}
    material=copy.deepcopy(out); material["verification_digest"]=""
    out["verification_digest"]=_sha_json(material); return out


def _parser()->argparse.ArgumentParser:
    p=argparse.ArgumentParser(prog="growth-r38-real-local-bundle-verifier")
    sub=p.add_subparsers(dest="command",required=True)
    v=sub.add_parser("verify")
    for name in ("bundle-dir","authority","policy","ledger-dir","out","growth-sha"): v.add_argument("--"+name,required=True)
    v.add_argument("--growth-ci-run-id",required=True,type=int); v.add_argument("--review-evidence"); v.add_argument("--media-qa")
    r=sub.add_parser("rehearse-adversarial")
    for name in ("authority","policy","out","growth-sha"): r.add_argument("--"+name,required=True)
    r.add_argument("--growth-ci-run-id",required=True,type=int)
    return p


def main(argv:Sequence[str]|None=None)->int:
    args=_parser().parse_args(argv); out=Path(args.out)
    try:
        if args.command=="rehearse-adversarial":
            from .real_local_bundle_verifier_r38_sim import rehearse
            result=rehearse(authority=_load(Path(args.authority)),policy=_load(Path(args.policy)),growth_sha=args.growth_sha,growth_ci_run_id=args.growth_ci_run_id)
            _write(out,result); print(json.dumps(result,sort_keys=True)); return 0
        result=verify(bundle_dir=Path(args.bundle_dir),review_evidence=None if not args.review_evidence else _load(Path(args.review_evidence)),
                      media_qa=None if not args.media_qa else _load(Path(args.media_qa)),authority=_load(Path(args.authority)),policy=_load(Path(args.policy)),
                      ledger_dir=Path(args.ledger_dir),growth_sha=args.growth_sha,growth_ci_run_id=args.growth_ci_run_id)
        _write(out,result); print(json.dumps(result,sort_keys=True))
        return 3 if result["final_decision"]==WAITING_MEDIA_QA else 0
    except R38Error as exc:
        result=blocked(exc); _write(out,result); print(json.dumps(result,sort_keys=True)); return 2

if __name__=="__main__":
    raise SystemExit(main())
