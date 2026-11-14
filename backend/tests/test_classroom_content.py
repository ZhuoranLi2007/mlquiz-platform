import json
from hashlib import sha256
from pathlib import Path
import pytest
from backend.app.services import classroom_content as c
from backend.app.services.question_dto import student_view, assert_no_answer_leak


@pytest.fixture()
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(c.cfg, "DATA_DIR", tmp_path)
    root = tmp_path / "classroom_source" / "fixture"
    (root / "media").mkdir(parents=True)
    raw = b"test-image-bytes"
    digest = sha256(raw).hexdigest()
    aid = "media-" + digest[:16]
    (root / "media" / (aid + ".png")).write_bytes(raw)
    fragment = {"type": "media", "selected_asset_id": aid}
    block = {"paragraph_index": 1, "fragments": [{"type": "text", "text": "先看共享图"}, fragment]}
    doc = {"source": {"sha256": "a"*64}, "media_assets": [{"asset_id": aid, "safe_path": "media/"+aid+".png", "sha256": digest}],
        "quizzes": [{"shared_materials": [{"shared_material_id": "shared", "blocks": [block]}],
          "questions": [{"question_id": "q", "content_hash": "pinned", "source_stem": "原文", "stem_block": block,
            "body_blocks": [], "options": [{"key": "A", "text": "x+1", "fragments": [{"type": "text", "text": "x+1"}]}],
            "shared_material_ids": ["shared"], "media": [fragment]}]}]}
    manifest = root / "teacher_docx.json"
    manifest.write_text(json.dumps(doc), encoding="utf-8")
    snapshot = {"docx_question_id": "q", "docx_content_hash": "pinned", "stem": "答卷快照文本",
       "source_refs": [{"kind": "teacher_docx", "file_sha256": "a"*64}], "answer_payload": {"key": "A"}}
    return snapshot, manifest, doc, aid


def test_order_shared_media_and_no_answer_leak(source):
    snapshot, _, _, aid = source
    result = student_view(snapshot, mode="quiz")
    assert result["stem"] == "答卷快照文本"
    assert result["source_stem"] == "原文"
    assert result["source_blocks"][0]["fragments"][0]["text"] == "先看共享图"
    assert result["shared_materials"][0]["blocks"][0]["fragments"][1]["available"]
    assert result["source_options"][0]["text"] == "x+1"
    assert not result["media_incomplete"]
    assert_no_answer_leak(result)


def test_unconvertible_media_explicitly_blocks(source):
    snapshot, manifest, doc, aid = source
    doc["media_assets"][0]["safe_path"] = "media/"+aid+".emf"
    manifest.write_text(json.dumps(doc), encoding="utf-8")
    result = student_view(snapshot, mode="quiz")
    assert result["media_incomplete"] and result["options_incomplete"]
    assert not result["media"][0]["available"]
    assert "url" not in result["media"][0]


def test_hash_mismatch_and_traversal_fail_closed(source):
    snapshot, manifest, doc, aid = source
    snapshot["docx_content_hash"] = "wrong"
    assert c.source_content(snapshot)["media_incomplete"]
    doc["media_assets"][0]["safe_path"] = "../../outside.png"
    manifest.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError): c.asset_path("a"*64, aid)
    with pytest.raises(ValueError): c.asset_path("a"*64, "../secret")


def test_search_entries_not_specific_verified_content():
    assert c.is_search_entry("https://search.bilibili.com/all?keyword=ml")
    assert c.is_search_entry("https://www.douyin.com/search/ml")
    assert c.is_search_entry("https://so.csdn.net/so/search?q=svm")
    assert not c.is_search_entry("https://example.com/course/linear-regression")


def test_packaged_matrix_questions_restore_original_options(monkeypatch):
    root = Path(__file__).resolve().parents[2] / "data" / "demo" / "assets"
    manifest = root / "classroom_source" / "teacher_docx_76eeea3d" / "teacher_docx.json"
    raw = manifest.read_bytes()
    assert sha256(raw).hexdigest() == "278a41c059ed041542e02c50a205c2d4d7b7ac6020f5b6514108f8205b6c5918"
    monkeypatch.setattr(c.cfg, "DATA_DIR", root)
    source_sha = "76eeea3dc966d430c663a2634c8eaf1ba03e0d665f28cd99ebff8ab7ca3aca00"
    for number, content_hash in ((3, "ec428ede39f3e1f6ce49d4c5604c458360741d73ee06b61252893fd166958329"),
                                 (5, "6fdbfe4d35b1171c5db8543f78682b684fa2dfd993444989bed0ee4c872ca80e")):
        snapshot = {"docx_question_id": f"teacher-2025-fall-matrix-factorization-q{number:02d}",
                    "docx_content_hash": content_hash,
                    "source_refs": [{"kind": "teacher_docx", "file_sha256": source_sha}]}
        result = c.source_content(snapshot)
        assert not result["media_incomplete"]
        assert [option["key"] for option in result["source_options"]] == ["A", "B", "C", "D"]


def test_packaged_emf_formulas_render_as_png_without_changing_source(monkeypatch):
    root = Path(__file__).resolve().parents[2] / "data" / "demo" / "assets"
    monkeypatch.setattr(c.cfg, "DATA_DIR", root)
    converted = []
    for _, doc in c.manifests():
        source_sha = doc["source"]["sha256"]
        for asset in doc.get("media_assets", []):
            if not str(asset.get("safe_path") or "").endswith(".emf"):
                continue
            path = c.asset_path(source_sha, asset["asset_id"])
            before = sha256(path.read_bytes()).hexdigest()
            png = c.render_emf_png(path)
            assert png.startswith(b"\x89PNG\r\n\x1a\n")
            assert sha256(path.read_bytes()).hexdigest() == before
            converted.append(asset["asset_id"])
    assert len(converted) == 4


from backend.tests.test_classroom_diagnosis import db

def test_search_entry_api_label_and_recommendation_exclusion(db):
    from backend.app.models import entities as E
    from backend.app.api.routes import resources
    from backend.app.services.classroom_diagnosis import _resources
    db.add(E.Resource(resource_id="search-test", title="检索入口", platform="bilibili",
        url="https://search.bilibili.com/all?keyword=loss", kp_ids=["kp.loss"], verified=True,
        source_type="external", is_test_fixture=False))
    db.commit()
    item = next(x for x in resources(db=db)["items"] if x["resource_id"] == "search-test")
    assert item["verified"] is False and item["verified_scope"] == "search_entry"
    assert "未核验具体内容" in item["content_label"]
    assert not any(x["resource_id"] == "search-test" for x in _resources(db, ["kp.loss"]))
    assert db.get(E.Resource, "search-test").verified is True  # presentation correction, no data mutation
