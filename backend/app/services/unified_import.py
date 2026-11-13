"""V3.5 统一资料导入流水线。

背景（只读审计结论）
--------------------
升级前项目里其实有**两套**导入链路，能力互补但互不相通：

  初始化导入 external_papers → paper_localization.localize()
      会：解析 PDF → 低文本页 OCR → 模型抽题 → 逐题翻译 → 公式规范化
          → 写 Question(origin=external) → 生成中文 PDF
      不会：知识点匹配（scope_class 停在 unconfirmed）、给无答案题补答案

  手动上传 material_uploads → material_service.process_upload()
      会：保存原件 → 解析 → 题型识别 → 规则抽题 → （可选）DeepSeek 生成答案
      不会：全文翻译、中文版 PDF、中文首页缩略图、课程范围判别

后果正是任务书点名的现象：初始化能翻译、手动上传不能；初始化能出中文版、
日常上传只留 PDF。本模块把两条链路收口到同一个阶段机，两边都跑完整阶段。

设计要点
--------
* 阶段词表统一（STAGE_ORDER），两个入口共用；每份资料一条 ImportRun 记录。
* 阶段可跳过已完成项（断点续跑），按 file_sha256 + 阶段名去重，重复执行不重复调模型。
* 题目写入只有一个入口 `_store_questions`，两种来源走同一套字段映射，
  因此不会再出现"两个入口生成的数据结构不同"。
* 授权边界不动：只有 `preview_allowed` / `allows_copy` 才对外提供缩略图与下载，
  翻译版也遵循同一判定。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pymupdf
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.models import entities as E
from backend.app.services import (answer_pipeline, booklet_pdf, latex_plain,
                                  model_provider, paper_localization,
                                  pdf_thumbnail)

# ---------------------------------------------------------------- 阶段词表
STAGE_ORDER = [
    "queued",
    "parse",                 # 文件解析 / 页面文本
    "detect_language",       # 依据正文判断语言，不看文件名
    "extract_questions",     # 结构化抽题
    "translate",             # 逐题 + 全文中文
    "generate_answers",      # 无答案题补 AI 答案与解析
    "match_knowledge",       # 课程知识点 + 范围判别
    "quality_gate",          # 结构与自洽校验
    "build_zh_pdf",          # 完整中文版 PDF
    "render_zh_thumbnail",   # 中文版首页缩略图
    "import_bank",           # 课内题进正式题库
    "finalize",
]

STAGE_LABELS = {
    "queued": "待处理", "parse": "文件解析中", "detect_language": "语言判定",
    "extract_questions": "正在提取题目", "translate": "正在翻译",
    "generate_answers": "正在生成答案", "match_knowledge": "正在匹配知识点",
    "quality_gate": "正在质量校验", "build_zh_pdf": "正在生成中文 PDF",
    "render_zh_thumbnail": "正在生成首页预览",
    "import_bank": "正在入库", "finalize": "已完成",
}

IMPORT_ROOT = cfg.DATA_DIR / "import"
ZH_DIR = IMPORT_ROOT / "zh"
THUMB_DIR = IMPORT_ROOT / "thumbs"
PROMPT_VERSION = "unified-import-1.0"
MODEL = ""  # 运行时取 cfg

SYS_TRANSLATE_DOC = """你是专业译者，正在把一份英文机器学习课程资料全文译成中文。

要求：
1. 逐段翻译，保留原有标题层级、章节、题号、编号、列表结构。
2. 数字、变量、数学符号、公式一律保持原样；公式用 LaTeX（行内 \\( ... \\)，独立 \\[ ... \\]）。
3. 专业术语按中国大学机器学习教材习惯翻译，全文保持一致：
   regularization=正则化, margin=间隔, kernel trick=核技巧, hinge loss=合页损失,
   gradient descent=梯度下降, backpropagation=反向传播, cross-validation=交叉验证,
   overfitting=过拟合, generalization=泛化, perceptron=感知器, ensemble=集成学习。
4. 保留考试说明、评分要求、题目条件与限制，不得省略。
5. 不要解题、不要补写原文没有的要求、不要改变难度。
6. 只输出译文，不要加任何解释或前后缀。

输出 JSON：{"pages":[{"page":1,"text_zh":"..."}]}"""

SYS_SOLVE = """你在为大学《机器学习》课程的试卷题目写标准答案与解析。

规则：
1. 以题干与选项为准。条件缺失或歧义时把问题写进 uncertain，不要猜。
2. 公式用 LaTeX：行内 \\( ... \\)，独立 \\[ ... \\]。
3. 单选：给出正确项，并说明其它项为什么错。多选：给全部正确项并逐项说明。
4. 计算/推导：给已知条件、方法、步骤、结果，并做符号与量纲检查。
5. 参数量计算遵循课堂口径：总参数量 = 权重参数数 + 偏置参数数。
6. 如果原卷已给答案，你的任务是解释它为什么成立；若你的推导与之矛盾，
   在 conflict 里如实写出，不要为了迎合原卷倒推错误解释。

只输出 JSON：
{"answer":{"kind":"keys|value|text","keys":[],"value":null,"text":""},
 "explanation":"详细解析","key_points":["评分要点"],
 "option_analysis":[{"key":"A","verdict":"正确|错误","reason":"..."}],
 "confidence":"high|medium|low","uncertain":[],"conflict":""}"""

SYS_KP = """你在把题目匹配到课程知识点，并判断是否在老师授课范围内。

给定：课程知识点清单（kp_id :: 章节 :: 名称）、若干题目。
对每道题给出：
  kp_id：最贴近的知识点 id；不确定写 "none"。
  scope：in_scope（PPT 讲过的考点）/ in_scope_advanced（考点在范围内但推导更深）
         / extension（PPT 没讲过的内容）/ unconfirmed（信息不足无法判断）
  reason：一句话依据。
