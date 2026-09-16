# Issue #77 设计文档：管理控制台（super_admin 看用户与文件）

- Issue: youayou-Lee/DataInfra-RedactionEverything#77
- 日期: 2026-09-17
- 状态: 设计已经用户确认（brainstorming 定稿），本文档为实现基准
- 分支: `feat/issue77-admin-console`（基于 preview 3937c4f）

## 1. 背景与目标

外部用户自行注册账号使用实例，密码仅用户本人掌握。用户通过外部渠道反馈问题时，管理员无法登录其账号查看文件，难以复现。本 Issue 交付一个 super_admin 专属控制台：

1. 看到有哪些注册用户（用户名/注册时间/角色/状态/文件数）；
2. 看到每个用户上传了什么文件（文件名/类型/大小/上传时间/来源，分页）；
3. 能下载用户上传的**原文件**（走后端鉴权的安全通道）；
4. 查看与下载动作写审计日志。

## 2. 范围决策（已与用户确认）

| # | 决策 | 内容 |
|---|---|---|
| D1 | 独立入口 | 独立页面 `/console` + 侧边栏「控制台」入口（仅 super_admin 可见），不塞进 /settings/system Tabs |
| D2 | 只列注册用户 | 控制台只列 auth.json 注册用户；无主文件（owner=local_user 的本地/导入文件）不在本期展示 |
| D3 | 计数口径 | 文件数排除软删除（`deleted_at` 非空）记录，与用户本人文件列表可见口径一致 |
| D4 | 下载仅原文件 | 只提供原文件下载；`has_output` 仅作列展示，脱敏成品不开放下载 |

**非目标（本期不做）**：查看用户脱敏任务/参数/输出结果；重置/修改用户密码；删除用户文件；模拟登录（impersonation）。

## 3. 现状参考（复用件）

- 用户体系：`backend/app/core/auth.py` —— `list_users()`（返回 username/role/created_at/updated_at/can_bulk_confirm/disabled）、`require_super_admin` 依赖（非 super_admin 403）。
- 文件元数据：`backend/app/services/file_store_db.py` —— `items_for_owner(owner_id)`（SQL `json_extract` 按 owner 过滤，全量返回）；记录字段含 `owner_id/original_filename/file_type/file_size/created_at/upload_source/output_path/deleted_at`。
- 下载模板：`backend/app/api/files.py` `GET /files/{file_id}/download` —— `FileResponse(path, filename=..., media_type="application/octet-stream")` + `safe_path_in_dir` 防路径穿越。
- 审计：`backend/app/core/audit.py` `audit_log(action, resource_type, resource_id, user, detail)`；现有 `/settings/system` audit 面板可直接查询新记录。
- 路由注册：`backend/app/main.py` `include_router(..., dependencies=[Depends(require_super_admin)])` 先例 = `model_config.router` / `ner_backend.router`。
- 前端：`app-sidebar.tsx` 的 `isAdmin` 条件追加导航先例；`system-settings.tsx` 页面内 super_admin 检查先例；`downloadFile(url, filename)`（api-client，fetch→blob 自动带 cookie）；i18n zh/en 扁平键 + parity 测试。

## 4. 后端设计

新增 `backend/app/api/admin.py`，`APIRouter(prefix="/admin")`，在 `main.py` 以 router 级 `dependencies=[Depends(require_super_admin)]` 注册。三个端点：

### 4.1 `GET /admin/users`

- 响应 `[{username, role, created_at, disabled, file_count}]`（按 username 排序）。
- 实现：`auth.list_users()` + `FileStoreDB.count_by_owner()`（新增方法）按 username 连接。
- `count_by_owner()`：SQL 按 `json_extract(data_json,'$.owner_id')` 分组计数，排除 `deleted_at` 非空（D3）。

### 4.2 `GET /admin/users/{username}/files`

