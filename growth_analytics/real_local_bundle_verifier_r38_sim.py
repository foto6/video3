from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .autonomous_reels import canonical_json
from . import real_local_bundle_verifier_r38 as r38


def _h(text:str)->str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write(path:Path,data:bytes)->dict[str,Any]:
    path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
    return {"sha256":hashlib.sha256(data).hexdigest(),"size":len(data)}


def _json(path:Path,value:Any)->dict[str,Any]:
    data=(json.dumps(value,sort_keys=True,separators=(",",":"))+"\n").encode()
    return _write(path,data)


def build_fixture(root:Path, *, evidence_class:str="OFFLINE_MODEL", winners:tuple[str,str,str]=("TARGETED_REEDIT","TARGETED_REEDIT","TARGETED_REEDIT"), confidences:tuple[float,float,float]=(0.94,0.92,0.90))->dict[str,Any]:
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    input_hash=_h("real-user-video-fixture"); normalized=_write(root/"normalized/source.mp4",b"normalized-real-video-fixture")
    candidates=[]
    for i in range(1,5):
        ident=_write(root/f"candidates/candidate-{i}/final.mp4",f"candidate-{i}-real-render".encode())
        candidates.append({"candidateId":f"candidate-{i}",**ident})
    target=_write(root/"targeted-reedit/final.mp4",b"targeted-reedit-real-render")
    final=_write(root/"final/final.mp4",b"targeted-reedit-real-render")
    bracket=_json(root/"initial/media.review_tournament_bracket.r25.v1.json",{"contractVersion":"fixture.bracket.v1","digest":_h("bracket")})
    target_ev={"contractVersion":"media.real_input_targeted_reedit.r27.v1","decisionClass":"DETERMINISTIC_OFFLINE_FIXTURE","modelReviewPerformed":False,"humanReviewPerformed":False,
               "baselineCandidateId":"candidate-1","baselineRenderSha256":candidates[0]["sha256"],"challengerCandidateId":"candidate-4-reedit",
               "challengerRenderSha256":target["sha256"],"targetedEvidenceDigest":_h("target-evidence"),"reviewPackageDigest":_h("review-package"),
               "sealedMappingDigest":_h("mapping"),"growthHandoffDigest":_h("handoff"),"providerMutation":False,"socialPublish":False}
    target_ev_id=_json(root/"targeted-reedit/r27-targeted-reedit-evidence.json",target_ev)
    norm_ev=_json(root/"evidence/normalization.json",{"contractVersion":"media.real_input_normalization.r27.v1","specDigest":_h("normalization-spec"),"normalizedSource":normalized})
    runtime={"manifestSha256":_h("runtime-manifest"),"ffmpegVersion":"ffmpeg fixture exact","ffprobeVersion":"ffprobe fixture exact",
             "files":[{"path":"runtime/ffmpeg.exe","sha256":_h("ffmpeg"),"size":123},{"path":"runtime/ffprobe.exe","sha256":_h("ffprobe"),"size":124}]}
    phases={name:i+1 for i,name in enumerate(["input-probe","normalize-source","pair-1","candidate-3","candidate-4-segment-1","candidate-4-segment-2","candidate-4-segment-3","candidate-4-segment-4","candidate-4-segment-5","candidate-4-segment-6","candidate-4-assemble","initial-finalize","targeted-reedit","verify-r25","final-artifact","seal-growth-bundle"])}
    resume={"invocationCount":2,"invocations":[{"sequence":1,"resumedCompletedPhases":[]},{"sequence":2,"resumedCompletedPhases":["input-probe","normalize-source"]}],
            "cancellationEvents":[],"completedBeforeSeal":list(phases)}
    summary={"contractVersion":r38.MEDIA_CONTRACT,"state":"LOCAL_REAL_INPUT_REHEARSAL_COMPLETE",
             "producer":{"repository":"foto6/video2","branch":"agent/media-r27-real-input-local-rehearsal-20261006","sha":r38.MEDIA_SHA,
                         "authorityState":"PENDING_INDEPENDENT_QA","acceptedByIndependentQa":False},
             "operationBinding":{"digest":_h("operation-binding"),"value":{"fixture":True}},
             "inputVideo":{"pathIdentity":_h("path"),"sha256":input_hash,"size":777001,"probe":{"hasVideo":True,"hasAudio":True,"width":1920,"height":1080,"fps":30,"durationMs":12000}},
             "normalization":{"specDigest":_h("normalization-spec"),"source":normalized},
             "runtime":runtime,"candidates":candidates,
             "bracket":{"digest":_h("bracket"),"reviewPackages":[]},
             "targetedReedit":target_ev,
             "finalArtifact":{"selectionClass":"DETERMINISTIC_OFFLINE_FIXTURE_TARGETED_REEDIT",**final},
             "phaseTimingsMs":phases,"resumeRestartEvidence":resume,
             "controls":{"realInputBytes":True,"realEncodedMp4":True,"sourceHashProbeBeforeRender":True,"fourCandidatesRequired":True,
                         "fixtureReviewDecision":True,"modelReviewPerformed":False,"humanReviewPerformed":False,"providerMutation":False,
                         "browserMutation":False,"socialPublish":False,"liveAuthorization":False}}
    summary_id=_json(root/"evidence/media.real_input_local_rehearsal.r27.evidence.json",summary)
    files=[
      {"path":"evidence/media.real_input_local_rehearsal.r27.evidence.json",**summary_id},
      {"path":"evidence/normalization.json",**norm_ev},
      {"path":"initial/media.review_tournament_bracket.r25.v1.json",**bracket},
    ]
    files += [{"path":f"candidates/candidate-{i}/final.mp4","sha256":candidates[i-1]["sha256"],"size":candidates[i-1]["size"]} for i in range(1,5)]
    files += [{"path":"targeted-reedit/r27-targeted-reedit-evidence.json",**target_ev_id},{"path":"targeted-reedit/final.mp4",**target},{"path":"final/final.mp4",**final}]
    manifest={"contractVersion":r38.MEDIA_GROWTH_CONTRACT,"mediaContractId":r38.MEDIA_CONTRACT,
              "producer":{"repository":"foto6/video2","sha":r38.MEDIA_SHA,"authorityState":"PENDING_INDEPENDENT_QA","acceptedByIndependentQa":False},
              "operationBindingDigest":_h("operation-binding"),
              "inputVideo":{"sha256":input_hash,"size":777001,"pathIdentity":_h("path")},
              "normalizedSource":{"sha256":normalized["sha256"],"size":normalized["size"],"normalizationSpecDigest":_h("normalization-spec")},
              "candidates":candidates,
              "targetedReedit":{"candidateId":"candidate-4-reedit","sha256":target["sha256"],"size":target["size"],"decisionClass":"DETERMINISTIC_OFFLINE_FIXTURE"},
              "finalArtifact":final,"files":sorted(files,key=lambda x:x["path"]),
              "evidenceBoundary":{"realInputBytes":True,"realEncodedMp4":True,"fixtureReviewDecision":True,"liveModelReview":False,
                                  "providerMutation":False,"browserMutation":False,"socialPublish":False,"liveAuthorization":False}}
    manifest["manifestDigest"]=r38._sha_json(manifest)
    _json(root/"media.real_input_growth_bundle.r27.manifest.json",manifest)
    review_material={"contract_version":r38.REVIEW_VERSION,"evidence_class":evidence_class,"manifest_digest":manifest["manifestDigest"],
                     "input_sha256":input_hash,"candidate_hashes":{x["candidateId"]:x["sha256"] for x in candidates},
                     "targeted_reedit_sha256":target["sha256"],
                     "reviews":[{"reviewer_id":f"reviewer-{i+1}","winner":winners[i],"confidence":confidences[i],
                                 "response_digest":_h(f"response-{i}-{winners[i]}-{confidences[i]}"),"human_ground_truth":False} for i in range(3)]}
    review={**review_material,"review_digest":r38._sha_json(review_material)}
    return {"root":root,"manifest":manifest,"review":review}


