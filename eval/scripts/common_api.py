"""评测脚本公共 API 层（Issue #37）：登录/上传/逐页识别/实体抽取。

run_eval（e2e 层）与 build_pseudonym_set / leak_check（复扫）共用，
保证三处走同一组生产端点与同一套实体归一口径（设计文档 D1）。
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx

_REPO_ROOT = Path(__file__).resolve().parents[2]

# 英文类型 ID → 中文名，读 preset 单一事实源（不硬编码，防漂移）
_PRESET_PATH = _REPO_ROOT / "backend" / "config" / "preset_entity_types.json"
with _PRESET_PATH.open(encoding="utf-8") as _f:
    _PRESET = json.load(_f)
TYPE_ID_TO_NAME: dict[str, str] = {tid: spec["name"] for tid, spec in _PRESET.items()}

_WS_RE = re.compile(r"\s+")


def squash(text: str) -> str:
    """去全部空白（中文实体 P/R 对齐域；数字保真不在此域，见 run_eval）。"""
    return _WS_RE.sub("", str(text or ""))


class EvalApi:
    """backend 公开 API 的最小封装（perf_bench.py 同模式）。"""

    def __init__(self, base_url: str, username: str, password: str, timeout: float = 600.0):
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, trust_env=False)
        r = self.client.post("/api/v1/auth/login", json={"username": username, "password": password})
        if r.status_code != 200:
            r = self.client.post("/api/v1/auth/register", json={"username": username, "password": password})
            r.raise_for_status()
            r = self.client.post("/api/v1/auth/login", json={"username": username, "password": password})
        r.raise_for_status()
        self.client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    def upload(self, path: Path) -> str:
        with path.open("rb") as f:
            r = self.client.post("/api/v1/files/upload",
                                 files={"file": (path.name, f, "application/octet-stream")})
        r.raise_for_status()
        return r.json()["file_id"]

    def vision(self, file_id: str, page: int, force: bool = True) -> dict:
        r = self.client.post(f"/api/v1/redaction/{file_id}/vision",
                             params={"page": page, "force": str(force).lower(),
                                     "include_result_image": "false"})
        if r.status_code != 200:
            raise RuntimeError(f"vision {file_id} p{page} -> HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

    def parse_and_hybrid_ner(self, file_id: str) -> tuple[dict[str, list[str]], float]:
        """docx/txt 链路（backend vision 仅支持 pdf/图片，云实测确认）：
        GET /files/{id}/parse → POST /files/{id}/ner/hybrid（HaS + 正则 + 共指）。
        返回 ({类型中文名: [实体串]}, 墙钟秒)。"""
        started = time.perf_counter()
        r = self.client.get(f"/api/v1/files/{file_id}/parse")
        if r.status_code != 200:
            raise RuntimeError(f"parse {file_id} -> HTTP {r.status_code}: {r.text[:200]}")
        r = self.client.post(f"/api/v1/files/{file_id}/ner/hybrid", json={})
        if r.status_code != 200:
            raise RuntimeError(f"ner/hybrid {file_id} -> HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        if body.get("recognition_failed"):
            raise RuntimeError(f"ner/hybrid {file_id} recognition_failed: {str(body.get('error'))[:200]}")
        entities: dict[str, list[str]] = {}
        for e in body.get("entities") or []:
            text = str(e.get("text") or "").strip()
            if not text:
                continue
            type_id = str(e.get("type") or "")
            entities.setdefault(TYPE_ID_TO_NAME.get(type_id, type_id), []).append(text)
        return entities, round(time.perf_counter() - started, 3)

    def delete_file(self, file_id: str) -> bool:
        """尽力清理；后端若无该端点则跳过（合成数据无敏感信息，残留可接受）。"""
        r = self.client.delete(f"/api/v1/files/{file_id}")
        return r.status_code in (200, 204)

    def close(self) -> None:
        self.client.close()


def extract_page_entities(vision_resp: dict) -> dict[str, list[str]]:
    """从单页 vision 响应抽实体：type 英文 ID 归一为中文名，text 原样保留（strip）。

    未映射类型原样保留键并计入返回（调用方决定如何呈现，不静默丢弃）。
    """
    entities: dict[str, list[str]] = {}
    for box in vision_resp.get("bounding_boxes") or []:
        text = str(box.get("text") or "").strip()
        if not text:
            continue
        type_id = str(box.get("type") or "")
        etype = TYPE_ID_TO_NAME.get(type_id, type_id)
        entities.setdefault(etype, []).append(text)
    return entities


def iter_pages_with_timing(api: EvalApi, file_id: str, total_pages: int,
                           warmup_pages: int = 0) -> list[dict]:
    """逐页 force 识别，收集实体、duration_ms 分解与墙钟（perf_bench 口径）。"""
    results = []
    for page in range(1, total_pages + 1):
        t0 = time.perf_counter()
        resp = api.vision(file_id, page)
        wall = round(time.perf_counter() - t0, 3)
        results.append({
            "page": page,
            "warmup": page <= warmup_pages,
            "wall_s": wall,
            "duration_ms": resp.get("duration_ms") or {},
            "pipeline_status": resp.get("pipeline_status") or {},
            "entities": extract_page_entities(resp),
        })
    return results
