"""Deterministic parser for the 2025 classroom quiz DOCX.

The teacher document is source evidence, not an instruction stream.  This
module therefore reads the OOXML package directly and never edits it.  It is
deliberately independent from the ORM so an import can be audited before any
database write happens.

Important properties of the parser:

* Word automatic numbering (``numbering.xml``) and literal question numbers
  are both supported.
* Paragraph fragments preserve text/drawing order.
* ``mc:AlternateContent`` uses a PNG fallback for display.  In particular,
  MathJax SVGs containing ``currentColor`` are not exposed directly because
  they render blank in the current Word/browser path and SVG is active content.
* Media is copied one file at a time to digest-based names; ``extractall`` is
  never used.  SVG is recorded but not exported by default.  EMF is retained
  as attachment-only evidence and is marked as needing conversion for web UI.
* IDs are based on quiz identity and question number.  A content hash is a
  duplicate signal only: duplicate questions remain separate records.
* Core-properties author fields are reported as redacted field names; their
  values are never returned.

The returned dictionaries contain only JSON-compatible values.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import posixpath
import re
import tempfile
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping
from xml.etree import ElementTree as ET


FORMAT_VERSION = "classroom-docx-1.0"
MAX_DOCX_BYTES = 100 * 1024 * 1024
MAX_XML_BYTES = 12 * 1024 * 1024
MAX_MEDIA_BYTES = 24 * 1024 * 1024
MAX_PACKAGE_UNCOMPRESSED = 240 * 1024 * 1024

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
V_NS = "urn:schemas-microsoft-com:vml"
CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DCTERMS_NS = "http://purl.org/dc/terms/"


def _q(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"


W = lambda name: _q(W_NS, name)  # noqa: E731 - compact namespace helpers
R = lambda name: _q(R_NS, name)  # noqa: E731
MC = lambda name: _q(MC_NS, name)  # noqa: E731
WP = lambda name: _q(WP_NS, name)  # noqa: E731


class ClassroomDocxError(ValueError):
    """Raised when a DOCX package cannot be parsed safely."""


QUIZ_SLUG_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("network-ml", ("网络机器学习",)),
    ("linear-regression", ("线性回归",)),
    ("logistic-lda", ("logistic", "lda")),
    ("neural-network", ("神经网络",)),
    ("svm", ("支持向量机", "svm")),
    ("model-evaluation", ("模型评估",)),
    ("bayes", ("贝叶斯",)),
    ("ensemble-learning", ("集成学习",)),
    ("clustering", ("聚类",)),
    ("matrix-factorization", ("矩阵分解", "pca", "nmf")),
)

QUIZ_TITLES = {
    "linear-regression": "线性回归",
    "logistic-lda": "Logistic回归与LDA",
    "neural-network": "神经网络",
    "svm": "支持向量机",
    "model-evaluation": "机器学习模型评估",
    "bayes": "贝叶斯分类",
    "network-ml": "网络机器学习",
    "ensemble-learning": "集成学习",
    "clustering": "聚类",
    "matrix-factorization": "矩阵分解",
}

EXPECTED_QUESTION_COUNTS = {
    "linear-regression": 4,
    "logistic-lda": 9,
    "neural-network": 4,
    "svm": 8,
    "model-evaluation": 7,
    "bayes": 5,
    "network-ml": 6,
    "ensemble-learning": 7,
    "clustering": 5,
    "matrix-factorization": 5,
}


def _mapped(kp_id: str, reason: str, *, confidence: str = "high",
            aux: Iterable[str] = ()) -> dict[str, Any]:
    return {
        "kp_id": kp_id,
        "aux_kp_ids": list(aux),
        "candidate_kp_ids": [],
        "status": "mapped",
        "confidence": confidence,
        "reason": reason,
    }


def _insufficient(reason: str, *candidates: str) -> dict[str, Any]:
    return {
        "kp_id": "",
        "aux_kp_ids": [],
        "candidate_kp_ids": [x for x in candidates if x],
        "status": "evidence_insufficient",
        "confidence": "low",
        "reason": reason,
    }


# Every entry below uses an ID seeded by backend/scripts/build_bank.py.  A blank
# kp_id is intentional: the current course ontology has no precise equivalent.
_KP_MAP: dict[str, dict[int, dict[str, Any]]] = {
    "linear-regression": {
        1: _insufficient("现有知识点没有通用的回归与分类输出类型考点", "kp.logistic.boundary"),
        2: _mapped("kp.linear.normal_eq", "正规方程与梯度下降的求解方式比较"),
        3: _mapped("kp.linear.mse", "线性回归均方误差与模型输出"),
        4: _insufficient("训练与在线推理资源并非现有泛化知识点的精确含义",
                         "kp.intro.generalization"),
    },
    "logistic-lda": {
        1: _insufficient("现有知识点没有通用的回归与分类输出类型考点", "kp.logistic.boundary"),
        2: _mapped("kp.logistic.cost", "Logistic 二元交叉熵与线性回归 MSE 对比",
                   aux=("kp.linear.mse",)),
        3: _mapped("kp.logistic.boundary", "Logistic 回归的分类器性质"),
        4: _mapped("kp.logistic.boundary", "sigmoid 与线性决策边界"),
        5: _mapped("kp.logistic.gradient", "sigmoid 导数"),
        6: _insufficient("偏置项 x0=1 的增广表示没有精确的现有知识点",
                         "kp.logistic.gradient"),
        7: _mapped("kp.lda.idea", "投影后类间距离与类内紧密度"),
        8: _mapped("kp.lda.objective", "LDA 比值目标与拉格朗日求解"),
        9: _insufficient("训练与在线推理资源并非现有泛化知识点的精确含义",
                         "kp.intro.generalization"),
    },
    "neural-network": {
        1: _mapped("kp.nn.activation", "神经元、感知器与非线性激活"),
        2: _insufficient("全连接层权重矩阵尺寸没有精确的现有知识点", "kp.nn.bp"),
        3: _mapped("kp.nn.bp", "反向传播的链式法则"),
        4: _mapped("kp.nn.bp", "误差项与权重梯度"),
    },
    "svm": {
        1: _mapped("kp.svm.margin", "硬间隔 SVM 的适用范围"),
        2: _mapped("kp.svm.margin", "最大间隔目标与线性可分约束"),
        3: _mapped("kp.svm.margin", "硬间隔原始优化问题"),
        4: _mapped("kp.svm.kkt", "拉格朗日函数构造"),
        5: _mapped("kp.svm.kkt", "最大最小对偶"),
        6: _mapped("kp.svm.kkt", "二次规划与 KKT 条件"),
        7: _mapped("kp.svm.kkt", "由对偶变量恢复最优 w"),
        8: _mapped("kp.svm.soft_margin", "软间隔、Hinge 损失与松弛变量"),
    },
    "model-evaluation": {
        1: _mapped("kp.eval.cv", "分类混淆矩阵属于现有评估指标范围", confidence="medium"),
        2: _mapped("kp.eval.cv", "查准率属于现有评估指标范围", confidence="medium"),
        3: _mapped("kp.eval.cv", "召回率属于现有评估指标范围", confidence="medium"),
        4: _mapped("kp.eval.cv", "F1 属于现有评估指标范围", confidence="medium"),
        5: _mapped("kp.eval.cv", "ROC/AUC 属于现有评估指标范围", confidence="medium"),
        6: _mapped("kp.eval.cv", "训练、验证、测试数据划分"),
        7: _mapped("kp.eval.cv", "多折交叉验证与 Bootstrap"),
    },
    "bayes": {
        1: _mapped("kp.logistic.boundary", "现有知识点明确包含判别式与生成式对比",
                   confidence="medium"),
        2: _mapped("kp.bayes.naive", "朴素贝叶斯的生成式性质"),
        3: _mapped("kp.bayes.naive", "给定类别下的条件独立分解"),
        4: _insufficient("MLE 的样本独立假设没有精确的现有知识点", "kp.bayes.formula"),
        5: _insufficient("训练与测试同分布假设没有精确的现有知识点",
                         "kp.intro.generalization"),
    },
    "network-ml": {
        1: _insufficient("图数据表示与归纳问题没有精确的现有知识点", "kp.network.rwr"),
        2: _insufficient("节点、链接、图级任务没有精确的现有知识点", "kp.network.rwr"),
        3: _mapped("kp.network.rwr", "带重启随机游走与同质性正则"),
        4: _insufficient("邻接矩阵稀疏性没有精确的现有知识点", "kp.network.rwr"),
        5: _mapped("kp.network.rwr", "疾病基因预测中的重启种子"),
        6: _insufficient("图卷积邻域聚合没有精确的现有知识点", "kp.network.rwr"),
    },
    "ensemble-learning": {
        1: _mapped("kp.ensemble.forest", "基学习器准确性与多样性", confidence="medium"),
        2: _insufficient("投票集成没有精确的现有知识点", "kp.ensemble.forest"),
        3: _mapped("kp.ensemble.forest", "样本与属性随机化带来的多样性"),
        4: _mapped("kp.ensemble.adaboost", "AdaBoost 权重更新与指数损失"),
        5: _insufficient("信息熵与交叉熵没有精确的现有知识点", "kp.logistic.cost"),
        6: _mapped("kp.ensemble.forest", "Bagging、Bootstrap 与随机森林"),
        7: _insufficient("Stacking 元学习器没有精确的现有知识点", "kp.ensemble.forest"),
    },
    "clustering": {
        1: _insufficient("聚类的一般定义与评价没有精确的现有知识点", "kp.cluster.kmeans"),
        2: _mapped("kp.cluster.kmeans", "K-means 目标与内部外部评价", confidence="medium"),
        3: _mapped("kp.cluster.kmeans", "原型聚类比较", confidence="medium",
                   aux=("kp.cluster.dbscan",)),
        4: _insufficient("距离、相似度与度量公理没有精确的现有知识点",
                         "kp.cluster.kmeans"),
        5: _mapped("kp.cluster.dbscan", "密度聚类及其他聚类类型", confidence="medium"),
    },
    "matrix-factorization": {
        1: _mapped("kp.pca.variance", "PCA 数据中心化"),
        2: _mapped("kp.pca.variance", "主成分投影与可视化"),
        3: _mapped("kp.pca.variance", "PCA 压缩、去相关与方差最大化"),
        4: _mapped("kp.pca.variance", "迹目标、正交约束与特征分解"),
        5: _mapped("kp.nmf.loss", "NMF 分解、软聚类与重构损失"),
    },
}


def map_question_knowledge_point(quiz: str, question_number: int,
                                 stem: str = "") -> dict[str, Any]:
    """Map a teacher-DOCX question to existing project ``kp_id`` values.

    ``quiz`` may be a title, a quiz ID, or a canonical slug.  The function is
    deterministic and never invents a new knowledge point.  When the current
    ontology is too coarse it returns an empty ``kp_id`` and explicitly marks
    ``evidence_insufficient``.
    """
    slug = canonical_quiz_slug(quiz)
    rule = _KP_MAP.get(slug, {}).get(int(question_number))
    if rule is None:
        rule = _insufficient("没有可核验的按题号映射")
    out = {**rule}
    out["quiz_slug"] = slug
    out["question_number"] = int(question_number)
    out["mapping_basis"] = "teacher_docx_section_question"
    if stem:
        out["stem_evidence_hash"] = hashlib.sha256(
            normalize_stem_for_comparison(stem).encode("utf-8")
        ).hexdigest()[:16]
    return out


def canonical_quiz_slug(title: str) -> str:
    """Return a stable quiz slug for Word or Excel quiz labels."""
    raw = unicodedata.normalize("NFKC", str(title or "")).casefold()
    canonical = raw.strip()
    for slug in QUIZ_TITLES:
        if canonical == slug or canonical.endswith(f"-{slug}"):
            return slug
    compact = re.sub(r"[\s_.·:：/\\\-—–]+", "", raw)
    for slug, markers in QUIZ_SLUG_RULES:
        if any(marker.casefold() in compact for marker in markers):
            # ``线性回归`` also appears in other contexts; known logistic/LDA
            # labels must win when both are present.
            if slug == "linear-regression" and (
                    "logistic" in compact or "lda" in compact):
                continue
            return slug
    digest = hashlib.sha256(compact.encode("utf-8")).hexdigest()[:12]
    return f"unknown-{digest}"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_part(zf: zipfile.ZipFile, name: str, limit: int) -> bytes:
    try:
        info = zf.getinfo(name)
    except KeyError as exc:
        raise ClassroomDocxError(f"DOCX 缺少必要部件: {name}") from exc
    if info.file_size > limit:
        raise ClassroomDocxError(f"DOCX 部件过大: {name}")
    data = zf.read(info)
    if len(data) != info.file_size:
        raise ClassroomDocxError(f"DOCX 部件读取不完整: {name}")
    return data


def _parse_xml(data: bytes, part_name: str) -> ET.Element:
    prefix = data[:4096].upper()
    if b"<!DOCTYPE" in prefix or b"<!ENTITY" in prefix:
        raise ClassroomDocxError(f"拒绝包含 DTD/ENTITY 的 XML: {part_name}")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ClassroomDocxError(f"XML 无法解析: {part_name}") from exc


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _clean_text(value: Any, max_length: int = 2000) -> str:
    text = str(value or "").replace("\x00", "")
    text = "".join(ch for ch in text if ch in "\t\n\r" or ord(ch) >= 32)
    return text.strip()[:max_length]


def _safe_media_part(target: str) -> str | None:
    target = str(target or "").replace("\\", "/")
    if not target or target.startswith(("/", "file:", "http:", "https:")):
        return None
    part = posixpath.normpath(posixpath.join("word", target))
    if part.startswith("../") or not part.startswith("word/media/"):
        return None
    path = PurePosixPath(part)
    if ".." in path.parts or len(path.parts) != 3:
        return None
    return part


def _mime_for(ext: str) -> str:
    return {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
        "svg": "image/svg+xml", "emf": "image/emf", "wmf": "image/wmf",
    }.get(ext, "application/octet-stream")


def _valid_signature(ext: str, data: bytes) -> bool:
    if ext == "png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in {"jpg", "jpeg"}:
        return data.startswith(b"\xff\xd8\xff")
    if ext == "gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if ext == "bmp":
        return data.startswith(b"BM")
    if ext == "webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if ext == "emf":
        return (len(data) >= 44 and data[:4] == b"\x01\x00\x00\x00"
                and data[40:44] == b" EMF")
    if ext == "wmf":
        return len(data) >= 4 and data[:4] in {b"\xd7\xcd\xc6\x9a", b"\x01\x00\x09\x00"}
    return False


def _build_media_registry(zf: zipfile.ZipFile) -> dict[str, dict[str, Any]]:
    assets: dict[str, dict[str, Any]] = {}
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if not name.startswith("word/media/"):
            continue
        if info.file_size > MAX_MEDIA_BYTES:
            raise ClassroomDocxError(f"媒体文件过大: {name}")
        data = zf.read(info)
        digest = hashlib.sha256(data).hexdigest()
        ext = PurePosixPath(name).suffix.lower().lstrip(".")
        svg_text = data[:2_000_000].decode("utf-8", "ignore") if ext == "svg" else ""
        active_svg = bool(re.search(
            r"<script\b|javascript:|(?:href|src)\s*=\s*['\"]https?://",
            svg_text, flags=re.I))
        uses_current_color = ext == "svg" and "currentColor" in svg_text
        browser_displayable = ext in {"png", "jpg", "jpeg", "gif", "bmp", "webp"}
        exportable = ext in {"png", "jpg", "jpeg", "gif", "bmp", "webp", "emf", "wmf"}
        assets[name] = {
            "asset_id": f"media-{digest[:16]}",
            "package_part": name,
            "source_name": PurePosixPath(name).name,
            "sha256": digest,
            "bytes": len(data),
            "extension": ext,
            "mime_type": _mime_for(ext),
            "browser_displayable": browser_displayable,
            "requires_conversion": ext in {"emf", "wmf"},
            "uses_current_color": uses_current_color,
            "active_svg_features": active_svg,
            "safe_path": None,
            "extraction_status": ("pending" if exportable else
                                  "blocked_active_vector" if ext == "svg" else
                                  "unsupported_format"),
            "serving_policy": ("inline" if browser_displayable else
                               "attachment_only" if ext in {"emf", "wmf"} else
                               "do_not_serve"),
            "_data": data,
        }
    return assets


def _export_safe_media(assets: dict[str, dict[str, Any]], output_dir: Path) -> None:
    root = output_dir.resolve()
    media_dir = (root / "media").resolve()
    if root != media_dir and root not in media_dir.parents:
        raise ClassroomDocxError("媒体输出目录越界")
    media_dir.mkdir(parents=True, exist_ok=True)
    for asset in assets.values():
        if asset["extraction_status"] != "pending":
            continue
        data = asset["_data"]
        ext = asset["extension"]
        if not _valid_signature(ext, data):
            asset["extraction_status"] = "invalid_signature"
            asset["serving_policy"] = "do_not_serve"
            continue
        filename = f"{asset['asset_id']}.{ext}"
        destination = (media_dir / filename).resolve()
        if media_dir not in destination.parents:
            raise ClassroomDocxError("媒体文件名越界")
        if destination.exists():
            existing = hashlib.sha256(destination.read_bytes()).hexdigest()
            if existing != asset["sha256"]:
                raise ClassroomDocxError(f"媒体目标冲突: {filename}")
        else:
            fd, temp_name = tempfile.mkstemp(prefix=f".{filename}.", dir=media_dir)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, destination)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        asset["safe_path"] = f"media/{filename}"
        asset["extraction_status"] = "extracted"


def _public_asset(asset: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in asset.items() if not k.startswith("_")}


def _parse_relationships(zf: zipfile.ZipFile) -> tuple[dict[str, dict[str, str]], int]:
    data = _read_part(zf, "word/_rels/document.xml.rels", MAX_XML_BYTES)
    root = _parse_xml(data, "word/_rels/document.xml.rels")
    relationships: dict[str, dict[str, str]] = {}
    external_count = 0
    for node in root:
        rid = node.attrib.get("Id", "")
        if not rid:
            continue
        if node.attrib.get("TargetMode") == "External":
            external_count += 1
            continue
        relationships[rid] = {
            "target": node.attrib.get("Target", ""),
            "type": node.attrib.get("Type", "").rsplit("/", 1)[-1],
        }
    return relationships, external_count


class _Numbering:
    def __init__(self, zf: zipfile.ZipFile):
        self.levels: dict[tuple[str, int], dict[str, Any]] = {}
        self.counters: dict[tuple[str, int], int] = {}
        try:
            data = _read_part(zf, "word/numbering.xml", MAX_XML_BYTES)
        except ClassroomDocxError:
            return
        root = _parse_xml(data, "word/numbering.xml")
        abstract: dict[str, dict[int, dict[str, Any]]] = {}
        for abs_node in root.findall(W("abstractNum")):
            aid = abs_node.attrib.get(W("abstractNumId"), "")
            by_level: dict[int, dict[str, Any]] = {}
            for level in abs_node.findall(W("lvl")):
                ilvl = int(level.attrib.get(W("ilvl"), "0") or 0)
                start = level.find(W("start"))
                num_fmt = level.find(W("numFmt"))
                lvl_text = level.find(W("lvlText"))
                by_level[ilvl] = {
                    "start": int(start.attrib.get(W("val"), "1")) if start is not None else 1,
                    "format": num_fmt.attrib.get(W("val"), "decimal") if num_fmt is not None else "decimal",
                    "text": lvl_text.attrib.get(W("val"), "%1.") if lvl_text is not None else "%1.",
                }
            abstract[aid] = by_level
        for num in root.findall(W("num")):
            num_id = num.attrib.get(W("numId"), "")
            abs_ref = num.find(W("abstractNumId"))
            aid = abs_ref.attrib.get(W("val"), "") if abs_ref is not None else ""
            for ilvl, config in abstract.get(aid, {}).items():
                self.levels[(num_id, ilvl)] = dict(config)
            for override in num.findall(W("lvlOverride")):
                ilvl = int(override.attrib.get(W("ilvl"), "0") or 0)
                start = override.find(W("startOverride"))
                if start is not None and (num_id, ilvl) in self.levels:
                    self.levels[(num_id, ilvl)]["start"] = int(
                        start.attrib.get(W("val"), "1"))

    def next_decimal(self, num_id: str, level: int) -> tuple[int, str] | None:
        config = self.levels.get((num_id, level))
        if not config or config.get("format") != "decimal":
            return None
        key = (num_id, level)
        number = self.counters.get(key, int(config.get("start", 1)))
        self.counters[key] = number + 1
        # Incrementing a parent list restarts deeper levels.
        for other in [k for k in self.counters if k[0] == num_id and k[1] > level]:
            self.counters.pop(other, None)
        display = str(config.get("text", "%1.")).replace(f"%{level + 1}", str(number))
        return number, display


def _core_metadata(zf: zipfile.ZipFile) -> dict[str, Any]:
    try:
        root = _parse_xml(_read_part(zf, "docProps/core.xml", MAX_XML_BYTES),
                          "docProps/core.xml")
    except ClassroomDocxError:
        return {"safe": {}, "redacted_fields": [], "redacted_count": 0,
                "privacy_status": "no_core_metadata"}
    safe_names = {"created", "modified", "revision"}
    safe: dict[str, str] = {}
    redacted: list[str] = []
    for child in root:
        local = _local(child.tag)
        value = _clean_text(child.text, 300)
        if not value:
            continue
        if local in safe_names:
            safe[local] = value
        else:
            redacted.append(local)
    return {
        "safe": safe,
        "redacted_fields": sorted(set(redacted)),
        "redacted_count": len(set(redacted)),
        "privacy_status": "redacted" if redacted else "clean",
    }


def _drawing_fragment(node: ET.Element, *, alternate_scope: ET.Element | None,
                      paragraph_index: int, sequence: int,
                      relationships: Mapping[str, Mapping[str, str]],
                      assets: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    scope = alternate_scope if alternate_scope is not None else node
    refs: list[dict[str, str]] = []
    seen_parts: set[str] = set()
    for elem in scope.iter():
        rid = elem.attrib.get(R("embed")) or elem.attrib.get(R("link"))
        relation = relationships.get(rid or "")
        if not relation:
            continue
        part = _safe_media_part(relation.get("target", ""))
        if not part or part not in assets or part in seen_parts:
            continue
        seen_parts.add(part)
        refs.append({"relationship_id": str(rid), "package_part": part,
                     "asset_id": str(assets[part]["asset_id"])})

    selected_part = ""
    reason = "no_media_reference"
    png_parts = [r["package_part"] for r in refs
                 if assets[r["package_part"]]["extension"] == "png"]
    svg_parts = [r["package_part"] for r in refs
                 if assets[r["package_part"]]["extension"] == "svg"]
    raster_parts = [r["package_part"] for r in refs
                    if assets[r["package_part"]]["browser_displayable"]]
    non_svg_parts = [r["package_part"] for r in refs
                     if assets[r["package_part"]]["extension"] != "svg"]
    if png_parts and svg_parts:
        selected_part = png_parts[0]
        if any(assets[p]["uses_current_color"] for p in svg_parts):
            reason = "png_fallback_for_svg_current_color"
        else:
            reason = "png_fallback_for_portable_display"
    elif raster_parts:
        selected_part = raster_parts[0]
        reason = "browser_safe_raster"
    elif non_svg_parts:
        selected_part = non_svg_parts[0]
        reason = "non_browser_vector_requires_conversion"
    elif svg_parts:
        selected_part = svg_parts[0]
        reason = "svg_recorded_but_not_exported"

    selected = assets.get(selected_part, {})
    extent_node = next(iter(node.iter(WP("extent"))), None)
    extent = None
    if extent_node is not None:
        try:
            extent = [int(extent_node.attrib.get("cx", "0")),
                      int(extent_node.attrib.get("cy", "0"))]
        except ValueError:
            extent = None
    doc_pr = next(iter(node.iter(WP("docPr"))), None)
    height = extent[1] if extent else 0
    role = "formula" if 0 < height <= 700_000 else "figure"
    return {
        "type": "media",
        "drawing_id": f"draw-p{paragraph_index:03d}-{sequence:02d}",
        "selected_asset_id": selected.get("asset_id", ""),
        "display_path": selected.get("safe_path"),
        "selection_reason": reason,
        "source_refs": refs,
        "alternative_asset_ids": [r["asset_id"] for r in refs
                                  if r["package_part"] != selected_part],
        "extent_emu": extent,
        "role": role,
        "browser_displayable": bool(selected.get("browser_displayable")),
        "requires_conversion": bool(selected.get("requires_conversion")),
        "name": _clean_text(doc_pr.attrib.get("name") if doc_pr is not None else "", 300),
        "alt_text": _clean_text(doc_pr.attrib.get("descr") if doc_pr is not None else "", 500),
        "title": _clean_text(doc_pr.attrib.get("title") if doc_pr is not None else "", 500),
    }


def _parse_paragraph(node: ET.Element, paragraph_index: int,
                     relationships: Mapping[str, Mapping[str, str]],
                     assets: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    fragments: list[dict[str, Any]] = []
    drawing_sequence = 0

    def walk(elem: ET.Element, alternate_scope: ET.Element | None = None) -> None:
        nonlocal drawing_sequence
        if elem.tag == W("del"):
            return
        if elem.tag == W("t"):
            if elem.text:
                fragments.append({"type": "text", "text": elem.text})
            return
        if elem.tag == W("tab"):
            fragments.append({"type": "text", "text": "\t"})
            return
        if elem.tag in {W("br"), W("cr")}:
            fragments.append({"type": "text", "text": "\n"})
            return
        if elem.tag in {W("drawing"), W("pict")}:
            drawing_sequence += 1
            fragments.append(_drawing_fragment(
                elem, alternate_scope=alternate_scope,
                paragraph_index=paragraph_index, sequence=drawing_sequence,
                relationships=relationships, assets=assets))
            return
        if elem.tag == MC("AlternateContent"):
            fallback = elem.find(MC("Fallback"))
            choices = elem.findall(MC("Choice"))
            branch = fallback if fallback is not None else (choices[0] if choices else None)
            if branch is not None:
                for child in list(branch):
                    walk(child, elem)
            return
        for child in list(elem):
            walk(child, alternate_scope)

    walk(node)
    texts = [str(f["text"]) for f in fragments if f["type"] == "text"]
    rendered = "".join(
        str(f["text"]) if f["type"] == "text"
        else f"[[MEDIA:{f.get('selected_asset_id') or 'missing'}]]"
        for f in fragments)
    ppr = node.find(W("pPr"))
    style_id = ""
    num_id = ""
    level = 0
    if ppr is not None:
        style = ppr.find(W("pStyle"))
        style_id = style.attrib.get(W("val"), "") if style is not None else ""
        num_pr = ppr.find(W("numPr"))
        if num_pr is not None:
            num = num_pr.find(W("numId"))
            ilvl = num_pr.find(W("ilvl"))
            num_id = num.attrib.get(W("val"), "") if num is not None else ""
            try:
                level = int(ilvl.attrib.get(W("val"), "0")) if ilvl is not None else 0
            except ValueError:
                level = 0
    text = "".join(texts).strip()
    return {
        "block_type": "paragraph",
        "paragraph_index": paragraph_index,
        "style_id": style_id,
        "numbering": ({"num_id": num_id, "level": level} if num_id else None),
        "text": text,
        "rendered_text": rendered.strip(),
        "fragments": fragments,
        "media": [f for f in fragments if f["type"] == "media"],
        "hidden": any(elem.tag == W("vanish") for elem in node.iter()),
    }


_DIRECT_QUESTION_RE = re.compile(r"^\s*(\d+)\s*[.．、]\s*")
_OPTION_RE = re.compile(r"^\s*[（(]?\s*([A-HＡ-Ｈ])\s*[）)]\s*", re.I)
_DUPLICATE_MARKER_RE = re.compile(r"\s*[（(]\s*重复\s*[）)]\s*")
_SHARED_RANGE_RE = re.compile(
    r"回答\s*第?\s*(\d+)\s*[~～\-—–至]\s*(\d+)\s*题"
)


def _remove_direct_number(text: str) -> str:
    return _DIRECT_QUESTION_RE.sub("", text, count=1).strip()


def _remove_duplicate_marker(text: str) -> str:
    return _DUPLICATE_MARKER_RE.sub(" ", text).strip()


def _option_from_block(block: Mapping[str, Any]) -> dict[str, Any] | None:
    match = _OPTION_RE.match(str(block.get("text") or ""))
    if not match:
        return None
    key = unicodedata.normalize("NFKC", match.group(1)).upper()
    return {
        "key": key,
        "text": str(block.get("text") or "")[match.end():].strip(),
        "source_paragraph": block.get("paragraph_index"),
        "fragments": block.get("fragments", []),
        "media": block.get("media", []),
    }


def _infer_question_type(stem: str, preamble: str, quiz_slug: str,
                         number: int) -> str:
    text = unicodedata.normalize("NFKC", stem)
    if "多选题" in text:
        return "multi_choice"
    if "单选题" in text:
        return "single_choice"
    if "填空题" in text:
        return "fill_blank"
    if "是非题" in text or "判断题" in text:
        return "true_false"
    if quiz_slug == "svm" and 3 <= number <= 7:
        return "matching"
    if "是非" in preamble or "判断" in preamble:
        return "true_false"
    return "unknown"


def _question_media(question: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    blocks = [question["stem_block"], *question.get("body_blocks", [])]
    for block in blocks:
        out.extend(block.get("media", []))
    for option in question.get("options", []):
        out.extend(option.get("media", []))
    return out


def _canonical_content(value: str) -> str:
    value = _remove_duplicate_marker(_remove_direct_number(value))
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\u00a0", " ").replace("\u3000", " ")
    value = re.sub(r"\s+", " ", value).strip()
    # Sentence punctuation is presentation-only for duplicate detection.  The
    # supplied Word source contains the same T/F item once with ``。其中`` and
    # once with ``，其中``; keeping punctuation in the content hash would hide
    # that duplicate.  Mathematical punctuation/operators remain untouched.
    return re.sub(r"[，。；：、,.!?！？]", "", value)


def _question_content_hash(question: Mapping[str, Any],
                           assets_by_id: Mapping[str, Mapping[str, Any]]) -> str:
    media_hashes = []
    for media in _question_media(question):
        asset = assets_by_id.get(str(media.get("selected_asset_id") or ""))
        if asset:
            media_hashes.append(asset.get("sha256"))
    payload = {
        "question_type": question.get("question_type"),
        "stem": _canonical_content(str(question.get("stem") or "")),
        "body": [_canonical_content(str(b.get("text") or ""))
                 for b in question.get("body_blocks", [])],
        "options": [{"key": o.get("key"),
                     "text": _canonical_content(str(o.get("text") or ""))}
                    for o in question.get("options", [])],
        "media_sha256": media_hashes,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_quiz_heading(block: Mapping[str, Any]) -> bool:
    text = str(block.get("text") or "")
    if not text or len(text) > 100:
        return False
    if block.get("style_id") in {"1", "Heading1", "heading 1"} and "2025" in text:
        return True
    return text.startswith("2025") and "秋" in text and canonical_quiz_slug(text).startswith(
        tuple(QUIZ_TITLES))


def _move_shared_materials(quiz: dict[str, Any]) -> None:
    by_number = {q["number"]: q for q in quiz["questions"]}
    for question in quiz["questions"]:
        body = question.get("body_blocks", [])
        for index, block in enumerate(body):
            match = _SHARED_RANGE_RE.search(str(block.get("text") or ""))
            if not match:
                continue
            first, last = int(match.group(1)), int(match.group(2))
            moved = body[index:]
            question["body_blocks"] = body[:index]
            material_id = f"{quiz['quiz_id']}-shared-q{first:02d}-q{last:02d}"
            target_ids = [by_number[n]["question_id"] for n in range(first, last + 1)
                          if n in by_number]
            quiz["shared_materials"].append({
                "shared_material_id": material_id,
                "applies_to_numbers": list(range(first, last + 1)),
                "applies_to_question_ids": target_ids,
                "blocks": moved,
                "media": [m for b in moved for m in b.get("media", [])],
            })
            for target_id in target_ids:
                target = next(q for q in quiz["questions"]
                              if q["question_id"] == target_id)
                target.setdefault("shared_material_ids", []).append(material_id)
            break


def _parse_quizzes(blocks: list[dict[str, Any]], numbering: _Numbering,
                   assets: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    quizzes: list[dict[str, Any]] = []
    current_quiz: dict[str, Any] | None = None
    current_question: dict[str, Any] | None = None
    assets_by_id = {str(a["asset_id"]): a for a in assets.values()}

    for block in blocks:
        text = str(block.get("text") or "")
        if _is_quiz_heading(block):
            slug = canonical_quiz_slug(text)
            quiz_id = f"teacher-2025-fall-{slug}"
            current_quiz = {
                "quiz_id": quiz_id,
                "quiz_slug": slug,
                "title": text,
                "display_title": QUIZ_TITLES.get(slug, text),
                "source_heading_paragraph": block["paragraph_index"],
                "preamble_blocks": [],
                "questions": [],
                "shared_materials": [],
            }
            quizzes.append(current_quiz)
            current_question = None
            continue
        if current_quiz is None:
            continue

        number: int | None = None
        display_number = ""
        direct = _DIRECT_QUESTION_RE.match(text)
        if direct:
            number = int(direct.group(1))
            display_number = direct.group(0).strip()
        elif block.get("numbering"):
            info = block["numbering"]
            generated = numbering.next_decimal(str(info["num_id"]), int(info["level"]))
            if generated:
                number, display_number = generated

        if number is not None:
            source_stem = _remove_direct_number(text) if direct else text.strip()
            stem = _remove_duplicate_marker(source_stem)
            preamble = "\n".join(b.get("text", "")
                                  for b in current_quiz["preamble_blocks"])
            qid = f"{current_quiz['quiz_id']}-q{number:02d}"
            current_question = {
                "question_id": qid,
                "quiz_id": current_quiz["quiz_id"],
                "quiz_slug": current_quiz["quiz_slug"],
                "number": number,
                "display_number": display_number,
                "source_stem": source_stem,
                "stem": stem,
                "duplicate_marker_in_source": bool(_DUPLICATE_MARKER_RE.search(source_stem)),
                "question_type": _infer_question_type(
                    stem, preamble, current_quiz["quiz_slug"], number),
                "stem_block": block,
                "body_blocks": [],
                "options": [],
                "shared_material_ids": [],
                "source_locator": {
                    "heading_paragraph": current_quiz["source_heading_paragraph"],
                    "start_paragraph": block["paragraph_index"],
                    "end_paragraph": block["paragraph_index"],
                },
                "knowledge_point": map_question_knowledge_point(
                    current_quiz["quiz_slug"], number, stem),
            }
            current_quiz["questions"].append(current_question)
            continue

        if current_question is None:
            if text or block.get("media"):
                current_quiz["preamble_blocks"].append(block)
            continue
        if not text and not block.get("media"):
            continue
        current_question["source_locator"]["end_paragraph"] = block["paragraph_index"]
        option = _option_from_block(block)
        if option:
            current_question["options"].append(option)
        else:
            current_question["body_blocks"].append(block)

    for quiz in quizzes:
        _move_shared_materials(quiz)
        for question in quiz["questions"]:
            question["media"] = _question_media(question)
            question["content_hash"] = _question_content_hash(question, assets_by_id)
            question["review_flags"] = []
            if any(m.get("requires_conversion") for m in question["media"]):
                question["review_flags"].append("media_requires_conversion")
            if any(not m.get("alt_text") for m in question["media"]):
                question["review_flags"].append("media_missing_alt_text")
            if question["question_type"] == "unknown":
                question["review_flags"].append("question_type_uncertain")
            if question["knowledge_point"]["status"] == "evidence_insufficient":
                question["review_flags"].append("knowledge_point_evidence_insufficient")
    return quizzes


def _mark_duplicates(quizzes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for quiz in quizzes:
        for question in quiz["questions"]:
            groups[question["content_hash"]].append(question)
    duplicate_groups: list[dict[str, Any]] = []
    for content_hash, questions in sorted(groups.items()):
        if len(questions) < 2:
            for q in questions:
                q["duplicate_group_id"] = None
                q["duplicate_of"] = []
            continue
        group_id = f"dup-{content_hash[:16]}"
        ids = [q["question_id"] for q in questions]
        duplicate_groups.append({
            "duplicate_group_id": group_id,
            "content_hash": content_hash,
            "question_ids": ids,
            "action": "mark_only_keep_separate",
        })
        for q in questions:
            q["duplicate_group_id"] = group_id
            q["duplicate_of"] = [qid for qid in ids if qid != q["question_id"]]
    return duplicate_groups


def parse_classroom_docx(source: str | Path,
                         media_output_dir: str | Path | None = None) -> dict[str, Any]:
    """Parse a teacher DOCX without modifying it.

    Args:
        source: Path to the source ``.docx``.
        media_output_dir: Optional import-output root.  Digest-named safe media
            is written below ``<root>/media``.  The source is never written.

    Returns:
        A JSON-compatible audit/import manifest.
    """
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise ClassroomDocxError(f"DOCX 不存在: {source_path}")
    size = source_path.stat().st_size
    if size > MAX_DOCX_BYTES:
        raise ClassroomDocxError("DOCX 超过安全大小限制")
    source_sha256 = _sha256_file(source_path)
    try:
        zf = zipfile.ZipFile(source_path, "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise ClassroomDocxError("文件不是有效的 DOCX/ZIP 包") from exc

    with zf:
        total_uncompressed = sum(i.file_size for i in zf.infolist())
        if total_uncompressed > MAX_PACKAGE_UNCOMPRESSED:
            raise ClassroomDocxError("DOCX 解压后体积超过安全限制")
        names = set(zf.namelist())
        active_parts = sorted(n for n in names if (
            "vbaProject" in n or n.startswith("word/activeX/")
            or n.startswith("word/embeddings/")))
        relationships, external_relationships = _parse_relationships(zf)
        assets = _build_media_registry(zf)
        if media_output_dir is not None:
            output_root = Path(media_output_dir).expanduser().resolve()
            if source_path == output_root or source_path in output_root.parents:
                raise ClassroomDocxError("媒体输出目录不能覆盖源文件")
            _export_safe_media(assets, output_root)
        document_root = _parse_xml(
            _read_part(zf, "word/document.xml", MAX_XML_BYTES),
            "word/document.xml")
        numbering = _Numbering(zf)
        body = document_root.find(W("body"))
        if body is None:
            raise ClassroomDocxError("document.xml 缺少正文")
        blocks: list[dict[str, Any]] = []
        paragraph_index = 0
        table_count = 0
        for child in list(body):
            if child.tag == W("p"):
                paragraph_index += 1
                blocks.append(_parse_paragraph(
                    child, paragraph_index, relationships, assets))
            elif child.tag == W("tbl"):
                table_count += 1
        quizzes = _parse_quizzes(blocks, numbering, assets)
        duplicate_groups = _mark_duplicates(quizzes)
        metadata = _core_metadata(zf)

    warnings: list[dict[str, Any]] = []
    if active_parts:
        warnings.append({"code": "active_package_parts_ignored",
                         "parts": active_parts})
    if external_relationships:
        warnings.append({"code": "external_relationships_ignored",
                         "count": external_relationships})
    for quiz in quizzes:
        expected = EXPECTED_QUESTION_COUNTS.get(quiz["quiz_slug"])
        actual = len(quiz["questions"])
        if expected is not None and expected != actual:
            warnings.append({"code": "question_count_mismatch",
                             "quiz_id": quiz["quiz_id"],
                             "expected": expected, "actual": actual})
        numbers = [q["number"] for q in quiz["questions"]]
        if len(numbers) != len(set(numbers)):
            warnings.append({"code": "duplicate_question_number",
                             "quiz_id": quiz["quiz_id"], "numbers": numbers})

    question_count = sum(len(q["questions"]) for q in quizzes)
    expected_teacher_file = (
        len(quizzes) == 10 and question_count == 60
        and all(len(q["questions"]) == EXPECTED_QUESTION_COUNTS.get(q["quiz_slug"])
                for q in quizzes)
    )
    public_assets = [_public_asset(a) for _, a in sorted(assets.items())]
    return {
        "format_version": FORMAT_VERSION,
        "source": {
            "filename": source_path.name,
            "sha256": source_sha256,
            "bytes": size,
            "read_only": True,
        },
        "metadata": metadata,
        "security": {
            "active_package_parts_ignored": len(active_parts),
            "external_relationships_ignored": external_relationships,
            "raw_svg_exported": False,
        },
        "stats": {
            "quiz_count": len(quizzes),
            "question_count": question_count,
            "paragraph_count": paragraph_index,
            "table_count": table_count,
            "drawing_count": sum(len(b["media"]) for b in blocks),
            "media_asset_count": len(assets),
            "duplicate_group_count": len(duplicate_groups),
            "matches_expected_teacher_file": expected_teacher_file,
        },
        "media_assets": public_assets,
        "quizzes": quizzes,
        "duplicate_groups": duplicate_groups,
        "warnings": warnings,
    }


# Backwards-friendly explicit name for callers that think in terms of source.
parse_teacher_docx = parse_classroom_docx


def flatten_questions(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return questions in document order without merging duplicates."""
    return [question for quiz in document.get("quizzes", [])
            for question in quiz.get("questions", [])]


