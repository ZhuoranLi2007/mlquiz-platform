"""原始 PDF → 结构化题目 → 中文译文 → 中文 PDF（V2.3）。

流程与边界：
  1. PyMuPDF 读文本层；文本层不足的页面渲染成图，用 `deepseek-flash` 视觉识别；
  2. 只依据原文提取题目：题号、题型、分值、选项、答案、页码、原文片段；
  3. 代码校验题号 / 页码 / 原文片段；对不上的一律标 `needs_review`，不静默接受；
  4. 逐题翻译，保留原文（`stem` 不动，译文写 `stem_zh`），公式统一 `$...$`；
  5. 生成中文 PDF，缺题时明确写缺哪几题，绝不拿部分题冒充整卷；
  6. 缓存键 = 模型 + 提示词版本 + PDF sha256，三者不变就不重复调用模型。

不做的事：不补写原卷没有的题目、不把推测答案标成官方答案、不伪造分值。
"""
from __future__ import annotations

import base64
import json
import re
import tempfile
from pathlib import Path

import pymupdf
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.models import entities as E
from backend.app.services import model_provider
from backend.app.services.booklet_pdf import Writer, font_path
from backend.app.services.latex_plain import to_plain

MODEL = "deepseek-flash"
PROMPT_VERSION = "paper-localize-1.0"
TRANSLATE_VERSION = "translate-1.0"
MAX_TEXT_CHARS = 24000
PAGE_TEXT_MIN = 120          # 每页少于此字符数视为「文本层不足」，转视觉识别

TYPE_MAP = {
    "single_choice": "single_choice", "multiple_choice": "multi_choice",
    "multi_choice": "multi_choice", "true_false": "true_false",
    "fill_blank": "short_answer", "short_answer": "short_answer",
    "calculation": "numeric", "numeric": "numeric", "proof": "derivation",
    "derivation": "derivation", "comprehensive": "comprehensive",
    "term": "term", "unknown": "unknown",
}
TYPE_LABELS = {
    "single_choice": "单选题", "multi_choice": "多选题", "true_false": "判断题",
    "numeric": "计算/数值题", "term": "名词解释", "short_answer": "简答题",
    "derivation": "证明/推导题", "comprehensive": "综合题", "unknown": "待核对题型",
}

SYS_EXTRACT = """你是试卷结构化助手。只依据给定的 PDF 原文提取题目，禁止补写、改写、编造或合并题目。

输出严格 JSON：
{"paper_title":"原文标题","language":"en|zh",
 "instructions_original":"原卷考试说明原文，没有就留空",
 "instructions_zh":"考试说明的中文翻译，没有就留空",
 "duration_minutes":75,
 "questions":[
 {"no":"1(a)","type":"single_choice|multi_choice|true_false|short_answer|numeric|derivation|comprehensive|term|unknown",
  "stem":"原题题干原文","options":[{"key":"A","text":"选项原文"}],"score":10,
  "answer":"原卷明确给出的答案，没有就留空","page":1,
  "quote":"原文中逐字出现的一段连续文字，用于校验","needs_review":false,
  "review_reason":""}]}

规则：
1. no 必须与原文题号完全一致；多小问保留 (a)(b) 层级，不能把一道大题拆成互不相关的题。
2. score 只能填原文明确写出的分值；原文没写就填 null，不要估算。
3. answer 只能填原卷实际给出的答案；原卷没给答案就留空字符串，不要自己解题。
4. page 是题目所在页码（原文用 === PAGE n === 分隔）。
5. quote 必须逐字摘自原文（不翻译、不改写），长度 20–200 字符。
6. 数学公式统一用 LaTeX：行内 $...$，独立公式 $$...$$。
   分段向量/矩阵必须用 bmatrix，例如 ψ(x)=[x1^2-x2^2, x1x2, x1^2+x2^2]^T
   写成 $\\psi(x)=\\begin{bmatrix}x_1^2-x_2^2 & x_1x_2 & x_1^2+x_2^2\\end{bmatrix}^{T}$，
   不能写成 $x_1$ $x_2$ 这种碎片。
7. 原文中的表格用小写 array/matrix 表达，并保留行列含义。
8. 任何不确定（文字模糊、公式缺失、分值不清）都要 needs_review=true 并写明 review_reason。
9. 如果原文不是完整试卷（例如只有答案页），照实提取，不要凑数。
10. duration_minutes 只能填原文明确写出的时长（如 "1 hour and 15 minutes" → 75），没写就填 null。"""

SYS_TRANSLATE = """你是机器学习课程助教，负责把英文试卷题目翻译成中文。只翻译，不解题、不补充条件、不改数字和分值。

术语必须统一：
Support Vector Machine→支持向量机；Perceptron→感知机；Maximum Margin→最大间隔；
Kernel Function→核函数；Bias-Variance Tradeoff→偏差-方差权衡；Cross Entropy→交叉熵；
Gradient Descent→梯度下降；Principal Component Analysis→主成分分析；
Regularization→正则化；Overfitting→过拟合；Hyperplane→超平面。

要求：
1. 保留原题的条件、数字、符号、单位、分值，不得增删。
2. 专业名词第一次出现可写成「支持向量机（SVM）」。
3. 公式保持 LaTeX：行内 $...$，独立 $$...$$；不要转义反斜杠。
4. 不要输出「解：」「答案：」之类原题没有的内容。
5. 只输出 JSON：{"items":[{"no":"1(a)","stem_zh":"中文题干","options":[{"key":"A","text_zh":"中文选项"}]}]}
   原文没有选项的题，options 返回空数组。"""


