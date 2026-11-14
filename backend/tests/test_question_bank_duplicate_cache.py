from difflib import SequenceMatcher
from types import SimpleNamespace

from backend.app.services.paper_builder_service import _duplicate_map, _duplicate_map_cached, _normalized_stem


def original_map(questions):
    result = {q.question_id: {"kind": "", "of": "", "similarity": 0.0} for q in questions}
    for index, left in enumerate(questions):
        for right in questions[index + 1:]:
            a = _normalized_stem(left.stem_zh or left.stem)
            b = _normalized_stem(right.stem_zh or right.stem)
            if not a or not b:
                continue
            same_family = bool(left.question_family_id and left.question_family_id == right.question_family_id)
            if a == b:
                kind, ratio = "exact", 1.0
            else:
                ratio = SequenceMatcher(None, a, b).ratio()
                if same_family and ratio >= .60:
                    kind = "family"
                elif ratio >= .82:
                    kind = "near"
                else:
                    continue
            for current, other in ((left.question_id, right.question_id), (right.question_id, left.question_id)):
                if ratio > result[current]["similarity"]:
                    result[current] = {"kind": kind, "of": other, "similarity": round(ratio, 3)}
    return result


def test_duplicate_labels_match_original_and_change_with_question_content():
    def q(qid, stem, family=""):
        return SimpleNamespace(question_id=qid, question_version=1, stem=stem, stem_zh="",
                               question_family_id=family)
    questions = [q("a", "线性回归参数怎样计算？"), q("b", "线性回归参数怎样计算？"),
                 q("c", "线性回归参数应该怎样计算？"), q("d", "完全不同的聚类方法", "f"),
                 q("e", "聚类方法还有哪些不同", "f"), q("f", "短"), q("g", "一段很长很长很长的机器学习题干")]
    _duplicate_map_cached.cache_clear()
    assert _duplicate_map(questions) == original_map(questions)
    assert _duplicate_map_cached.cache_info().hits == 0
    assert _duplicate_map(questions) == original_map(questions)
    assert _duplicate_map_cached.cache_info().hits == 1
    questions[1].stem = "更新后这道题已经不是重复题"
    assert _duplicate_map(questions) == original_map(questions)
    assert _duplicate_map_cached.cache_info().misses == 2