def normalize_stem_for_comparison(stem: str) -> str:
    """Normalize only presentation differences for Word/Excel comparison."""
    text = unicodedata.normalize("NFKC", str(stem or ""))
    text = _remove_duplicate_marker(_remove_direct_number(text))
    text = re.sub(r"^\s*[（(](?:单选题|多选题|是非题|判断题|填空题)[）)]\s*", "", text)
    text = text.casefold()
    text = re.sub(r"[\s\u00a0\u3000]+", "", text)
    # Keep numbers, letters and mathematical operators; remove punctuation that
    # frequently differs between Word and Excel exports.
    text = re.sub(r"[，。；：、,.!?！？“”‘’'\"（）()【】\[\]{}]", "", text)
    return text


def _first_value(row: Mapping[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return ""


def _question_number(value: Any) -> int | None:
    match = re.search(r"\d+", unicodedata.normalize("NFKC", str(value or "")))
    return int(match.group()) if match else None


def compare_excel_stems(document: Mapping[str, Any],
                        excel_rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Compare Excel stems with DOCX stems by quiz identity and question no.

    The function reports differences and ambiguous keys.  It never merges or
    drops a record, including questions already marked as duplicates in Word.
    Chinese and English column aliases used by the supplied workbook are
    accepted.
    """
    doc_index: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for question in flatten_questions(document):
        doc_index[(question["quiz_slug"], int(question["number"]))].append(question)

    excel_index: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    unkeyed: list[dict[str, Any]] = []
    for row_index, raw in enumerate(excel_rows, 1):
        row = dict(raw)
        quiz_value = _first_value(row, (
            "quiz_title", "quiz_name", "paper_title", "paper_name",
            "小测名称", "小测", "试卷名称", "试卷", "测验名称", "title"))
        number_value = _first_value(row, (
            "question_number", "question_no", "q_no", "题号", "序号"))
        stem_value = _first_value(row, (
            "stem", "question_text", "题干", "题目内容", "题目"))
        number = _question_number(number_value)
        item = {"row_index": row_index, "quiz_value": str(quiz_value or ""),
                "question_number": number, "stem": str(stem_value or ""),
                "source_id": str(_first_value(row, ("question_id", "题目ID", "id")) or "")}
        if not quiz_value or number is None:
            unkeyed.append(item)
            continue
        excel_index[(canonical_quiz_slug(str(quiz_value)), number)].append(item)

    results: list[dict[str, Any]] = []
    for key in sorted(set(doc_index) | set(excel_index)):
        docs = doc_index.get(key, [])
        rows = excel_index.get(key, [])
        if not docs:
            for row in rows:
                results.append({"status": "missing_in_docx", "quiz_slug": key[0],
                                "question_number": key[1], "excel": row,
                                "merge_action": "keep_separate"})
            continue
        if not rows:
            for question in docs:
                results.append({
                    "status": "missing_in_excel", "quiz_slug": key[0],
                    "question_number": key[1], "question_id": question["question_id"],
                    "docx_stem": question["stem"],
                    "duplicate_group_id": question.get("duplicate_group_id"),
                    "merge_action": "keep_separate",
                })
            continue
        if len(docs) != 1 or len(rows) != 1:
            results.append({
                "status": "ambiguous_key", "quiz_slug": key[0],
                "question_number": key[1],
                "docx_question_ids": [q["question_id"] for q in docs],
                "excel_rows": [r["row_index"] for r in rows],
                "merge_action": "keep_separate",
            })
            continue
        question, row = docs[0], rows[0]
        doc_text = str(question.get("stem") or "")
        excel_text = str(row.get("stem") or "")
        doc_norm = normalize_stem_for_comparison(doc_text)
        excel_norm = normalize_stem_for_comparison(excel_text)
        if doc_text.strip() == excel_text.strip():
            status = "exact"
        elif doc_norm == excel_norm:
            status = "normalized_equal"
        else:
            status = "different"
        similarity = difflib.SequenceMatcher(None, doc_norm, excel_norm).ratio()
        results.append({
            "status": status,
            "quiz_slug": key[0], "question_number": key[1],
            "question_id": question["question_id"],
            "excel_row_index": row["row_index"],
            "excel_source_id": row["source_id"],
            "docx_stem": doc_text, "excel_stem": excel_text,
            "normalized_similarity": round(similarity, 6),
            "duplicate_group_id": question.get("duplicate_group_id"),
            "merge_action": "keep_separate",
        })
    for row in unkeyed:
        results.append({"status": "unkeyed_excel_row", "excel": row,
                        "merge_action": "keep_separate"})
    return results


__all__ = [
    "ClassroomDocxError", "FORMAT_VERSION", "canonical_quiz_slug",
    "compare_excel_stems", "flatten_questions", "map_question_knowledge_point",
    "normalize_stem_for_comparison", "parse_classroom_docx", "parse_teacher_docx",
]
