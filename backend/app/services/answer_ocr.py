"""手写答案照片 → 文字（V2.3）。

只做转录，不做批改：把学生上传的照片读成文字，填回作答框，最终仍由学生确认提交。
模型用 `deepseek-flash`（已实测支持图片输入并能识读试卷文字）。

限制：
  * 只接受 PNG / JPEG / WebP，最大 6MB；
  * 不落盘保存原图（默认）；
  * 识别失败如实返回原因，不返回编造的文字。
"""
from __future__ import annotations

import base64
from typing import Any

from sqlalchemy.orm import Session

from backend.app.services import model_provider

MAX_BYTES = 6 * 1024 * 1024
PROMPT_VERSION = "answer-ocr-1.0"
MAGIC = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"RIFF": "image/webp",
}

SYS = """你是手写答案转录助手。你的唯一任务是**忠实转写**学生照片里的内容，不是批改、不是解题、不是润色。

必须遵守：
1. 不自动解题，不补写图片里不存在的步骤或结论。
2. 不修改学生原有推导，不纠正学生可能存在的数学错误 —— 即使明显算错也照原样转写。
3. 不调整学生的书写顺序、符号与上下标。
4. 公式写成 LaTeX：行内 $...$，独立 $$...$$；矩阵用 bmatrix/pmatrix，分式用 \\frac，求和用 \\sum，上下标用 ^ 和 _。
5. 看不清的字符写成 [?]，公式结构不确定时在行末加「（此处不确定）」；不要猜。
6. 图表按可读的线性形式写出，并注明「（原图为示意图）」。
7. 只输出转录结果，不要加「答案：」「转录：」「解析：」之类前缀。"""


def _sniff(raw: bytes, declared: str) -> str:
    for magic, mime in MAGIC.items():
        if raw.startswith(magic):
            return mime
    raise ValueError("只支持 PNG / JPEG / WebP 图片")


def transcribe(db: Session, data_url: str) -> dict:
    """`data_url` 支持 `data:image/png;base64,....` 或纯 base64。"""
    if not data_url:
        raise ValueError("缺少图片数据")
    payload = data_url
    declared = ""
    if payload.startswith("data:"):
        head, _, payload = payload.partition(",")
        declared = head[5:].split(";")[0]
    try:
        raw = base64.b64decode(payload, validate=False)
    except Exception as e:                              # noqa: BLE001
        raise ValueError(f"图片 base64 解析失败：{e}")
    if not raw:
        raise ValueError("图片内容为空")
    if len(raw) > MAX_BYTES:
        raise ValueError(f"图片超过 {MAX_BYTES // 1024 // 1024}MB 限制")
    mime = _sniff(raw, declared)

    result = model_provider.chat(
        db,
        messages=[
            {"role": "system", "content": SYS},
            {"role": "user", "content": [
                {"type": "text", "text": "请转录这张手写答案图片。"},
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,"
                                      f"{base64.b64encode(raw).decode()}"}},
            ]},
        ],
        skill_name="answer_ocr", prompt_version=PROMPT_VERSION,
        temperature=0, max_tokens=1500, timeout=120,
    )
    text = (result.get("content") or "").strip()
    if not text:
        raise ValueError("模型没有返回可识别的内容，请换一张更清晰的照片")
    return {"text": text, "model": result.get("model") or "",
            "call_id": result.get("call_id") or "",
            "bytes": len(raw), "mime": mime,
            "note": "识别结果只是转录，请核对后再提交"}