def fixture_qa(authority:Mapping[str,Any])->dict[str,Any]:
    m=authority["media_r27"]
    return {"contract_version":"growth.media_r27_independent_qa.r38.v1","repository":"foto6/boss","producer_sha":"9"*40,"ci_run_id":49999999038,
            "artifact_id":12999999038,"artifact_digest":"sha256:"+_h("qa-artifact"),"matrix_digest":_h("qa-matrix"),"disposition":"ACCEPTED",
            "accepted_media_sha":m["producer_sha"],"accepted_media_ci_run_id":m["ci_run_id"],"accepted_media_artifact_id":m["artifact_id"],
            "accepted_media_artifact_digest":m["artifact_digest"],"accepted_media_contract":m["contract"],
            "accepted_growth_bundle_contract":m["growth_bundle_contract"],"fixture_only":True}


def _case(name:str,expected:str,fn)->dict[str,Any]:
    try:
        r=fn(); actual=r["final_decision"]
        return {"name":name,"expected":expected,"actual":actual,"passed":actual==expected,"digest":r.get("verification_digest")}
    except Exception as exc:
        actual=f"REJECTED:{type(exc).__name__}"
        return {"name":name,"expected":expected,"actual":actual,"passed":actual==expected,"detail":str(exc)}


def rehearse(*,authority:Mapping[str,Any],policy:Mapping[str,Any],growth_sha:str,growth_ci_run_id:int)->dict[str,Any]:
    authority=r38.validate_authority(authority); policy=r38.validate_policy(policy); cases={}
    with tempfile.TemporaryDirectory() as td:
        base=Path(td)/"base"; fx=build_fixture(base,evidence_class="FIXTURE"); qa=fixture_qa(authority)
        def run(root=base,review=fx["review"],qa_value=None,allow=False,ledger="ledger"):
            return r38.verify(bundle_dir=root,review_evidence=review,media_qa=qa_value,authority=authority,policy=policy,
                              ledger_dir=Path(td)/ledger,growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id,allow_fixture_qa=allow)
        cases["01_pending_media_qa"]= _case("01_pending_media_qa",r38.WAITING_MEDIA_QA,lambda:run())
        cases["02_fixture_after_qa_human"]= _case("02_fixture_after_qa_human",r38.HUMAN_REVIEW,lambda:run(qa_value=qa,allow=True,ledger="l2"))
        off=build_fixture(Path(td)/"off",evidence_class="OFFLINE_MODEL")
        cases["03_offline_model_target_ready"]= _case("03_offline_model_target_ready",r38.READY,lambda:r38.verify(bundle_dir=off["root"],review_evidence=off["review"],media_qa=qa,authority=authority,policy=policy,ledger_dir=Path(td)/"l3",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id,allow_fixture_qa=True))
        gen=build_fixture(Path(td)/"gen",evidence_class="GENUINE_REVIEW")
        cases["04_genuine_target_ready"]= _case("04_genuine_target_ready",r38.READY,lambda:r38.verify(bundle_dir=gen["root"],review_evidence=gen["review"],media_qa=qa,authority=authority,policy=policy,ledger_dir=Path(td)/"l4",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id,allow_fixture_qa=True))
        for n,w,expect in [("05_candidate_needs_reedit","CANDIDATE",r38.NEEDS_REEDIT),("06_baseline_needs_reedit","BASELINE",r38.NEEDS_REEDIT),("07_tie_human","tie",r38.HUMAN_REVIEW),("08_insufficient_human","insufficient_evidence",r38.HUMAN_REVIEW)]:
            z=build_fixture(Path(td)/n,evidence_class="OFFLINE_MODEL",winners=(w,w,w))
            cases[n]=_case(n,expect,lambda z=z,n=n:r38.verify(bundle_dir=z["root"],review_evidence=z["review"],media_qa=qa,authority=authority,policy=policy,ledger_dir=Path(td)/("l"+n),growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id,allow_fixture_qa=True))
        dis=build_fixture(Path(td)/"dis",evidence_class="OFFLINE_MODEL",winners=("TARGETED_REEDIT","TARGETED_REEDIT","CANDIDATE"))
        cases["09_disagreement_human"]=_case("09_disagreement_human",r38.HUMAN_REVIEW,lambda:r38.verify(bundle_dir=dis["root"],review_evidence=dis["review"],media_qa=qa,authority=authority,policy=policy,ledger_dir=Path(td)/"l9",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id,allow_fixture_qa=True))
        low=build_fixture(Path(td)/"low",evidence_class="OFFLINE_MODEL",confidences=(0.7,0.95,0.95))
        cases["10_low_confidence_human"]=_case("10_low_confidence_human",r38.HUMAN_REVIEW,lambda:r38.verify(bundle_dir=low["root"],review_evidence=low["review"],media_qa=qa,authority=authority,policy=policy,ledger_dir=Path(td)/"l10",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id,allow_fixture_qa=True))
        missing=build_fixture(Path(td)/"missing",evidence_class="OFFLINE_MODEL"); missing["review"]["reviews"]=missing["review"]["reviews"][:2]; material=dict(missing["review"]); material.pop("review_digest"); missing["review"]["review_digest"]=r38._sha_json(material)
        cases["11_missing_review_human"]=_case("11_missing_review_human",r38.HUMAN_REVIEW,lambda:r38.verify(bundle_dir=missing["root"],review_evidence=missing["review"],media_qa=qa,authority=authority,policy=policy,ledger_dir=Path(td)/"l11",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id,allow_fixture_qa=True))
        dup=copy.deepcopy(off["review"]); dup["reviews"][1]["reviewer_id"]=dup["reviews"][0]["reviewer_id"]; m=dict(dup); m.pop("review_digest"); dup["review_digest"]=r38._sha_json(m)
        cases["12_duplicate_reviewer_invalid"]=_case("12_duplicate_reviewer_invalid","REJECTED:EvidenceInvalid",lambda:r38.verify(bundle_dir=off["root"],review_evidence=dup,media_qa=qa,authority=authority,policy=policy,ledger_dir=Path(td)/"l12",growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id,allow_fixture_qa=True))
        bad=copy.deepcopy(off["review"]); bad["reviews"][0]["confidence"]=0.1
        cases["13_review_digest_tamper_invalid"]=_case("13_review_digest_tamper_invalid","REJECTED:EvidenceInvalid",lambda:r38.validate_review(bad,r38.validate_bundle(off["root"],authority),policy))
        bad=copy.deepcopy(off["review"]); bad["manifest_digest"]="0"*64; m=dict(bad);m.pop("review_digest");bad["review_digest"]=r38._sha_json(m)
        cases["14_review_manifest_lineage_invalid"]=_case("14_review_manifest_lineage_invalid","REJECTED:EvidenceInvalid",lambda:r38.validate_review(bad,r38.validate_bundle(off["root"],authority),policy))
        bad=copy.deepcopy(off["review"]); bad["input_sha256"]="0"*64;m=dict(bad);m.pop("review_digest");bad["review_digest"]=r38._sha_json(m)
        cases["15_review_input_lineage_invalid"]=_case("15_review_input_lineage_invalid","REJECTED:EvidenceInvalid",lambda:r38.validate_review(bad,r38.validate_bundle(off["root"],authority),policy))
        bad=copy.deepcopy(off["review"]); bad["candidate_hashes"]["candidate-1"]="0"*64;m=dict(bad);m.pop("review_digest");bad["review_digest"]=r38._sha_json(m)
        cases["16_review_candidate_lineage_invalid"]=_case("16_review_candidate_lineage_invalid","REJECTED:EvidenceInvalid",lambda:r38.validate_review(bad,r38.validate_bundle(off["root"],authority),policy))
        tamper=Path(td)/"tamper"; shutil.copytree(off["root"],tamper); (tamper/"candidates/candidate-1/final.mp4").write_bytes(b"tampered")
        cases["17_candidate_bytes_tamper"]=_case("17_candidate_bytes_tamper","REJECTED:EvidenceInvalid",lambda:r38.validate_bundle(tamper,authority))
        tamper=Path(td)/"sumtamper"; shutil.copytree(off["root"],tamper); (tamper/"evidence/media.real_input_local_rehearsal.r27.evidence.json").write_text("{}\n")
        cases["18_summary_bytes_tamper"]=_case("18_summary_bytes_tamper","REJECTED:EvidenceInvalid",lambda:r38.validate_bundle(tamper,authority))
        tamper=Path(td)/"mantamper"; shutil.copytree(off["root"],tamper); p=tamper/"media.real_input_growth_bundle.r27.manifest.json"; v=json.loads(p.read_text());v["manifestDigest"]="0"*64;p.write_text(json.dumps(v))
        cases["19_manifest_digest_tamper"]=_case("19_manifest_digest_tamper","REJECTED:EvidenceInvalid",lambda:r38.validate_bundle(tamper,authority))
        for key,name in [("sha","20_producer_sha_drift"),("contract","21_contract_drift")]:
            tamper=Path(td)/name;shutil.copytree(off["root"],tamper);p=tamper/"media.real_input_growth_bundle.r27.manifest.json";v=json.loads(p.read_text())
            if key=="sha": v["producer"]["sha"]="0"*40
            else: v["mediaContractId"]="wrong.contract"
            v["manifestDigest"]=r38._sha_json({k:x for k,x in v.items() if k!="manifestDigest"});p.write_text(json.dumps(v))
            cases[name]=_case(name,"REJECTED:EvidenceInvalid",lambda tamper=tamper:r38.validate_bundle(tamper,authority))
        for name,mut in [
          ("22_duplicate_candidate_id",lambda v:v["candidates"].__setitem__(1,{**v["candidates"][1],"candidateId":v["candidates"][0]["candidateId"]})),
          ("23_duplicate_candidate_bytes",lambda v:v["candidates"].__setitem__(1,{**v["candidates"][1],"sha256":v["candidates"][0]["sha256"]})),
          ("24_target_final_mismatch",lambda v:v["finalArtifact"].__setitem__("sha256","0"*64)),
        ]:
            tamper=Path(td)/name;shutil.copytree(off["root"],tamper);p=tamper/"media.real_input_growth_bundle.r27.manifest.json";v=json.loads(p.read_text());mut(v);v["manifestDigest"]=r38._sha_json({k:x for k,x in v.items() if k!="manifestDigest"});p.write_text(json.dumps(v))
            cases[name]=_case(name,"REJECTED:EvidenceInvalid",lambda tamper=tamper:r38.validate_bundle(tamper,authority))
        for name,mut in [
          ("25_runtime_missing",lambda s:s.__setitem__("runtime",{})),
          ("26_phase_timings_incomplete",lambda s:s.__setitem__("phaseTimingsMs",{"input-probe":1})),
          ("27_resume_evidence_incomplete",lambda s:s.__setitem__("resumeRestartEvidence",{})),
          ("28_boundary_provider_mutation",lambda s:s["controls"].__setitem__("providerMutation",True)),
        ]:
            tamper=Path(td)/name;shutil.copytree(off["root"],tamper);sp=tamper/"evidence/media.real_input_local_rehearsal.r27.evidence.json";s=json.loads(sp.read_text());mut(s);sid=_json(sp,s)
            mp=tamper/"media.real_input_growth_bundle.r27.manifest.json";v=json.loads(mp.read_text())
            for row in v["files"]:
                if row["path"]=="evidence/media.real_input_local_rehearsal.r27.evidence.json": row.update(sid)
            v["manifestDigest"]=r38._sha_json({k:x for k,x in v.items() if k!="manifestDigest"});mp.write_text(json.dumps(v))
            cases[name]=_case(name,"REJECTED:EvidenceInvalid",lambda tamper=tamper:r38.validate_bundle(tamper,authority))
        wrongqa=fixture_qa(authority);wrongqa["accepted_media_sha"]="0"*40
        cases["29_wrong_qa_media_pin"]=_case("29_wrong_qa_media_pin","REJECTED:AuthorityDrift",lambda:r38.validate_media_qa(wrongqa,authority,allow_fixture=True))
        cases["30_fixture_qa_rejected_real_mode"]=_case("30_fixture_qa_rejected_real_mode","REJECTED:AuthorityDrift",lambda:r38.validate_media_qa(qa,authority,allow_fixture=False))
        ledger=Path(td)/"replay";first=r38.verify(bundle_dir=off["root"],review_evidence=off["review"],media_qa=qa,authority=authority,policy=policy,ledger_dir=ledger,growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id,allow_fixture_qa=True)
        second=r38.verify(bundle_dir=off["root"],review_evidence=off["review"],media_qa=qa,authority=authority,policy=policy,ledger_dir=ledger,growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id,allow_fixture_qa=True)
        cases["31_exact_replay_noop"]={"name":"31_exact_replay_noop","expected":"NOOP","actual":"NOOP" if second["replay_noop"] else "WRITE","passed":second["replay_noop"]}
        conflict=Path(td)/"conflict";shutil.copytree(off["root"],conflict);mp=conflict/"media.real_input_growth_bundle.r27.manifest.json";v=json.loads(mp.read_text());v["normalizedSource"]["size"]+=1;v["manifestDigest"]=r38._sha_json({k:x for k,x in v.items() if k!="manifestDigest"});mp.write_text(json.dumps(v))
        cases["32_same_identity_changed_manifest_conflict"]=_case("32_same_identity_changed_manifest_conflict","REJECTED:ReplayConflict",lambda:r38.verify(bundle_dir=conflict,review_evidence=None,media_qa=None,authority=authority,policy=policy,ledger_dir=ledger,growth_sha=growth_sha,growth_ci_run_id=growth_ci_run_id))
        bada=copy.deepcopy(authority);bada["creator_r39"]["producer_sha"]="0"*40
        cases["33_creator_authority_drift"]=_case("33_creator_authority_drift","REJECTED:AuthorityDrift",lambda:r38.validate_authority(bada))
        bada=copy.deepcopy(authority);bada["media_r27"]["artifact_id"]+=1
        cases["34_media_artifact_authority_drift"]=_case("34_media_artifact_authority_drift","REJECTED:AuthorityDrift",lambda:r38.validate_authority(bada))
        boundary_ok=all(first[k] is False for k in ["publish_authorized","live_authorization","provider_mutation_authorized","browser_mutation_authorized","creator_mutation_authorized","credential_access_authorized"])
        cases["35_no_publish_authorization"]={"name":"35_no_publish_authorization","expected":"SAFE","actual":"SAFE" if boundary_ok else "UNSAFE","passed":boundary_ok}
        failures=[x["name"] for x in cases.values() if not x["passed"]]
        if failures: raise AssertionError(f"R38 adversarial failures: {failures}")
        report={"report_version":"growth.real_local_bundle_verifier.r38.rehearsal.v1","scenario_count":len(cases),"all_expected_dispositions_stable":True,
                "cases":{k:cases[k] for k in sorted(cases)},"source_ready_status":r38.WAITING_MEDIA_QA,"media_r27_qa_disposition":"PENDING",
                "creator_r39_source_sha":r38.CREATOR_R39_SHA,"media_r27_sha":r38.MEDIA_SHA,"publish_authorized":False,"provider_mutation_authorized":False,
                "browser_mutation_authorized":False,"credential_access_authorized":False,"human_ground_truth":False,"report_digest":""}
        material=copy.deepcopy(report);material["report_digest"]="";report["report_digest"]=r38._sha_json(material);return report