SYS_FORMULA = """你是公式规范化助手。把题目里的数学公式改写成标准 LaTeX，**只改公式写法**，文字、数字、分值、语序都不动。

规则：
1. 行内公式用 $...$，独立公式用 $$...$$。
2. Unicode 上标（如 A−1、x²）改成 LaTeX：$A^{-1}$、$x^{2}$。
3. 下标写成 $x_{i}$；矩阵、求和、积分、分式用标准 LaTeX 命令。
4. 普通英文单词与标点不要包进 $...$。
5. 不要翻译，不要解题，不要增删条件。
6. 只输出 JSON：{"items":[{"no":"1(a)","stem":"改好的原文","stem_zh":"改好的中文"}]}
   原文或中文哪一项为空就返回空字符串。"""


class LocalizeError(Exception):
    """本地化失败，message 可直接展示。"""


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _trigrams(text: str) -> set[str]:
    s = _norm(text)
    return {s[i:i + 3] for i in range(max(0, len(s) - 2))} or ({s} if s else set())


def _overlap(a: str, b: str) -> float:
    """字符 3-gram 覆盖率：容忍换行、连字符与少量转写差异。"""
    A, B = _trigrams(a), _trigrams(b)
    if not A:
        return 0.0
    return len(A & B) / len(A)


def _no_tokens(no: str) -> list[str]:
    """把 `2(a)` 拆成主号与子号，PDF 里常见 `2.` 与 `(a)` 分行。"""
    main = re.findall(r"\d+", no or "")
    sub = re.findall(r"[a-hA-H]", re.sub(r"^.*?[（(]", "", no or ""))
    tokens = list(main)
    if sub:
        tokens.append(f"({sub[0].lower()})")
        tokens.append(f"{sub[0].lower()})")
    return tokens


def _verify(question: dict, pages: list[dict]) -> list[str]:
    """代码侧校验：对不上原文的一律进待审核，不静默接受。

    真实 PDF 排版很碎（题号 `2.` 与 `(a)` 分行、分值写成 `[15]`），
    所以用「子号 + 3-gram 覆盖率」而不是整串精确匹配，避免把好题误判成坏题。
    """
    issues: list[str] = []
    page_no = int(question.get("page") or 0)
    page_text = next((p["text"] for p in pages if p["page"] == page_no), "")
    if not page_text:
        issues.append(f"页码 {page_no} 不在范围内")
        page_text = " ".join(p["text"] for p in pages)
    all_text = " ".join(p["text"] for p in pages)

    quote = str(question.get("quote") or "")
    stem = str(question.get("stem") or "")
    score_ok = False
    if quote and _overlap(quote, page_text) >= 0.55:
        score_ok = True
    if not score_ok and stem and _overlap(stem, page_text) >= 0.55:
        score_ok = True
    if not score_ok and stem and _overlap(stem, all_text) >= 0.55:
        score_ok = True
    if not score_ok:
        issues.append("题干/原文片段未在对应页命中")

    no = str(question.get("no") or "")
    if not no:
        issues.append("缺少题号")
    else:
        tokens = _no_tokens(no)
        lowered = all_text.lower()
        if tokens and not any(t.lower() in lowered for t in tokens):
            issues.append(f"题号 {no} 未在原文出现")

    score = question.get("score")
    if score is not None:
        try:
            value = float(score)
            if value < 0 or value > 200:
                issues.append("分值异常")
            elif f"{value:g}" not in all_text:
                issues.append(f"分值 {value:g} 未在原文出现")
        except (TypeError, ValueError):
            issues.append("分值不是数字")

    if not stem:
        issues.append("缺少题干")
    return issues


def read_pages(pdf_path: Path) -> list[dict]:
    doc = pymupdf.open(str(pdf_path))
    try:
        pages = []
        for index in range(doc.page_count):
            page = doc[index]
            text = page.get_text() or ""
            pages.append({"page": index + 1, "text": text.strip(),
                          "chars": len(text.strip())})
        return pages
    finally:
        doc.close()


def extract_page_images(pdf_path: Path, out_dir: Path, *,
                        min_pixels: int = 3600) -> dict[int, list[Path]]:
    """提取 PDF 每页的嵌入图片（图表、示意图、扫描页），跳过图标级小图。

    中文版要"保留原有图表"，但翻译是纯文本重排，所以必须先把原图抽出来，
    再按页插回中文 PDF。返回 {页码: [图片路径...]}。
    """
    out: dict[int, list[Path]] = {}
    doc = pymupdf.open(str(pdf_path))
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        for index in range(doc.page_count):
            page = doc[index]
            files: list[Path] = []
            for idx, info in enumerate(page.get_images(full=True)):
                xref = info[0]
                try:
                    base = doc.extract_image(xref)
                except Exception:                      # noqa: BLE001
                    continue
                data = base.get("image")
                w = int(base.get("width") or 0)
                h = int(base.get("height") or 0)
                if not data or w * h < min_pixels:
                    continue
                ext = str(base.get("ext") or "png").lower()
                if ext not in ("png", "jpg", "jpeg", "webp", "bmp"):
                    ext = "png"
                dest = out_dir / f"p{index + 1:03d}_{idx}.{ext}"
                dest.write_bytes(data)
                files.append(dest)
            if files:
                out[index + 1] = files
        return out
    finally:
        doc.close()


