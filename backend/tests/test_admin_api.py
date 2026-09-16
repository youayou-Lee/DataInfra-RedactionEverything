"""Issue #77 管理控制台 API：权限门 / 用户汇总 / 文件清单 / 原文件下载 / 审计。

隔离策略：auth 指到 tmp_path；file_store 单例整体替换为 tmp_path 新实例；
admin.audit_log 打桩为内存记录器（audit handler 在 import 时绑定真实 DATA_DIR）。
"""
from __future__ import annotations

import os
import pathlib

import pytest
from fastapi.testclient import TestClient

from app.core import auth
from app.core.config import settings
from app.main import app
from app.services import file_management_service as fms
from app.services.file_store_db import FileStoreDB

client = TestClient(app)


@pytest.fixture(autouse=True)
def audit_events(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(auth, "_AUTH_FILE", str(tmp_path / "auth.json"))
    monkeypatch.setattr(
        fms, "file_store", FileStoreDB(str(tmp_path / "file_store.sqlite3"))
    )
    os.makedirs(tmp_path / "uploads", exist_ok=True)

    from app.api import admin as admin_api

    events: list[dict] = []

    def _record(
        action: str,
        resource_type: str,
        resource_id: str = "",
        user: str = "anonymous",
        detail: dict | None = None,
    ) -> None:
        events.append(
            {
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "user": user,
                "detail": detail or {},
            }
        )

    monkeypatch.setattr(admin_api, "audit_log", _record)
    yield events


def _seed_user(username: str, role: str = "user") -> dict[str, str]:
    auth.create_user(username, "Passw0rd!", role=role)
    return {"Authorization": f"Bearer {auth.create_token(username)}"}


def _seed_file(
    fid: str,
    *,
    owner: str,
    filename: str = "doc.txt",
    content: bytes = b"hello",
    created_at: str = "2026-09-17T00:00:00+00:00",
    deleted: bool = False,
) -> dict:
    path = pathlib.Path(settings.UPLOAD_DIR) / f"{fid}.txt"
    path.write_bytes(content)
    info: dict = {
        "id": fid,
        "original_filename": filename,
        "stored_filename": path.name,
        "file_path": str(path),
        "file_type": "txt",
        "file_size": len(content),
        "created_at": created_at,
        "upload_source": "playground",
        "owner_id": owner,
    }
    if deleted:
        info["deleted_at"] = "2026-09-17T01:00:00+00:00"
    fms.file_store.set(fid, info)
    return info


# ---------------------------------------------------------------------------
# 权限门
# ---------------------------------------------------------------------------


def test_unauthenticated_gets_401():
    assert client.get("/api/v1/admin/users").status_code == 401


def test_non_super_admin_gets_403_on_all_endpoints():
    emp = _seed_user("emp")
    urls = [
        "/api/v1/admin/users",
        "/api/v1/admin/users/emp/files",
        "/api/v1/admin/users/emp/files/no-such/download",
    ]
    for url in urls:
        assert client.get(url, headers=emp).status_code == 403, url


# ---------------------------------------------------------------------------
# 用户汇总
# ---------------------------------------------------------------------------


def test_admin_users_summary_with_file_counts():
    boss = _seed_user("boss", role="super_admin")
    _seed_user("alice")
    _seed_user("carol")
    _seed_file("f1", owner="alice")
    _seed_file("f2", owner="alice")
    _seed_file("f3", owner="alice", deleted=True)
    _seed_file("f4", owner="local_user")  # 无主文件不计入注册用户（D2/D3）

    r = client.get("/api/v1/admin/users", headers=boss)
    assert r.status_code == 200
    users = {u["username"]: u for u in r.json()}
    assert set(users) == {"boss", "alice", "carol"}
    assert users["alice"]["file_count"] == 2
    assert users["carol"]["file_count"] == 0
    assert users["alice"]["role"] == "user"
    assert users["alice"]["disabled"] is False
    assert users["alice"]["created_at"]


# ---------------------------------------------------------------------------
# 文件清单
# ---------------------------------------------------------------------------


def test_admin_user_files_pagination_newest_first():
    boss = _seed_user("boss", role="super_admin")
    _seed_user("alice")
    _seed_file("old", owner="alice", created_at="2026-09-01T00:00:00+00:00")
    _seed_file("mid", owner="alice", created_at="2026-09-10T00:00:00+00:00")
    _seed_file("new", owner="alice", created_at="2026-09-17T00:00:00+00:00")

    r = client.get(
        "/api/v1/admin/users/alice/files", headers=boss, params={"page": 1, "page_size": 2}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert body["page"] == 1 and body["page_size"] == 2
    assert [it["file_id"] for it in body["items"]] == ["new", "mid"]

    r2 = client.get(
        "/api/v1/admin/users/alice/files", headers=boss, params={"page": 2, "page_size": 2}
    )
    assert [it["file_id"] for it in r2.json()["items"]] == ["old"]


def test_admin_user_files_only_target_owner_and_live():
    boss = _seed_user("boss", role="super_admin")
    _seed_user("alice")
    _seed_file("a1", owner="alice")
    _seed_file("a2", owner="alice", deleted=True)
    _seed_file("b1", owner="bob")

    r = client.get("/api/v1/admin/users/alice/files", headers=boss)
    assert r.status_code == 200
    items = r.json()["items"]
    assert [it["file_id"] for it in items] == ["a1"]
    item = items[0]
    assert item["original_filename"] == "doc.txt"
    assert item["file_size"] == len(b"hello")
    assert item["upload_source"] == "playground"
    assert item["has_output"] is False


def test_admin_user_files_unknown_user_404():
    boss = _seed_user("boss", role="super_admin")
    r = client.get("/api/v1/admin/users/ghost/files", headers=boss)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 原文件下载
# ---------------------------------------------------------------------------


def test_admin_download_original_file():
    boss = _seed_user("boss", role="super_admin")
    _seed_user("alice")
    _seed_file("d1", owner="alice", filename="证据卷.pdf", content=b"secret-bytes")

    r = client.get("/api/v1/admin/users/alice/files/d1/download", headers=boss)
    assert r.status_code == 200
    assert r.content == b"secret-bytes"
    assert "attachment" in r.headers.get("content-disposition", "")


def test_admin_download_rejects_cross_owner_file_id():
    boss = _seed_user("boss", role="super_admin")
    _seed_user("alice")
    _seed_file("bobs", owner="bob")
    # 路径里的 username 与文件属主不符：super_admin 也拿不到（防错配/越权枚举）
    r = client.get("/api/v1/admin/users/alice/files/bobs/download", headers=boss)
    assert r.status_code == 404


def test_admin_download_missing_disk_file_404():
    boss = _seed_user("boss", role="super_admin")
    _seed_user("alice")
    info = _seed_file("gone", owner="alice")
    os.remove(info["file_path"])
    r = client.get("/api/v1/admin/users/alice/files/gone/download", headers=boss)
    assert r.status_code == 404


def test_admin_download_path_traversal_blocked():
    boss = _seed_user("boss", role="super_admin")
    _seed_user("alice")
    outside = pathlib.Path(settings.DATA_DIR) / "outside.txt"
    outside.write_bytes(b"evil")
    fms.file_store.set(
        "trav",
        {
            "id": "trav",
            "original_filename": "outside.txt",
            "file_path": str(outside),
            "owner_id": "alice",
            "created_at": "2026-09-17T00:00:00+00:00",
        },
    )
    r = client.get("/api/v1/admin/users/alice/files/trav/download", headers=boss)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------


def test_audit_records_view_and_download(audit_events):
    boss = _seed_user("boss", role="super_admin")
    _seed_user("alice")
    _seed_file("d9", owner="alice", filename="证据卷.pdf")

    client.get("/api/v1/admin/users/alice/files", headers=boss)
    client.get("/api/v1/admin/users/alice/files/d9/download", headers=boss)

    actions = [(e["action"], e["resource_type"]) for e in audit_events]
    assert ("view_files", "user_account") in actions
    assert ("download", "file") in actions

    view = next(e for e in audit_events if e["action"] == "view_files")
    assert view["user"] == "boss" and view["resource_id"] == "alice"
    dl = next(e for e in audit_events if e["action"] == "download")
    assert dl["resource_id"] == "d9"
    assert dl["detail"] == {"owner": "alice", "filename": "证据卷.pdf"}


# ---------------------------------------------------------------------------
# count_by_owner（DB 层）
# ---------------------------------------------------------------------------


def test_count_by_owner_excludes_deleted_and_defaults_owner(tmp_path):
    db = FileStoreDB(str(tmp_path / "fs.sqlite3"))
    db.set("a", {"owner_id": "alice"})
    db.set("b", {"owner_id": "alice", "deleted_at": "2026-09-17T01:00:00+00:00"})
    db.set("c", {})  # 无 owner 字段 → local_user 口径

    assert db.count_by_owner() == {"alice": 1, "local_user": 1}
