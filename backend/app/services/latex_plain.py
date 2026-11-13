"""Render LaTeX math as readable plain text for the PDF booklet.

The web view renders `$...$` with KaTeX. The PDF writer is a line-based text
renderer with an embedded CJK font, so raw LaTeX would print as
`$w^{\\top}x+b=0$` — technically faithful, practically unreadable.

This module is a *presentation* transform only: the stored answer, the rubric
and the scoring pipeline keep the original LaTeX untouched. It favours
readability over typographic perfection and never invents content.
"""
from __future__ import annotations

import re

GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι",
    "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π",
    "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
SYMBOLS = {
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠",
    "approx": "≈", "equiv": "≡", "sim": "~", "propto": "∝", "infty": "∞",
    "times": "×", "div": "÷", "cdot": "·", "pm": "±", "mp": "∓",
    "sum": "Σ", "prod": "Π", "int": "∫", "partial": "∂", "nabla": "∇",
    "forall": "∀", "exists": "∃", "in": "∈", "notin": "∉", "subset": "⊂",
    "cup": "∪", "cap": "∩", "to": "→", "rightarrow": "→", "leftarrow": "←",
    "Rightarrow": "⇒", "Leftrightarrow": "⇔", "mapsto": "↦",
    "top": "ᵀ", "perp": "⊥", "mid": "|", "vert": "|", "Vert": "‖",
    "ldots": "…", "cdots": "…", "dots": "…", "circ": "∘", "ast": "∗",
    "star": "⋆", "ell": "ℓ", "hbar": "ℏ", "Re": "ℜ", "Im": "ℑ",
    "odot": "⊙", "otimes": "⊗", "oplus": "⊕", "oslash": "⊘",
    "langle": "⟨", "rangle": "⟩", "lVert": "‖", "rVert": "‖",
    "lvert": "|", "rvert": "|", "setminus": "∖", "triangleq": "≜",
    "leqslant": "≤", "geqslant": "≥", "varnothing": "∅",
}
BLACKBOARD = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "E": "E"}
SUP = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶",
       "7": "⁷", "8": "⁸", "9": "⁹", "n": "ⁿ", "i": "ⁱ", "k": "ᵏ", "m": "ᵐ",
       "+": "⁺", "-": "⁻", "−": "⁻", "(": "⁽", ")": "⁾", "T": "ᵀ", "t": "ᵀ",
       "ᵀ": "ᵀ", "*": "*"}
SUB = {"0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆",
       "7": "₇", "8": "₈", "9": "₉", "i": "ᵢ", "j": "ⱼ", "k": "ₖ", "n": "ₙ",
       "m": "ₘ", "t": "ₜ", "x": "ₓ", "a": "ₐ", "e": "ₑ", "o": "ₒ",
       "l": "ₗ", "s": "ₛ", "p": "ₚ"}
HATS = {"hat": "\u0302", "bar": "\u0304", "tilde": "\u0303", "vec": "\u20d7"}
FONT_WRAPPERS = ("text", "mathrm", "mathbf", "mathit", "mathsf", "mathtt",
                 "boldsymbol", "operatorname", "textrm", "mbox")


def _big_operators(s: str) -> str:
    """\\sum_{i=1}^{n} -> Σ(i=1..n) so the limits stay readable in plain text."""
    pat = re.compile(
        r"\\(sum|prod|int|bigcup|bigcap)\s*(?:_\{([^{}]*)\})?\s*(?:\^\{([^{}]*)\})?")

    def repl(m: re.Match) -> str:
        sym = {"sum": "Σ", "prod": "Π", "int": "∫",
               "bigcup": "∪", "bigcap": "∩"}[m.group(1)]
        lo, hi = (m.group(2) or "").strip(), (m.group(3) or "").strip()
        if lo and hi:
            return f"{sym}({lo}..{hi})"
        if lo:
            return f"{sym}({lo})"
        if hi:
            return f"{sym}^({hi})"
        return sym

    s = pat.sub(repl, s)

    # \min_{w,b} / \max_{\alpha} -> "min(w,b) " so the following expression is
    # not glued to the operator name.
    def named(m: re.Match) -> str:
        body = (m.group(2) or "").strip()
        return f"{m.group(1)}{f'({body})' if body else ''} "

    return re.sub(
        r"\\(min|max|argmin|argmax|sup|inf|lim)\s*(?:_\{([^{}]*)\})?\s*",
        named, s)