def render_page_png(pdf_path: Path, page_no: int, out_dir: Path) -> Path:
    doc = pymupdf.open(str(pdf_path))
    try:
        page = doc[page_no - 1]
        zoom = min(2.0, 1600 / max(1.0, page.rect.width))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"page_{page_no:04d}.png"
        pix.save(str(out))
        return out
    finally:
        doc.close()


def _ocr_page(db: Session, image: Path, page_no: int) -> str:
    """扫描页兜底：用支持图片输入的 deepseek-flash 读出页面文字。"""
    b64 = base64.b64encode(image.read_bytes()).decode()
    result = model_provider.chat(
        db,
        messages=[
            {"role": "system", "content":
             "你是 OCR 助手。只逐字转录图片中的试卷文字与公式，不要解题、不要补全。"
             "公式用 LaTeX（行内 $...$）。看不清的字符写成 [?]。"},
            {"role": "user", "content": [
                {"type": "text", "text": f"这是试卷第 {page_no} 页，请转录全部文字。"},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ],
        skill_name="paper_ocr", prompt_version=PROMPT_VERSION,
        temperature=0, max_tokens=3000, timeout=180,
    )
    return (result.get("content") or "").strip()


def _extract(db: Session, pages: list[dict]) -> dict:
    blocks = []
    for page in pages:
        blocks.append(f"=== PAGE {page['page']} ===\n{page['text']}")
    text = "\n\n".join(blocks)[:MAX_TEXT_CHARS]
    result = model_provider.chat(
        db,
        messages=[{"role": "system", "content": SYS_EXTRACT},
                  {"role": "user", "content": text}],
        skill_name="paper_extract", prompt_version=PROMPT_VERSION,
        temperature=0, max_tokens=6000, timeout=240,
    )
    if result.get("finish_reason") == "length":
        raise LocalizeError("提取结果被截断（max_tokens 用尽），请缩小页范围后重试")
    data = model_provider.parse_json_content(result.get("content") or "")
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise LocalizeError("模型未返回合法的题目 JSON")
    return data


def _translate(db: Session, items: list[dict]) -> dict[str, dict]:
    """批量翻译，返回 no -> {stem_zh, options_zh}。"""
    payload = [{"no": q["no"], "stem": q["stem"],
                "options": q.get("options") or []} for q in items]
    result = model_provider.chat(
        db,
        messages=[{"role": "system", "content": SYS_TRANSLATE},
                  {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        skill_name="paper_translate", prompt_version=TRANSLATE_VERSION,
        temperature=0, max_tokens=6000, timeout=240,
    )
    if result.get("finish_reason") == "length":
        raise LocalizeError("翻译结果被截断，请分批重试")
    data = model_provider.parse_json_content(result.get("content") or "")
    out: dict[str, dict] = {}
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        for row in data["items"]:
            if isinstance(row, dict) and row.get("no"):
                out[str(row["no"])] = row
    return out


def _normalize_formulas(db: Session, items: list[dict],
                        zh_map: dict[str, dict]) -> int:
    """把没有 `$` 定界符的题干交给模型做公式规范化，返回改动题数。

    模型两次提取同一份 PDF 时，公式可能一次写 LaTeX、一次写 Unicode 上标
    （`A−1`）；前端只渲染 `$...$`，所以这一步是必需的兜底，而不是可选项。
    """
    need = [it for it in items if "$" not in str(it.get("stem") or "")]
    if not need:
        return 0
    payload = [{"no": it.get("no"), "stem": it.get("stem") or "",
                "stem_zh": (zh_map.get(str(it.get("no"))) or {}).get("stem_zh", "")}
               for it in need]
    try:
        result = model_provider.chat(
            db,
            messages=[{"role": "system", "content": SYS_FORMULA},
                      {"role": "user",
                       "content": json.dumps(payload, ensure_ascii=False)}],
            skill_name="paper_formula", prompt_version=PROMPT_VERSION,
            temperature=0, max_tokens=6000, timeout=240,
        )
        data = model_provider.parse_json_content(result.get("content") or "")
    except model_provider.ModelUnavailable:
        return 0
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return 0

    by_no = {str(it.get("no")): it for it in items}
    changed = 0
    for row in data["items"]:
        if not isinstance(row, dict):
            continue
        no = str(row.get("no") or "")
        target = by_no.get(no)
        if target is None:
            continue
        new_stem = str(row.get("stem") or "").strip()
        # 只在模型确实补上了定界符、且长度没有明显缩水时采用，避免丢条件。
        if new_stem and "$" in new_stem and len(new_stem) >= len(
                str(target.get("stem") or "")) * 0.8:
            target["stem"] = new_stem
            changed += 1
        new_zh = str(row.get("stem_zh") or "").strip()
        if new_zh and "$" in new_zh and no in zh_map:
            zh_map[no]["stem_zh"] = new_zh
    return changed


def _parse_position(no: str) -> int:
    m = re.search(r"(\d+)\s*[（(]?\s*([a-zA-Z])?", no or "")
    if not m:
        return 0
    num = int(m.group(1))
    letter = (m.group(2) or "").lower()
    return num * 10 + ((ord(letter) - 96) if letter else 0)


def _answer_payload(qtype: str, answer: str) -> dict:
    text = (answer or "").strip()
    if not text:
        return {}
    if qtype in ("single_choice", "multi_choice", "true_false"):
        keys = re.findall(r"\b([A-H])\b", text.upper())
        if qtype == "single_choice" and keys:
            return {"key": keys[0]}
        if qtype == "multi_choice" and keys:
            return {"keys": sorted(set(keys))}
        if qtype == "true_false":
            low = text.lower()
            if any(w in low for w in ("true", "correct", "对", "正确")):
                return {"value": True}
            if any(w in low for w in ("false", "incorrect", "错", "错误")):
                return {"value": False}
        return {}
    return {"reference_answer": text}


def _upsert(db: Session, paper: E.ExternalPaper, item: dict,
            translated: dict | None, *, pages: list[dict]) -> dict:
    no = str(item.get("no") or "").strip()
    position = _parse_position(no)
    qtype = TYPE_MAP.get(str(item.get("type") or "").lower(), "unknown")
    stem = str(item.get("stem") or "").strip()
    issues = _verify(item, pages)
    if not translated or not (translated.get("stem_zh") or "").strip():
        issues.append("中文翻译缺失")
    needs_review = bool(item.get("needs_review")) or bool(issues)

    # 复用同题号条目，避免重复建题；没有就补一条
    ext_item = db.execute(select(E.ExternalItem).where(
        E.ExternalItem.paper_id == paper.epaper_id,
        E.ExternalItem.position == position,
    )).scalars().first()
    if ext_item is None:
        ext_item = E.ExternalItem(
            item_id=E.new_id("exti"), source_id=paper.source_id,
            kind="question", category="university_exam",
            title=(stem[:80] or no), url=paper.source_url or paper.pdf_url,
            scope_class="unconfirmed", review_status="auto",
            paper_id=paper.epaper_id, position=position,
            model=MODEL, prompt_version=PROMPT_VERSION,
            uncertainties=["由 PDF 自动提取，尚未做课程范围判别"],
        )
        db.add(ext_item)
        db.flush()
    ext_item.title = (f"第{no}题 {stem[:60]}" if no else stem[:80])
    ext_item.question_type = qtype
    ext_item.position = position
    # 未做课程范围判别的题要留痕：既要说清来源，也要说清「为什么还没判」。
    if not ext_item.model:
        ext_item.model = MODEL
    if not ext_item.prompt_version:
        ext_item.prompt_version = PROMPT_VERSION
    if not ext_item.uncertainties:
        ext_item.uncertainties = ["由 PDF 自动提取，尚未做课程范围判别"]

    options = []
    zh_options = {str(o.get("key")): str(o.get("text_zh") or "")
                  for o in (translated or {}).get("options") or []
                  if isinstance(o, dict)}
    for opt in item.get("options") or []:
        if not isinstance(opt, dict):
            continue
        key = str(opt.get("key") or "")
        text = str(opt.get("text") or "")
        options.append({"key": key, "text": text,
                        "text_zh": zh_options.get(key, "")})

    score = item.get("score")
    try:
        score_val = float(score) if score is not None else 5.0
    except (TypeError, ValueError):
        score_val = 5.0
    score_val = max(0.0, min(200.0, score_val))

    answer_text = str(item.get("answer") or "").strip()
    payload = _answer_payload(qtype, answer_text)

    existing = None
    if ext_item.imported_question_id:
        existing = db.execute(select(E.Question).where(
            E.Question.question_id == ext_item.imported_question_id,
        ).order_by(E.Question.question_version.desc())).scalars().first()

    zh_text = str((translated or {}).get("stem_zh") or "")
    # 同一份 PDF 的重复处理：原地刷新最新版本，不制造无意义的新版本。
    # 已交卷答卷使用的是自己的 PaperItem 快照，不会因这里刷新而改变分数。
    if (existing is not None
            and (existing.original_question_no or "") == no
            and (existing.translation_version or "") == TRANSLATE_VERSION):
        existing.stem = stem
        existing.stem_zh = zh_text
        existing.origin = "external"
        existing.options = options
        existing.score = score_val
        existing.answer_payload = payload
        existing.answer_status = "verified" if payload else "unknown"
        existing.answer_origin = "source" if payload else "none"
        existing.review_status = "pending" if needs_review else "verified"
        existing.translation_status = "done" if zh_text else "failed"
        existing.translated_at = E.utcnow()
        existing.extraction_status = ("needs_review" if needs_review
                                      else "extracted")
        existing.source_page = int(item.get("page") or 0)
        existing.source_quote = str(item.get("quote") or "")
        existing.notes = ("；".join(issues) if issues else
                          f"PDF 自动提取并通过原文校验；模型 {MODEL}")
        ext_item.imported_question_id = existing.question_id
        db.flush()
        return {"question_id": existing.question_id,
                "version": existing.question_version, "no": no, "type": qtype,
                "score": score_val, "issues": issues, "reused": True,
                "has_answer": bool(payload), "zh": bool(zh_text),
                "needs_review": bool(item.get("needs_review"))}

    question_id = (existing.question_id if existing
                   else E.new_id("q"))
    version = (existing.question_version + 1) if existing else 1
    if existing is not None:
        # 旧版本被本次重新提取取代：保留行以备追溯，但不再参与组卷与统计。
        existing.active = False
    row = E.Question(
        row_id=E.new_id("qrow"), question_id=question_id, question_version=version,
        origin="external", original_exam_id=paper.epaper_id,
        original_question_no=no, question_type=qtype,
        stem=stem, stem_zh=str((translated or {}).get("stem_zh") or ""),
        options=options, stem_images=[],
        score=score_val, main_kp_id="", aux_kp_ids=[],
        cognitive_level="理解", difficulty="中等",
        estimated_minutes=max(2.0, score_val * 1.2),
        source_refs=[{"document_id": "", "rel_path": paper.source_url or "",
                      "page": int(item.get("page") or 0),
                      "label": f"原卷第 {no} 题" if no else "原卷"}],
        question_family_id=question_id, parent_question_id="",
        review_status="pending" if needs_review else "verified",
        answer_status=("verified" if payload else "unknown"),
        answer_origin="source" if payload else "none",
        active=True, created_by="paper_localize",
        notes=("；".join(issues) if issues else
               f"PDF 自动提取并通过原文校验；模型 {MODEL}"),
        scope_class="unconfirmed",
        external_item_id=ext_item.item_id,
        answer_payload=payload,
        rubric={"points": []} if payload else {},
        explanation="",
        original_language=str(item.get("_language") or "en"),
        translation_status=("done" if (translated or {}).get("stem_zh") else "failed"),
        translation_model=MODEL, translation_version=TRANSLATE_VERSION,
        translated_at=E.utcnow(),
        extraction_status=("needs_review" if needs_review else "extracted"),
        source_page=int(item.get("page") or 0),
        source_quote=str(item.get("quote") or ""),
    )
    db.add(row)
    ext_item.imported_question_id = question_id
    db.flush()
    return {"question_id": question_id, "version": version, "no": no,
            "type": qtype, "score": score_val, "issues": issues,
            "has_answer": bool(payload), "zh": bool(row.stem_zh),
            "needs_review": bool(item.get("needs_review"))}


def _build_zh_pdf(paper: E.ExternalPaper, rows: list[E.Question],
                  out_path: Path, *, instructions: str = "",
                  duration: int | None = None, partial: bool = False,
                  missing_note: str = "") -> dict:
    w = Writer(font_path())
    w.keep_together(90)
    w.para("中文版试卷", size=18, gap=2)
    w.para(paper.title or "扩展试卷", size=13, gap=2)
    meta = [f"原始来源：{paper.source_url or paper.pdf_url or '未登记'}"]
    if paper.institution:
        meta.insert(0, f"原始机构：{paper.institution}")
    if paper.year:
        meta.append(f"年份：{paper.year}" + (f" {paper.semester}" if paper.semester else ""))
    if duration:
        meta.append(f"考试时长：{duration} 分钟")
    for line in meta:
        w.para(to_plain(line), size=9.5, color=(0.35, 0.35, 0.35), gap=1)

    if instructions:
        w.keep_together(48)
        w.para("考试说明", size=10.5, color=(0.16, 0.32, 0.55), gap=2)
        w.para(to_plain(instructions), size=9.5,
               color=(0.3, 0.3, 0.3), gap=3)

    if partial:
        w.keep_together(30)
        w.para("本文件为部分版本：" + (missing_note or "部分题目未能识别"),
               size=9.5, color=(0.6, 0.25, 0.1), gap=3)

    w.para("说明：本文件由平台按原卷自动翻译生成，供中文学习使用；"
           "如与原卷表述有出入，以原卷为准。",
           size=9, color=(0.45, 0.45, 0.45), gap=1)
    w.rule(gap=10)

    for index, q in enumerate(rows, start=1):
        head = (f"{q.original_question_no or index}　"
                f"{TYPE_LABELS.get(q.question_type, '题目')}　{q.score:g} 分")
        # 标题 + 至少两行题干留在同一页
        w.keep_together(3 * 11 * 1.35 + 12)
        w.para(head, size=11, color=(0.16, 0.32, 0.55), gap=3)
        w.para(to_plain(q.stem_zh or q.stem or ""), size=10.5, indent=10, gap=2)
        for opt in q.options or []:
            if not isinstance(opt, dict):
                continue
            text = opt.get("text_zh") or opt.get("text") or ""
            w.para(f"{opt.get('key', '')}. {to_plain(text)}", size=10,
                   indent=22, gap=1)
        if not (q.options or []):
            w.y += 3 * 11
        w.y += 6
    size = w.save(out_path)
    return {"pdf_bytes": size, "pages": w.page_no}


def _version_key(paper: E.ExternalPaper) -> str:
    return f"{MODEL}|{PROMPT_VERSION}|{TRANSLATE_VERSION}|{paper.pdf_sha256 or ''}"


def _deactivate_superseded(db: Session) -> int:
    """同一 question_id 只保留最新版本 active。

    重复提取同一份 PDF 会产生多个版本；旧版本若继续 active，题库统计会把它
    算成多道题，去重检查也会报重复。历史版本保留（可追溯），但不再参与组卷。
    """
    rows = db.execute(select(E.Question).where(
        E.Question.active.is_(True))).scalars().all()
    latest: dict[str, E.Question] = {}
    for q in rows:
        cur = latest.get(q.question_id)
        if cur is None or q.question_version > cur.question_version:
            latest[q.question_id] = q
    n = 0
    for q in rows:
        if latest[q.question_id].row_id != q.row_id:
            q.active = False
            n += 1
    if n:
        db.commit()
    return n


def _refresh_match(paper: E.ExternalPaper, items: list[E.ExternalItem]) -> None:
    """按实际条目的范围判别情况重算匹配度说明。

    迁移时写死的「仅 2/6 题完成判别」在结构化变 8 题后就是错的，
    所以每次处理完都要重算，不能让旧文案留在详情里。
    """
    classes = [(it.scope_class or "unconfirmed") for it in items]
    total = len(classes)
    classified = [c for c in classes if c != "unconfirmed"]
    if total == 0:
        paper.course_match_level = "unassessed"
        paper.match_basis = "还没有结构化题目，暂不给出匹配度"
        return
    if not classified:
        paper.course_match_level = "unassessed"
        paper.match_basis = f"{total} 题均未完成课程范围判别，暂不给出匹配度"
    elif len(classified) < total:
        paper.course_match_level = "unassessed"
        paper.match_basis = (f"仅 {len(classified)}/{total} 题完成课程范围判别，"
                             f"样本不足，暂不给出整体匹配度")
    elif "extension" in classes:
        paper.course_match_level = "challenge"
        paper.match_basis = "已判别题目中含明确超出课件深度的扩展题"
    elif "in_scope_advanced" in classes:
        paper.course_match_level = "partial"
        paper.match_basis = "已判别题目均在课件考点内，部分要求更完整的推导"
    else:
        paper.course_match_level = "high"
        paper.match_basis = "已判别题目全部落在课程范围内且深度与课件一致"


def localize_web_paper(db: Session, paper: E.ExternalPaper, *,
                       force: bool = False) -> dict:
    """网页题集（没有 PDF）：只做结构化 + 翻译，不生成中文 PDF。

    面试开放题没有原卷答案，`answer_payload` 保持空，绝不把 AI 生成的答案
    标成作者官方答案。
    """
    key = f"{MODEL}|web-{TRANSLATE_VERSION}|{paper.source_url or paper.epaper_id}"
    if (not force and paper.localize_version == key
            and paper.zh_status in ("done", "partial")):
        return {"ok": True, "cached": True, "status": paper.zh_status,
                "note": paper.zh_missing_note}

    items = db.execute(select(E.ExternalItem).where(
        E.ExternalItem.paper_id == paper.epaper_id,
        E.ExternalItem.kind == "question")).scalars().all()
    items.sort(key=lambda i: i.item_id)
    if not items:
        raise LocalizeError("这套题集没有可结构化的条目")

    payload = []
    for index, item in enumerate(items, start=1):
        stem = (item.content_excerpt or item.title or "").strip()
        payload.append({"no": f"Q{index}", "stem": stem, "options": []})
    zh_map = _translate(db, payload)

    results, missing = [], []
    for index, item in enumerate(items, start=1):
        no = f"Q{index}"
        stem = (item.content_excerpt or item.title or "").strip()
        translated = (zh_map.get(no) or {}).get("stem_zh") or item.title or ""
        if not translated.strip():
            missing.append(no)
        existing = None
        if item.imported_question_id:
            existing = db.execute(select(E.Question).where(
                E.Question.question_id == item.imported_question_id,
                E.Question.active.is_(True),
            ).order_by(E.Question.question_version.desc())).scalars().first()
        if existing is None:
            existing = db.execute(select(E.Question).where(
                E.Question.external_item_id == item.item_id,
                E.Question.active.is_(True),
            ).order_by(E.Question.question_version.desc())).scalars().first()
        question_id = existing.question_id if existing else E.new_id("q")
        if existing is None:
            row = E.Question(
                row_id=E.new_id("qrow"), question_id=question_id,
                question_version=1, origin="external",
                original_exam_id=paper.epaper_id, original_question_no=no,
                question_type="short_answer", stem=stem, stem_zh=translated,
                options=[], stem_images=[], score=5.0, main_kp_id="",
                aux_kp_ids=[], cognitive_level="理解", difficulty="中等",
                estimated_minutes=6.0,
                source_refs=[{"document_id": "", "rel_path": item.url,
                              "page": 0, "label": f"网页题集 {no}"}],
                question_family_id=question_id, parent_question_id="",
                review_status="pending", answer_status="unknown",
                answer_origin="none", active=True, created_by="web_localize",
                notes="网页题集：题干来自原页短摘录；开放题无官方答案，不自动生成",
                scope_class=item.scope_class or "unconfirmed",
                external_item_id=item.item_id, answer_payload={}, rubric={},
                explanation="", original_language="en",
                translation_status="done",
                translation_model=MODEL, translation_version=TRANSLATE_VERSION,
                translated_at=E.utcnow(), extraction_status="extracted",
                source_page=0, source_quote=stem[:200],
            )
            db.add(row)
            item.imported_question_id = question_id
        else:
            existing.stem = stem
            existing.stem_zh = translated
            existing.original_question_no = no
            # 旧题可能来自别处，必须归到这套题集名下，否则统计只认新建的那几道
            existing.original_exam_id = paper.epaper_id
            existing.external_item_id = item.item_id
            existing.origin = "external"
            existing.original_language = "en"
            existing.translation_status = "done"
            existing.translation_model = MODEL
            existing.translation_version = TRANSLATE_VERSION
            existing.translated_at = E.utcnow()
            # 面试开放题不提供官方答案，清空历史答案，避免被当成作者答案
            existing.answer_payload = {}
            existing.answer_status = "unknown"
            existing.answer_origin = "none"
            existing.source_page = 0
            existing.source_quote = stem[:200]
            existing.extraction_status = "extracted"
            item.imported_question_id = question_id
        results.append({"no": no, "question_id": question_id,
                        "zh": bool(translated.strip())})
    db.commit()

    paper.zh_status = "partial" if missing else "done"
    paper.zh_pdf_rel_path = ""      # 没有原卷 PDF，不虚构中文 PDF
    paper.zh_generated_at = E.utcnow()
    paper.zh_missing_note = (
        "该资源是网页题集，没有 PDF 原卷，因此不生成中文版 PDF；"
        f"题目已结构化并翻译（{len(results)} 题），可直接在线练习。"
        "开放题不提供官方答案，答题后按学情分析规则处理。")
    paper.localize_version = key
    paper.localize_model = MODEL
    paper.localized_at = E.utcnow()
    paper.localize_note = f"网页题集；模型 {MODEL}；结构化 {len(results)} 题；未生成 PDF"
    paper.question_count = len(results)
    paper.duration_minutes = None
    paper.total_score = round(5.0 * len(results), 2)
    paper.online_question_count = len(results)
    paper.online_available = bool(results)
    _refresh_match(paper, items)
    paper.updated_at = E.utcnow()
    db.commit()
    return {"ok": True, "cached": False, "status": paper.zh_status,
            "questions": results, "zh_pdf": "", "zh_pages": 0,
            "needs_review": [], "missing": missing, "note": paper.zh_missing_note}


def localize(db: Session, paper: E.ExternalPaper, *, force: bool = False) -> dict:
    """执行一次完整的提取 + 翻译 + 中文 PDF。幂等：版本相同直接复用。"""
    if not paper.pdf_rel_path:
        # 网页题集：没有 PDF 就不生成中文 PDF，只做结构化 + 翻译。
        return localize_web_paper(db, paper, force=force)
    pdf_path = cfg.resolve_storage_ref(paper.pdf_rel_path)
    if not pdf_path.exists():
        raise LocalizeError("本地 PDF 文件不存在")

    key = _version_key(paper)
    if (not force and paper.localize_version == key
            and paper.zh_status in ("done", "partial")
            and paper.zh_pdf_rel_path):
        return {"ok": True, "cached": True, "status": paper.zh_status,
                "pdf": paper.zh_pdf_rel_path, "note": paper.zh_missing_note}

    pages = read_pages(pdf_path)
    if not pages:
        raise LocalizeError("PDF 没有可读取的页面")
    _stage(db, paper, "parsing")

    # 文本层不足的页面用视觉模型补
    ocr_pages = []
    with tempfile.TemporaryDirectory(prefix="mlq_ocr_") as tmp:
        for page in pages:
            if page["chars"] >= PAGE_TEXT_MIN:
                continue
            image = render_page_png(pdf_path, page["page"], Path(tmp))
            text = _ocr_page(db, image, page["page"])
            page["text"] = (page["text"] + "\n" + text).strip()
            ocr_pages.append(page["page"])

    data = _extract(db, pages)
    extracted = data.get("questions") or []
    if not extracted:
        raise LocalizeError("未能从这份 PDF 中提取到题目")
    _stage(db, paper, "translating")

    language = str(data.get("language") or "").strip().lower() or "en"
    for item in extracted:
        item["_language"] = language

    zh_map = _translate(db, extracted) if language != "zh" else {}
    if language == "zh":
        for item in extracted:
            zh_map[str(item.get("no"))] = {"stem_zh": item.get("stem"),
                                           "options": [
                                               {"key": o.get("key"),
                                                "text_zh": o.get("text")}
                                               for o in item.get("options") or []
                                               if isinstance(o, dict)]}

    # 公式必须带 $ 定界符前端才会渲染；模型偶发输出 Unicode 上标，这里兜底。
    formula_fixed = _normalize_formulas(db, extracted, zh_map)
    _stage(db, paper, "generating")

    results, missing = [], []
    for item in extracted:
        no = str(item.get("no") or "")
        try:
            results.append(_upsert(db, paper, item, zh_map.get(no), pages=pages))
        except Exception as e:                      # noqa: BLE001
            missing.append(f"{no or '?'}：写入失败 {type(e).__name__}")
    db.commit()

    rows = []
    for r in results:
        # 必须锁定到本次写入的版本：同一 question_id 的旧版本可能没有译文，
        # 只按 question_id 取 first() 会把英文旧版混进中文 PDF。
        q = db.execute(select(E.Question).where(
            E.Question.question_id == r["question_id"],
            E.Question.question_version == r["version"],
        )).scalars().first()
        if q is not None:
            rows.append(q)
    rows.sort(key=lambda q: _parse_position(q.original_question_no or ""))

    out_dir = cfg.PROJECT_ROOT / "data" / "external_pdfs" / "zh"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{paper.epaper_id}_{key.split('|')[-1][:8]}.pdf"

    needs_review = [r["no"] for r in results if r["issues"]]
    model_review = [r["no"] for r in results
                    if r.get("needs_review") and not r["issues"]]
    duration = None
    try:
        if data.get("duration_minutes"):
            duration = int(float(data["duration_minutes"]))
            if not (5 <= duration <= 600):
                duration = None
    except (TypeError, ValueError):
        duration = None
    info = _build_zh_pdf(
        paper, rows, out_path,
        instructions=str(data.get("instructions_zh") or ""),
        duration=duration,
        partial=bool(needs_review or missing or model_review),
        missing_note=("待核对题号：" + "、".join(needs_review + model_review))
        if (needs_review or model_review) else "",
    )
    if duration and not paper.duration_minutes:
        paper.duration_minutes = duration

    note_bits = [f"已结构化 {len(results)} 题并生成中文版"]
    if formula_fixed:
        note_bits.append(f"其中 {formula_fixed} 题补做了 LaTeX 公式规范化")
    if ocr_pages:
        note_bits.append(f"第 {'、'.join(map(str, ocr_pages))} 页使用图像识别")
    if needs_review:
        note_bits.append(f"校验未通过、待人工核对：{'、'.join(needs_review)}")
    if model_review:
        note_bits.append(f"模型标记建议复核：{'、'.join(model_review)}")
    if missing:
        note_bits.append("写入失败：" + "；".join(missing))
    note_bits.append("未识别的题目不会出现在中文版中，需人工补充")

    paper.zh_status = ("partial" if (needs_review or missing)
                       else "review" if model_review else "done")
    paper.zh_pdf_rel_path = cfg.storage_ref(out_path)
    paper.zh_generated_at = E.utcnow()
    paper.zh_missing_note = "；".join(note_bits)
    paper.localize_version = key
    paper.localize_model = MODEL
    paper.localized_at = E.utcnow()
    paper.localize_note = (f"模型 {MODEL}；原文 {len(pages)} 页；"
                           f"结构化 {len(results)} 题；中文 PDF {info['pages']} 页")
    # 迁移期写的「第 3 题未收录」在补全后就是错的，按本次结果改写。
    nos = "、".join(sorted(r["no"] for r in results if r.get("no")))
    paper.origin_note = re.sub(
        r"已收录第[^。]*。",
        f"已结构化 {len(results)} 个小题（题号 {nos}）。",
        paper.origin_note or "")
    # 题量/分值以本次结构化结果为准：迁移时的 6 题是人工登记的旧值，
    # 不更新会出现「已收录 6 题却作答 8 题」的矛盾。
    paper.question_count = len(results)
    paper.total_score = round(sum(float(r["score"]) for r in results), 2)
    online = db.execute(select(E.ExternalItem).where(
        E.ExternalItem.paper_id == paper.epaper_id,
        E.ExternalItem.imported_question_id != "")).scalars().all()
    paper.online_question_count = len(online)
    paper.online_available = bool(online)
    _refresh_match(paper, db.execute(select(E.ExternalItem).where(
        E.ExternalItem.paper_id == paper.epaper_id)).scalars().all())
    paper.updated_at = E.utcnow()
    db.commit()

    superseded = _deactivate_superseded(db)

    return {"ok": True, "cached": False, "status": paper.zh_status,
            "questions": results, "zh_pdf": paper.zh_pdf_rel_path,
            "zh_pages": info["pages"], "ocr_pages": ocr_pages,
            "needs_review": needs_review, "missing": missing,
            "note": paper.zh_missing_note}


def mark_failed(db: Session, paper: E.ExternalPaper, message: str) -> None:
    """失败也要落到状态里，不能让详情页一直停在「正在翻译」。"""
    paper.zh_status = "failed"
    paper.zh_missing_note = f"处理失败：{message}"[:500]
    paper.updated_at = E.utcnow()
    db.commit()


def _stage(db: Session, paper: E.ExternalPaper, value: str) -> None:
    """把处理阶段写进 zh_status，后台任务与详情页都能看到当前进度。"""
    paper.zh_status = value
    paper.updated_at = E.utcnow()
    db.commit()


def status(db: Session, paper: E.ExternalPaper) -> dict:
    """处理状态 + 实际计数。计数实时查库，不用写死的数字糊弄。"""
    rows = db.execute(select(E.Question).where(
        E.Question.original_exam_id == paper.epaper_id,
        E.Question.active.is_(True))).scalars().all()
    translated = sum(1 for q in rows if (q.stem_zh or "").strip())
    formula = sum(1 for q in rows if "$" in (q.stem or ""))
    pending_review = sum(1 for q in rows if q.review_status == "pending")
    value = paper.zh_status or "pending"
    # 中文版首页预览：不是每一份都有（只有走过统一流水线的才有）。
    # 存在就给出受控 URL，不存在就不给字段，前端自然退回原版预览。
    run = db.execute(select(E.ImportRun).where(
        E.ImportRun.source_kind == "external_paper",
        E.ImportRun.source_id == paper.epaper_id).order_by(
        E.ImportRun.created_at.desc())).scalars().first()
    zh_thumb = ((run.result or {}).get("zh_thumbnail") if run else "") or ""
    viewable = bool(paper.zh_pdf_rel_path
                    and (paper.preview_allowed or paper.allows_copy))
    labels = {
        "pending": "待处理", "parsing": "正在解析 PDF",
        "extracting": "正在提取题目", "translating": "正在翻译",
        "generating": "正在生成中文 PDF", "review": "待审核",
        "done": "已就绪", "partial": "部分完成", "failed": "处理失败",
        "running": "处理中",
    }
    return {
        "status": value,
        "label": labels.get(value, "待处理"),
        "pdf_available": bool(paper.zh_pdf_rel_path),
        "pdf_url": (f"/api/external/papers/{paper.epaper_id}/zh.pdf"
                    if paper.zh_pdf_rel_path else ""),
        "generated_at": paper.zh_generated_at,
        "note": paper.zh_missing_note,
        "thumb_available": bool(zh_thumb and viewable),
        "thumbnail_url": (f"/api/external/papers/{paper.epaper_id}/zh-thumbnail"
                          if (zh_thumb and viewable) else ""),
        "model": paper.localize_model,
        "version": paper.localize_version,
        "downloadable": bool(paper.zh_pdf_rel_path and paper.allows_copy),
        "viewable": bool(paper.zh_pdf_rel_path
                         and (paper.preview_allowed or paper.allows_copy)),
        "download_blocked_reason": ("" if paper.allows_copy else
                                    "未确认改编/再分发授权，仅提供在线查看"),
        "counts": {
            "questions": len(rows),
            "translated": translated,
            "formula_latex": formula,
            "online": int(paper.online_question_count or 0),
            "pending_review": pending_review,
            "pdf": bool(paper.zh_pdf_rel_path),
        },
        "failed_reason": (paper.zh_missing_note
                          if value == "failed" else ""),
    }
