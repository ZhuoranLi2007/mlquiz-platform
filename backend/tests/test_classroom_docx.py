"""Pure regression tests for the classroom teacher-DOCX parser."""
from __future__ import annotations

import base64
import hashlib
import json
import zipfile
from pathlib import Path

from backend.app.services.classroom_docx import (
    compare_excel_stems,
    flatten_questions,
    map_question_knowledge_point,
    parse_classroom_docx,
)


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "/x8AAusB9Y9Zl1sAAAAASUVORK5CYII=")


def _p(text: str, *, style: str = "", num_id: str = "") -> str:
    ppr = ""
    props = []
    if style:
        props.append(f'<w:pStyle w:val="{style}"/>')
    if num_id:
        props.append(
            f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{num_id}"/></w:numPr>')
    if props:
        ppr = f"<w:pPr>{''.join(props)}</w:pPr>"
    return f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"


def _alternate_formula_paragraph(prefix: str, suffix: str) -> str:
    return f"""
    <w:p>
      <w:r><w:t>{prefix}</w:t></w:r>
      <mc:AlternateContent>
        <mc:Choice Requires="asvg">
          <w:r><w:drawing><wp:inline><wp:extent cx="900000" cy="200000"/>
            <wp:docPr id="1" name="formula"/>
            <a:graphic><a:graphicData><a:blip r:embed="rIdPng"/>
              <asvg:svgBlip r:embed="rIdSvg"/>
            </a:graphicData></a:graphic>
          </wp:inline></w:drawing></w:r>
        </mc:Choice>
        <mc:Fallback>
          <w:r><w:drawing><wp:inline><wp:extent cx="900000" cy="200000"/>
            <wp:docPr id="2" name="formula fallback"/>
            <a:graphic><a:graphicData><a:blip r:embed="rIdPng"/>
            </a:graphicData></a:graphic>
          </wp:inline></w:drawing></w:r>
        </mc:Fallback>
      </mc:AlternateContent>
      <w:r><w:t>{suffix}</w:t></w:r>
    </w:p>"""


def _make_docx(path: Path) -> None:
    paragraphs = [
        _p("2025秋-线性回归", style="1"),
        _p("请回答以下是非题。"),
        _p("相同题干", num_id="1"),
        _alternate_formula_paragraph("2. 公式前", "公式后"),
        _p("2025秋-Logistic回归与LDA", style="1"),
        _p("请回答以下是非题。"),
        _p("1. 相同题干（重复）"),
        _p("2025秋-支持向量机", style="1"),
        _p("1. 前置题"),
        _p("2. 最大间隔题"),
        _p("请根据下图回答第3~7题"),
        _alternate_formula_paragraph("", ""),
        _p("3. 原始问题"), _p("4. 拉格朗日"), _p("5. 对偶"),
        _p("6. KKT"), _p("7. 恢复w"),
    ]
    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
      xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
      xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
      xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
      xmlns:asvg="http://schemas.microsoft.com/office/drawing/2016/SVG/main">
      <w:body>{''.join(paragraphs)}<w:sectPr/></w:body>
    </w:document>"""
    numbering = """<?xml version="1.0" encoding="UTF-8"?>
    <w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0">
        <w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>
      </w:lvl></w:abstractNum>
      <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
    </w:numbering>"""
    relationships = """<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rIdPng" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/formula.png"/>
      <Relationship Id="rIdSvg" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/formula.svg"/>
    </Relationships>"""
    core = """<?xml version="1.0" encoding="UTF-8"?>
    <cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
      xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:dcterms="http://purl.org/dc/terms/">
      <dc:creator>teacher.private@example.edu</dc:creator>
      <cp:lastModifiedBy>Teacher Private</cp:lastModifiedBy>
      <dcterms:created>2026-11-10T14:09:00Z</dcterms:created>
      <cp:revision>1</cp:revision>
    </cp:coreProperties>"""
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg"><path fill="currentColor" '
           b'd="M0 0h10v10z"/></svg>')
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", document)
        zf.writestr("word/numbering.xml", numbering)
        zf.writestr("word/_rels/document.xml.rels", relationships)
        zf.writestr("docProps/core.xml", core)
        zf.writestr("word/media/formula.png", PNG_1X1)
        zf.writestr("word/media/formula.svg", svg)


def _parsed(tmp_path: Path) -> tuple[Path, dict]:
    source = tmp_path / "teacher.docx"
    _make_docx(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    document = parse_classroom_docx(source, media_output_dir=tmp_path / "out")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    return source, document


def test_numbering_and_stable_question_ids(tmp_path):
    _, document = _parsed(tmp_path)
    questions = flatten_questions(document)
    first = questions[0]
    assert first["number"] == 1
    assert first["display_number"] == "1."
    assert first["question_id"] == "teacher-2025-fall-linear-regression-q01"
    assert first["question_type"] == "true_false"
    assert document["source"]["read_only"] is True


def test_png_fallback_preserves_text_drawing_order(tmp_path):
    _, document = _parsed(tmp_path)
    question = next(q for q in flatten_questions(document)
                    if q["question_id"].endswith("linear-regression-q02"))
    fragments = question["stem_block"]["fragments"]
    assert [f["type"] for f in fragments] == ["text", "media", "text"]
    media = question["media"][0]
    assert media["selection_reason"] == "png_fallback_for_svg_current_color"
    assert media["display_path"].endswith(".png")
    assert (tmp_path / "out" / media["display_path"]).is_file()
    svg_asset = next(a for a in document["media_assets"] if a["extension"] == "svg")
    assert svg_asset["safe_path"] is None
    assert svg_asset["extraction_status"] == "blocked_active_vector"


def test_duplicates_are_marked_not_merged_and_shared_image_is_reused(tmp_path):
    _, document = _parsed(tmp_path)
    questions = flatten_questions(document)
    same = [q for q in questions if q["stem"] == "相同题干"]
    assert len(same) == 2
    assert same[0]["question_id"] != same[1]["question_id"]
    assert same[0]["content_hash"] == same[1]["content_hash"]
    assert same[0]["duplicate_group_id"] == same[1]["duplicate_group_id"]
    assert document["duplicate_groups"][0]["action"] == "mark_only_keep_separate"

    svm = next(q for q in document["quizzes"] if q["quiz_slug"] == "svm")
    shared = svm["shared_materials"][0]
    assert shared["applies_to_numbers"] == [3, 4, 5, 6, 7]
    assert len(shared["applies_to_question_ids"]) == 5
    q2 = next(q for q in svm["questions"] if q["number"] == 2)
    assert not q2["body_blocks"]


def test_core_metadata_is_filtered_and_kp_mapping_is_evidence_aware(tmp_path):
    _, document = _parsed(tmp_path)
    serialized = json.dumps(document, ensure_ascii=False)
    assert "teacher.private@example.edu" not in serialized
    assert "Teacher Private" not in serialized
    assert document["metadata"]["redacted_fields"] == ["creator", "lastModifiedBy"]
    assert document["metadata"]["safe"]["revision"] == "1"

    exact = map_question_knowledge_point("支持向量机", 7, "恢复 w")
    assert exact["kp_id"] == "kp.svm.kkt"
    canonical_slug = map_question_knowledge_point(
        "linear-regression", 2, "正规方程与梯度下降")
    assert canonical_slug["kp_id"] == "kp.linear.normal_eq"
    insufficient = map_question_knowledge_point("网络机器学习", 6, "图卷积")
    assert insufficient["kp_id"] == ""
    assert insufficient["status"] == "evidence_insufficient"


def test_excel_comparison_reports_difference_without_merging(tmp_path):
    _, document = _parsed(tmp_path)
    comparisons = compare_excel_stems(document, [
        {"小测名称": "1.1 线性回归小测", "题号": 1, "题干": "相同题干"},
        {"小测名称": "1.1 线性回归小测", "题号": 2, "题干": "公式内容不同"},
    ])
    by_number = {x["question_number"]: x for x in comparisons
                 if x["quiz_slug"] == "linear-regression"}
    assert by_number[1]["status"] == "exact"
    assert by_number[2]["status"] == "different"
    assert by_number[2]["merge_action"] == "keep_separate"
