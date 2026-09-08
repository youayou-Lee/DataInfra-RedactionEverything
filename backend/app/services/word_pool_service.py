"""替换词池服务（化名模式）。

按租户存储每类实体的替换词池与「原词→替换词」精确映射，存储模式与
preset_service 一致（tenant_store_path + store_lock 的 JSON 文件）。
内置默认词池在 backend/config/default_word_pools.json，租户可整体覆盖
某类型的词池或精确映射；删除租户配置即回退默认。
"""

from __future__ import annotations

import os as _os
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.persistence import load_json, save_json
from app.core.tenant_config import store_lock, tenant_store_path

_DEFAULT_POOLS_PATH = _os.path.join(
    _os.path.dirname(__file__), "..", "..", "config", "default_word_pools.json"
)

# 实体类型 → 词池类型的别名归并（法官/律师/证人共用人名词池等）
POOL_TYPE_ALIASES: dict[str, str] = {
    "LAWYER": "PERSON",
    "JUDGE": "PERSON",
    "WITNESS": "PERSON",
    "LEGAL_PARTY": "PERSON",
    "ORG": "INSTITUTION_NAME",
    "COMPANY_NAME": "INSTITUTION_NAME",
    "BANK": "BANK_NAME",
    "LOCATION": "ADDRESS",
}

VALID_STRATEGIES = ("numbered", "cycle", "generated")


class WordPoolUpdate(BaseModel):
    """词池更新请求体。"""

    words: list[str] = Field(default_factory=list)
    strategy: str = Field(default="numbered")
    custom_map: dict[str, str] = Field(default_factory=dict)


def pool_type_for(type_key: str) -> str:
    """实体类型键归并到词池类型键。"""
    return POOL_TYPE_ALIASES.get(type_key, type_key)


def load_default_pools() -> dict[str, dict[str, Any]]:
    raw = load_json(_DEFAULT_POOLS_PATH, default={})
    return {k: _normalize_pool(v) for k, v in raw.items() if isinstance(v, dict)}


def _normalize_pool(pool: dict[str, Any]) -> dict[str, Any]:
    raw_words = pool.get("words")
    if isinstance(raw_words, str):
        # 字符串不是词列表（会被逐字符迭代成单字词），按无效处理
        raw_words = []
    words = list(dict.fromkeys(str(w).strip() for w in (raw_words or []) if str(w).strip()))
    strategy = str(pool.get("strategy") or "numbered")
    if strategy not in VALID_STRATEGIES:
        strategy = "numbered"
    custom_map = {
        str(k).strip(): str(v).strip()
        for k, v in (pool.get("custom_map") or {}).items()
        if str(k).strip() and str(v).strip()
    }
    return {"words": words, "strategy": strategy, "custom_map": custom_map}


def _store_path(owner_id: str | None = None) -> str:
    return tenant_store_path(owner_id, settings.WORD_POOL_STORE_PATH, "word_pools.json")


def load_word_pools(owner_id: str | None = None) -> dict[str, dict[str, Any]]:
    """默认词池 + 租户覆盖的合并视图（供替换引擎直接使用）。"""
    pools = load_default_pools()
    with store_lock(_store_path(owner_id)):
        overrides = load_json(_store_path(owner_id), default={})
    for key, value in (overrides or {}).items():
        if isinstance(value, dict):
            pools[str(key)] = _normalize_pool(value)
    return pools


def list_word_pools(owner_id: str | None = None) -> dict[str, Any]:
    """词池管理视图：区分默认与租户覆盖。"""
    defaults = load_default_pools()
    with store_lock(_store_path(owner_id)):
        overrides = load_json(_store_path(owner_id), default={}) or {}
    return {
        "defaults": defaults,
        "overrides": {k: _normalize_pool(v) for k, v in overrides.items() if isinstance(v, dict)},
        "merged": {**defaults, **{k: _normalize_pool(v) for k, v in overrides.items() if isinstance(v, dict)}},
    }


def update_word_pool(type_id: str, body: WordPoolUpdate, owner_id: str | None = None) -> dict[str, Any]:
    if not type_id.strip():
        raise ValueError("类型 ID 不能为空")
    pool = _normalize_pool(body.model_dump())
    path = _store_path(owner_id)
    with store_lock(path):
        store = load_json(path, default={}) or {}
        store[type_id] = pool
        save_json(path, store)
    return pool


def delete_word_pool(type_id: str, owner_id: str | None = None) -> bool:
    """删除租户覆盖，回退默认词池。返回是否存在覆盖。"""
    path = _store_path(owner_id)
    with store_lock(path):
        store = load_json(path, default={}) or {}
        if type_id not in store:
            return False
        del store[type_id]
        save_json(path, store)
    return True


def export_word_pools(owner_id: str | None = None) -> dict[str, Any]:
    """导出租户词池覆盖 + 精确映射（跨文档/跨案件复用同一套化名）。"""
    with store_lock(_store_path(owner_id)):
        overrides = load_json(_store_path(owner_id), default={}) or {}
    return {
        "overrides": {k: _normalize_pool(v) for k, v in overrides.items() if isinstance(v, dict)},
    }


def import_word_pools(data: dict[str, Any], owner_id: str | None = None, *, merge: bool = True) -> int:
    """导入词池；merge=True 与现有覆盖合并（custom_map 键级合并），否则整体替换。"""
    overrides_in = data.get("overrides") if isinstance(data, dict) and "overrides" in data else data
    if not isinstance(overrides_in, dict):
        raise ValueError("导入内容需为 {overrides: {...}} 或词池字典")
    path = _store_path(owner_id)
    count = 0
    with store_lock(path):
        store = load_json(path, default={}) or {}
        for key, value in overrides_in.items():
            if not isinstance(value, dict):
                continue
            pool = _normalize_pool(value)
            if merge and key in store and isinstance(store[key], dict):
                existing = _normalize_pool(store[key])
                existing["words"] = list(dict.fromkeys(existing["words"] + pool["words"]))
                existing["custom_map"] = {**existing["custom_map"], **pool["custom_map"]}
                existing["strategy"] = pool["strategy"]
                pool = existing
            store[str(key)] = pool
            count += 1
        save_json(path, store)
    return count