- Query：`page: int = Query(1, ge=1)`、`page_size: int = Query(20, ge=1, le=100)`（与 `/files`、`/jobs` 命名一致）。
- 校验：username 必须存在于 auth.json，否则 404。
- 实现：`items_for_owner(username)` → 过滤软删除 → 按 `created_at` 倒序 → 切片分页。
- 每条：`{file_id, original_filename, file_type, file_size, created_at, upload_source, has_output}`（`has_output = bool(output_path)`，D4）。
- 响应包络：`{items, total, page, page_size}`。
- 审计：`audit_log("view_files", "user_account", username, user=actor)`。

### 4.3 `GET /admin/users/{username}/files/{file_id}/download`

- 校验链：username 存在（404）→ file 存在且 `owner_id == username`（404，防拿 A 的 file_id 走 B 的路径）→ `safe_path_in_dir` 路径防护 → 磁盘文件存在（404）。
- 返回 `FileResponse(path, filename=original_filename, media_type="application/octet-stream")`。
- 审计（仅成功时）：`audit_log("download", "file", file_id, user=actor, detail={"owner": username, "filename": original_filename})`。

## 5. 前端设计

- 路由 `/console` → `frontend/src/features/console/console-page.tsx`（`React.lazy`，包在 `RequireAuth` 下）；页面内 `is_super_admin` 检查（非管理员显示权限 Alert，照 `system-settings.tsx`）。
- 侧边栏：`app-sidebar.tsx` `isAdmin` 条件追加「控制台」项。
- 布局：上=用户表（用户名/角色/注册时间/状态/文件数；本地过滤用户名，用户量小不做服务端搜索）；点击行 → 下=该用户文件表（服务端分页：文件名/类型/大小/上传时间/来源/下载按钮 + 分页器）。
- API 封装：`frontend/src/services/adminApi.ts`（axios 实例 get，照 `wordPoolsApi.ts` 模式）。
- Query：中央 `lib/query-keys.ts` 增加 `admin.users` / `admin.files(username, page)`；hook 就近写在页面文件（ TanStack Query `useQuery` + 文件表分页换 key）。
- 下载：复用 `downloadFile(url, filename)`。
- i18n：全部走 `t()`，zh/en 同步加键（parity 测试把关）。

## 6. 错误处理

- 未登录 401（router 级 require_auth 链）、非 super_admin 403（require_super_admin）。
- 用户不存在 / 文件不存在 / 文件属主不符 / 磁盘缺失：一律 404 + 明确 message（不泄露他人文件存在性）。
- 前端 Query error 态展示错误信息；下载沿用 `downloadFile` 的 401 处理。

## 7. 测试计划

- 后端新增 `backend/tests/test_admin_api.py`（照 `test_audit_api.py` 模板：tmp auth 文件 + `create_user`/`create_token` 直签）：
  1. 未登录 401；普通用户访问三个端点各 403；
  2. `/admin/users` 返回注册用户且 file_count 正确（排除软删除）；
  3. `/admin/users/{u}/files` 分页/排序/只含该用户文件；不存在用户 404；
  4. download 成功返回原字节 + Content-Disposition 文件名；跨用户 file_id 错配 404；路径穿越拒绝；
  5. view/download 审计记录落盘断言。
- `count_by_owner()` 单测（含软删除排除、无 owner 记录回落 local_user 口径）。
- 前端：不新造组件渲染测试基建（仓库现状无先例）；i18n parity 测试自动覆盖新键；tsc/vitest/build 全绿为门禁。

## 8. 验收标准（对齐 Issue #77 五条）

1. super_admin 登录后侧边栏见「控制台」；普通角色不可见，直连 API 得 403（测试锁）。
2. 用户列表含用户名/注册时间/角色/状态/文件数。
3. 文件清单含文件名/大小/上传时间/来源，分页可用。
4. 可下载原文件，走后端鉴权流，不暴露磁盘路径。
5. 查看与下载动作进审计日志，现有审计面板可查。

## 9. 部署注意

merge 后实例需**重启 backend + 重建前端 dist** 才生效（后端新路由 + 前端新页面）。
