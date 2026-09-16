"""Issue #78 识别项账号级启用/停用：系统默认 + 账号覆盖。

语义（与 Issue #78 产品规则一一对应）：
- 内置识别项对账号暴露 enabled / default_enabled 两个覆盖位，缺省跟随系统；
- owner 存储只持久化偏差位——未被账号触碰的内置项不落盘，
  「显式设置」与「跟随默认」因此可区分，系统默认后续调整不被冻结；
- 停用 = 本账号一切识别路径都不查：默认范围不含、显式清单勾了也不查（#5 文本洞）、
  OCR+HaS 视觉链路同样过滤（#6 视觉洞）；
- 显式启用非默认项（default_enabled）= 纳入本账号默认范围（律师场景：出生日期）；
- 自定义识别项（custom_ 前缀）不引入覆盖位，整行存储与增删改行为不变。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services import entity_type_service as ets
from app.services.entity_type_service import (
    PRESET_ENTITY_TYPES,
    get_default_generic_types,
    resolve_requested_entity_types,
    set_type_override,
    toggle_type,
)


@pytest.fixture()
def scoped_store(monkeypatch, tmp_path):
    """把运行时存储隔离到 tmp，并让内置项从源码预设出发。"""
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "ENTITY_TYPES_STORE_PATH", str(tmp_path / "entity_types.json"))
    monkeypatch.setattr(settings, "PRESET_STORE_PATH", str(tmp_path / "presets.json"))
    monkeypatch.setattr(settings, "PIPELINE_STORE_PATH", str(tmp_path / "pipelines.json"))
    yield tmp_path


def _raw_owner_file(owner_id: str) -> dict:
    path = ets._entity_types_store_path(owner_id)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ── 覆盖位持久化：只写偏差位 ─────────────────────────────────


def test_disabled_builtin_persists_deviation_only_and_reloads(scoped_store):
    set_type_override("DATE", enabled=False, owner_id="lawyer_a")

    raw = _raw_owner_file("lawyer_a")
    # DATE 只写偏差位
    assert set(raw["DATE"].keys()) <= {"id", "enabled", "default_enabled"}
    assert raw["DATE"]["enabled"] is False
    # 未触碰的内置项不落盘（跟随系统默认）
    assert "PERSON" not in raw

    db = ets._load_entity_types("lawyer_a")
    assert db["DATE"].enabled is False
    # 定义仍来自源码预设（系统字典不被账号冻结）
    assert db["DATE"].name == PRESET_ENTITY_TYPES["DATE"].name
    assert db["DATE"].description == PRESET_ENTITY_TYPES["DATE"].description

    # 默认范围不再含日期
    assert "DATE" not in {t.id for t in get_default_generic_types("lawyer_a")}


def test_enable_nondefault_builtin_enters_default_scope(scoped_store):
    # 当前系统默认里出生日期已入默认范围（PR#67 起），律师场景只需停用日期；
    # 用系统默认不在默认范围的 SOCIAL_SECURITY 验证"显式启用=纳入默认范围"语义。
    assert "SOCIAL_SECURITY" not in {t.id for t in get_default_generic_types("lawyer_a")}

    set_type_override("SOCIAL_SECURITY", default_enabled=True, owner_id="lawyer_a")

    raw = _raw_owner_file("lawyer_a")
    assert set(raw["SOCIAL_SECURITY"].keys()) <= {"id", "enabled", "default_enabled"}
    assert raw["SOCIAL_SECURITY"]["default_enabled"] is True

    default_ids = {t.id for t in get_default_generic_types("lawyer_a")}
    assert "SOCIAL_SECURITY" in default_ids
    assert "DATE" in default_ids  # 未触碰，仍跟随系统默认


def test_explicit_toggle_back_to_system_value_drops_deviation(scoped_store):
    set_type_override("DATE", enabled=False, owner_id="lawyer_a")
    assert _raw_owner_file("lawyer_a")["DATE"]["enabled"] is False

    # 改回系统默认值后，偏差位消失（回到"跟随默认"）
    set_type_override("DATE", enabled=True, owner_id="lawyer_a")
    assert "DATE" not in _raw_owner_file("lawyer_a")


# ── 旧格式兼容：整行文件只认 enabled，default_enabled 跟随源码 ──


def test_legacy_full_row_file_honors_enabled_follows_source_default(scoped_store):
    legacy_row = PRESET_ENTITY_TYPES["DATE"].model_dump()
    legacy_row["enabled"] = False
    legacy_row["default_enabled"] = False  # 旧快照里的陈旧值，应被忽略
    path = Path(ets._entity_types_store_path("legacy_user"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"DATE": legacy_row}), encoding="utf-8")

    db = ets._load_entity_types("legacy_user")
    assert db["DATE"].enabled is False  # 停用选择保留
    assert db["DATE"].default_enabled is PRESET_ENTITY_TYPES["DATE"].default_enabled  # 跟随源码


# ── 解析洞修补：显式清单不能复活停用类型 ─────────────────────


def test_explicit_checklist_cannot_revive_disabled_type(scoped_store):
    set_type_override("DATE", enabled=False, owner_id="lawyer_a")

    resolved = resolve_requested_entity_types(["DATE", "BIRTH_DATE"], owner_id="lawyer_a")
    assert [t.id for t in resolved] == ["BIRTH_DATE"]

    # 未停用的账号不受影响
    resolved_b = resolve_requested_entity_types(["DATE", "BIRTH_DATE"], owner_id="lawyer_b")
    assert [t.id for t in resolved_b] == ["DATE", "BIRTH_DATE"]


def test_owner_isolation_between_accounts_and_global(scoped_store):
    set_type_override("DATE", enabled=False, owner_id="lawyer_a")

    assert ets._load_entity_types("lawyer_b")["DATE"].enabled is True
    assert ets.entity_types_db["DATE"].enabled is True  # 全局存储不受账号覆盖影响


# ── toggle 兼容与重置 ────────────────────────────────────────


def test_toggle_compat_persists_deviation_only_and_reset_clears(scoped_store):
    assert toggle_type("DATE", owner_id="lawyer_a") is False
    assert _raw_owner_file("lawyer_a")["DATE"]["enabled"] is False

    ets.reset_types(owner_id="lawyer_a")
    assert "DATE" not in _raw_owner_file("lawyer_a")
    assert ets._load_entity_types("lawyer_a")["DATE"].enabled is True


# ── 自定义项不引入覆盖位 ─────────────────────────────────────


def test_custom_type_rows_remain_full_and_builtins_absent(scoped_store):
    from app.services.entity_type_service import CreateEntityTypeRequest, create_type

    created = create_type(
        CreateEntityTypeRequest(
            name="文书编号",
            regex_pattern=r"\d+",
            use_llm=False,
        ),
        owner_id="lawyer_a",
    )

    raw = _raw_owner_file("lawyer_a")
    row = raw[created.id]
    assert row["name"] == "文书编号"  # 整行存储
    assert "PERSON" not in raw and "DATE" not in raw  # 内置项仍不落盘

    listed = {t.id: t for t in ets.list_types(owner_id="lawyer_a").custom_types}
    assert listed[created.id].system_enabled is None  # 自定义项没有"系统默认"概念


def test_set_override_rejects_unknown_and_custom_types(scoped_store):
    from app.services.entity_type_service import CreateEntityTypeRequest, create_type

    # 未知的类型（无论前缀）返回 None
    assert set_type_override("NO_SUCH_TYPE", enabled=False, owner_id="lawyer_a") is None
    # 已存在的自定义项没有覆盖位，明确拒绝
    created = create_type(
        CreateEntityTypeRequest(name="文书编号", regex_pattern=r"\d+", use_llm=False),
        owner_id="lawyer_a",
    )
    with pytest.raises(ValueError):
        set_type_override(created.id, enabled=False, owner_id="lawyer_a")


# ── 列表暴露系统默认值（供 UI 显示"跟随默认"） ────────────────


def test_list_types_exposes_system_defaults(scoped_store):
    set_type_override("DATE", enabled=False, owner_id="lawyer_a")
    types = {t.id: t for t in ets.list_types(owner_id="lawyer_a").custom_types}

    assert types["DATE"].enabled is False
    assert types["DATE"].system_enabled is True  # 系统默认仍是开
    assert types["DATE"].system_default_enabled is True
    assert types["PERSON"].enabled is True
    assert types["PERSON"].system_enabled is True
    assert types["PERSON"].system_default_enabled is True


# ── 视觉链路（OCR+HaS）：停用类型从选择集中过滤 ───────────────


def test_account_disabled_ids_removed_from_ocr_selection(scoped_store):
    from app.services.pipeline_service import filter_types_by_account_enabled

    set_type_override("DATE", enabled=False, owner_id="lawyer_a")
    items = [
        SimpleNamespace(id="DATE"),
        SimpleNamespace(id="BIRTH_DATE"),
        SimpleNamespace(id="custom_seal_like"),  # 不在实体库中的视觉类型不受影响
    ]
    kept = [t.id for t in filter_types_by_account_enabled(items, owner_id="lawyer_a")]
    assert kept == ["BIRTH_DATE", "custom_seal_like"]

    kept_b = [t.id for t in filter_types_by_account_enabled(items, owner_id="lawyer_b")]
    assert kept_b == ["DATE", "BIRTH_DATE", "custom_seal_like"]


# ── API：PATCH 覆盖位 ────────────────────────────────────────


def test_override_endpoint_round_trip(scoped_store, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core import auth
    from app.main import app

    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "_AUTH_FILE", str(scoped_store / "auth.json"))
    auth.create_user("lawyer_a", "Passw0rd!", role="user")
    headers = {"Authorization": f"Bearer {auth.create_token('lawyer_a')}"}
    client = TestClient(app)

    resp = client.patch(
        "/api/v1/custom-types/DATE/override",
        json={"enabled": False},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is False
    assert body["default_enabled"] is True

    listed = client.get("/api/v1/custom-types", headers=headers).json()
    row = next(t for t in listed["custom_types"] if t["id"] == "DATE")
    assert row["enabled"] is False and row["system_enabled"] is True

    # 内置项定义仍不可改
    resp_put = client.put(
        "/api/v1/custom-types/DATE",
        json={"name": "改名字", "description": None, "color": "#000000", "use_llm": True},
        headers=headers,
    )
    assert resp_put.status_code == 400

    # 未知类型 404，自定义 id 400
    assert client.patch(
        "/api/v1/custom-types/NO_SUCH/override", json={"enabled": False}, headers=headers
    ).status_code == 404
