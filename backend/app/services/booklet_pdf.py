"""Answer-booklet PDF rendering (CJK-capable, zero extra dependencies).

PyMuPDF is already a project dependency for parsing, and it can also write
PDFs. Chinese text needs an embedded TrueType font: Windows ships `simhei.ttf`
and `Deng.ttf`, so we embed one of those rather than relying on a CJK CID font.

The renderer ONLY formats whatever the caller passes in. It never computes or
re-derives answers, so the PDF cannot disagree with the web view or scoring.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from backend.app.services.latex_plain import to_plain

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\Deng.ttf",
    r"C:\Windows\Fonts\simkai.ttf",
    r"C:\Windows\Fonts\simfang.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)

A4_W, A4_H = 595.0, 842.0
MARGIN = 54.0
LINE_RATIO = 1.62

# The embedded CJK font has no superscript/subscript letters and no ∂/∇/⇒/ℝ/ℒ.
# A missing glyph silently disappears from the rendered page (`wᵀx` printed as
# `w x`), so every character is checked against the font and replaced by an
# ASCII equivalent when needed. Verified against simhei.ttf/Deng.ttf.
_ASCII_FALLBACK = {
    "ᵀ": "^T", "ᵗ": "^t", "⁰": "^0", "¹": "^1", "²": "^2", "³": "^3",
    "⁴": "^4", "⁵": "^5", "⁶": "^6", "⁷": "^7", "⁸": "^8", "⁹": "^9",
    "ⁱ": "^i", "ⁿ": "^n", "⁺": "+", "⁻": "-", "⁽": "(", "⁾": ")",
    "₀": "_0", "₁": "_1", "₂": "_2", "₃": "_3", "₄": "_4", "₅": "_5",
    "₆": "_6", "₇": "_7", "₈": "_8", "₉": "_9", "ᵢ": "_i", "ⱼ": "_j",
    "ₖ": "_k", "ₗ": "_l", "ₘ": "_m", "ₙ": "_n", "ₚ": "_p", "ₛ": "_s",
    "ₜ": "_t", "ₓ": "_x", "ₐ": "_a", "ₑ": "_e", "ₒ": "_o",
    "∂": "d", "∇": "grad ", "⇒": "=>", "⇔": "<=>", "ℝ": "R", "ℕ": "N",
    "ℤ": "Z", "ℚ": "Q", "ℂ": "C", "ℒ": "L", "ℰ": "E", "ℓ": "l",
    "ŷ": "y^", "x̂": "x^", "ŵ": "w^", "ā": "a-", "∀": "for all ",
    "∃": "exists ", "̂": "", "̄": "", "̃": "", "⃗": "",
    "∗": "*", "⋆": "*", "≪": "<<", "≫": ">>", "≡": "==", "~": "~",
    # Glyphs simhei.ttf really lacks (checked with Font.has_glyph):
    "−": "-", "𝔼": "E", "⊗": "(x)", "⟨": "<", "⟩": ">", "⌈": "ceil(",
    "⌉": ")", "⌊": "floor(", "⌋": ")", "ϕ": "φ", "⋯": "...", "⋮": "...",
    "≜": ":=", "≔": ":=", "∖": "\\", "⌀": "0", "⊘": "/", "∅": "0",
}


def font_path() -> str | None:
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


class _Writer:
    """Line-oriented writer with automatic page breaks and CJK wrapping."""

    def __init__(self, fontfile: str | None):
        self.doc = pymupdf.open()
        self.fontfile = fontfile
        self.font = (pymupdf.Font(fontfile=fontfile) if fontfile
                     else pymupdf.Font("helv"))
        self._glyph_cache: dict[str, bool] = {}
        self._dropped: set[str] = set()
        self.page = None
        self.y = 0.0
        self.page_no = 0
        self._new_page()

    def _safe(self, text: str) -> str:
        """Replace characters the embedded font cannot draw."""
        out: list[str] = []
        for ch in text:
            if ch in "\n\t ":
                out.append(ch)
                continue
            ok = self._glyph_cache.get(ch)
            if ok is None:
                try:
                    ok = bool(self.font.has_glyph(ord(ch)))
                except Exception:                  # noqa: BLE001
                    ok = True
                self._glyph_cache[ch] = ok
            if ok:
                out.append(ch)
                continue
            repl = _ASCII_FALLBACK.get(ch)
            if repl is None:
                self._dropped.add(ch)
                repl = "?"
            out.append(repl)
        return "".join(out)

    def _new_page(self) -> None:
        self.page = self.doc.new_page(width=A4_W, height=A4_H)
        self.page_no += 1
        if self.fontfile:
            self.page.insert_font(fontname="cn", fontfile=self.fontfile)
        self.y = MARGIN
        # footer
        self._footer()

    def _footer(self) -> None:
        label = f"- {self.page_no} -"
        size = 8.5
        w = self.font.text_length(label, size)
        self.page.insert_text(((A4_W - w) / 2, A4_H - 28), label,
                              fontname="cn" if self.fontfile else "helv",
                              fontsize=size, color=(0.55, 0.55, 0.55))

    def _ensure(self, height: float) -> None:
        if self.y + height > A4_H - MARGIN - 14:
            self._new_page()

    def _wrap(self, text: str, size: float, width: float) -> list[str]:
        lines: list[str] = []
        cur = ""
        for ch in text:
            if ch == "\n":
                lines.append(cur)
                cur = ""
                continue
            if not cur:
                cur = ch
                continue
            if self.font.text_length(cur + ch, size) <= width:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        return lines or [""]

    def para(self, text: str, *, size: float = 10.5, indent: float = 0.0,
             color=(0.1, 0.1, 0.1), gap: float = 4.0) -> None:
        if text is None:
            return
        text = self._safe(str(text))
        width = A4_W - 2 * MARGIN - indent
        lh = size * LINE_RATIO
        for line in self._wrap(text, size, width):
            self._ensure(lh)
            self.page.insert_text((MARGIN + indent, self.y + size), line,
                                  fontname="cn" if self.fontfile else "helv",
                                  fontsize=size, color=color)
            self.y += lh
        self.y += gap

    def keep_together(self, height: float) -> None:
        """把接下来 height 点内容留在同一页，避免标题落在页脚。"""
        self._ensure(height)

    def image(self, path: str | Path, *, max_width: float | None = None,
              max_height: float | None = None, gap: float = 8.0,
              caption: str = "") -> bool:
        """插入一张真实图片（按比例缩放，不拉伸、不超出页面）。

        统一导入的中文版需要保留原 PDF 的图表：这里是唯一的贴图入口，
        图片尺寸只缩小不放大，避免把小图糊成马赛克。
        """
        try:
            pix = pymupdf.Pixmap(str(path))
        except Exception:                              # noqa: BLE001
            return False
        w_px, h_px = pix.width, pix.height
        if not w_px or not h_px:
            return False
        avail_w = max_width or (A4_W - 2 * MARGIN)
        avail_h = max_height or (A4_H - 2 * MARGIN - 80)
        scale = min(avail_w / w_px, avail_h / h_px, 1.0)
        w, h = w_px * scale, h_px * scale
        if caption:
            self._ensure(h + gap + 16)
            self.para(caption, size=8.5, color=(0.5, 0.5, 0.5), gap=2)
        else:
            self._ensure(h + gap)
        rect = pymupdf.Rect(MARGIN, self.y, MARGIN + w, self.y + h)
        self.page.insert_image(rect, filename=str(path))
        self.y += h + gap
        return True

    def rule(self, gap: float = 8.0) -> None:
        self._ensure(gap)
        self.page.draw_line((MARGIN, self.y), (A4_W - MARGIN, self.y),
                            color=(0.82, 0.82, 0.82), width=0.6)
        self.y += gap

    def save(self, out: Path) -> int:
        out.parent.mkdir(parents=True, exist_ok=True)
        # Subset the embedded CJK font: without this a Chinese answer booklet
        # carries the whole 10 MB typeface and the download is unusable.
        try:
            self.doc.subset_fonts()
        except Exception:                             # noqa: BLE001 - older PyMuPDF
            pass
        self.doc.save(str(out), garbage=4, deflate=True, clean=True)
        self.doc.close()
        return out.stat().st_size


def render_booklet(booklet_title: str, meta: dict, sections: list[dict],
                   out_path: Path) -> dict:
    """Render the assembled booklet. `sections` is exactly AnswerBooklet.sections."""
    w = _Writer(font_path())
    w.para(booklet_title, size=19, gap=2)
    w.para("AI 参考答案 · 非教师或原试卷提供的标准答案", size=9.5,
           color=(0.72, 0.28, 0.18), gap=8)
    w.rule()
    for k, v in meta.items():
        if v in ("", None):
            continue
        w.para(f"{k}：{v}", size=9.5, color=(0.35, 0.35, 0.35), gap=1)
    w.y += 8

    for sec in sections:
        w._ensure(46)
        w.para(sec.get("section") or "（未分区的题目）", size=13.5, gap=2)
        w.rule(gap=7)
        for q in sec.get("questions", []):
            head = f"{q.get('no', '')}　{q.get('type_label', '')}　{q.get('score', '')} 分"
            w.para(head, size=10.5, color=(0.16, 0.32, 0.55), gap=2)
            # KaTeX renders the web view; the PDF is text-only, so LaTeX is
            # converted to readable Unicode math here (stored data untouched).
            w.para("【题目】" + to_plain(q.get("stem") or ""), size=10, indent=10,
                   gap=2)
            for o in q.get("options", []) or []:
                w.para(f"{o.get('key')}. {to_plain(o.get('text', ''))}",
                       size=9.8, indent=22, gap=1)
            if q.get("answered", True):
                ans = to_plain(q.get("answer_text") or "") or "（见网页）"
            else:
                reason = q.get("pending_note") or ""
                ans = "（尚未生成" + (f"：{reason}" if reason else "") + "）"
            label = q.get("answer_label") or ""
            w.para(f"【答案】{ans}" + (f"　［{label}］" if label else ""),
                   size=10.5, indent=10, color=(0.10, 0.42, 0.24), gap=2)
            if q.get("explanation"):
                w.para("【解析】" + to_plain(q["explanation"]), size=9.8,
                       indent=10, gap=2)
            points = q.get("rubric_points") or []
            if points:
                w.para("【评分要点】", size=9.8, indent=10, gap=1)
                for p in points:
                    if isinstance(p, dict):
                        txt = to_plain(p.get("point") or p.get("text")
                                       or p.get("desc") or "")
                        sc = p.get("score")
                        w.para(f"· {txt}" + (f"（{sc} 分）" if sc is not None else ""),
                               size=9.5, indent=22, gap=1)
                    else:
                        w.para(f"· {to_plain(str(p))}", size=9.5, indent=22, gap=1)
            w.y += 5
        w.y += 6

    size = w.save(out_path)
    return {"pdf_bytes": size, "pages": w.page_no, "path": str(out_path),
            "unsupported_chars": sorted(w._dropped)}


# 公开的排版器别名：组卷 PDF 复用同一套 CJK 字体与安全字符替换，
# 保证「答案册 PDF」与「试卷 PDF」的字体、页边距、页码完全一致。
Writer = _Writer


def render_text_pdf(title: str, lines: list[str], out_path: Path) -> dict:
    """Small helper used by verification scripts."""
    w = _Writer(font_path())
    w.para(title, size=17, gap=8)
    for ln in lines:
        w.para(ln, size=10.5, gap=2)
    size = w.save(out_path)
    return {"pdf_bytes": size, "pages": w.page_no, "path": str(out_path)}