def _accents(s: str) -> str:
    def repl(m: re.Match) -> str:
        return m.group(2) + HATS[m.group(1)]

    s = re.sub(r"\\(hat|bar|tilde|vec)\s*\{([^{}]*)\}", repl, s)
    # `\hat y` (no braces) is common in model output.
    return re.sub(r"\\(hat|bar|tilde|vec)\s+([A-Za-z])",
                  lambda m: m.group(2) + HATS[m.group(1)], s)
DROP = ("quad", "qquad", "left", "right", "big", "Big", "bigg", "Bigg",
        "displaystyle", "limits", "nolimits", "!", ",", ";", ":", " ")


def _strip_wrappers(s: str) -> str:
    """Unwrap \\text{...}, \\mathrm{...}, \\mathbf{...} and friends."""
    changed = True
    while changed:
        changed = False
        for name in FONT_WRAPPERS:
            pat = re.compile(r"\\" + name + r"\s*\{")
            m = pat.search(s)
            while m:
                # find the matching closing brace
                depth, i = 1, m.end()
                while i < len(s) and depth:
                    if s[i] == "{":
                        depth += 1
                    elif s[i] == "}":
                        depth -= 1
                    i += 1
                inner = s[m.end():i - 1] if depth == 0 else s[m.end():]
                s = s[:m.start()] + inner + s[i:]
                changed = True
                m = pat.search(s)
    return s


def _frac(s: str) -> str:
    pat = re.compile(r"\\(?:d|t)?frac\s*\{")
    m = pat.search(s)
    while m:
        def grab(start: int) -> tuple[str, int] | None:
            depth, i = 1, start
            while i < len(s) and depth:
                if s[i] == "{":
                    depth += 1
                elif s[i] == "}":
                    depth -= 1
                i += 1
            return (s[start:i - 1], i) if depth == 0 else None
        a = grab(m.end())
        if not a:
            break
        b_start = a[1]
        while b_start < len(s) and s[b_start] in " \n":
            b_start += 1
        if b_start >= len(s) or s[b_start] != "{":
            break
        b = grab(b_start + 1)
        if not b:
            break
        num, den = a[0].strip(), b[0].strip()
        if re.fullmatch(r"[\w.]+", num):
            num = num
        else:
            num = f"({num})"
        if re.fullmatch(r"[\w.]+", den):
            den = den
        else:
            den = f"({den})"
        s = s[:m.start()] + f"{num}/{den}" + s[b[1]:]
        m = pat.search(s)
    return s


def _sqrt(s: str) -> str:
    s = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"√(\1)", s)
    return re.sub(r"\\sqrt\s*\[([^\]]*)\]\s*\{([^{}]*)\}", r"\1√(\2)", s)


def _sup_of(body: str) -> str:
    body = body.strip()
    if body in SUP:
        return SUP[body]
    mapped = "".join(SUP.get(c, "") for c in body)
    if mapped and len(mapped) == len(body):
        return mapped
    # `^{(l)}` must not become `^((l))`
    return f"^{body}" if body.startswith("(") and body.endswith(")") \
        else f"^({body})"


def _sub_of(body: str) -> str:
    body = body.strip()
    if body in SUB:
        return SUB[body]
    mapped = "".join(SUB.get(c, "") for c in body)
    return mapped if mapped and len(mapped) == len(body) else f"_({body})"


def _scripts(s: str) -> str:
    """x^{2} -> x², x_{i} -> xᵢ; fall back to ^(...) / _(...) for long bodies."""
    s = re.sub(r"\^\s*\{([^{}]*)\}", lambda m: _sup_of(m.group(1)), s)
    s = re.sub(r"\^\s*([A-Za-z0-9])", lambda m: _sup_of(m.group(1)), s)
    s = re.sub(r"_\s*\{([^{}]*)\}", lambda m: _sub_of(m.group(1)), s)
    s = re.sub(r"_\s*([A-Za-z0-9])", lambda m: _sub_of(m.group(1)), s)
    return s


