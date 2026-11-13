r"""Build the reproducible, sanitised database shipped with the GitHub release.

The source database is never modified.  SQLite's online backup API creates a
copy, then this script removes credentials, sessions, student attachments and
model/worker logs.  Course questions, the official 16-student class,
historical attempts, grading evidence, diagnoses, retrieval chunks and the
four teacher-facing materials remain.  Every file-backed reference needed by
the UI is copied into ``data/demo/assets`` and is required to resolve.

Run from the repository root::

    .venv\Scripts\python.exe -X utf8 backend/scripts/create_release_snapshot.py
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "data" / "db" / "mlquiz.sqlite3"
DEFAULT_OUTPUT = ROOT / "data" / "demo" / "mlquiz-course-demo.sqlite3.gz"

# These tables contain operational traces, not course evidence shown to users.
RUNTIME_TABLES = (
    "session_tokens",
    "model_calls",
    "skill_runs",
    "jobs",
    "chat_messages",
    "favorites",
    "user_settings",
    "learning_events",
    "import_runs",
    "answer_generations",
    "answer_validations",
    "answer_reveals",
    "answer_disputes",
    "answer_attachments",
    "ocr_results",
)


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    return db.execute(
        "select 1 from sqlite_master where type='table' and name=?", (table,)
    ).fetchone() is not None


def _count(db: sqlite3.Connection, table: str) -> int:
    if not _table_exists(db, table):
        return 0
    return int(db.execute(f'select count(*) from "{table}"').fetchone()[0])


def _normalise_asset_path(value: str) -> Path | None:
    raw = value.strip().replace("\\", "/")
    if not raw:
        return None
    if raw.startswith("_mlq-assets/"):
        raw = "data/assets/" + raw.removeprefix("_mlq-assets/")
    elif not raw.startswith("data/"):
        raw = "data/" + raw
    candidate = (ROOT / raw).resolve()
    data_root = (ROOT / "data").resolve()
    try:
        candidate.relative_to(data_root)
    except ValueError:
        return None
    return candidate


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _json_asset_values(value: object) -> list[str]:
    paths: list[str] = []
    for item in _json_list(value):
        if isinstance(item, str):
            paths.append(item)
            continue
        if not isinstance(item, dict):
            continue
        for key in ("display_path", "path", "rel_path", "url"):
            raw = item.get(key)
            if isinstance(raw, str) and raw:
                paths.append(raw)
                break
    return paths


def _release_asset_references(db: sqlite3.Connection) -> tuple[set[Path], list[str]]:
    """Return every local file the released UI can request.

    Metadata-only source names such as ``5.支持向量机-2026.pdf`` are not file
    URLs and are deliberately not included.  Direct storage references and
    image arrays are included and must exist; silently shipping a broken card
    or preview is a release error.
    """
    paths: set[Path] = set()
    invalid: list[str] = []

    def add(raw: object, label: str) -> None:
        value = str(raw or "").strip()
        if not value:
            return
        path = _normalise_asset_path(value)
        if path is None:
            invalid.append(f"{label}:unsafe:{value}")
            return
        paths.add(path)

    direct_columns = (
        ("material_uploads", "stored_rel_path"),
        ("answer_booklets", "pdf_rel_path"),
        ("external_papers", "pdf_rel_path"),
        ("external_papers", "thumb_rel_path"),
        ("external_papers", "zh_pdf_rel_path"),
    )
    for table, column in direct_columns:
        if not _table_exists(db, table):
            continue
        for rowid, raw in db.execute(
            f'select rowid, "{column}" from "{table}" '
            f"where coalesce(cast(\"{column}\" as text), '') <> ''"
        ):
            add(raw, f"{table}[{rowid}].{column}")

    for table, column in (("questions", "stem_images"), ("chunks", "assets")):
        if not _table_exists(db, table):
            continue
        for rowid, raw in db.execute(f'select rowid, "{column}" from "{table}"'):
            for value in _json_asset_values(raw):
                add(value, f"{table}[{rowid}].{column}")

    # Resource cover URLs are API routes; the route resolves them by ID to a
    # deterministic local filename rather than storing that filename in SQLite.
    if _table_exists(db, "resources"):
        for resource_id, thumbnail in db.execute(
            "select resource_id, thumbnail from resources "
            "where coalesce(thumbnail, '') like '/api/resources/%/cover'"
        ):
            add(
                f"data/assets/covers/{resource_id}.jpg",
                f"resources[{resource_id}].thumbnail",
            )
    # Historical paper snapshots point to a hash-pinned teacher DOCX manifest.
    # The source DOCX is not shipped, but its anonymised parsed questions are.
    source_hashes: set[str] = set()
    if _table_exists(db, "paper_items"):
        for (raw,) in db.execute("select snapshot from paper_items"):
            try:
                snapshot = json.loads(raw or "{}")
            except json.JSONDecodeError:
                continue
            for ref in snapshot.get("source_refs", []):
                if isinstance(ref, dict) and ref.get("kind") == "teacher_docx":
                    source_hashes.add(str(ref.get("file_sha256") or ""))
    for digest in source_hashes:
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            invalid.append("teacher_docx:invalid_hash")
            continue
        manifest = ROOT / "data" / "classroom_source" / f"teacher_docx_{digest[:8]}" / "teacher_docx.json"
        if not manifest.is_file():
            invalid.append(f"teacher_docx:{digest}:missing_manifest")
            continue
        doc = json.loads(manifest.read_text(encoding="utf-8"))
        if doc.get("source", {}).get("sha256") != digest:
            invalid.append(f"teacher_docx:{digest}:source_mismatch")
            continue
        paths.add(manifest)
        for asset in doc.get("media_assets", []):
            if asset.get("extraction_status") != "extracted":
                continue
            relative = Path(str(asset.get("safe_path") or ""))
            if relative.parts[:1] != ("media",) or ".." in relative.parts:
                invalid.append(f"teacher_docx:{digest}:unsafe_media")
                continue
            media = (manifest.parent / relative).resolve()
            if media.parent != (manifest.parent / "media").resolve():
                invalid.append(f"teacher_docx:{digest}:unsafe_media")
                continue
            if not media.is_file() or hashlib.sha256(media.read_bytes()).hexdigest() != asset.get("sha256"):
                invalid.append(f"teacher_docx:{digest}:media_hash_mismatch")
                continue
            paths.add(media)
    return paths, invalid


def _copy_release_assets(paths: set[Path], demo_dir: Path) -> int:
    target_root = demo_dir / "assets"
    if target_root.exists():
        resolved = target_root.resolve()
        if demo_dir.resolve() not in resolved.parents:
            raise RuntimeError(f"refusing to clear unexpected path: {resolved}")
        shutil.rmtree(target_root)
    copied = 0
    data_root = (ROOT / "data").resolve()
    for source in sorted(paths):
        if not source.is_file():
            raise FileNotFoundError(source)
        relative = source.resolve().relative_to(data_root)
        target = target_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    return copied


def _scan_for_private_values(db: sqlite3.Connection) -> list[str]:
    findings: list[str] = []
    patterns = ("%C:\\Users\\%", "%/Users/%", "%Bearer %")
    secret_patterns = (
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
        re.compile(r"\bBearer\s+[A-Za-z0-9._-]{16,}", re.IGNORECASE),
    )
    for (table,) in db.execute(
        "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
    ):
        for row in db.execute(f'pragma table_info("{table}")'):
            column = row[1]
            declared = str(row[2] or "").upper()
            if declared and not any(kind in declared for kind in ("TEXT", "CHAR", "JSON")):
                continue
            for pattern in patterns:
                try:
                    hit = db.execute(
                        f'select 1 from "{table}" where cast("{column}" as text) like ? limit 1',
                        (pattern,),
                    ).fetchone()
                except sqlite3.DatabaseError:
                    continue
                if hit:
                    findings.append(f"{table}.{column}:{pattern}")
            try:
                values = db.execute(
                    f'select cast("{column}" as text) from "{table}" '
                    f'where coalesce(cast("{column}" as text), \'\') <> \'\''
                )
            except sqlite3.DatabaseError:
                continue
            if any(
                pattern.search(value or "")
                for (value,) in values
                for pattern in secret_patterns
            ):
                findings.append(f"{table}.{column}:secret-pattern")
    return findings


def _redact_local_paths(db: sqlite3.Connection) -> None:
    """Remove author-machine paths embedded in imported Markdown text."""
    windows_user_path = re.compile(
        r"(?i)[A-Z]:\\Users\\[^\s\)\]\}\"']+"
    )
    mac_user_path = re.compile(r"/Users/[^\s\)\]\}\"']+")
    if not _table_exists(db, "chunks"):
        return
    for column in ("content_raw", "content_normalized"):
        rows = db.execute(
            f'select rowid, "{column}" from chunks '
            f'where "{column}" like \'%C:\\Users\\%\' '
            f'or "{column}" like \'%/Users/%\''
        ).fetchall()
        for rowid, text in rows:
            cleaned = windows_user_path.sub("[本机路径已移除]", text or "")
            cleaned = mac_user_path.sub("[本机路径已移除]", cleaned)
            db.execute(
                f'update chunks set "{column}"=? where rowid=?',
                (cleaned, rowid),
            )


def create_snapshot(source: Path, output: Path) -> dict:
    source = source.resolve()
    output = output.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="mlquiz-release-") as tmp_dir:
        temp_db = Path(tmp_dir) / "release.sqlite3"
        source_db = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        target_db = sqlite3.connect(temp_db)
        try:
            source_db.backup(target_db)
        finally:
            source_db.close()

        try:
            target_db.execute("pragma foreign_keys=off")
            for table in RUNTIME_TABLES:
                if _table_exists(target_db, table):
                    target_db.execute(f'delete from "{table}"')

            # Keep the four visible teaching resources but remove worker IDs,
            # user-local ownership and verbose processing traces.
            if _table_exists(target_db, "material_uploads"):
                release_progress = json.dumps(
                    {
                        "stage": "completed",
                        "stage_status": "ok",
                        "detail": {"note": "已从脱敏课程快照恢复"},
                    },
                    ensure_ascii=False,
                )
                target_db.execute(
                    "update material_uploads set user_id='teacher_demo', "
                    "job_id='', slides_job_id='', error='', progress=?",
                    (release_progress,),
                )
            _redact_local_paths(target_db)

            # No usable credential or login timestamp is shipped.  The local
            # bootstrap script creates fresh hashes and a gitignored password file.
            target_db.execute(
                "update users set password_hash='', last_login_at='', active=0"
            )
            allowed = ["teacher_demo", *[f"stu{i:03d}" for i in range(1, 17)]]
            placeholders = ",".join("?" for _ in allowed)
            target_db.execute(
                f"update users set active=1 where user_id in ({placeholders})", allowed
            )

            # The source workspace also contains two isolated test students and
            # one legacy local profile.  They are not part of the official
            # 16-person class and would only add noise to the published demo.
            official_ids = [row[0] for row in target_db.execute(
                "select student_id from students "
                "where display_code glob 'STU[0-9][0-9][0-9]'"
            )]
            official_placeholders = ",".join("?" for _ in official_ids)
            nonofficial_ids = [row[0] for row in target_db.execute(
                f"select student_id from students "
                f"where student_id not in ({official_placeholders})",
                official_ids,
            )]
            if nonofficial_ids:
                student_placeholders = ",".join("?" for _ in nonofficial_ids)
                removed_attempts = [row[0] for row in target_db.execute(
                    f"select attempt_id from attempts "
                    f"where student_id in ({student_placeholders})",
                    nonofficial_ids,
                )]
                table_names = [row[0] for row in target_db.execute(
                    "select name from sqlite_master "
                    "where type='table' and name not like 'sqlite_%'"
                )]
                if removed_attempts:
                    attempt_placeholders = ",".join("?" for _ in removed_attempts)
                    for table in table_names:
                        columns = {
                            row[1] for row in target_db.execute(
                                f'pragma table_info("{table}")'
                            )
                        }
                        if "attempt_id" in columns:
                            target_db.execute(
                                f'delete from "{table}" '
                                f"where attempt_id in ({attempt_placeholders})",
                                removed_attempts,
                            )
                for table in table_names:
                    columns = {
                        row[1] for row in target_db.execute(
                            f'pragma table_info("{table}")'
                        )
                    }
                    if "student_id" in columns:
                        target_db.execute(
                            f'delete from "{table}" '
                            f"where student_id in ({student_placeholders})",
                            nonofficial_ids,
                        )
                target_db.execute(
                    "delete from classrooms where source='demo_account' "
                    "or course_key='teacher-demo'"
                )

            # Only the teacher and the official anonymous student accounts are
            # part of the release.  Material ownership was reassigned above.
            target_db.execute(
                f"delete from users where user_id not in ({placeholders})", allowed
            )
            target_db.commit()

            # AnswerRegrade rows are course evidence. Keep their completed run
            # headers so the diagnosis service can distinguish them from
            # unfinished runs. Strip operational parameters and actor details.
            if _table_exists(target_db, "scoring_runs"):
                target_db.execute("delete from scoring_runs where status != 'completed'")
                target_db.execute("update scoring_runs set parameters='{}', summary='{}', created_by='system'")
                target_db.commit()

            assets, invalid_refs = _release_asset_references(target_db)
            missing_assets = [
                str(path.relative_to(ROOT)) for path in sorted(assets)
                if not path.is_file()
            ]
            if invalid_refs or missing_assets:
                details = [*invalid_refs, *[f"missing:{p}" for p in missing_assets]]
                raise RuntimeError(
                    "release asset references are incomplete: " + "; ".join(details)
                )

            official_students = target_db.execute(
                "select count(*) from students where display_code glob 'STU[0-9][0-9][0-9]'"
            ).fetchone()[0]
            if official_students != 16:
                raise RuntimeError(
                    f"expected 16 anonymised students, found {official_students}"
                )
            if _count(target_db, "questions") < 1 or _count(target_db, "attempts") < 1:
                raise RuntimeError("release snapshot lost required course evidence")
            if _count(target_db, "session_tokens") or _count(target_db, "model_calls"):
                raise RuntimeError("runtime secrets/logs remain in release snapshot")
            expected_release_counts = {
                "documents": 60,
                "chunks": 4612,
                "material_uploads": 4,
                "answer_booklets": 1,
            }
            for table, expected in expected_release_counts.items():
                actual = _count(target_db, table)
                if actual != expected:
                    raise RuntimeError(
                        f"expected {expected} release rows in {table}, found {actual}"
                    )

            target_db.execute("vacuum")
            integrity = target_db.execute("pragma integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"SQLite integrity check failed: {integrity}")
            findings = _scan_for_private_values(target_db)
            if findings:
                raise RuntimeError("private-looking values remain: " + ", ".join(findings))

            counts = {
                name: _count(target_db, name)
                for name in (
                    "students", "questions", "papers", "attempts", "answers",
                    "answer_scores", "diagnoses", "resources", "documents",
                    "document_versions", "chunks", "material_uploads",
                    "answer_booklets", "external_sources", "external_papers",
                    "external_items", "session_tokens", "model_calls",
                    "skill_runs", "jobs", "answer_attachments",
                )
            }
        finally:
            target_db.close()

        copied_assets = _copy_release_assets(assets, output.parent)
        temp_gz = output.with_suffix(output.suffix + ".tmp")
        with temp_db.open("rb") as source_file, temp_gz.open("wb") as raw_output:
            with gzip.GzipFile(
                filename="mlquiz-course-demo.sqlite3",
                mode="wb",
                fileobj=raw_output,
                compresslevel=9,
                mtime=0,
            ) as compressed:
                shutil.copyfileobj(source_file, compressed)
        temp_gz.replace(output)

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return {
        "output": str(output),
        "bytes": output.stat().st_size,
        "sha256": digest,
        "release_assets": copied_assets,
        "asset_references": len(assets),
        "missing_asset_references": 0,
        "counts": counts,
        "integrity_check": "ok",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = create_snapshot(args.source, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
