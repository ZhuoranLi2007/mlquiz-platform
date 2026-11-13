"""User upload -> original saved -> questions recognised -> AI answers ->
whole-paper answer booklet -> PDF, all tied to ONE material item.

Design rules enforced here:

* The uploaded original is written INSIDE the project (`data/uploads/`), never
  into the read-only course-material root.
* Same content (sha256) is never processed twice: the pipeline is reused and the
  existing `upload_id` is returned.
* Answers are produced per question with `answer_pipeline`, so ONE failing
  question never stops the rest, and a retry only touches unfinished ones.
* The answer booklet is the single structure the web view, the PDF and the
  scoring path all read. `content_hash` detects a corrected answer and rebuilds
  instead of letting the three diverge.
* Generating an answer never marks it as "seen". Only an explicit view or PDF
  download writes `AnswerReveal`, which later makes attempts non-independent.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.ingest import material_classifier, paper_extract, parsers
from backend.app.models import entities as E
from backend.app.services import (answer_pipeline, bank_service, material_naming,
                                  model_provider)
from backend.app.services.booklet_pdf import render_booklet

USER_ID = "local_user"

# V2.2：对外统一用用户要求的四类说法（key 不变，历史数据与前端筛选不受影响）
TYPE_LABELS = {**paper_extract.TYPE_LABELS,
               "exam_paper": "考试试卷", "slides": "课程课件",
               "notes": "学习笔记", "other": "其他"}

STATUS_LABELS = {
    "queued": "排队中",
    "parsing": "解析中",
    "parsed": "已解析（答案待生成）",
    "generating": "生成答案中",
    "partial": "部分完成",
    "completed": "完成",
    "failed": "失败",
}

TYPE_LABEL_OF_QUESTION = {
    "single_choice": "单选题", "multi_choice": "多选题",
    "true_false": "判断题", "numeric": "数值题", "term": "名词解释",
    "short_answer": "简答题", "derivation": "推导/计算题",
    "comprehensive": "综合题", "unknown": "待定题型",
}

ALLOWED_EXT = {".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown", ".html",
               ".htm", ".py", ".ipynb", ".csv", ".json", ".png", ".jpg",
               ".jpeg", ".bmp"}
MAX_UPLOAD_BYTES = 80 * 1024 * 1024

# Deterministic keyword -> knowledge point map. Any automatic label is recorded
# with the keyword that matched so it can be audited; an unmatched question
# keeps an empty kp instead of inventing one.
KP_KEYWORDS: dict[str, tuple[str, ...]] = {
    "kp.svm.soft_margin": ("软间隔", "hinge", "Hinge", "松弛变量"),
    "kp.svm.kkt": ("KKT", "对偶问题", "对偶"),
    "kp.svm.kernel": ("核函数", "核技巧", "kernel"),
    "kp.svm.margin": ("支持向量机", "SVM", "最大间隔", "超平面", "间隔"),
    "kp.logistic.cost": ("Logistic", "逻辑回归", "logistic"),
    "kp.logistic.gradient": ("梯度下降", "梯度"),
    "kp.logistic.boundary": ("决策边界", "判决式", "生成式", "分类问题"),
    "kp.lda.objective": ("类内散度", "类间散度", "LDA"),
    "kp.lda.idea": ("线性判别", "LDA"),
    "kp.regularization.l1l2": ("正则化", "L1", "L2", "惩罚项"),
    "kp.nn.bp": ("反向传播", "误差项", "BP"),
    "kp.nn.activation": ("激活函数", "sigmoid", "ReLU", "梯度消失", "梯度爆炸"),
    "kp.nn.loss": ("交叉熵", "损失函数"),
    "kp.dl.residual": ("残差", "ResNet"),
    "kp.dl.transformer": ("Transformer", "注意力", "LSTM", "RNN"),
    "kp.eval.bias_var": ("偏差-方差", "偏差", "方差", "过拟合", "欠拟合"),
    "kp.eval.cv": ("交叉验证", "准确率", "召回率", "评估指标"),
    "kp.bayes.formula": ("贝叶斯公式", "先验概率", "后验概率", "条件概率", "贝叶斯"),
    "kp.bayes.naive": ("朴素贝叶斯", "条件独立"),
    "kp.cluster.kmeans": ("K-means", "KMeans", "k均值", "聚类"),
    "kp.cluster.dbscan": ("DBSCAN", "密度聚类"),
    "kp.pca.variance": ("PCA", "主成分", "累计方差"),
    "kp.nmf.loss": ("NMF", "非负矩阵分解"),
    "kp.ensemble.adaboost": ("AdaBoost", "提升方法"),
    "kp.ensemble.forest": ("随机森林", "Bagging", "bagging"),
    "kp.theory.vc": ("VC 维", "VC维"),
    "kp.theory.pac": ("PAC", "统计学习理论"),
    "kp.linear.mse": ("最小二乘", "MSE", "均方误差"),
    "kp.linear.normal_eq": ("正规方程", "解析解"),
    "kp.intro.definition": ("任务T", "经验E", "性能P", "机器学习指"),
    "kp.intro.generalization": ("泛化", "训练误差", "测试误差"),
    "kp.network.rwr": ("随机游走", "重启随机游走"),
    "kp.framework.mindspore": ("MindSpore",),
}


# --------------------------------------------------------------------- utils
def _safe_name(filename: str) -> str:
    base = Path(filename or "upload").name
    base = re.sub(r"[\\/:*?\"<>|\r\n\t]", "_", base).strip() or "upload"
    return base[:180]


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def guess_kp(stem: str) -> tuple[str, str]:
    """Return (kp_id, matched_keyword). Empty kp when nothing matches."""
    text = stem or ""
    best: tuple[str, str] = ("", "")
    for kp_id, kws in KP_KEYWORDS.items():
        for kw in kws:
            if kw and kw in text:
                if len(kw) > len(best[1]):
                    best = (kp_id, kw)
    return best


# Shown next to "（尚未生成）" in the booklet so the reader knows why.
PENDING_REASON_LABEL = {
    "incomplete_stem": "题干在原卷中为图片，文本层被截断",
    "missing_figure_or_formula": "题干依赖的图/表不在文本中",
    "insufficient_conditions": "题目条件不足，无法唯一作答",
    "contradictory_result": "自检发现结果矛盾",
    "calculation_unevaluable": "自校验式无法独立求值",
    "output_truncated": "模型输出被截断",
    "malformed_output": "模型输出结构不合规",
    "unexpected_error": "生成过程内部异常",
}


def _answer_text(q: E.Question) -> str:
    """Render the SAVED answer payload as display text. Never re-derives."""
    a = q.answer_payload or {}
    t = q.question_type
    if t == "single_choice":
        return str(a.get("key") or a.get("answer") or "")
    if t == "multi_choice":
        keys = a.get("keys") or ([a["key"]] if a.get("key") else [])
        return "".join(sorted(str(k) for k in keys))
    if t == "true_false":
        v = a.get("value", a.get("key"))
        if isinstance(v, bool):
            return "正确" if v else "错误"
        s = str(v).strip().lower()
        if s in ("true", "t", "1", "对", "正确", "是"):
            return "正确"
        if s in ("false", "f", "0", "错", "错误", "否"):
            return "错误"
        return str(v)
    if t == "numeric":
        v = a.get("value")
        unit = a.get("unit") or ""
        return f"{v}{unit}" if v is not None else ""
    # 题型未知但答案本身就是选项键（`{"key": "B"}`）：按证据渲染，
    # 否则客观题会显示成「有答案但答案文本为空」（实测发生过）。
    if a.get("keys") and isinstance(a["keys"], list):
        return "".join(sorted(str(k) for k in a["keys"] if str(k).strip()))
    if str(a.get("key") or "").strip():
        return str(a["key"]).strip()
    for key in ("text", "reference_answer", "answer", "content", "values"):
        if a.get(key):
            v = a[key]
            return "；".join(map(str, v)) if isinstance(v, list) else str(v)
    return ""


def _rubric_points(rubric: dict) -> list:
    r = rubric or {}
    for key in ("points", "rubric_points", "criteria"):
        if isinstance(r.get(key), list):
            return r[key]
    return []


def _pdf_rel(upload_id: str) -> str:
    return f"data/booklets/{upload_id}.pdf"


# ------------------------------------------------------------------ upload in
def _auto_generate_default(db: Session, user_id: str) -> bool:
    """The explicit opt-in switch from V2 §5.4 rule 9 — OFF unless asked for."""
    row = db.get(E.UserSetting, user_id)
    if row is None:
        return False
    return bool((row.payload or {}).get("auto_generate_answers", False))


def save_upload(db: Session, *, filename: str, content: bytes,
                declared_type: str = "auto",
                auto_generate: bool | None = None,
                user_id: str = USER_ID) -> dict:
    """Persist the original, dedupe by content, create the parsing job."""
    safe = _safe_name(filename)
    ext = Path(safe).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"不支持的资料格式：{ext or '无扩展名'}；"
                         f"支持 {', '.join(sorted(ALLOWED_EXT))}")
    if not content:
        raise ValueError("文件内容为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"文件超过 {MAX_UPLOAD_BYTES // (1024*1024)} MB 上限")

    sha = _sha256_bytes(content)
    existing = db.execute(
        select(E.MaterialUpload).where(E.MaterialUpload.sha256 == sha)
        .order_by(E.MaterialUpload.created_at)
    ).scalars().first()
    if existing:
        return {"upload_id": existing.upload_id, "reused": True,
                "deduped": True, "status": existing.status,
                "filename": existing.filename,
                "note": "相同内容已上传过，直接复用已有的解析与答案结果，未重复处理"}

    stored = cfg.UPLOAD_DIR / sha[:14] / safe
    stored.parent.mkdir(parents=True, exist_ok=True)
    if not stored.exists():
        stored.write_bytes(content)
    rel = cfg.storage_ref(stored)

    declared = declared_type if declared_type in (
        "exam_paper", "slides", "notes", "code", "other") else "auto"
    # V2.2：导入时先按【正文内容】做一次零调用的规则判别，让列表立刻显示类别。
    # 扩展名只用于 read_text 决定怎么读字节，不参与打分；解析完成后
    # process_upload 会再用完整正文复核，并在证据不足时自动接入 DeepSeek。
    sample = material_classifier.read_text(content, ext)
    auto = material_classifier.classify(
        db, text=sample, allow_model=False)
    detected = auto["type"]
    reason = (f"内容规则：{auto['reason']}"
              f"（置信度 {auto['confidence']:.2f}）")
    resolved = declared if declared != "auto" else detected
    if declared != "auto":
        reason = (f"用户手动指定为「{TYPE_LABELS.get(declared, declared)}」；"
                  f"按正文自动识别：{TYPE_LABELS.get(detected, detected)}（{auto['reason']}）")
    cls_source = "user" if declared != "auto" else "rule"

    up = E.MaterialUpload(
        upload_id=E.new_id("upl"), user_id=user_id, filename=safe,
        stored_rel_path=rel, sha256=sha, size_bytes=len(content),
        mime=_guess_mime(ext), declared_type=declared,
        resolved_type=resolved, detected_type=detected, detect_reason=reason,
        classify_source=cls_source,
        classify_confidence=auto["confidence"],
        classify_needs_review=auto["needs_review"],
        status="queued", stage="queued",
        progress={"note": "已保存原卷，等待后台处理"},
    )
    db.add(up)
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=user_id,
        event_type="material_uploaded", target_id=up.upload_id,
        payload={"filename": safe, "sha256": sha,
                 "resolved_type": resolved, "detected_type": detected,
                 "declared_type": declared, "size_bytes": len(content)}))
    db.commit()

    from backend.app.workers.worker import create_job
    if auto_generate is None:
        auto_generate = _auto_generate_default(db, user_id)
    job = create_job(db, "paper_ingest",
                     {"upload_id": up.upload_id,
                      "auto_generate": bool(auto_generate)},
                     user_id, idempotency_key=f"ingest:{up.upload_id}")
    up.job_id = job["job_id"]
    db.commit()

    # V3.5：上传成功即进入**统一导入流水线**（翻译 / 中文版 PDF / 首页预览 /
    # 课程知识点匹配 / 课内题入库），用户不需要再点「翻译」「提取题目」等按钮。
    # 与初始化批量导入走的是同一个 services/unified_import 阶段机。
    unified = {}
    try:
        from backend.app.services import unified_import
        unified = unified_import.enqueue(
            db, source_kind="material_upload", source_id=up.upload_id,
            user_id=user_id)
    except Exception as exc:                          # noqa: BLE001
        # 排队失败不能把"已保存成功"的上传说成失败，如实记录即可。
        unified = {"error": f"{type(exc).__name__}: {exc}"[:200]}

    return {"upload_id": up.upload_id, "reused": False, "deduped": False,
            "job_id": job["job_id"], "status": up.status,
            "filename": up.filename, "resolved_type": resolved,
            "material_type_label": TYPE_LABELS.get(resolved, resolved),
            "detect_reason": reason,
            "auto_generate": bool(auto_generate),
            "import_run": unified,
            "note": ("原卷已保存，已进入后台处理；本次按你的选择同时生成 AI 答案解析"
                     if auto_generate else
                     "原卷已保存，已进入后台识别题目与原始题号；"
                     "AI 答案解析需在列表中点击「AI 生成答案解析 PDF」后才会调用模型")}


def _guess_mime(ext: str) -> str:
    return {".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument."
                     "wordprocessingml.document",
            ".pptx": "application/vnd.openxmlformats-officedocument."
                     "presentationml.presentation",
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".py": "text/x-python", ".md": "text/markdown",
            ".txt": "text/plain", ".html": "text/html",
            ".ipynb": "application/json"}.get(ext, "application/octet-stream")


def _manifest_rec(up: E.MaterialUpload) -> dict:
    ext = Path(up.filename).suffix.lower()
    sniffed = {".pdf": "pdf", ".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg",
               ".bmp": "image"}.get(ext, ext.lstrip("."))
    # Reuse the review-note QA splitter for uploaded notes: it already separates
    # 题干 / 选项 / 答案 / 解析 across page boundaries.
    category = "review_note" if up.resolved_type == "notes" else \
        f"uploaded_{up.resolved_type}"
    return {
        "relative_path": up.stored_rel_path,
        "sha256": up.sha256,
        "source_category": category,
        "extension": ext,
        "sniffed_type": sniffed,
        "size_bytes": up.size_bytes,
        "readability": "ok",
        "document_version_id": "ver_" + up.sha256[:14] + "_" +
                               cfg.PARSER_CONFIG_VERSION.replace(".", ""),
    }


def _schedule_slides_questions(db: Session, up: E.MaterialUpload) -> dict:
    """登记课件后台出题任务（幂等）。不在页面上提示，也不需要用户点击。"""
    if up.slides_question_count > 0:
        return {"status": "skipped",
                "reason": "该课件已完成过自动出题，不重复调用模型",
                "questions": up.slides_question_count}
    if up.slides_job_id:
        old = db.get(E.Job, up.slides_job_id)
        if old is not None and old.status in ("queued", "running", "succeeded"):
            return {"status": "skipped", "job_id": old.job_id,
                    "reason": f"已有自动出题任务（{old.status}），不重复登记"}
        if old is not None and old.status == "failed":
            # 模型临时不可用/输出不合规会失败，但此时并未出过题；把同一个
            # 任务重新排队，而不是被幂等字段永久挡住，也不新建重复调用。
            old.status, old.stage = "queued", "retry_queued"
            old.attempts, old.error = 0, ""
            db.commit()
            return {"status": "requeued", "job_id": old.job_id,
                    "reason": "上次的后台出题未成功且未入库题目，已重新排队"}
    if not model_provider.chat_status()["configured"]:
        return {"status": "deferred",
                "reason": "模型未配置，未登记自动出题任务（配置后重新解析该课件即可）"}
    from backend.app.workers.worker import create_job
    job = create_job(db, "slides_questions", {"upload_id": up.upload_id},
                     up.user_id, idempotency_key=f"slidesq:{up.upload_id}")
    up.slides_job_id = job["job_id"]
    db.commit()
    return {"status": "queued", "job_id": job["job_id"],
            "note": "后台静默识别课件题目并按考点补题"}


def reclassify_upload(db: Session, upload_id: str) -> dict:
    """按【已解析的正文内容】重新识别资料类型。

    用于两处：老资料在 V2.2 之前是按文件名/后缀归类的，需要按新规则回填；
    以及用户手动点「重新识别」。用户手动指定过的类型仍优先保留，但自动判别
    结果会完整留痕。识别为老师课件时顺带登记后台静默出题任务。
    """
    up = db.get(E.MaterialUpload, upload_id)
    if up is None:
        raise KeyError(upload_id)
    text = _sample_text(_read_units(up.document_id))
    auto = material_classifier.classify(db, text=text, allow_model=True)
    before = up.resolved_type
    up.detected_type = auto["type"]
    up.classify_confidence = auto["confidence"]
    up.classify_needs_review = auto["needs_review"]
    label = TYPE_LABELS.get(auto["type"], auto["type"])
    reason = (f"{'模型判别' if auto['source'] == 'model' else '内容规则'}："
              f"{auto['reason']}（{label}，置信度 {auto['confidence']:.2f}）")
    usable = not auto["needs_review"] and auto["confidence"] >= 0.5
    if up.declared_type == "auto" and usable:
        up.classify_source = auto["source"]
        up.resolved_type = auto["type"]
        up.detect_reason = reason
    elif up.declared_type == "auto":
        up.classify_source = auto["source"]
        up.classify_needs_review = True
        up.detect_reason = (reason + "；判别证据不足，保留原归类并标记待复核")
    else:
        up.classify_source = "user"
        up.detect_reason = (f"用户手动指定为「{TYPE_LABELS.get(up.resolved_type)}」；"
                            f"按正文自动识别：{reason}")
    db.commit()
    steps = []
    if not usable:
        steps.append({"stage": "classify_material", "status": "needs_review",
                      "kept": up.resolved_type, "detected": auto["type"],
                      "reason": auto["reason"][:200]})
    if up.resolved_type == "slides":
        steps.append({"stage": "slides_questions",
                      **_schedule_slides_questions(db, up)})
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=up.user_id,
        event_type="material_reclassified", target_id=up.upload_id,
        payload={"from": before, "to": up.resolved_type,
                 "detected": auto["type"], "source": auto["source"],
                 "confidence": auto["confidence"],
                 "needs_review": auto["needs_review"],
                 "text_len": auto.get("text_len", 0),
                 "prompt_version": material_classifier.PROMPT_VERSION,
                 "note": "仅依据解析后的正文内容判别，不使用文件名或扩展名"}))
    db.commit()
    return {"upload_id": upload_id, "from": before,
            "resolved_type": up.resolved_type, "detected_type": up.detected_type,
            "type_label": TYPE_LABELS.get(up.resolved_type, up.resolved_type),
            "classify_source": up.classify_source,
            "classify_confidence": up.classify_confidence,
            "classify_needs_review": up.classify_needs_review,
            "detect_reason": up.detect_reason,
            "text_len": auto.get("text_len", 0), "steps": steps}


# ------------------------------------------------------------------ pipeline
def process_upload(db: Session, upload_id: str, *,
                   auto_generate: bool = True) -> dict:
    """Full pipeline. Idempotent: safe to re-run after a crash or a retry.

    `auto_generate=False` stops after question extraction: the original, its
    parsed chunks and the numbered question list are saved, but no paid model
    call is made until the user explicitly asks for the answer booklet.
    """
    up = db.get(E.MaterialUpload, upload_id)
    if not up:
        raise KeyError(upload_id)
    steps: list[dict] = []

    def stage(name: str, status: str, detail: dict | None = None) -> None:
        up.stage = name
        up.progress = {"stage": name, "stage_status": status,
                       "updated_at": E.utcnow(),
                       "resolved_type": up.resolved_type,
                       "detail": detail or {},
                       "steps": steps}
        up.updated_at = E.utcnow()
        db.commit()

    # 1 ---------------------------------------------------------- save original
    src = cfg.resolve_storage_ref(up.stored_rel_path)
    if not src.exists():
        src = cfg.UPLOAD_DIR / up.sha256[:14] / up.filename
    if not src.exists():
        up.status, up.stage = "failed", "save_original"
        up.error = "原卷文件丢失，无法继续"
        db.commit()
        return {"upload_id": upload_id, "status": "failed", "error": up.error}
    steps.append({"stage": "save_original", "status": "ok",
                  "path": up.stored_rel_path, "bytes": up.size_bytes})
    stage("parse", "running")

    # 2 ---------------------------------------------------------------- parse
    up.status = "parsing"
    db.commit()
    rec = _manifest_rec(up)
    pr = parsers.parse_one(db, rec, render_limit=12)
    up.document_id = pr["document_id"]
    up.document_version_id = pr.get("document_version_id", rec["document_version_id"])
    up.parse_status = pr.get("parse_status", "")
    steps.append({"stage": "parse", "status": pr.get("status"),
                  "parse_status": up.parse_status,
                  "chunks": pr.get("chunks", 0),
                  "notes": pr.get("notes", [])})
    if pr.get("status") == "failed":
        up.status, up.stage = "failed", "parse"
        up.error = "；".join(pr.get("notes") or ["解析失败"])
        db.commit()
        return {"upload_id": upload_id, "status": "failed", "error": up.error}

    # V2.2：用解析后的完整正文复核类型；证据不足时 classify() 会自动接 DeepSeek。
    # 用户手动指定的类型不会被覆盖，但自动判别结果仍然留痕，便于日后核对。
    parsed = _read_units(up.document_id)
    sample = _sample_text(parsed)
    auto = material_classifier.classify(db, text=sample, allow_model=True)
    detected = auto["type"]
    up.detected_type = detected
    up.classify_confidence = auto["confidence"]
    up.classify_needs_review = auto["needs_review"]
    reason = (f"{'模型判别' if auto['source'] == 'model' else '内容规则'}："
              f"{auto['reason']}（{TYPE_LABELS.get(detected, detected)}，"
              f"置信度 {auto['confidence']:.2f}）")
    if up.declared_type == "auto":
        up.classify_source = auto["source"]
        # 证据不足（例如扫描件没有文本层）时保留导入阶段的归类，不拿一个
        # 低置信的「其他」去覆盖已经识别好的试卷/课件。
        usable = not auto["needs_review"] and auto["confidence"] >= 0.5
        if usable and detected != up.resolved_type:
            steps.append({"stage": "classify_material", "status": "refined",
                          "from": up.resolved_type, "to": detected,
                          "source": auto["source"],
                          "confidence": auto["confidence"]})
            up.resolved_type = detected
        if not usable:
            up.classify_needs_review = True
            steps.append({"stage": "classify_material", "status": "needs_review",
                          "kept": up.resolved_type, "detected": detected,
                          "confidence": auto["confidence"],
                          "reason": auto["reason"][:200]})
        up.detect_reason = reason + ("" if usable else
                                     "；判别证据不足，保留原归类并标记待复核")
    else:
        up.classify_source = "user"
        up.detect_reason = (f"用户手动指定为「{TYPE_LABELS.get(up.resolved_type)}」；"
                            f"按正文自动识别：{reason}")
    db.commit()

    if up.resolved_type != "exam_paper":
        # 老师课件：后台静默识别课件里的题目 + 按考点补题并直接入库。
        # 用户明确要求「系统后台自己做，不用告知用户去做这个事情」，
        # 因此这里只登记一个幂等后台任务，不弹窗、不要求点击、不阻塞导入。
        if up.resolved_type == "slides":
            job_note = _schedule_slides_questions(db, up)
            steps.append({"stage": "slides_questions", **job_note})
        else:
            steps.append({"stage": "extract_questions", "status": "skipped",
                          "reason": f"资料类型为「{TYPE_LABELS.get(up.resolved_type)}」，"
                                    "不做试卷题目抽取"})
        up.status = "completed"
        up.stage = "completed"
        up.question_count, up.answer_ready_count = 0, 0
        up.progress = {"stage": "completed", "stage_status": "ok",
                       "updated_at": E.utcnow(),
                       "resolved_type": up.resolved_type,
                       "detail": {"note": "非试卷资料：已完成内容识别与解析入库"
                                          + ("，课件题目由后台自动补充"
                                             if up.resolved_type == "slides" else
                                             "，未生成试卷答案")},
                       "steps": steps}
        db.commit()
        return {"upload_id": upload_id, "status": "completed",
                "resolved_type": up.resolved_type, "steps": steps}

    # 3 ------------------------------------------------------- extract questions
    stage("extract_questions", "running")
    ex = extract_questions(db, up, parsed)
    steps.append({"stage": "extract_questions",
                  "status": "ok" if ex["questions"] else "empty",
                  "stats": ex["stats"]})
    if not ex["questions"]:
        up.status, up.stage = "failed", "extract_questions"
        up.error = "未识别到任何题目；请确认这是试卷类资料，或手动指定资料类型后重试"
        db.commit()
        return {"upload_id": upload_id, "status": "failed", "error": up.error}

    # 4 ------------------------------------------------------- generate answers
    # V2 §5.4 规则 9：默认不因为一次上传就触发付费调用。解析与生成是两件事，
    # 没被要求生成时，题目清单已经可用，状态如实停在「已解析（答案待生成）」。
    if not auto_generate:
        steps.append({"stage": "generate_answers", "status": "deferred",
                      "reason": "未勾选「上传后自动生成」，等待用户点击生成"})
        up.status = "parsed"
        up.stage = "awaiting_answer_generation"
        up.progress = {
            "stage": "awaiting_answer_generation", "stage_status": "ok",
            "updated_at": E.utcnow(), "resolved_type": up.resolved_type,
            "detail": {"questions": up.question_count, "answer_ready": 0,
                       "answer_pending": up.question_count,
                       "note": "原卷已保存、题目与原始题号已识别；"
                               "AI 答案解析需要点击「AI 生成答案解析 PDF」才会调用模型"},
            "steps": steps,
        }
        up.updated_at = E.utcnow()
        db.add(E.LearningEvent(
            event_id=E.new_id("evt"), user_id=up.user_id,
            event_type="material_ingest_finished", target_id=up.upload_id,
            payload={"status": up.status, "questions": up.question_count,
                     "answer_ready": 0, "auto_generate": False,
                     "note": "仅解析与题目识别；答案生成等待用户点击"}))
        db.commit()
        return {"upload_id": upload_id, "status": up.status,
                "question_count": up.question_count, "answer_ready_count": 0,
                "booklet_id": "", "awaiting_answer_request": True,
                "steps": steps}

    return _answer_stage(db, up, steps, regenerate=False)


def _answer_stage(db: Session, up: E.MaterialUpload, steps: list[dict], *,
                  regenerate: bool = False) -> dict:
    """Steps 4–7: generate answers → assemble booklet → render PDF → finalize.

    Kept separate from `process_upload` so the same code runs when the user
    clicks 「AI 生成答案解析 PDF」 long after the original was parsed. Both paths
    end with the same truthful status, so a page refresh never shows a
    different answer set from the one that was saved.
    """
    upload_id = up.upload_id
    up.stage = "generate_answers"
    up.status = "generating"
    up.error = ""
    up.progress = {"stage": "generate_answers", "stage_status": "running",
                   "updated_at": E.utcnow(), "resolved_type": up.resolved_type,
                   "detail": {"done": 0, "total": len(up.question_order or [])},
                   "steps": steps}
    db.commit()
    gen = generate_answers(db, up)
    steps.append({"stage": "generate_answers", **gen})

    # 5 ---------------------------------------------------------- answer booklet
    up.stage = "assemble_booklet"
    db.commit()
    bk = build_booklet(db, up, force=True, bump_version=regenerate)
    steps.append({"stage": "assemble_booklet",
                  "status": "ok" if bk.get("booklet_id") else "none",
                  "answered": bk.get("answered_count"),
                  "total": bk.get("total_count"),
                  "version": bk.get("version")})

    # 6 ----------------------------------------------------------------- PDF
    up.stage = "render_pdf"
    db.commit()
    pf = render_booklet_pdf(db, up) if bk.get("booklet_id") else {"ok": False}
    steps.append({"stage": "render_pdf",
                  "status": "ok" if pf.get("ok") else "skipped",
                  "bytes": pf.get("pdf_bytes", 0)})

    # 7 ------------------------------------------------------------- finalize
    ready = _ready_count(db, up)
    up.answer_ready_count = ready
    total = up.question_count
    if ready == 0:
        up.status = "parsed"
    elif ready < total:
        up.status = "partial"
    else:
        up.status = "completed"
    up.stage = "completed"
    up.progress = {
        "stage": "completed", "stage_status": "ok", "updated_at": E.utcnow(),
        "resolved_type": up.resolved_type,
        "detail": {"questions": total, "answer_ready": ready,
                   "answer_pending": total - ready,
                   "booklet_version": bk.get("version"),
                   "model_configured": model_provider.chat_status()["configured"],
                   "note": ("全部题目已有可用答案" if ready == total else
                            "部分题目答案尚未就绪；配置模型后可只重试未完成部分")},
        "steps": steps,
    }
    up.updated_at = E.utcnow()
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=up.user_id,
        event_type="material_ingest_finished", target_id=up.upload_id,
        payload={"status": up.status, "questions": total,
                 "answer_ready": ready, "booklet_id": up.booklet_id,
                 "booklet_version": bk.get("version"),
                 "regenerated": bool(regenerate), "steps": steps}))
    db.commit()
    return {"upload_id": upload_id, "status": up.status,
            "question_count": total, "answer_ready_count": ready,
            "booklet_id": up.booklet_id, "booklet_version": bk.get("version"),
            "steps": steps}


def _read_units(doc_id: str) -> dict:
    f = cfg.PARSED_DIR / doc_id / "units.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _sample_text(parsed: dict) -> str:
    """分类样本取前 24 页有文本的页，而不是固定前 6 页。

    实测：老师课件前几页常是封面、章节标题与「回顾」页，只有几十个字；
    只取前 6 页会让正文证据不足，把课件误判成笔记。样本仍按内容截断，
    不把文件名或后缀送进分类器。
    """
    pages = parsed.get("pages") or parsed.get("units") or []
    parts = [(p.get("raw_text") or "").strip() for p in pages[:24]]
    return "\n".join(t for t in parts if t)[:12000]


# --------------------------------------------------------------- questions
def extract_questions(db: Session, up: E.MaterialUpload, parsed: dict) -> dict:
    """Create one immutable question version per recognised question."""
    res = paper_extract.extract_paper(parsed)
    doc_id = up.document_id
    order_ids: list[str] = []
    created = 0
    used: set[str] = set()

    for item in res["questions"]:
        qid = (f"upq_{up.sha256[:10]}_s{item['section_index']}_"
               f"q{item['source_no']}")
        # 同一份卷子里不同分区可以有相同的原始题号（如两段都从「第1题」开始），
        # 当分区标题没被识别出来时 section_index 全是 0，朴素 id 会撞唯一键
        # （实测：整卷解析失败 UNIQUE constraint failed: questions.question_id）。
        # 撞了就补一个识别顺序后缀；顺序稳定，所以重复解析仍得到同一个 id。
        if qid in used:
            qid = f"{qid}_o{item['order']}"
        used.add(qid)
        ref = [{
            "document_id": doc_id,
            "document_version_id": up.document_version_id,
            "rel_path": up.stored_rel_path,
            "page": (item["pages"] or [1])[0],
            "pages": item["pages"],
            "unit_label": "page",
            "chunk_label": item["no"],
            "section": item["section"],
            "section_index": item["section_index"],
            "original_order": item["order"],
            "upload_id": up.upload_id,
        }]
        kp_id, kw = guess_kp(item["stem"])
        # 有选项就是客观题：题型没被分区标题识别出来时，不要停在「待定题型」，
        # 否则组卷筛选、评分分支和答案渲染都会各走一条不一致的路。
        qtype = item["question_type"]
        if qtype in ("", "unknown", "other") and len(item["options"] or []) >= 2:
            qtype = "single_choice"
        row = db.execute(select(E.Question).where(
            E.Question.question_id == qid,
            E.Question.question_version == 1)).scalars().first()
        if row is None:
            row = E.Question(
                row_id=E.new_id("qrow"), question_id=qid, question_version=1,
                origin="original", original_exam_id=up.upload_id,
                original_question_no=item["no"],
                question_type=qtype,
                stem=item["stem"], options=item["options"],
                score=float(item["score"]), main_kp_id=kp_id,
                aux_kp_ids=[], question_family_id=f"fam_{qid}",
                review_status="pending", answer_status="unknown",
                answer_origin="none", active=True,
                source_refs=ref,
                notes=("由上传试卷自动识别（题型分区 + 原始题号）；原卷无答案，"
                       "已进入 DeepSeek 解答队列"
                       + (f"；知识点按关键词『{kw}』自动标注" if kw else
                          "；知识点未自动标注")),
            )
            db.add(row)
            created += 1
        elif not answer_pipeline.is_answer_ready(row):
            # Refresh the text only while no fixed answer exists.
            row.stem = item["stem"]
            row.options = item["options"]
            row.score = float(item["score"])
            row.question_type = qtype
            if kp_id:
                row.main_kp_id = kp_id
            row.source_refs = ref
        order_ids.append(qid)
        if item["order"] % 8 == 0:
            db.commit()
    up.question_count = len(order_ids)
    up.question_order = order_ids
    db.commit()
    return {**res, "created": created, "question_order": order_ids}


def _ready_count(db: Session, up: E.MaterialUpload) -> int:
    n = 0
    for qid in up.question_order or []:
        q = answer_pipeline.latest_question(db, qid)
        if q and answer_pipeline.is_answer_ready(q):
            n += 1
    return n


def generate_answers(db: Session, up: E.MaterialUpload) -> dict:
    """Per-question generation with failure isolation and a real progress feed."""
    ok = pending = attention = failed = cached = 0
    failures: list[dict] = []
    total = len(up.question_order or [])
    for i, qid in enumerate(up.question_order or [], start=1):
        q = answer_pipeline.latest_question(db, qid)
        if not q:
            failed += 1
            failures.append({"question_id": qid, "reason": "question_missing"})
            continue
        try:
            if answer_pipeline.is_answer_ready(q):
                ok += 1
            else:
                info = answer_pipeline.enqueue(db, q, "import_batch")
                gen = db.get(E.AnswerGeneration, info["generation_id"])
                if gen and gen.status == "succeeded" and gen.result:
                    answer_pipeline.publish_answer(db, q, gen, gen.result,
                                                   gen.checks or {})
                    cached += 1
                    ok += 1
                else:
                    r = answer_pipeline.generate(db, info["generation_id"])
                    st = r.get("status")
                    if st == "succeeded":
                        ok += 1
                    elif st == "model_not_configured":
                        pending += 1
                    elif st == "needs_attention":
                        attention += 1
                        failures.append({"question_id": qid,
                                         "reason": r.get("exception_code", ""),
                                         "detail": r.get("exception_detail", "")})
                    else:
                        failed += 1
                        failures.append({"question_id": qid,
                                         "reason": st or "unknown",
                                         "detail": str(r.get("errors") or "")[:300]})
        except Exception as e:                      # noqa: BLE001 - isolate one Q
            failed += 1
            failures.append({"question_id": qid,
                             "reason": f"{type(e).__name__}: {e}"[:300]})
        up.progress = {
            "stage": "generate_answers", "stage_status": "running",
            "updated_at": E.utcnow(), "resolved_type": up.resolved_type,
            "detail": {"done": i, "total": total, "answer_ready": ok,
                       "cached": cached, "pending_model": pending,
                       "needs_attention": attention, "failed": failed,
                       "model_configured":
                           model_provider.chat_status()["configured"],
                       "failures": failures[-6:]},
        }
        db.commit()
    up.answer_ready_count = ok
    db.commit()
    return {"ok": ok, "cached": cached, "pending_model_configuration": pending,
            "needs_attention": attention, "failed": failed,
            "total": total, "failures": failures}


# ---------------------------------------------------------------- booklet
def _booklet_hash(items: list[dict]) -> str:
    payload = json.dumps(items, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_booklet(db: Session, up: E.MaterialUpload, *,
                  force: bool = False, bump_version: bool = False) -> dict:
    """Assemble sections in original order from the SAVED answers.

    `bump_version` is only passed when the user explicitly asks to regenerate:
    the booklet keeps one row per upload and its `version` moves forward, so the
    answer the web view shows, the PDF that is downloaded and the answers used
    for scoring are always the same, single saved set.
    """
    sections: list[dict] = []
    version_map: dict[str, int] = {}
    hash_items: list[dict] = []
    answered = 0

    by_section: dict[int, dict] = {}
    for order, qid in enumerate(up.question_order or [], start=1):
        q = answer_pipeline.latest_question(db, qid)
        if not q:
            continue
        ref = (q.source_refs or [{}])[0]
        sec_idx = int(ref.get("section_index") or 0)
        ready = answer_pipeline.is_answer_ready(q)
        if ready:
            answered += 1
        meta = answer_pipeline.answer_meta_for(db, q.question_id,
                                               q.question_version)
        entry = {
            "no": q.original_question_no,
            "order": order,
            "question_id": q.question_id,
            "question_version": q.question_version,
            "question_type": q.question_type,
            "type_label": TYPE_LABEL_OF_QUESTION.get(q.question_type, q.question_type),
            "score": q.score,
            "stem": q.stem,
            "options": q.options or [],
            "answer_text": _answer_text(q) if ready else "",
            "answer_label": meta.get("label") or ("AI参考答案" if ready else ""),
            "answer_origin": q.answer_origin,
            "answer_version": meta.get("answer_version") or 0,
            "explanation": q.explanation if ready else "",
            "rubric_points": _rubric_points(q.rubric) if ready else [],
            "answered": ready,
            "pending_code": "",
            "pending_note": "",
            "source_refs": ref,
        }
        if not ready:
            # Tell the reader WHY a question has no answer instead of a bare
            # "（尚未生成）": truncated stem vs. missing figure are different
            # problems and need different follow-up.
            gen = db.execute(
                select(E.AnswerGeneration).where(
                    E.AnswerGeneration.question_id == q.question_id,
                    E.AnswerGeneration.question_version == q.question_version)
                .order_by(E.AnswerGeneration.answer_version.desc())
            ).scalars().first()
            if gen is not None and gen.exception_code:
                entry["pending_code"] = gen.exception_code
                entry["pending_note"] = PENDING_REASON_LABEL.get(
                    gen.exception_code, gen.exception_code)
        if meta.get("answer_version"):
            version_map[q.question_id] = int(meta["answer_version"])
        hash_items.append({"q": q.question_id, "v": q.question_version,
                           "av": entry["answer_version"],
                           "a": q.answer_payload if ready else None,
                           "r": q.rubric if ready else None,
                           # The explanation and the label are printed on the
                           # web page AND in the PDF, so a corrected explanation
                           # must invalidate the booklet exactly like a
                           # corrected answer does.
                           "e": (q.explanation or "") if ready else None,
                           # The pending reason is printed too.
                           "pn": entry.get("pending_note", "")})
        sec = by_section.setdefault(sec_idx, {
            "section": ref.get("section") or "（未分区的题目）",
            "section_index": sec_idx,
            "questions": [],
        })
        sec["questions"].append(entry)
    sections = [by_section[k] for k in sorted(by_section)]
    total = sum(len(s["questions"]) for s in sections)
    content_hash = _booklet_hash(hash_items)
    status = ("complete" if total and answered == total else
              "partial" if answered else "pending")

    bk = db.execute(select(E.AnswerBooklet).where(
        E.AnswerBooklet.upload_id == up.upload_id)).scalars().first()
    # Keep the upload's own counters truthful: the library list reads them, and
    # they used to go stale the moment an answer was withdrawn or regenerated
    # outside the ingest job (showed 21/22 while the booklet had 20/22).
    up.answer_ready_count = answered
    if up.status in ("completed", "partial"):
        up.status = "completed" if status == "complete" else "partial"
    if bk and bk.content_hash == content_hash and not force:
        bk.answered_count, bk.total_count = answered, total
        db.commit()
        return {"booklet_id": bk.booklet_id, "cached": True,
                "status": bk.status, "answered_count": answered,
                "total_count": total, "content_hash": content_hash,
                "version": bk.version or 1}
    if not bk:
        bk = E.AnswerBooklet(booklet_id=E.new_id("bk"), upload_id=up.upload_id,
                             version=1, generated_at=E.utcnow())
        db.add(bk)
    elif bump_version:
        bk.version = (bk.version or 1) + 1
        bk.generated_at = E.utcnow()

    bk.document_id = up.document_id
    bk.title = f"{Path(up.filename).stem} · AI 参考答案"
    if not bk.generated_at:
        bk.generated_at = E.utcnow()
    bk.status = status
    bk.sections = sections
    bk.answer_version_map = version_map
    bk.content_hash = content_hash
    bk.answered_count, bk.total_count = answered, total
    bk.updated_at = E.utcnow()
    up.booklet_id = bk.booklet_id
    db.commit()
    return {"booklet_id": bk.booklet_id, "cached": False, "status": status,
            "answered_count": answered, "total_count": total,
            "content_hash": content_hash, "version": bk.version or 1}


def render_booklet_pdf(db: Session, up: E.MaterialUpload) -> dict:
    bk = db.execute(select(E.AnswerBooklet).where(
        E.AnswerBooklet.upload_id == up.upload_id)).scalars().first()
    if not bk:
        return {"ok": False, "reason": "no_booklet"}
    out = cfg.PROJECT_ROOT / _pdf_rel(up.upload_id)
    meta = {
        "原卷文件": up.filename,
        "题目数量": f"{bk.total_count}",
        "已生成答案": f"{bk.answered_count}/{bk.total_count}",
        "生成时间": E.utcnow().replace("T", " ").replace("+00:00", " UTC"),
        "答案来源": "DeepSeek 生成并通过结构检查 + 可执行自动检查"
                    if _has_ai_answer(bk) else "尚无模型生成的答案",
        "说明": "本答案册与网页答案、在线评分读取同一份已保存答案",
    }
    try:
        info = render_booklet(bk.title or "AI 参考答案", meta, bk.sections or [],
                              out)
    except Exception as e:                          # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}
    bk.pdf_rel_path = cfg.storage_ref(out)
    bk.pdf_bytes = info["pdf_bytes"]
    bk.pdf_generated_at = E.utcnow()
    bk.updated_at = E.utcnow()
    db.commit()
    return {"ok": True, "pdf_bytes": info["pdf_bytes"],
            "pages": info["pages"], "pdf_rel_path": bk.pdf_rel_path}


def _has_ai_answer(bk: E.AnswerBooklet) -> bool:
    for sec in bk.sections or []:
        for q in sec.get("questions", []):
            if q.get("answer_origin", "").startswith(("deepseek", "ai")):
                return True
    return False


# --------------------------------------------------------------- reveals
def record_reveal(db: Session, up: E.MaterialUpload, question_ids: list[str],
                  channel: str) -> dict:
    """Called ONLY when the user really opens the answers or downloads the PDF."""
    n = 0
    for qid in question_ids:
        q = answer_pipeline.latest_question(db, qid)
        if not q:
            continue
        row = db.execute(select(E.AnswerReveal).where(
            E.AnswerReveal.user_id == up.user_id,
            E.AnswerReveal.question_id == qid)).scalars().first()
        if row:
            chans = list(row.channels or [])
            if channel not in chans:
                chans.append(channel)
            row.channels = chans
            row.reveal_count += 1
            row.last_revealed_at = E.utcnow()
            row.question_version = q.question_version
        else:
            db.add(E.AnswerReveal(
                reveal_id=E.new_id("rev"), user_id=up.user_id,
                question_id=qid, question_version=q.question_version,
                channels=[channel], reveal_count=1, upload_id=up.upload_id))
            n += 1
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=up.user_id,
        event_type="answer_revealed", target_id=up.upload_id,
        payload={"channel": channel, "questions": len(question_ids),
                 "new_reveals": n,
                 "note": "自动生成答案不计为已见；此处是用户实际查看/下载"}))
    db.commit()
    return {"channel": channel, "recorded": len(question_ids), "new": n}


# ------------------------------------------------------- original paper quiz
def assemble_original_paper(db: Session, up: E.MaterialUpload,
                            user_id: str = USER_ID) -> dict:
    """Build (or reuse) a paper that keeps the ORIGINAL order and scores."""
    if up.paper_id:
        p = db.get(E.Paper, up.paper_id)
        if p:
            return {"paper_id": p.paper_id, "reused": True,
                    "item_count": p.item_count, "total_score": p.total_score,
                    "coverage_report": p.coverage_report}
    items: list[tuple[E.Question, dict]] = []
    pending: list[str] = []
    for qid in up.question_order or []:
        q = answer_pipeline.latest_question(db, qid)
        if not q:
            continue
        meta = answer_pipeline.answer_meta_for(db, q.question_id,
                                               q.question_version)
        snap = bank_service.question_snapshot(q, answer_meta=meta)
        items.append((q, snap))
        if not answer_pipeline.is_answer_ready(q):
            pending.append(qid)
    if not items:
        raise ValueError("该资料没有可作答的题目")

    paper_id = E.new_id("paper")
    total = round(sum(q.score for q, _ in items), 2)
    coverage = {
        "source": "original_upload",
        "original_upload_id": up.upload_id,
        "keep_original_order": True,
        "positions_followed": f"1..{len(items)}（按原卷分区与题号）",
        "total_score": total,
        "answer_pending": pending,
        "answers_fixed": not pending,
        "note": ("题目、顺序与分值均取自原卷；评分依据复用已保存的答案与评分要点"
                 if not pending else
                 f"{len(pending)} 道题尚无可用答案，这些题本次不自动评分"),
    }
    p = E.Paper(paper_id=paper_id, user_id=user_id,
                title=f"{Path(up.filename).stem} · 原卷", mode="quiz",
                source="original_upload",
                blueprint={"original_upload_id": up.upload_id,
                           "keep_original_order": True},
                total_score=total, item_count=len(items),
                estimated_minutes=round(sum(q.estimated_minutes for q, _ in items), 1),
                coverage_report=coverage)
    db.add(p)
    for i, (q, snap) in enumerate(items, start=1):
        s = dict(snap)
        s["score"] = q.score
        s["position"] = i
        s["answer_fixed_at"] = E.utcnow()
        db.add(E.PaperItem(item_id=E.new_id("pi"), paper_id=paper_id,
                           position=i, question_id=q.question_id,
                           question_version=q.question_version,
                           score=q.score, snapshot=s))
    up.paper_id = paper_id
    db.commit()
    return {"paper_id": paper_id, "reused": False, "item_count": len(items),
            "total_score": total, "coverage_report": coverage}


# ---------------------------------------------------------------- DTO
def related_kps(db: Session, up: E.MaterialUpload, limit: int = 6) -> list[dict]:
    """考点 from the SAVED questions — never guessed from the filename."""
    from collections import Counter
    counter: Counter[str] = Counter()
    for qid in up.question_order or []:
        q = answer_pipeline.latest_question(db, qid)
        if q and q.main_kp_id:
            counter[q.main_kp_id] += 1
    if not counter:
        return []
    rows = {k.kp_id: k for k in db.execute(
        select(E.KnowledgePoint).where(
            E.KnowledgePoint.kp_id.in_(list(counter)))).scalars().all()}
    out = []
    for kp_id, n in counter.most_common(limit):
        kp = rows.get(kp_id)
        out.append({"kp_id": kp_id, "name": kp.name if kp else kp_id,
                    "question_count": n})
    return out


def upload_public(db: Session, up: E.MaterialUpload) -> dict:
    bk = db.execute(select(E.AnswerBooklet).where(
        E.AnswerBooklet.upload_id == up.upload_id)
        .order_by(E.AnswerBooklet.version.desc())).scalars().first()
    ready = up.answer_ready_count or 0
    has_questions = bool(up.question_order)
    return {
        "upload_id": up.upload_id,
        "filename": up.filename,
        # V2.2：统一显示名只用于页面展示，磁盘文件与 filename 均不改名。
        "display_name": material_naming.normalize(
            up.filename, up.resolved_type or "")["display_name"],
        "material_type": up.resolved_type,
        "material_type_label": TYPE_LABELS.get(up.resolved_type, up.resolved_type),
        "declared_type": up.declared_type,
        "detected_type": up.detected_type,
        "detect_reason": up.detect_reason,
        # V2.2：分类依据对前端可见，便于核对「是不是按内容判的」
        "classify_source": up.classify_source or "",
        "classify_source_label": {"rule": "内容规则", "model": "DeepSeek 判别",
                                  "user": "手动指定"}.get(
                                      up.classify_source or "", "未记录"),
        "classify_confidence": round(float(up.classify_confidence or 0), 2),
        "classify_needs_review": bool(up.classify_needs_review),
        "slides_question_count": up.slides_question_count or 0,
        "size_bytes": up.size_bytes,
        # V2.2：只用于前端决定「页面内预览」用 iframe / 文本 / 图片 / 下载，
        # 不参与资料类型判别（分类只看正文内容）。
        "mime": up.mime or "",
        "status": up.status,
        "status_label": STATUS_LABELS.get(up.status, up.status),
        "stage": up.stage,
        "progress": up.progress,
        "error": up.error,
        "document_id": up.document_id,
        "parse_status": up.parse_status,
        "question_count": up.question_count,
        "answer_ready_count": ready,
        "answer_pending_count": max(0, (up.question_count or 0) - ready),
        "booklet_id": up.booklet_id,
        "booklet_status": bk.status if bk else "",
        "booklet_total": bk.total_count if bk else 0,
        "booklet_answered": bk.answered_count if bk else 0,
        "booklet_version": bk.version if bk else 0,
        "booklet_generated_at": bk.generated_at if bk else "",
        "pdf_ready": bool(bk and bk.pdf_rel_path),
        "pdf_bytes": bk.pdf_bytes if bk else 0,
        "paper_id": up.paper_id,
        "deduped": up.deduped,
        "related_kps": related_kps(db, up),
        "created_at": up.created_at,
        "updated_at": up.updated_at,
        "actions": {
            "can_view_original": bool(up.document_id),
            "can_view_answers": bool(has_questions and bk and
                                     (bk.total_count or 0) > 0),
            "can_download_pdf": bool(bk and bk.pdf_rel_path),
            "can_start_quiz": has_questions,
            # V2 §5.4: generating is an explicit click, and it is only offered
            # when there is really something to generate.
            "can_generate_answers": bool(has_questions and
                                         up.resolved_type == "exam_paper" and
                                         ready < (up.question_count or 0)),
            "can_regenerate_answers": bool(has_questions and bk and
                                           (bk.total_count or 0) > 0),
        },
        # --- V2.3 学生端卡片：预览方式 / 下载 / 简介（全部来自真实文件）---
        "preview": _preview_info(db, up, has_questions),
        "intro": _intro(db, up),
        # 展示名去掉「类型 ·」前缀与年份；filename 保持原样
        "card_title": card_title(up.filename, up.resolved_type or ""),
        "download_url": f"/api/uploads/{up.upload_id}/original?download=1",
        "label_policy": answer_pipeline.ANSWER_DISCLAIMER,
    }


TEXT_EXTS = {".md", ".markdown", ".txt", ".rst", ".log"}
CODE_EXTS = {".py", ".ipynb", ".js", ".ts", ".java", ".c", ".cpp", ".h",
             ".sql", ".sh", ".json", ".yaml", ".yml", ".html", ".css"}


def _preview_info(db: Session, up: E.MaterialUpload, has_questions: bool) -> dict:
    """决定卡片怎么展示、哪些按钮可用。只依据真实文件与已生成内容。"""
    name = Path(up.filename or "")
    ext = name.suffix.lower()
    mime = (up.mime or "").lower()
    if ext == ".pdf" or mime == "application/pdf":
        kind = "pdf"
    elif mime.startswith("image/"):
        kind = "image"
    elif ext in CODE_EXTS:
        kind = "code"
    elif ext in TEXT_EXTS or mime.startswith("text/"):
        kind = "text"
    else:
        kind = "none"

    text_preview = ""
    if kind in ("text", "code") and up.stored_rel_path:
        path = cfg.resolve_storage_ref(up.stored_rel_path)
        if path.exists() and path.stat().st_size <= 512 * 1024:
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
                text_preview = "\n".join(
                    line for line in raw.splitlines()[:9] if line.strip())[:420]
            except OSError:
                text_preview = ""

    url = ""
    if kind == "pdf" and up.document_id:
        url = f"/api/materials/{up.document_id}/page/1/image"
    elif kind in ("image", "text", "code"):
        url = ""      # 图片用原文件直出，文本/代码用内容预览，不走页图
    return {
        "kind": kind,
        "image_url": url,
        "file_url": f"/api/uploads/{up.upload_id}/original",
        "text_preview": text_preview,
        "can_preview": bool(kind in ("pdf", "image", "text", "code")
                            and (url or text_preview or kind == "image")),
        "can_quiz": bool(has_questions),
        "can_view_code": kind == "code",
        "note": ("" if kind != "none" else
                 "该格式暂不支持在线预览，可下载后用本地应用打开"),
    }


def _intro(db: Session, up: E.MaterialUpload) -> str:
    """一句话简介：取已解析正文的第一段，取不到就留空，不编造。"""
    if not up.document_id:
        return ""
    chunk = db.execute(select(E.Chunk).where(
        E.Chunk.document_id == up.document_id)
        .order_by(E.Chunk.chunk_id)).scalars().first()
    if chunk is None:
        return ""
    text = (chunk.content_normalized or chunk.content_raw or "").strip()
    if not text:
        return ""
    # 去掉 markdown 标记与多余空白，取前 90 字
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"[>*`]|[*_]{1,2}", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:90] + ("…" if len(text) > 90 else "")


def card_title(filename: str, material_type: str) -> str:
    """学生端显示名：去掉「类型 ·」前缀与尾部年份，类型交给标签展示。

    磁盘文件与 `filename` 一律不改，只影响展示。
    """
    base = material_naming.normalize(filename, material_type or "")["display_name"]
    base = re.sub(r"^(老师课件|考试卷|考试试卷|笔记|复习笔记|实验代码|实验与代码|"
                  r"其他资料|其他)\s*[·•\-:：]\s*", "", base).strip()
    base = re.sub(r"[（(]\s*\d{4}(\s*[-–~]\s*\d{4})?\s*[)）]\s*$", "", base).strip()
    base = re.sub(r"[\s_\-]+$", "", base)
    return base or Path(filename).stem


def list_uploads(db: Session, limit: int = 100) -> dict:
    rows = db.execute(select(E.MaterialUpload)
                      .order_by(E.MaterialUpload.created_at.desc())
                      .limit(limit)).scalars().all()
    from collections import Counter
    items = [upload_public(db, r) for r in rows]
    # 学生端卡片右上角书签：一次查完，避免每张卡片单独查库
    favs = {f.target_id: f.entry_id for f in db.execute(select(E.Favorite).where(
        E.Favorite.user_id == USER_ID,
        E.Favorite.kind == "document")).scalars().all()}
    # 去掉前缀后可能出现同名，用序号区分，不合并也不改名
    taken: dict[str, int] = {}
    for item in items:
        doc_id = item.get("document_id") or ""
        item["favorited"] = bool(doc_id and doc_id in favs)
        item["favorite_entry_id"] = favs.get(doc_id, "")
        title = item["card_title"]
        taken[title] = taken.get(title, 0) + 1
        if taken[title] > 1:
            item["card_title"] = f"{title}（{taken[title]}）"
    return {"total": len(rows),
            "by_status": dict(Counter(r.status for r in rows)),
            "items": items,
            "note": "状态来自数据库；刷新或离开页面不会丢失任务"}


def retry_upload(db: Session, upload_id: str) -> dict:
    """Retry ONLY the unfinished part: never regenerates a succeeded answer.

    Idempotency is per *active* job, not per content: while a paper_ingest job
    for this upload is still queued/running we return that one (no duplicate
    work), but once it has finished a new retry MUST create a new job. Using a
    content-derived key would make the second retry silently reuse the finished
    job while the upload sits in "queued" forever.
    """
    up = db.get(E.MaterialUpload, upload_id)
    if not up:
        raise KeyError(upload_id)
    todo = []
    for qid in up.question_order or []:
        q = answer_pipeline.latest_question(db, qid)
        if not q or not answer_pipeline.is_answer_ready(q):
            todo.append(qid)
    if not todo:
        return {"upload_id": upload_id, "status": up.status,
                "retried": 0, "note": "所有题目都已有可用答案，无需重试"}
    active = db.execute(select(E.Job).where(
        E.Job.job_type == "paper_ingest",
        E.Job.status.in_(("queued", "running")))).scalars().all()
    for j in active:
        if (j.payload or {}).get("upload_id") == upload_id:
            up.job_id = j.job_id
            db.commit()
            return {"upload_id": upload_id, "retried": len(todo),
                    "job_id": j.job_id, "status": up.status, "reused": True,
                    "note": "已有正在排队/运行的处理任务，未重复创建；"
                            "该任务只会处理尚未完成的题目"}
    up.status = "queued"
    up.stage = "queued"
    up.error = ""
    db.commit()
    from backend.app.workers.worker import create_job
    job = create_job(db, "paper_ingest",
                     {"upload_id": upload_id, "retry_only": True},
                     up.user_id,
                     idempotency_key=f"ingest-retry:{upload_id}:"
                                     f"{E.new_id('rk')}")
    up.job_id = job["job_id"]
    db.commit()
    return {"upload_id": upload_id, "retried": len(todo),
            "job_id": job["job_id"], "status": up.status, "reused": False,
            "note": "只重试尚未完成的题目；已成功的答案不会重新生成"}


# ------------------------------------------------- answers on demand (§5.4)
def request_answers(db: Session, upload_id: str, *,
                    regenerate: bool = False) -> dict:
    """User clicked 「AI 生成答案解析 PDF」 (or 「重新生成」).

    Nothing is generated inside the HTTP request: a job is queued so the click
    returns immediately and the page can show real progress. Re-clicking while
    the job is alive reuses it instead of paying twice; `regenerate` bumps the
    booklet version once the new answers are saved.
    """
    up = db.get(E.MaterialUpload, upload_id)
    if not up:
        raise KeyError(upload_id)
    if not up.question_order:
        raise ValueError("该资料尚未识别出题目，无法生成答案解析")
    if up.resolved_type != "exam_paper":
        raise ValueError(f"资料类型为「{TYPE_LABELS.get(up.resolved_type)}」，"
                         "不做试卷答案解析")

    # A live job for THIS upload wins over everything: no duplicate spend.
    live = db.execute(select(E.Job).where(
        E.Job.job_type == "answer_booklet",
        E.Job.status.in_(("queued", "running")))).scalars().all()
    for j in live:
        if (j.payload or {}).get("upload_id") == upload_id:
            up.job_id = j.job_id
            db.commit()
            return {"upload_id": upload_id, "job_id": j.job_id,
                    "reused": True, "status": up.status,
                    "note": "已有正在排队/运行的答案解析任务，未重复创建"}

    bk = db.execute(select(E.AnswerBooklet).where(
        E.AnswerBooklet.upload_id == upload_id)
        .order_by(E.AnswerBooklet.version.desc())).scalars().first()
    if bk and not regenerate and (bk.total_count or 0) > 0 \
            and bk.answered_count >= up.answer_ready_count > 0 \
            and bk.pdf_rel_path:
        return {"upload_id": upload_id, "reused": True, "job_id": "",
                "status": up.status, "booklet_id": bk.booklet_id,
                "note": "该试卷已有可用答案解析；如需重新生成请点「重新生成」"}

    up.status = "queued"
    up.stage = "answer_queued"
    up.error = ""
    up.progress = {"stage": "answer_queued", "stage_status": "queued",
                   "updated_at": E.utcnow(),
                   "detail": {"questions": up.question_count,
                              "regenerate": bool(regenerate),
                              "note": "已加入答案解析队列；"
                                      "进度以数据库状态为准，可离开页面"},
                   "steps": []}
    db.commit()

    from backend.app.workers.worker import create_job
    job = create_job(db, "answer_booklet",
                     {"upload_id": upload_id,
                      "regenerate": bool(regenerate)},
                     up.user_id,
                     idempotency_key=f"answers:{upload_id}:"
                                     f"{E.new_id('ak')}")
    up.job_id = job["job_id"]
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=up.user_id,
        event_type="material_answers_requested", target_id=upload_id,
        payload={"regenerate": bool(regenerate), "job_id": job["job_id"],
                 "questions": up.question_count,
                 "note": "用户点击生成答案解析，仅此时才调用模型"}))
    db.commit()
    return {"upload_id": upload_id, "job_id": job["job_id"], "reused": False,
            "status": up.status, "regenerate": bool(regenerate),
            "note": "已开始生成 AI 答案解析；进度以数据库状态为准"}


def run_answer_job(db: Session, upload_id: str, *,
                   regenerate: bool = False) -> dict:
    """Worker entry point for the `answer_booklet` job."""
    up = db.get(E.MaterialUpload, upload_id)
    if not up:
        raise KeyError(upload_id)
    steps: list[dict] = [{"stage": "resume", "status": "ok",
                          "note": "从已保存的题目清单继续生成答案解析",
                          "regenerate": bool(regenerate)}]
    return _answer_stage(db, up, steps, regenerate=regenerate)