def _matrix(s: str) -> str:
    def repl(m: re.Match) -> str:
        env, body = m.group(1), m.group(2)
        open_c, close_c = ("[", "]") if "bmatrix" in env else \
            ("{", "}") if "Bmatrix" in env else ("(", ")") if "pmatrix" in env \
            else ("|", "|") if "vmatrix" in env else ("", "")
        body = body.replace("\\\\", "; ")
        body = re.sub(r"&", ", ", body)
        body = re.sub(r"\s+", " ", body).strip()
        return f"{open_c}{body}{close_c}"

    return re.sub(r"\\begin\{(\w*matrix\*?)\}([\s\S]*?)\\end\{\1\}", repl, s)


def _align(s: str) -> str:
    s = re.sub(r"\\begin\{aligned\}([\s\S]*?)\\end\{aligned\}", r"\1", s)
    s = re.sub(r"\\begin\{array\}\{[^}]*\}([\s\S]*?)\\end\{array\}", r"\1", s)
    s = re.sub(r"\\begin\{cases\}([\s\S]*?)\\end\{cases\}", r"\1", s)
    s = re.sub(r"\\end\{[a-z*]+\}", "", s)
    s = re.sub(r"\\begin\{[a-z*]+\}", "", s)
    return s.replace("\\\\", "；").replace("&", " ")


def _math_body(s: str) -> str:
    s = _strip_wrappers(s)
    s = _matrix(s)
    s = _align(s)
    s = _accents(s)
    s = _big_operators(s)
    s = _frac(s)
    s = _sqrt(s)
    for k, v in BLACKBOARD.items():
        s = re.sub(r"\\mathbb\s*\{?" + k + r"\}?", v, s)
    s = re.sub(r"\\mathcal\s*\{([^{}]*)\}", r"\1", s)
    for name in sorted(GREEK, key=len, reverse=True):
        s = re.sub(r"\\" + name + r"(?![A-Za-z])", GREEK[name], s)
    for name in sorted(SYMBOLS, key=len, reverse=True):
        s = re.sub(r"\\" + name + r"(?![A-Za-z])", SYMBOLS[name], s)
    s = _scripts(s)          # after symbols, so ^{ᵀ} keeps its glyph
    s = s.replace("\\quad", "  ").replace("\\qquad", "    ")
    s = s.replace("\\ ", " ").replace("\\,", " ").replace("\\;", " ")
    for d in DROP:
        s = s.replace("\\" + d, " " if d in ("quad", "qquad") else "")
    s = s.replace("\\{", "{").replace("\\}", "}").replace("\\|", "‖")
    s = s.replace("\\\\", "；")
    s = s.replace("&", " ")
    s = re.sub(r"\\[A-Za-z]+", lambda m: m.group(0)[1:], s)   # unknown cmd -> name
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\\\s*$", "", s)                             # dangling row break
    return s.strip()


def _tidy(s: str) -> str:
    s = s.replace("^T", "ᵀ")
    s = re.sub(r"\s+([，。；、）)\]])", r"\1", s)
    s = re.sub(r"([（\[(])\s+", r"\1", s)
    return s


def to_plain(text: str) -> str:
    """Convert LaTeX in a message to readable plain text for the PDF booklet."""
    if not text:
        return ""
    s = str(text)

    # Display math becomes its own indented block so long derivations stay legible.
    def display(m: re.Match) -> str:
        body = _tidy(_math_body(m.group(1)))
        return "\n    " + body + "\n"

    s = re.sub(r"\$\$([\s\S]+?)\$\$", display, s)
    s = re.sub(r"\\\[([\s\S]+?)\\\]", display, s)

    def inline(m: re.Match) -> str:
        return _tidy(_math_body(m.group(1)))

    s = re.sub(r"\$([^$\n]+?)\$", inline, s)
    s = re.sub(r"\\\((.+?)\\\)", inline, s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    # Markdown emphasis/heading markers are noise in a text-only PDF.
    s = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", s)
    s = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", s)
    s = re.sub(r"`([^`\n]+)`", r"\1", s)
    return s.strip()
