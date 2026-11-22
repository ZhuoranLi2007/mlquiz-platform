"""Validated teacher edits with immutable historical content."""
from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.models import entities as E
from backend.app.services import answer_pipeline, bank_service, chapter_mapping, paper_builder_service


def save(db: Session, fields: dict, actor: str, *, question_id: str = "",
         version: int | None = None) -> dict:
    db.execute(text("BEGIN IMMEDIATE"))
    old = answer_pipeline.latest_question(db, question_id) if question_id else None
    if question_id and old is None:
        raise KeyError(question_id)
    if old is not None and old.question_version != version:
        raise ValueError("题目已更新，请刷新后重新编辑")
    for kp_id in [fields["main_kp_id"], *fields["aux_kp_ids"]]:
        kp = db.get(E.KnowledgePoint, kp_id)
        if kp is None or kp.status != "active":
            raise ValueError("请选择有效的知识点")
    fields["aux_kp_ids"] = list(dict.fromkeys(
        kp for kp in fields["aux_kp_ids"] if kp != fields["main_kp_id"]))
    valid, reason = answer_pipeline.answer_structurally_valid(SimpleNamespace(**fields))
    if not valid:
        raise ValueError(reason)
    fingerprint = paper_builder_service._normalized_stem(fields["stem"])
    if not fingerprint:
        raise ValueError("题干需要包含有效内容")
    stems = db.execute(select(E.Question.stem).where(
        E.Question.active.is_(True), E.Question.question_id != question_id)).scalars()
    if any(paper_builder_service._normalized_stem(stem) == fingerprint for stem in stems):
        raise ValueError("已有相同题干，请编辑现有题目")
    if old is None or old.main_kp_id != fields["main_kp_id"]:
        main = db.get(E.KnowledgePoint, fields["main_kp_id"])
        chapters = list(db.scalars(select(E.Chapter)))
        fields["chapter_id"], _ = chapter_mapping.infer(
            chapters, {main.kp_id: main}, main.kp_id, fields["stem"])
    if old is not None:
        return bank_service.revise_question(db, question_id, fields,
                                            actor_user_id=actor, previous=old)
    question_id = E.new_id("q")
    db.add(E.Question(row_id=question_id, question_id=question_id,
                      question_version=1, origin="teacher", active=True,
                      review_status="verified", answer_status="verified",
                      answer_origin="teacher", question_family_id=E.new_id("fam"),
                      created_by=actor, **fields))
    db.commit()
    return {"question_id": question_id, "new_version": 1}


def archive(db: Session, question_id: str, version: int) -> dict:
    db.execute(text("BEGIN IMMEDIATE"))
    question = answer_pipeline.latest_question(db, question_id)
    if question is None:
        raise KeyError(question_id)
    if question.question_version != version:
        raise ValueError("题目已更新，请刷新后重新操作")
    question.active = False
    db.commit()
    return {"question_id": question_id, "active": False}