宁可判 extension 也不要强行归入课内；不要因为题目属于机器学习就默认它在范围内。

只输出 JSON：{"items":[{"no":"题号","kp_id":"...","scope":"...","reason":"..."}]}"""


# ---------------------------------------------------------------- 小工具
def _cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    total = cjk + latin
    return (cjk / total) if total else 0.0


def detect_language(text: str) -> str:
    """按正文判断语言：en / zh / mixed / other。不看文件名。

    用两个独立信号（汉字数、英文单词数）而不是单一比例：一份中文试卷里
    出现 "SVM"、"K-means" 这类英文术语很常见，比例法会把它误判成混合语料。
    """
    t = text or ""
    cjk = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
    latin_words = len(re.findall(r"[A-Za-z]{3,}", t))
    if cjk >= 30 and latin_words >= 10:
        return "mixed"
    if cjk >= 20:
        return "zh"
    if latin_words >= 8:
        return "en"
    if cjk > 0:
        return "mixed"
    return "other"


def _stage(run: E.ImportRun, name: str, status: str, **detail) -> None:
    run.stage = name
    steps = list(run.steps or [])
    steps.append({"stage": name, "label": STAGE_LABELS.get(name, name),
                  "status": status, "at": E.utcnow(), **detail})
    run.steps = steps
    run.updated_at = E.utcnow()


def _done(run: E.ImportRun, name: str) -> dict | None:
    """断点续跑：本阶段已经成功过就返回当时的明细。"""
    for step in reversed(list(run.steps or [])):
        if step.get("stage") == name and step.get("status") == "ok":
            return step
    return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- 运行记录
def create_run(db: Session, *, source_kind: str, source_id: str,
               file_rel_path: str = "", user_id: str = "local_user",
               job_id: str = "", is_test_fixture: bool = False) -> E.ImportRun:
    existing = db.execute(select(E.ImportRun).where(
        E.ImportRun.source_kind == source_kind,
        E.ImportRun.source_id == source_id,
        E.ImportRun.status.in_(("queued", "running")))).scalars().first()
    if existing is not None:
        return existing
    run = E.ImportRun(
        run_id=E.new_id("imp"), source_kind=source_kind, source_id=source_id,
        file_rel_path=file_rel_path, status="queued", stage="queued",
        steps=[], result={}, job_id=job_id, is_test_fixture=is_test_fixture)
    db.add(run)
    db.commit()
    return run


def list_runs(db: Session, limit: int = 50) -> dict:
    rows = db.execute(select(E.ImportRun).order_by(
        E.ImportRun.created_at.desc()).limit(limit)).scalars().all()
    return {"total": len(rows), "items": [
        {"run_id": r.run_id, "source_kind": r.source_kind,
         "source_id": r.source_id, "status": r.status, "stage": r.stage,
         "stage_label": STAGE_LABELS.get(r.stage, r.stage),
         "language": r.language, "steps": len(r.steps or []),
         "result": r.result or {}, "error": r.error,
         "updated_at": r.updated_at} for r in rows],
        "stage_labels": STAGE_LABELS}


# ---------------------------------------------------------------- 适配器
def _describe(db: Session, run: E.ImportRun) -> dict:
    """把两种来源描述成同一份 {pdf_path, title, ...}。"""
    if run.source_kind == "external_paper":
        paper = db.get(E.ExternalPaper, run.source_id)
        if paper is None:
            raise RuntimeError("外部试卷不存在")
        pdf = (cfg.resolve_storage_ref(paper.pdf_rel_path)
               if paper.pdf_rel_path else None)
        return {"kind": "pdf", "path": pdf, "title": paper.title or "",
                "paper": paper, "institution": paper.institution or "",
                "year": paper.year or "", "source_url": paper.source_url or "",
                "entity": paper}
    if run.source_kind == "material_upload":
        up = db.get(E.MaterialUpload, run.source_id)
        if up is None:
            raise RuntimeError("上传资料不存在")
        src = cfg.resolve_storage_ref(up.stored_rel_path)
        is_pdf = src.suffix.lower() == ".pdf"
        return {"kind": "pdf" if is_pdf else "document", "path": src,
                "title": up.filename or "", "upload": up,
                "institution": "", "year": "", "source_url": "",
                "entity": up}
    raise RuntimeError(f"未知来源类型: {run.source_kind}")


def _pages(ctx: dict) -> list[dict]:
    return ctx.get("pages") or []


# ---------------------------------------------------------------- 各阶段
def _stage_parse(db: Session, run: E.ImportRun, ctx: dict) -> None:
    src = ctx["src"]
    path: Path | None = src["path"]
    if path is None or not path.exists():
        raise RuntimeError("原始文件不存在或未登记路径")
    ctx["file_sha256"] = _sha256(path)
    run.file_sha256 = ctx["file_sha256"]

    if src["kind"] == "pdf":
        pages = paper_localization.read_pages(path)
        if not pages:
            raise RuntimeError("PDF 没有可读取的页面")
        # 文本层不足的页用视觉模型补（扫描件/图表页）
        ocr_pages: list[int] = []
        with tempfile.TemporaryDirectory(prefix="mlq_unified_") as tmp:
            for page in pages:
                if page["chars"] >= paper_localization.PAGE_TEXT_MIN:
                    continue
                img = paper_localization.render_page_png(path, page["page"], Path(tmp))
                page["text"] = (page["text"] + "\n"
                                + paper_localization._ocr_page(db, img, page["page"])).strip()
                ocr_pages.append(page["page"])
        ctx["pages"] = pages
        ctx["ocr_pages"] = ocr_pages
        # 保留原 PDF 里的图表：抽出来按页插回中文版，并供题目关联使用
        img_dir = IMPORT_ROOT / "pages" / run.run_id
        try:
            ctx["page_images"] = paper_localization.extract_page_images(
                path, img_dir)
        except Exception:                              # noqa: BLE001
            ctx["page_images"] = {}
        _stage(run, "parse", "ok", pages=len(pages), ocr_pages=ocr_pages,
               figures=sum(len(v) for v in ctx["page_images"].values()))
        return

    # 非 PDF（docx / pptx / 图片等）：复用已有解析结果拼成"页"
    up: E.MaterialUpload = src["upload"]
    units = _document_units(db, up)
    if not units:
        raise RuntimeError("文档没有可读取的正文")
    ctx["pages"] = [{"page": i + 1, "text": u, "chars": len(u)}
                    for i, u in enumerate(units)]
    ctx["ocr_pages"] = []
    _stage(run, "parse", "ok", pages=len(units), kind=src["kind"])


def _document_units(db: Session, up: E.MaterialUpload) -> list[str]:
    """从已解析的 document chunks 取正文；没有就现场解析一次。"""
    from backend.app.services import material_service
    units: list[str] = []
    if up.document_id:
        try:
            parsed = material_service._read_units(up.document_id)
            for unit in parsed.get("units") or []:
                text = (unit.get("text") or "").strip()
                if text:
                    units.append(text)
        except Exception:                              # noqa: BLE001
            units = []
    if units:
        return units
    # 还没解析过：用与手动上传流水线相同的 rec 结构解析一次并落库
    if up.stored_rel_path:
        src = cfg.resolve_storage_ref(up.stored_rel_path)
        if src.exists():
            from backend.app.ingest import parsers
            rec = material_service._manifest_rec(up)
            pr = parsers.parse_one(db, rec, render_limit=0)
            up.document_id = pr.get("document_id") or up.document_id
            up.document_version_id = (pr.get("document_version_id")
                                      or up.document_version_id)
            up.parse_status = pr.get("parse_status") or up.parse_status
            db.commit()
            try:
                parsed = material_service._read_units(up.document_id)
                for unit in parsed.get("units") or []:
                    text = (unit.get("text") or "").strip()
                    if text:
                        units.append(text)
            except Exception:                          # noqa: BLE001
                units = []
    return units


def _stage_language(db: Session, run: E.ImportRun, ctx: dict) -> None:
    text = "\n".join(p.get("text") or "" for p in _pages(ctx))
    lang = detect_language(text)
    ctx["language"] = lang
    run.language = lang
    _stage(run, "detect_language", "ok", language=lang,
           cjk_ratio=round(_cjk_ratio(text), 3), chars=len(text))


def _stage_extract(db: Session, run: E.ImportRun, ctx: dict) -> None:
    data = paper_localization._extract(db, _pages(ctx))
    items = data.get("questions") or []
    if not items:
        raise RuntimeError("未能从这份资料中提取到题目")
    ctx["items"] = items
    ctx["doc_meta"] = data
    # 若模型给了语言判定，与本地检测交叉验证
    model_lang = str(data.get("language") or "").strip().lower()
    if model_lang and not run.language:
        run.language = model_lang
        ctx["language"] = model_lang
    _stage(run, "extract_questions", "ok", questions=len(items),
           model_language=model_lang)


def _stage_translate(db: Session, run: E.ImportRun, ctx: dict) -> None:
    lang = ctx.get("language") or "en"
    items = ctx["items"]
    if lang == "zh":
        zh_map = {str(it.get("no")): {
            "stem_zh": it.get("stem"),
            "options": [{"key": o.get("key"), "text_zh": o.get("text")}
                        for o in it.get("options") or [] if isinstance(o, dict)],
        } for it in items}
        ctx["zh_map"] = zh_map
        ctx["pages_zh"] = [{"page": p["page"], "text_zh": p.get("text") or ""}
                           for p in _pages(ctx)]
        _stage(run, "translate", "ok", skipped=True,
               reason="原文已是中文，无需翻译")
        return

    # 1) 逐题翻译（复用已有、已验证的批量翻译）
    zh_map = paper_localization._translate(db, items)
    ctx["zh_map"] = zh_map
    fixed = paper_localization._normalize_formulas(db, items, zh_map)
    ctx["formula_fixed"] = fixed

    # 2) 全文翻译（这是"翻译整份资料"而不是只翻译题目）
    pages_zh, page_err = _translate_pages(db, _pages(ctx))
    ctx["pages_zh"] = pages_zh
    _stage(run, "translate", "ok", questions=len(zh_map),
           pages=len(pages_zh), formula_fixed=fixed,
           page_error=page_err or "")


def _translate_pages(db: Session, pages: list[dict],
                     chunk_chars: int = 5000) -> tuple[list[dict], str]:
    """整份资料的中文翻译，按页返回。长文按字符预算分批，避免输出被截断。"""
    out: list[dict] = []
    batch: list[dict] = []
    size = 0

    def flush() -> tuple[bool, str]:
        nonlocal batch, size
        if not batch:
            return True, ""
        payload = [{"page": p["page"], "text": p.get("text") or ""} for p in batch]
        try:
            res = model_provider.chat(
                db, messages=[{"role": "system", "content": SYS_TRANSLATE_DOC},
                              {"role": "user",
                               "content": json.dumps(payload, ensure_ascii=False)}],
                skill_name="unified_translate_doc", prompt_version=PROMPT_VERSION,
                temperature=0, max_tokens=8000, timeout=300)
            data = model_provider.parse_json_content(res.get("content") or "")
            rows = (data or {}).get("pages") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise ValueError("翻译结果不是合法 JSON")
            got = {int(r.get("page") or 0): str(r.get("text_zh") or "")
                   for r in rows if isinstance(r, dict)}
            for p in batch:
                out.append({"page": p["page"],
                            "text_zh": got.get(p["page"], "")})
            batch, size = [], 0
            return True, ""
        except Exception as exc:                        # noqa: BLE001
            for p in batch:
                out.append({"page": p["page"], "text_zh": ""})
            batch, size = [], 0
            return False, f"{type(exc).__name__}: {str(exc)[:160]}"

    error = ""
    for p in pages:
        text = p.get("text") or ""
        if size + len(text) > chunk_chars and batch:
            ok, err = flush()
            error = error or ("" if ok else err)
        batch.append(p)
        size += len(text)
    ok, err = flush()
    error = error or ("" if ok else err)
    return out, error


def _stage_answers(db: Session, run: E.ImportRun, ctx: dict) -> None:
    items = ctx["items"]
    need = [it for it in items if not str(it.get("answer") or "").strip()]
    solved = 0
    failed = 0
    for it in need:
        try:
            prompt = _solve_prompt(it, ctx.get("zh_map") or {})
            res = model_provider.chat(
                db, messages=[{"role": "system", "content": SYS_SOLVE},
                              {"role": "user", "content": prompt}],
                skill_name="unified_solve", prompt_version=PROMPT_VERSION,
                temperature=0.1, max_tokens=2200, timeout=200)
            parsed = model_provider.parse_json_content(res.get("content") or "")
            if not isinstance(parsed, dict):
                raise ValueError("答案 JSON 解析失败")
            it["_ai_answer"] = parsed
            solved += 1
        except Exception as exc:                        # noqa: BLE001
            it["_ai_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            failed += 1
    _stage(run, "generate_answers", "ok" if failed == 0 else "partial",
           requested=len(need), solved=solved, failed=failed)


def _solve_prompt(item: dict, zh_map: dict) -> str:
    zh = zh_map.get(str(item.get("no"))) or {}
    opts = "\n".join(
        f"{o.get('key')}. {o.get('text')}"
        + (f"（中文：{o.get('text_zh')}）" if o.get("text_zh") else "")
        for o in item.get("options") or [] if isinstance(o, dict))
    parts = [f"【题号】{item.get('no')}",
             f"【题型】{item.get('type')}",
             f"【题干】\n{item.get('stem')}"]
    if zh.get("stem_zh"):
        parts.append(f"【题干中文】\n{zh['stem_zh']}")
    if opts:
        parts.append(f"【选项】\n{opts}")
    if item.get("score") is not None:
        parts.append(f"【分值】{item.get('score')}")
    return "\n\n".join(parts)


def _stage_match(db: Session, run: E.ImportRun, ctx: dict) -> None:
    items = ctx["items"]
    kps = db.execute(select(E.KnowledgePoint)).scalars().all()
    catalog = "\n".join(f"- {k.kp_id} :: {k.chapter} :: {k.name}" for k in kps)
    mapping: dict[str, dict] = {}
    for i in range(0, len(items), 12):
        chunk = items[i:i + 12]
        payload = [{"no": it.get("no"), "stem": (it.get("stem") or "")[:600],
                    "stem_zh": (ctx.get("zh_map", {}).get(str(it.get("no")))
                                or {}).get("stem_zh", "")[:600]}
                   for it in chunk]
        try:
            res = model_provider.chat(
                db, messages=[
                    {"role": "system", "content": SYS_KP},
                    {"role": "user", "content":
                     f"课程知识点清单：\n{catalog}\n\n题目：\n"
                     + json.dumps(payload, ensure_ascii=False)}],
                skill_name="unified_kp", prompt_version=PROMPT_VERSION,
                temperature=0, max_tokens=3000, timeout=240)
            data = model_provider.parse_json_content(res.get("content") or "")
            rows = (data or {}).get("items") if isinstance(data, dict) else None
            for row in rows or []:
                if isinstance(row, dict) and row.get("no"):
                    mapping[str(row["no"])] = row
        except Exception:                              # noqa: BLE001
            continue
    valid = {k.kp_id for k in kps}
    for it in items:
        row = mapping.get(str(it.get("no"))) or {}
        kp = str(row.get("kp_id") or "none")
        it["_kp_id"] = kp if kp in valid else ""
        scope = str(row.get("scope") or "unconfirmed")
        it["_scope"] = scope if scope in ("in_scope", "in_scope_advanced",
                                          "extension") else "unconfirmed"
        it["_kp_reason"] = str(row.get("reason") or "")
    matched = sum(1 for it in items if it["_kp_id"])
    _stage(run, "match_knowledge", "ok", matched=matched, total=len(items),
           in_scope=sum(1 for it in items if it["_scope"] in
                        ("in_scope", "in_scope_advanced")),
           extension=sum(1 for it in items if it["_scope"] == "extension"),
           unconfirmed=sum(1 for it in items if it["_scope"] == "unconfirmed"))


def _stage_gate(db: Session, run: E.ImportRun, ctx: dict) -> None:
    """结构自洽 + 答案来源标注。不合格的题只标记，不丢弃。"""
    passed = 0
    problems_total: list[dict] = []
    for it in ctx["items"]:
        problems: list[str] = []
        qtype = paper_localization.TYPE_MAP.get(
            str(it.get("type") or "").lower(), "unknown")
        opts = [o for o in it.get("options") or [] if isinstance(o, dict)]
        keys = {str(o.get("key") or "").upper() for o in opts}
        ai = it.get("_ai_answer") or {}
        official = str(it.get("answer") or "").strip()
        payload = (paper_localization._answer_payload(qtype, official)
                   if official else _payload_from_ai(qtype, ai))
        if qtype in ("single_choice", "multi_choice"):
            if len(keys) < 2:
                problems.append("选项不足 2 个")
            elif payload.get("key") and str(payload["key"]).upper() not in keys:
                problems.append(f"答案键 {payload['key']} 不在选项内")
            elif payload.get("keys") and not set(
                    str(k).upper() for k in payload["keys"]) <= keys:
                problems.append("答案键不在选项内")
        if not payload:
            problems.append("无可用答案")
        zh = (ctx.get("zh_map", {}).get(str(it.get("no"))) or {}).get("stem_zh")
        if (ctx.get("language") or "en") != "zh" and not zh:
            problems.append("缺少中文译文")
        it["_payload"] = payload
        it["_answer_origin"] = ("source" if official and payload
                                else "deepseek_generated" if payload else "none")
        it["_problems"] = problems
        if problems:
            problems_total.append({"no": it.get("no"), "problems": problems})
        else:
            passed += 1
    ctx["gate_problems"] = problems_total
    _stage(run, "quality_gate", "ok", passed=passed,
           failed=len(problems_total), detail=problems_total[:20])


def _payload_from_ai(qtype: str, ai: dict) -> dict:
    ans = (ai or {}).get("answer") or {}
    if qtype == "single_choice":
        keys = ans.get("keys") or ([ans["key"]] if ans.get("key") else [])
        return {"key": str(keys[0]).upper()} if keys else {}
    if qtype == "multi_choice":
        keys = [str(k).upper() for k in (ans.get("keys") or [])]
        return {"keys": keys, "partial_rule": "proportional_no_wrong"} if keys else {}
    if qtype == "true_false":
        v = ans.get("value")
        return {"value": v} if isinstance(v, bool) else {}
    text = ans.get("text") or (ai or {}).get("explanation") or ""
    return {"reference_answer": text} if text else {}


def _stage_zh_pdf(db: Session, run: E.ImportRun, ctx: dict) -> None:
    if (ctx.get("language") or "en") == "zh":
        _stage(run, "build_zh_pdf", "ok", skipped=True,
               reason="原文即为中文，不重复生成中文版")
        return
    ZH_DIR.mkdir(parents=True, exist_ok=True)
    out = ZH_DIR / f"{run.run_id}.pdf"
    info = _render_bilingual_pdf(run, ctx, out)
    ctx["zh_pdf_path"] = out
    run.result = {**(run.result or {}), "zh_pdf": cfg.storage_ref(out),
                  "zh_pdf_pages": info["pages"]}
    _stage(run, "build_zh_pdf", "ok", pages=info["pages"],
           bytes=info["pdf_bytes"], path=cfg.storage_ref(out))


def _render_bilingual_pdf(run: E.ImportRun, ctx: dict, out: Path) -> dict:
    """中文版 PDF：整份资料的译文 + 结构化题目清单（题目带中文选项）。"""
    src = ctx["src"]
    w = booklet_pdf.Writer(booklet_pdf.font_path())
    w.para("中文版资料", size=18, gap=2)
    w.para(src.get("title") or "资料", size=13, gap=2)
    meta = []
    if src.get("institution"):
        meta.append(f"原始机构：{src['institution']}")
    if src.get("year"):
        meta.append(f"年份：{src['year']}")
    if src.get("source_url"):
        meta.append(f"原始来源：{src['source_url']}")
    meta.append("说明：本文件由平台按原文自动翻译生成，供中文学习使用；"
                "如与原文表述有出入，以原文为准。")
    for line in meta:
        w.para(latex_plain.to_plain(line), size=9.5,
               color=(0.35, 0.35, 0.35), gap=1)
    w.rule(gap=8)

    # 一、整份资料译文
    pages_zh = ctx.get("pages_zh") or []
    has_text = any((p.get("text_zh") or "").strip() for p in pages_zh)
    if has_text:
        w.para("一、资料全文（中文）", size=12, color=(0.16, 0.32, 0.55), gap=4)
        page_images = ctx.get("page_images") or {}
        for p in pages_zh:
            body = (p.get("text_zh") or "").strip()
            if not body:
                continue
            w.keep_together(34)
            w.para(f"—— 原文第 {p.get('page')} 页 ——", size=8.5,
                   color=(0.55, 0.55, 0.55), gap=1)
            for para in re.split(r"\n{2,}", body):
                w.para(latex_plain.to_plain(para.strip()), size=10,
                       indent=10, gap=2)
            # 原页的图表按顺序插回，避免中文版丢图
            for idx, img in enumerate(page_images.get(int(p.get("page") or 0), [])):
                w.image(img, caption=f"（原文第 {p.get('page')} 页配图 {idx + 1}）")
            w.y += 4

    # 二、结构化题目
    items = ctx["items"]
    w.para("二、结构化题目（中文）", size=12, color=(0.16, 0.32, 0.55), gap=4)
    for idx, it in enumerate(items, start=1):
        no = str(it.get("no") or idx)
        qtype = paper_localization.TYPE_MAP.get(
            str(it.get("type") or "").lower(), "unknown")
        label = {"single_choice": "单选题", "multi_choice": "多选题",
                 "true_false": "判断题", "term": "名词解释",
                 "short_answer": "简答题", "numeric": "数值题",
                 "derivation": "推导题"}.get(qtype, "题目")
        zh = ctx.get("zh_map", {}).get(str(it.get("no"))) or {}
        head = f"{no}　{label}　{float(it.get('score') or 0):g} 分"
        w.keep_together(40)
        w.para(head, size=11, color=(0.16, 0.32, 0.55), gap=3)
        stem = zh.get("stem_zh") or it.get("stem") or ""
        w.para(latex_plain.to_plain(str(stem)), size=10.5, indent=10, gap=2)
        zh_opts = {str(o.get("key")): str(o.get("text_zh") or "")
                   for o in zh.get("options") or [] if isinstance(o, dict)}
        for opt in it.get("options") or []:
            if not isinstance(opt, dict):
                continue
            text = zh_opts.get(str(opt.get("key"))) or opt.get("text") or ""
            w.para(f"{opt.get('key', '')}. {latex_plain.to_plain(str(text))}",
                   size=10, indent=22, gap=1)
        w.y += 6
    if not (has_text or items):
        w.para("本资料未生成可读译文。", size=10, color=(0.6, 0.25, 0.1))
    size = w.save(out)
    return {"pdf_bytes": size, "pages": w.page_no}


def _stage_thumb(db: Session, run: E.ImportRun, ctx: dict) -> None:
    path: Path | None = ctx.get("zh_pdf_path")
    if path is None or not path.exists():
        _stage(run, "render_zh_thumbnail", "skipped",
               reason="没有中文版 PDF，用原资料预览图")
        return
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    out = THUMB_DIR / f"{run.run_id}.png"
    info = pdf_thumbnail._render_first_page(path, out)
    ctx["zh_thumb_path"] = out
    run.result = {**(run.result or {}), "zh_thumbnail": cfg.storage_ref(out)}
    _stage(run, "render_zh_thumbnail", "ok", bytes=info.get("bytes", 0),
           path=cfg.storage_ref(out))


# ---------------------------------------------------------------- 统一写库
def _store_questions(db: Session, run: E.ImportRun, ctx: dict) -> list[dict]:
    """两种来源共用的写库入口：一份题目一条 Question 版本。"""
    src = ctx["src"]
    items = ctx["items"]
    lang = ctx.get("language") or "en"
    origin = ("external" if run.source_kind == "external_paper" else "original")
    source_name = ("外部试卷" if run.source_kind == "external_paper" else "上传资料")
    stored: list[dict] = []

    for idx, it in enumerate(items, start=1):
        no = str(it.get("no") or idx)
        qtype = paper_localization.TYPE_MAP.get(
            str(it.get("type") or "").lower(), "unknown")
        stem = str(it.get("stem") or "").strip()
        if not stem:
            continue
        zh = ctx.get("zh_map", {}).get(no) or {}
        zh_opts = {str(o.get("key")): str(o.get("text_zh") or "")
                   for o in zh.get("options") or [] if isinstance(o, dict)}
        options = []
        for opt in it.get("options") or []:
            if not isinstance(opt, dict):
                continue
            key = str(opt.get("key") or "")
            options.append({"key": key, "text": str(opt.get("text") or ""),
                            "text_zh": zh_opts.get(key, "")})

        payload = it.get("_payload") or {}
        origin_answer = it.get("_answer_origin") or "none"
        ai = it.get("_ai_answer") or {}
        rubric = {"points": ai.get("key_points") or []} if ai else {}
        explanation = str(ai.get("explanation") or "")
        ai_conf = str(ai.get("confidence") or "")
        ai_conflict = str(ai.get("conflict") or "")

        problems = it.get("_problems") or []
        scope = it.get("_scope") or "unconfirmed"
        kp = it.get("_kp_id") or ""
        in_scope = scope in ("in_scope", "in_scope_advanced")
        # 入库判定：课内 + 无结构问题 + 有答案 才算"可用于练习"。
        publishable = bool(in_scope and not problems and payload)
        hold_note = "" if publishable else (
            "；".join(problems) if problems else
            ("超出课程范围，保留为扩展学习" if scope == "extension" else
             "课程范围未确定，待人工核对"))
        if hold_note and "待人工核对" not in hold_note and not publishable:
            hold_note += "；待人工核对后发布"

        notes = (f"统一导入流水线 {run.run_id}；来源：{source_name}；"
                 f"语言：{lang}；答案来源："
                 f"{'原资料' if origin_answer == 'source' else 'DeepSeek 生成' if origin_answer == 'deepseek_generated' else '无'}；"
                 f"知识点：{kp or '待分类'}（{it.get('_kp_reason') or '未判定'}）"
                 + (f"；{hold_note}" if hold_note else ""))

        qid = f"imp_{run.run_id[-8:]}_{idx:03d}"
        # ---- 去重：同一份资料重复导入 / 与既有题库重题，都不另建一道题 ----
        exam_ref = (src.get("paper").epaper_id
                    if run.source_kind == "external_paper" else run.source_id)
        norm = re.sub(r"\s+", "", stem)
        existing = db.execute(select(E.Question).where(
            E.Question.original_exam_id == exam_ref,
            E.Question.original_question_no == no,
            E.Question.active.is_(True))).scalars().first()
        same_stem = existing is not None and re.sub(
            r"\s+", "", existing.stem or "") == norm
        if existing is None or not same_stem:
            for cand in db.execute(select(E.Question).where(
                    E.Question.active.is_(True))).scalars().all():
                if re.sub(r"\s+", "", cand.stem or "") == norm:
                    existing, same_stem = cand, True
                    break
        if existing is not None and same_stem:
            # 同一道题：原地刷新知识点/答案/译文，不产生新版本
            qid = existing.question_id
            row = existing
        else:
            if existing is not None:
                # 同题号但正文变了：保留旧版本以便追溯，另起一个新版本
                existing.active = False
                db.add(existing)
                qid = existing.question_id
                row = E.Question(
                    row_id=E.new_id("qrow"), question_id=qid,
                    question_version=int(existing.question_version or 1) + 1,
                    question_family_id=existing.question_family_id or qid,
                    parent_question_id="")
            else:
                row = E.Question(
                    row_id=E.new_id("qrow"), question_id=qid,
                    question_version=1, question_family_id=qid,
                    parent_question_id="")
        row.origin = origin
        row.original_exam_id = (src.get("paper").epaper_id
                                if run.source_kind == "external_paper"
                                else run.source_id)
        row.original_question_no = no
        row.question_type = qtype
        row.stem = stem
        row.stem_zh = str(zh.get("stem_zh") or "")
        row.options = options
        # 题目所在原页有图表 → 把图挂到题目上，保证"图表与题目关联"不丢。
        # 但只挂给**确实引用图**的题（题干里出现 Figure / 图 N / 下图 之类），
        # 否则同一页的每道题都会重复挂同一张图，属于噪声。
        page_no = int(it.get("page") or 0)
        figs = (ctx.get("page_images") or {}).get(page_no) or []
        images: list[str] = []
        refs_figure = bool(re.search(
            r"(Figure\s*\d|Fig\.\s*\d|shown\s+below|as\s+shown|下图|如图|图示|图\s*\d)",
            (stem or "") + " " + str(zh.get("stem_zh") or ""), re.I))
        if figs and (refs_figure or it.get("has_figure")):
            asset_dir = cfg.ASSET_DIR / "import"
            asset_dir.mkdir(parents=True, exist_ok=True)
            for fidx, src_img in enumerate(figs[:3]):
                dest = asset_dir / f"{run.run_id}_p{page_no}_{fidx}{src_img.suffix}"
                if not dest.exists():
                    shutil.copyfile(src_img, dest)
                images.append(f"_mlq-assets/import/{dest.name}")
        # 只在本次确实抽到图时才写入；否则保留原值，避免"重跑一次把已挂的图清空"
        if images:
            row.stem_images = images
        row.score = float(it.get("score") or 5.0)
        row.main_kp_id = kp
        row.cognitive_level = "理解"
        row.difficulty = "中等"
        row.estimated_minutes = max(2.0, float(it.get("score") or 5.0) * 1.2)
        row.source_refs = [{
            "document_id": "", "rel_path": run.file_rel_path or "",
            "page": int(it.get("page") or 0),
            "label": f"{source_name} 第 {no} 题",
            "import_run": run.run_id, "source_kind": run.source_kind,
            "source_id": run.source_id,
        }]
        row.review_status = "verified" if publishable else "pending"
        row.answer_status = ("ai_auto_checked" if (payload and origin_answer
                                                   == "deepseek_generated")
                             else "verified" if payload else "unknown")
        row.answer_origin = origin_answer
        row.active = True
        row.created_by = "unified_import"
        row.notes = notes
        row.scope_class = (scope if scope in ("in_scope", "in_scope_advanced",
                                              "extension") else "unconfirmed")
        row.answer_payload = payload
        row.rubric = rubric
        row.explanation = explanation
        row.stem_zh = row.stem_zh
        row.original_language = lang
        row.translation_status = "done" if row.stem_zh else (
            "not_needed" if lang == "zh" else "failed")
        row.translation_model = cfg.MODEL_CHAT_MODEL
        row.translation_version = PROMPT_VERSION
        row.translated_at = E.utcnow()
        row.extraction_status = "needs_review" if problems else "extracted"
        row.source_page = int(it.get("page") or 0)
        row.source_quote = str(it.get("quote") or "")[:400]
        db.add(row)
        stored.append({
            "question_id": qid, "no": no, "type": qtype,
            "scope": row.scope_class, "kp_id": kp,
            "publishable": publishable, "answer_origin": origin_answer,
            "has_answer": bool(payload), "problems": problems,
            "ai_confidence": ai_conf, "ai_conflict": ai_conflict,
            "zh": bool(row.stem_zh),
        })
    db.flush()
    return stored


def _stage_import(db: Session, run: E.ImportRun, ctx: dict) -> None:
    stored = _store_questions(db, run, ctx)
    db.commit()
    ctx["stored"] = stored
    # 试卷实体上的统计与状态也要跟着更新，详情页才不会自相矛盾
    if run.source_kind == "external_paper":
        paper: E.ExternalPaper = ctx["src"]["paper"]
        papers_items = db.execute(select(E.ExternalItem).where(
            E.ExternalItem.paper_id == paper.epaper_id)).scalars().all()
        paper.question_count = len(stored)
        paper.total_score = round(sum(float(it.get("score") or 0)
                                      for it in ctx["items"]), 2)
        if ctx.get("zh_pdf_path"):
            paper.zh_pdf_rel_path = cfg.storage_ref(ctx["zh_pdf_path"])
            paper.zh_generated_at = E.utcnow()
            paper.zh_status = ("partial" if any(s["problems"] for s in stored)
                               else "done")
            paper.zh_missing_note = (
                "统一流水线：已生成中文版；" +
                ("部分题目待核对：" + "、".join(
                    s["no"] for s in stored if s["problems"])
                 if any(s["problems"] for s in stored) else "全部题目通过校验"))
            paper.localize_version = f"unified|{cfg.MODEL_CHAT_MODEL}|{PROMPT_VERSION}"
            paper.localize_model = cfg.MODEL_CHAT_MODEL
            paper.localized_at = E.utcnow()
        if ctx.get("zh_thumb_path"):
            # ExternalPaper 没有独立的 zh 缩略图列（不新增列以免迁移风险），
            # 中文版预览图路径记在 run.result 里，由专用接口按 run 反查提供。
            paper.zh_missing_note = (paper.zh_missing_note or
                                     "").rstrip() + "；中文版首页预览已生成"
        paper.updated_at = E.utcnow()
        db.commit()
    _stage(run, "import_bank", "ok",
           stored=len(stored),
           publishable=sum(1 for s in stored if s["publishable"]),
           in_scope=sum(1 for s in stored
                        if s["scope"] in ("in_scope", "in_scope_advanced")),
           extension=sum(1 for s in stored if s["scope"] == "extension"),
           unconfirmed=sum(1 for s in stored if s["scope"] == "unconfirmed"))


def _stage_finalize(db: Session, run: E.ImportRun, ctx: dict) -> None:
    stored = ctx.get("stored") or []
    publishable = sum(1 for s in stored if s["publishable"])
    pending = len(stored) - publishable
    lang = ctx.get("language") or ""
    result = {
        "language": lang,
        "questions": len(stored),
        "publishable": publishable,
        "pending_review": pending,
        "in_scope": sum(1 for s in stored
                        if s["scope"] in ("in_scope", "in_scope_advanced")),
        "extension": sum(1 for s in stored if s["scope"] == "extension"),
        "unconfirmed": sum(1 for s in stored if s["scope"] == "unconfirmed"),
        "answer_source": sum(1 for s in stored if s["answer_origin"] == "source"),
        "answer_ai": sum(1 for s in stored
                         if s["answer_origin"] == "deepseek_generated"),
        "zh_questions": sum(1 for s in stored if s["zh"]),
        "zh_pdf": (run.result or {}).get("zh_pdf", ""),
        "zh_pdf_pages": (run.result or {}).get("zh_pdf_pages", 0),
        "zh_thumbnail": (run.result or {}).get("zh_thumbnail", ""),
        "zh_pages_translated": sum(
            1 for p in (ctx.get("pages_zh") or []) if (p.get("text_zh") or "").strip()),
        "ocr_pages": ctx.get("ocr_pages") or [],
        "formula_fixed": ctx.get("formula_fixed", 0),
        "gate_failed": ctx.get("gate_problems") or [],
        "failed_pages_translate": ctx.get("page_translate_error") or "",
    }
    if not stored:
        run.status = "failed"
        run.error = "没有可入库的题目"
    elif pending and publishable:
        run.status = "partial"
    elif pending and not publishable:
        run.status = "needs_review"
    else:
        run.status = "completed"
    run.result = {**(run.result or {}), **result}
    _stage(run, "finalize", "ok", **{k: v for k, v in result.items()
                                     if k not in ("gate_failed",)})
    db.commit()


# ---------------------------------------------------------------- 入口
def enqueue(db: Session, *, source_kind: str, source_id: str,
            user_id: str = "local_user", force: bool = False,
            is_test_fixture: bool = False) -> dict:
    """入口 A/B/C 共用：只登记来源并排队，不做任何具体处理。"""
    from backend.app.workers.worker import create_job
    row = create_run(db, source_kind=source_kind, source_id=source_id,
                     user_id=user_id, is_test_fixture=is_test_fixture)
    job = create_job(db, "unified_import",
                     {"run_id": row.run_id, "force": force},
                     user_id=user_id,
                     idempotency_key=f"unified:{row.run_id}:{int(row.attempts or 0)}")
    row.job_id = job["job_id"]
    db.commit()
    return {"run_id": row.run_id, "job_id": job["job_id"],
            "status": row.status, "reused": job.get("reused", False)}


def run_for_source(db: Session, *, source_kind: str, source_id: str,
                   user_id: str = "local_user", force: bool = False,
                   is_test_fixture: bool = False) -> dict:
    """同步执行（脚本/测试用，不依赖 worker 是否在跑）。"""
    row = create_run(db, source_kind=source_kind, source_id=source_id,
                     user_id=user_id, is_test_fixture=is_test_fixture)
    return run(db, row.run_id, force=force)


def status_for(db: Session, *, source_kind: str, source_id: str) -> dict:
    row = db.execute(select(E.ImportRun).where(
        E.ImportRun.source_kind == source_kind,
        E.ImportRun.source_id == source_id).order_by(
        E.ImportRun.created_at.desc())).scalars().first()
    if row is None:
        return {"found": False}
    return {"found": True, "run_id": row.run_id, "status": row.status,
            "stage": row.stage, "stage_label": STAGE_LABELS.get(row.stage, row.stage),
            "language": row.language, "steps": row.steps or [],
            "result": row.result or {}, "error": row.error,
            "attempts": row.attempts, "updated_at": row.updated_at}


# ---------------------------------------------------------------- 主流程
def run(db: Session, run_id: str, *, force: bool = False,
        max_seconds: float | None = None) -> dict:
    run_row = db.get(E.ImportRun, run_id)
    if run_row is None:
        raise KeyError(f"导入记录不存在: {run_id}")
    if run_row.status == "completed" and not force:
        return {"ok": True, "cached": True, "status": run_row.status,
                "result": run_row.result}
    run_row.status = "running"
    run_row.attempts = int(run_row.attempts or 0) + 1
    run_row.error = ""
    db.commit()

    ctx: dict[str, Any] = {}
    try:
        ctx["src"] = _describe(db, run_row)
        if not run_row.file_rel_path and ctx["src"].get("path"):
            run_row.file_rel_path = cfg.storage_ref(ctx["src"]["path"])
            db.commit()

        for name, fn in (("parse", _stage_parse),
                         ("detect_language", _stage_language),
                         ("extract_questions", _stage_extract),
                         ("translate", _stage_translate),
                         ("generate_answers", _stage_answers),
                         ("match_knowledge", _stage_match),
                         ("quality_gate", _stage_gate),
                         ("build_zh_pdf", _stage_zh_pdf),
                         ("render_zh_thumbnail", _stage_thumb),
                         ("import_bank", _stage_import),
                         ("finalize", _stage_finalize)):
            if not force and _done(run_row, name):
                continue
            fn(db, run_row, ctx)
            db.commit()
        return {"ok": True, "cached": False, "status": run_row.status,
                "result": run_row.result, "run_id": run_row.run_id}
    except Exception as exc:                           # noqa: BLE001
        run_row.status = "failed"
        run_row.error = f"{type(exc).__name__}: {exc}"[:500]
        _stage(run_row, run_row.stage or "queued", "failed",
               error=run_row.error)
        db.commit()
        return {"ok": False, "status": "failed", "error": run_row.error,
                "run_id": run_row.run_id}
