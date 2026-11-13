from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import config as cfg
from backend.app.api import auth_routes
from backend.app.models import entities as E
from backend.app.models.base import Base, get_session
from backend.app.services import auth_service


@pytest.fixture()
def quick_login(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'quick-login.db').as_posix()}",
        connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(engine)
    local = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    teacher_password = "TeacherDemo!2026"
    student_passwords = {
        f"stu{index:03d}": f"Student-{index:03d}-Demo!"
        for index in range(1, 17)
    }
    db = local()
    db.add(E.User(
        user_id="teacher_demo", display_name="演示教师", role="teacher",
        password_hash=auth_service.hash_password(teacher_password), active=True))
    for user_id, password in student_passwords.items():
        db.add(E.User(
            user_id=user_id, display_name=user_id.upper(), role="student",
            password_hash=auth_service.hash_password(password), active=True))
        db.add(E.Student(
            student_id=f"student-{user_id}", external_key=f"official:{user_id}",
            display_code=user_id.upper(), linked_user_id=user_id, active=True))
    db.commit()
    db.close()

    private_dir = tmp_path / "private"
    private_dir.mkdir()
    credential_path = private_dir / "stu_accounts.json"
    credential_path.write_text(json.dumps({
        "accounts": [
            {"student_code": user_id.upper(), "user_id": user_id,
             "initial_password": password}
            for user_id, password in student_passwords.items()
        ]
    }, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "QUICK_LOGIN_ENABLED", True)
    monkeypatch.setattr(cfg, "QUICK_LOGIN_TEACHER_PASSWORD", teacher_password)

    app = FastAPI()
    app.include_router(auth_routes.router)

    def override_session():
        session = local()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    try:
        yield TestClient(app), local, credential_path, student_passwords
    finally:
        engine.dispose()


def test_quick_login_returns_verified_teacher_and_sixteen_students(quick_login):
    client, _local, _path, passwords = quick_login
    response = client.get("/api/auth/quick-login-accounts")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["enabled"] is True
    assert len(body["accounts"]) == 17
    assert body["accounts"][0] == {
        "user_id": "teacher_demo", "label": "演示教师",
        "role": "teacher", "password": "TeacherDemo!2026",
    }
    assert [row["user_id"] for row in body["accounts"][1:]] == list(passwords)
    assert not ({"student_id", "password_hash"} & set(body["accounts"][1]))

    for user_id in ("teacher_demo", "stu001", "stu008", "stu016"):
        row = next(item for item in body["accounts"] if item["user_id"] == user_id)
        logged_in = client.post("/api/auth/login", json={
            "user_id": row["user_id"], "password": row["password"]})
        assert logged_in.status_code == 200


def test_quick_login_omits_stale_and_inactive_accounts(quick_login):
    client, local, path, passwords = quick_login
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["accounts"][0]["initial_password"] = "Stale-Password!"
    path.write_text(json.dumps(payload), encoding="utf-8")
    db = local()
    db.get(E.User, "stu002").active = False
    db.commit()
    db.close()

    accounts = client.get("/api/auth/quick-login-accounts").json()["accounts"]
    ids = [row["user_id"] for row in accounts]
    assert "stu001" not in ids
    assert "stu002" not in ids
    assert len(accounts) == 15
    assert passwords["stu003"] == next(
        row["password"] for row in accounts if row["user_id"] == "stu003")


@pytest.mark.parametrize("file_content", [None, "{not-json"])
def test_quick_login_bad_or_missing_file_falls_back_to_teacher(
        quick_login, file_content):
    client, _local, path, _passwords = quick_login
    if file_content is None:
        path.unlink()
    else:
        path.write_text(file_content, encoding="utf-8")
    body = client.get("/api/auth/quick-login-accounts").json()
    assert [row["user_id"] for row in body["accounts"]] == ["teacher_demo"]


def test_quick_login_can_be_disabled_without_affecting_login(
        quick_login, monkeypatch):
    client, _local, _path, _passwords = quick_login
    monkeypatch.setattr(cfg, "QUICK_LOGIN_ENABLED", False)
    response = client.get("/api/auth/quick-login-accounts")
    assert response.json() == {"enabled": False, "accounts": []}
    login = client.post("/api/auth/login", json={
        "user_id": "teacher_demo", "password": "TeacherDemo!2026"})
    assert login.status_code == 200


def test_fast_identities_and_tab_sessions_are_independent(quick_login):
    client, _local, _path, _passwords = quick_login
    identities = client.get("/api/auth/quick-login-identities")
    assert identities.status_code == 200
    assert identities.headers["cache-control"] == "no-store"
    accounts = identities.json()["accounts"]
    assert len(accounts) == 17
    assert accounts[0]["user_id"] == "teacher_demo"
    assert [row["label"] for row in accounts[1:]] == [f"STU{i:03d}" for i in range(1, 17)]
    assert all("password" not in row for row in accounts)

    teacher = client.post("/api/auth/quick-login", json={"user_id": "teacher_demo"})
    student = client.post("/api/auth/quick-login", json={"user_id": "stu001"})
    assert teacher.status_code == student.status_code == 200
    assert "set-cookie" not in teacher.headers
    teacher_headers = {"Authorization": f"Bearer {teacher.json()['session_token']}"}
    student_headers = {"Authorization": f"Bearer {student.json()['session_token']}"}
    assert client.get("/api/auth/me", headers=teacher_headers).json()["role"] == "teacher"
    assert client.get("/api/auth/me", headers=student_headers).json()["student_code"] == "STU001"
    assert client.post("/api/auth/logout", headers=teacher_headers).status_code == 200
    assert client.get("/api/auth/me", headers=teacher_headers).status_code == 401
    assert client.get("/api/auth/me", headers=student_headers).status_code == 200


def test_quick_login_rejects_stale_password_and_disabled_mode(quick_login, monkeypatch):
    client, _local, path, _passwords = quick_login
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["accounts"][0]["initial_password"] = "stale-password"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert client.post("/api/auth/quick-login", json={"user_id": "stu001"}).status_code == 401
    assert client.post("/api/auth/quick-login", json={"user_id": "unknown"}).status_code == 401
    monkeypatch.setattr(cfg, "QUICK_LOGIN_ENABLED", False)
    assert client.get("/api/auth/quick-login-identities").json() == {"enabled": False, "accounts": []}
    assert client.post("/api/auth/quick-login", json={"user_id": "teacher_demo"}).status_code == 404
