"""Transparent evidence independence heuristics, not a proof of mastery."""
import json
import re
import unicodedata
from hashlib import sha256


def item_evidence(item, score, attempt, *, assessment_id=""):
    snap = item.snapshot or {} if item else {}
    stem = str(snap.get("stem") or snap.get("stem_zh") or "")
    content = {"stem": re.sub(r"\s+", "", unicodedata.normalize("NFC", stem)),
               "options": snap.get("options") or []}
    return {"score_id": score.score_id, "paper_item_id": score.paper_item_id,
            "attempt_id": attempt.attempt_id, "assessment_version_id": attempt.assessment_version_id,
            "assessment_id": assessment_id,
            "student_id": attempt.student_id, "kp_id": score.kp_id or snap.get("main_kp_id") or "",
            "authority_status": score.authority_status, "source_ref": score.source_ref,
            "correct": score.correct, "computed_at": score.computed_at or "",
            "family_id": score.family_id or snap.get("question_family_id") or "",
            "content_fingerprint": sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest() if stem.strip() else ""}


def summarize_independence(refs):
    # Union by either declared family OR exact canonical content. A copied item
    # cannot become independent just because its question/family ID changed.
    parents = list(range(len(refs)))
    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]; i = parents[i]
        return i
    keys = {}
    for i, r in enumerate(refs):
        for field in ("family_id", "content_fingerprint"):
            key = (field, r.get(field))
            if not key[1]: continue
            if key in keys: parents[find(i)] = find(keys[key])
            else: keys[key] = i
    groups = {}
    for i, r in enumerate(refs):
        if not r.get("family_id") and not r.get("content_fingerprint"): continue
        groups.setdefault(find(i), []).append(r)
    latest = [max(rows, key=lambda r: (r.get("computed_at", ""), r.get("score_id", ""))) for rows in groups.values()]
    count = len(latest)
    correct = sum(r.get("correct") is True for r in latest)
    assessments = len({r.get("assessment_id") for r in refs if r.get("assessment_id")})
    accuracy = correct / count if count else None
    enough = count >= 3 and assessments >= 2
    state = ("需加强" if enough and accuracy is not None and accuracy < .6 else
             "初步通过" if enough and accuracy is not None and accuracy >= .8 else "证据不足")
    return {"independent_family_count": count, "independent_correct_count": correct,
            "independent_accuracy": round(accuracy, 4) if accuracy is not None else None,
            "raw_evidence_count": len(refs), "assessment_count": assessments,
            "state": state, "observation": "单次或不足量观察，尚不能断定持续弱点" if not enough else "跨测验独立题族的重复证据",
            "rule": "至少3个独立题族且覆盖2次实际测验（同卷修订版本只算一次）才作持续判断；同题族或完全相同内容合并，采用最近证据。阈值是保守启发式，不是统计证明；未标注的近义变式仍需复核。"}


def aggregate_reference(summaries):
    refs = [r for summary in summaries for r in summary.get("evidence_refs", [])]
    grouped = {}
    for ref in refs:
        if ref.get("kp_id"): grouped.setdefault(ref["kp_id"], []).append(ref)
    rows = []
    for kp, evidence in sorted(grouped.items()):
        count = len(evidence); correct = sum(r.get("correct") is True for r in evidence)
        rows.append({"kp_id": kp, "evidence_count": count, "correct_count": correct,
                     "reference_accuracy": round(correct / count, 4),
                     "student_count": len({r.get("student_id") for r in evidence}),
                     "assessment_count": len({r.get("assessment_id") for r in evidence if r.get("assessment_id")}),
                     "evidence_refs": evidence,
                     "interpretation": "仅推断评分参考，不能作为正式掌握或持续薄弱结论"})
    return {"basis": "历史反推评分参考层，不进入正式掌握率/知识状态",
            "evidence_count": len(refs), "unmapped_evidence_count": sum(not r.get("kp_id") for r in refs),
            "knowledge_points": rows,
            "assumptions": ["依赖历史总分及每题分值正确、答卷选择唯一的假设", "推导一致不等于教师标准答案", "缺考与未评分不按答错计算"],
            "formal_mastery": False}
