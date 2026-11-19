"""按【文件内容】判别资料类型：老师课件 / 课堂笔记 / 考试题 / 其他。

需求原文：「这个需要从文件内容进行判断，而不是通过文件后缀名，如果自动化脚本
无法实现我们可以自动接入 deepseek 的 api 进行一个判别」。

设计要点：
  1. 扩展名只用来决定「怎么把字节读成文本」，**绝不参与分类打分**；
     文件名也不参与规则打分、也不传给模型。
  2. 规则层给出四类得分与置信度；证据充分就直接判定（0 次模型调用）。
  3. 证据不足/两类分数接近/正文为空时，自动调用 DeepSeek 判别（只看正文）。
  4. 模型不可用或输出不合法时，如实降级为规则结果并标 needs_review，
     不假装已经判对。
  5. 用户手动指定永远优先，但系统仍会把自动判别结果记录在案。
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any

from sqlalchemy.orm import Session

from backend.app.services import model_provider

TYPES = ("exam_paper", "slides", "notes", "other")
TYPE_LABELS = {
    "exam_paper": "考试题",
    "slides": "老师课件",
    "notes": "课堂笔记",
    "other": "其他",
}
PROMPT_VERSION = "material-type-1.0"

# ---------------------------------------------------------------- 文本抽取
def read_text(content: bytes, ext: str, *, limit: int = 12000) -> str:
    """把上传的字节读成文本。ext 仅用于选择解析方式，不用于判断类型。"""
    ext = (ext or "").lower()
    try:
        if ext == ".pdf":
            import pymupdf
            doc = pymupdf.open(stream=content, filetype="pdf")
            parts = []
            for i, page in enumerate(doc):
                if i >= 24:
                    break
                parts.append(page.get_text())
            doc.close()
            return "\n".join(parts)[:limit]
        if ext in (".docx", ".pptx", ".xlsx"):
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                parts = []
                names = [n for n in z.namelist()
                         if n.endswith(".xml") and (
                             "document" in n or "slide" in n or "sheet" in n
                             or "notesSlide" in n)]
                for n in sorted(names)[:60]:
                    raw = z.read(n).decode("utf-8", "replace")
                    parts.append(" ".join(re.findall(r"<(?:a:w|w:t|a:t)[^>]*>(.*?)</",
                                                     raw, re.S)))
                return re.sub(r"<[^>]+>", " ", "\n".join(parts))[:limit]
        if ext in (".txt", ".md", ".markdown", ".py", ".java", ".cpp", ".c",
                   ".js", ".ts", ".sql", ".json", ".yaml", ".yml", ".csv"):
            return content.decode("utf-8", "replace")[:limit]
        if ext in (".html", ".htm"):
            return re.sub(r"<[^>]+>", " ",
                          content.decode("utf-8", "replace"))[:limit]
    except Exception:
        return ""
    return ""


# ---------------------------------------------------------------- 规则特征
_EXAM_MARKS = [
    (r"\b(?:midterm|final)\s+(?:exam|answers)|\bthis exam is\b", 10, "英文考试说明"),
    (r"\(\s*[a-z]\s*\)\s*\[\s*\d+\s*\]|\b\d+\s+points?\b", 6, "英文分值与小题号"),
    (r"[一二三四五六七八九十]+\s*[、.．]\s*[^\n]{0,14}(?:选择题|名词解释|判断题|"
     r"填空题|简答题|计算题|证明题|综合题|论述题)", 6, "题型分区标题"),
    (r"(?:每\s*(?:小)?题|共\s*\d+\s*分|满分|总分)\s*\d*\s*分?", 4, "分值标记"),
    (r"(?:^|\n)\s*\d{1,2}\s*[.．、]\s*\S", 3, "连续题号"),
    (r"(?:考试时间|考试日期|学号|班级|姓名|考号|密封线|得分|阅卷)", 3, "考试栏目"),
    (r"(?:^|\n)\s*[A-D]\s*[）).．、]\s*\S", 3, "客观题选项"),
    (r"（\s*[）)]|\(\s*\)|_{4,}", 2, "作答空位"),
]
_SLIDES_MARKS = [
    (r"第\s*[一二三四五六七八九十\d]+\s*[章节讲]", 4, "章节标题"),
    (r"(?:本章|本节)\s*(?:要点|小结|目标|内容|总结)|教学目标|学习目标|课程大纲",
     4, "教学目标/小结"),
    (r"(?:定义|定理|引理|推论|性质|公式|证明|算法)\s*[：:0-9]", 3, "定义定理公式"),
    (r"(?:例\s*\d|例题|示例|举例|如图所示|下图|图\s*\d)", 3, "例题/图示"),
    (r"[A-Za-z]\s*=\s*[^=\n]{2,}|∑|∫|∇|\\frac|argmax|argmin|≤|≥",
     3, "数学公式"),
    (r"第\s*\d+\s*页|Page\s*\d+|\d+\s*/\s*\d+", 2, "页码版式"),
    # 课件特有：授课单位/课程名、回顾页、页脚独立页码。试卷与笔记一般没有。
    (r"(?:大学|学院|课程|讲义|课件|Lecture|Slides?)", 3, "授课单位/课程标识"),
    (r"(?:回顾|主要内容|目录|Outline|Agenda)\s*[：:]?", 2, "课件导航页"),
    (r"(?:^|\n)\s*\d{1,3}\s*(?=\n|$)", 2, "页脚页码"),
]
_NOTES_MARKS = [
    (r"(?:我的理解|个人总结|自己整理|笔记|复习提纲|易错点|重点记|要记住|"
     r"注意[:：]|划重点|踩坑|错题整理|背诵|口诀)", 5, "整理语气"),
    # 课件同样大量使用项目符号，权重压低，避免把讲义误判成课堂笔记。
    (r"(?:^|\n)\s*[-*•·]\s+\S", 1, "条目化笔记"),
    (r"(?:概念对比|区别与联系|总结一下|小结[:：]|记忆方法|考试常考)",
     3, "归纳表达"),
    (r"(?:首先|其次|另外|补充|备注)[：:，,)]", 2, "自述补充"),
]
_CODE_MARKS = [
    (r"(?:^|\n)\s*(?:import |from \S+ import |def |class |function |"
     r"public static|#include|SELECT |console\.log)", 5, "代码语句"),
    (r"[{};]\s*$", 2, "代码标点"),
]

_LINE_RE = re.compile(r"[^\n]+")


def _hits(text: str, marks: list[tuple[str, int, str]]) -> tuple[int, list[str]]:
    score, why = 0, []
    for pattern, weight, label in marks:
        n = len(re.findall(pattern, text, re.M | re.I))
        if n:
            # 同一类特征重复出现有收益，但设上限，避免长课件靠行数刷分
            score += min(n, 6) * weight
            why.append(f"{label}×{n}")
    return score, why


def rule_scores(text: str) -> dict[str, Any]:
    """四类打分，全部来自正文。返回每类得分与证据。"""
    t = text or ""
    lines = _LINE_RE.findall(t)
    short_ratio = (sum(1 for ln in lines if len(ln.strip()) <= 24) / len(lines)
                   if lines else 0.0)
    out: dict[str, Any] = {}
    for key, marks in (("exam_paper", _EXAM_MARKS),
                       ("slides", _SLIDES_MARKS),
                       ("notes", _NOTES_MARKS)):
        s, why = _hits(t, marks)
        out[key] = {"score": s, "evidence": why}

    # 课件通常是「短行 + 标题 + 公式」，笔记通常有整理语气而试卷一定有分值/题号。
    if short_ratio > 0.65 and len(lines) >= 8 and out["slides"]["score"] >= 6:
        out["slides"]["score"] += 4
        out["slides"]["evidence"].append(f"短行版式 {short_ratio:.0%}")
    # 没有分值/题号时，试卷分要打折扣，防止「一、选择题」出现在课件目录里误判
    if not re.search(r"\d+\s*分|满分|分值|每题|\[\s*\d+\s*\]|\bpoints?\b", t):
        out["exam_paper"]["score"] = int(out["exam_paper"]["score"] * 0.55)

    code_s, code_why = _hits(t, _CODE_MARKS)
    if code_s >= 10:
        # 代码是内容特征，但归类到 other（本项目保留四类法）
        out["other"] = {"score": code_s, "evidence": code_why + ["代码正文"]}
    else:
        out["other"] = {"score": 0, "evidence": []}
    out["_text_len"] = len(t)
    out["_short_line_ratio"] = round(short_ratio, 3)
    return out


def _from_rules(scores: dict[str, Any]) -> tuple[str, float, str, bool]:
    """返回 (type, confidence, reason, needs_model)。"""
    ranked = sorted(
        ((k, v["score"], v["evidence"]) for k, v in scores.items()
         if k in TYPES), key=lambda x: -x[1])
    if scores.get("_text_len", 0) < 80:
        return "other", 0.0, "正文文本过少，规则无法判别", True
    top, top_s, top_why = ranked[0]
    second_s = ranked[1][1]
    if top_s < 8:
        return "other", 0.25, "正文没有足够的类型特征", True
    margin = (top_s - second_s) / max(top_s, 1)
    conf = round(min(0.95, 0.45 + 0.5 * margin), 2)
    reason = f"内容特征：{'、'.join(top_why[:4])}"
    # 两类势均力敌，或绝对置信度不够 → 交给模型
    needs_model = margin < 0.28 or conf < 0.62
    return top, conf, reason, needs_model


_MODEL_SYS = (
    "你是课程资料分类器。只能根据下面这段【正文内容】判断资料类型，"
    "不要根据文件名、扩展名或文件来源猜测。\n"
    "四类：\n"
    "- exam_paper 考试题/试卷：有统一题号、分值、题型分区、作答栏或考试时间；\n"
    "- slides 老师课件：有章节标题、定义/定理/公式/例题、教学目标或小结，按页组织；\n"
    "- notes 课堂笔记：第一人称整理、复习提纲、易错点归纳，没有统一题号和分值；\n"
    "- other 其他：以上都不像，或正文信息不足以判断。\n"
    "注意：课件里偶尔出现「例题」不等于试卷；笔记里抄了题目也不等于试卷。"
    "判不准时给 other 并把 confidence 压低。\n"
    "只输出 JSON：{\"type\": \"...\", \"confidence\": 0-1, "
    "\"evidence\": [\"...\"], \"reason\": \"...\"}，不要解释文字。"
)


def _parse_model_json(raw: str) -> dict | None:
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    body = fence.group(1) if fence else raw
    if not fence:
        s, e = body.find("{"), body.rfind("}")
        if s < 0 or e <= s:
            return None
        body = body[s:e + 1]
    try:
        d = json.loads(body)
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) else None


def classify(db: Session, *, text: str, allow_model: bool = True,
             fallback: str = "other") -> dict:
    """主入口。返回 type / confidence / source / reason / needs_review。"""
    scores = rule_scores(text)
    kind, conf, reason, needs_model = _from_rules(scores)
    base = {
        "type": kind, "confidence": conf, "reason": reason,
        "evidence": scores[kind]["evidence"][:6],
        "scores": {k: v["score"] for k, v in scores.items() if k in TYPES},
        "source": "rule", "needs_review": False,
        "model": "", "prompt_version": PROMPT_VERSION,
        "text_len": int(scores.get("_text_len", 0)),
    }
    if not (needs_model and allow_model):
        if needs_model:
            # 规则自己都说「证据不足」，又没有走模型复核：必须标成待复核，
            # 不能让调用方拿这个低置信结果去覆盖一份已经归好类的资料。
            base["needs_review"] = True
            base["reason"] += "；规则证据不足，本次未做模型复核"
        return base

    sample = (text or "").strip()
    if len(sample) < 80:
        # 扫描/图片型资料没有文本层，模型同样无从判断。此时不浪费一次调用，
        # 也不按文件名或后缀猜测，如实标记为「需要人工确认」。
        base["needs_review"] = True
        base["evidence"] = ["文本层不足"]
        base["reason"] = ("正文文本层不足（可能是扫描/图片型资料），没有可供判别的"
                          "内容；未调用模型，也未依据文件名或扩展名猜测")
        return base
    sample = sample[:6000]
    try:
        res = model_provider.chat(
            db,
            messages=[{"role": "system", "content": _MODEL_SYS},
                      {"role": "user", "content": "【正文内容开始】\n" + sample
                       + "\n【正文内容结束】"}],
            skill_name="material_type", prompt_version=PROMPT_VERSION,
            temperature=0.0, max_tokens=400, timeout=45.0)
    except model_provider.ModelUnavailable as e:
        base["needs_review"] = True
        base["reason"] += f"；模型不可用（{e.status}），暂按规则结果"
        return base
    if res.get("finish_reason") == "length":
        base["needs_review"] = True
        base["reason"] += "；模型输出被截断，未采用"
        return base
    parsed = _parse_model_json((res.get("content") or "").strip())
    if not parsed or parsed.get("type") not in TYPES:
        base["needs_review"] = True
        base["reason"] += "；模型输出不是合法分类结果，未采用"
        return base
    try:
        mconf = max(0.0, min(1.0, float(parsed.get("confidence") or 0)))
    except (TypeError, ValueError):
        mconf = 0.0
    ev = [str(x)[:120] for x in (parsed.get("evidence") or [])
          if str(x).strip()][:4] if isinstance(parsed.get("evidence"), list) else []
    return {
        "type": parsed["type"], "confidence": round(mconf, 2),
        "reason": str(parsed.get("reason") or "")[:300],
        "evidence": ev,
        "scores": base["scores"], "source": "model",
        "needs_review": mconf < 0.6,
        "text_len": base["text_len"],
        "model": res.get("model", ""),
        "prompt_version": PROMPT_VERSION,
        "call_id": res.get("call_id", ""),
    }
