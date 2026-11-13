"""Retrieval service.

Current mode: REAL keyword retrieval over normalized chunk text, with
chapter / knowledge-point filters. It supports Chinese and English terms
(NFKC-normalized compatibility ideographs are indexed separately).

Vector mode is NOT claimed: it requires an embedding provider config and a
built index. `retrieval_status()` reports the true mode so the UI can say so.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.models import entities as E


def retrieval_status() -> dict:
    configured = bool(cfg.EMBED_PROVIDER and cfg.EMBED_MODEL and cfg.EMBED_API_KEY)
    index_file = cfg.INDEX_DIR / "chroma"
    return {
        "mode": "keyword",
        "vector_enabled": False,
        "vector_status": ("configured_but_index_not_built" if configured
                          else "embedding_not_configured"),
        "index_built": index_file.exists(),
        "embedding_model": cfg.EMBED_MODEL or None,
        "embedding_dim": cfg.EMBED_DIM or None,
        "note": ("当前为真实关键词检索（中英文术语 + 章节/知识点过滤）。"
                 "未配置嵌入模型或未构建索引时，不宣称语义检索可用。"),
    }


def tokenize(q: str) -> list[str]:
    q = q.strip()
    if not q:
        return []
    # Latin words / numbers kept whole; CJK runs are also matched as a whole
    # and additionally split into bigrams for partial matching.
    tokens = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", q)
    out: list[str] = []
    for t in tokens:
        out.append(t.lower())
        if re.fullmatch(r"[\u4e00-\u9fff]+", t) and len(t) >= 3:
            out.extend(t[i:i + 2] for i in range(len(t) - 1))
    return list(dict.fromkeys(out))


def keyword_search(db: Session, q: str, *, kp_ids: list[str] | None = None,
                   chapter: str = "", limit: int = 12,
                   include_flagged: bool = True) -> dict:
    tokens = tokenize(q)
    if not tokens:
        return {"mode": "keyword", "query": q, "results": [], "total": 0,
                "message": "请输入检索词"}
    stmt = select(E.Chunk)
    if chapter:
        stmt = stmt.where(E.Chunk.chapter == chapter)
    rows = db.execute(stmt).scalars().all()
    scored: list[tuple[float, E.Chunk, list[str]]] = []
    for c in rows:
        text = (c.content_normalized or c.content_raw or "").lower()
        title = (c.title or "").lower()
        if not text:
            continue
        if kp_ids and not (set(c.kp_ids or []) & set(kp_ids)):
            # kp filter is advisory for now: chunks mostly unlabelled
            if not any(k.lower() in text for k in kp_ids):
                continue
        if not include_flagged and c.review_status != "auto_ok":
            continue
        hits = [t for t in tokens if t in text]
        if not hits:
            continue
        score = sum(3.0 if len(t) > 3 else 1.0 for t in hits)
        score += sum(2.0 for t in tokens if t in title)
        if c.kind in ("question", "cross_page_question"):
            score += 0.5
        scored.append((score, c, hits))
    scored.sort(key=lambda x: (-x[0], x[1].chunk_id))
    results = []
    for score, c, hits in scored[:limit]:
        raw = c.content_raw or ""
        snippet = " ".join(raw.split())[:280]
        results.append({
            "chunk_id": c.chunk_id,
            "document_id": c.document_id,
            "document_version_id": c.document_version_id,
            "title": c.title,
            "kind": c.kind,
            "score": round(score, 2),
            "matched_terms": hits[:8],
            "snippet": snippet,
            "unit_refs": c.unit_refs,
            "source_refs": c.source_refs,
            "assets": c.assets,
            "review_status": c.review_status,
            "quality": c.quality,
            "chapter": c.chapter,
        })
    return {
        "mode": "keyword", "query": q, "tokens": tokens,
        "total": len(scored), "returned": len(results),
        "index_scanned": len(rows), "results": results,
        "status": retrieval_status(),
    }
