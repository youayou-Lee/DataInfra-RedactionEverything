# CLAUDE.md — DataInfra-RedactionEverything（源码侧）

> 本仓库是 `ttttccxxui/DataInfra-RedactionEverything` 的 fork（origin = `youayou-Lee/...`）。工作区整体指引（部署、规范、实例）在上一级 `../CLAUDE.md` 与 `../docs/`；本文件只管源码本身。

## 自含铁律（即使没载入上级 CLAUDE.md 也必须遵守）
1. **upstream 只读**：不对 `ttttccxxui/...` 提 Issue/PR/push；`gh` 一律显式 `--repo youayou-Lee/DataInfra-RedactionEverything`。
2. **分支从 preview 切，合入 preview**（不是 main）：`feat|fix|perf/issue-N-主题`；main 只接收晋级 PR 与 hotfix。模型与晋级/hotfix 细则见 `../docs/WORKFLOW.md` §7。
3. **敏感数据绝不入库**：真实案卷、SCNet 凭据/IP 不进代码、文档、commit message、本文件（本文件随仓库公开）。
4. **开发门禁（`../docs/开发门禁-测试与独立评审规范.md`）是铁律**：验收标准前置、独立 review、用户手动验收放行后才 merge。

## 服务拓扑（docker compose --profile gpu）
| 容器 | 端口 | 说明 |
|---|---|---|
| frontend | 3000 | 工作台界面（Node） |
| backend | 8000 | FastAPI 编排服务 |
| ocr | 8082 | PP-StructureV3 OCR |
| ner | 8080 | HaS Text NER（vLLM） |
| visual-features | 8090 | LocateAnything-3B |

自研 yoloe-service（8095）在仓库外 `../yoloe-service/`，不在本 compose 内。

## 常用命令
```bash
# 前端门禁（本地可跑）
cd frontend && npm run build && npm test && npm run lint
# 后端测试（本地可跑）
cd backend && pytest tests/
# 本地起全栈（NVIDIA 小卡可用；首次构建较久）
docker compose --profile gpu up -d
```

GPU 栈（paddle/vLLM/DTK）相关改动本地**不可验证**，必须上云实测（门禁④）；云上部署与 DCU 差异见 `../docs/deploy/` 与 `../cloud-deploy/`。

## worktree（多功能并行开发）
主检出留给集流与发布操作；每个功能分支一个独立 worktree，位置固定 `../.worktrees/<分支名>`（工作区根下、仓库外）。创建与 setup：

```bash
# 在主检出执行（先快进本地 preview，不切分支；分支名含 / 时目录保留全名）
git fetch origin preview:preview
git worktree add ../.worktrees/feat-issue25-x -b feat/issue25-x preview
cd ../.worktrees/feat-issue25-x
# setup：不入库的东西从主检出补（主检出在 ../../DataInfra-RedactionEverything）
cp ../../DataInfra-RedactionEverything/.env .    # 主检出没有则从 .env.example 新建
ln -s ../../DataInfra-RedactionEverything/backend/models backend/models   # 模型权重软链，不重复占盘；不跑 GPU 栈可跳过
cd frontend && npm install
# 基线（动手前必须绿）：cd backend && pytest tests/  +  cd frontend && npm run build && npm test && npm run lint
# 收尾（PR squash 合并后；先回主检出——不能在 worktree 内部删它自己）：
cd ../../DataInfra-RedactionEverything
git worktree remove ../.worktrees/feat-issue25-x && git branch -D feat-issue25-x
```

注意：
- **worktree 只包含已提交的文件**——`.env`、`backend/models/`、`node_modules/`（.gitignore L9/L21/L34）乃至仓库里未提交的文档都不会出现，按上面 setup 补；
- worktree 内工作区文档/规范在 `../../docs/`（不是 `../docs/`）；
- 每个 worktree 独立一份 node_modules/构建产物（几百 MB 量级），模型权重靠软链共享；用完即删，不囤。

## 模型权重（不入库，手动放置）
- `backend/models/has/HaS_Text_0209_0.6B/` — NER（HF，MIT）
- `backend/models/locateanything/LocateAnything-3B-HF/` — 视觉特征（NVIDIA 非商用许可）

## 文档与坑
- 方案文档：`docs/issue-<编号>-<主题>.md`（背景/分析/方案/验收标准），Issue 正文链接之
- 坑按域沉淀总览（如 `docs/dcu/海光DCU适配总览.md`）：通用坑进域文档，一次性坑留 issue 文档互链
