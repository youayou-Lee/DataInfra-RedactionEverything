"""管理控制台 API（Issue #77）——super_admin 跨用户查看注册用户与上传文件。

设计文档 docs/issue-77-admin-console.md：
- D2 只列 auth.json 注册用户（无主文件 local_user 不展示）；
- D3 文件数/清单排除软删除（deleted_at 非空），与用户本人历史口径一致；
- D4 仅开放原文件下载（脱敏成品不开放）；
- 查看与下载写审计（操作者=super_admin）。

router 级 require_super_admin 在 main.py 注册时统一挂载。
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

import app.services.file_management_service as _fms
from app.core import auth
from app.core.audit import audit_log
from app.core.auth import normalize_username, require_super_admin
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")


def _require_existing_user(username: str) -> str:
    """规范化并校验用户存在；不存在一律 404（不区分禁用账号，禁用用户文件仍可查）。"""
    subject = normalize_username(username)
    if auth.get_user(subject) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return subject


@router.get("/users", response_model=list[dict])
async def admin_list_users(actor: str = Depends(require_super_admin)):
    """注册用户清单 + 每人未删除文件数（按用户名排序）。"""
    users = auth.list_users()
    counts = _fms.get_file_store().count_by_owner()
    return [
        {
            "username": u["username"],
            "role": u["role"],
            "created_at": u["created_at"],
            "disabled": u["disabled"],
            "file_count": counts.get(u["username"], 0),
        }
        for u in users
    ]


@router.get("/users/{username}/files", response_model=dict)
async def admin_list_user_files(
    username: str,
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    actor: str = Depends(require_super_admin),
):
    """某注册用户的文件清单（created_at 倒序，排除软删除，服务端分页）。"""
    subject = _require_existing_user(username)

    entries: list[tuple[str, dict]] = []
    for fid, info in _fms.get_file_store().items_for_owner(subject):
        if not isinstance(info, dict) or info.get("deleted_at"):
            continue
        entries.append((fid, info))
    # (created_at, file_id) 全序：created_at 并列时翻页顺序仍确定
    # （file_store 是 INSERT OR REPLACE，rowid 会变，不能依赖表扫描序）
    entries.sort(key=lambda pair: (str(pair[1].get("created_at") or ""), pair[0]), reverse=True)

    total = len(entries)
    start = (page - 1) * page_size
    items = [
        {
            "file_id": fid,
            "original_filename": info.get("original_filename"),
            "file_type": info.get("file_type"),
            "file_size": int(info.get("file_size") or 0),
            "created_at": info.get("created_at"),
            "upload_source": _fms.effective_upload_source(info),
            "has_output": bool(info.get("output_path")),
        }
        for fid, info in entries[start : start + page_size]
    ]

    audit_log(
        "view_files",
        "user_account",
        subject,
        user=actor,
        detail={"page": page, "page_size": page_size, "total": total},
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/users/{username}/files/{file_id}/download")
async def admin_download_user_file(
    username: str,
    file_id: str,
    actor: str = Depends(require_super_admin),
):
    """下载指定用户的原始上传文件（安全通道，不暴露磁盘路径）。

    属主双重校验：file 的 owner_id 必须等于路径中的 username，
    防止拿 A 的 file_id 走 B 的路径越权取件。
    """
    subject = _require_existing_user(username)

    snapshot = await _fms.get_file_snapshot(file_id)
    # 软删除文件不在清单/计数中，但保留可下载：对齐用户本人
    # /files/{id}/download 的回收站行为，复现「文件找不到了」类反馈需要。
    if not snapshot or _fms.file_owner_id(snapshot) != subject:
        raise HTTPException(status_code=404, detail="文件不存在")

    file_path = snapshot.get("file_path")
    if not file_path:
        raise HTTPException(status_code=404, detail="原文件路径缺失")

    # 路径遍历保护（与 /files/{id}/download 同源）
    if not _fms.safe_path_in_dir(file_path, settings.UPLOAD_DIR):
        raise HTTPException(status_code=403, detail="禁止访问该路径")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="文件不存在")

    filename = snapshot.get("original_filename") or file_id
    audit_log(
        "download",
        "file",
        file_id,
        user=actor,
        detail={"owner": subject, "filename": filename},
    )
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/octet-stream",
    )
