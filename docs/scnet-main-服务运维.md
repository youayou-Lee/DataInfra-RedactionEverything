---
created: 2026-09-13
tags: [deploy, scnet-main, ops]
相关: "[[SSH-云实例信息]] · 服务拓扑与 venv 详见仓库 main 分支《scnet-main-环境清单》（实例 /root/ENVIRONMENT.md 同步存放）"
---

# scnet-main 服务运维（一键脚本）

全栈五个服务（ner / locateanything / backend / frontend / ocr，tmux 会话 `redaction` 内另有一个 init 常驻窗口，共 6 个窗口），统一入口为仓库内脚本 **`cloud-deploy/scnet-main-ctl.sh`**（实例上位于 `/root/redaction/DataInfra-RedactionEverything/cloud-deploy/`）。替代旧的「/root/start_core.sh + /root/respawn_ocr_gpu_dcu.sh 手工两段式」，脚本不含凭据，可随仓库分发。

## 命令速查

```bash
CTL=/root/redaction/DataInfra-RedactionEverything/cloud-deploy/scnet-main-ctl.sh

bash $CTL start            # 冷启动全部六服务（约 13 分钟，LA 模型加载是瓶颈，不是卡死）
bash $CTL stop             # 全部停止（连 tmux 会话 redaction 一起清）
bash $CTL restart          # 全量重启（= stop + 冷启动）
bash $CTL restart backend  # 单服务重启（backend ~20s；ner/la/ocr 需等模型重载）
bash $CTL status           # 各服务健康 + tmux 窗口 + backend 服务总览
bash $CTL logs ocr         # 尾随某服务日志（ner|locateanything|backend|frontend|ocr）
```

单服务同样适用于 start / stop：`bash $CTL start ocr`、`bash $CTL stop frontend`。

## 行为要点

- **健康等待**：start/restart 逐服务 curl 健康端点（ner 8080 / la 8090 / backend 8000 / frontend 10800 / ocr 8082），OCR 与 LA 超时放宽到 900s。
- **`.env` 自举**：`$UP/.env` 缺失时自动生成（含随机 JWT），`backend/.env` 软链指向它；已存在则不动。
- **前端 dist 缺失时自动 `npm run build`**（node 在 `/root/.local/node-v22/bin`）。
- **代理凭据不入库**：平台代理写在实例侧 `/root/redaction/cloud-deploy/instance.env`（`PROXY_URL=http://user:pass@host:port`），缺省按离线启动（模型全本地，常规起服务不需要代理）。
- **DTK 环境自带两处修正**：`/opt/dtk/hip/lib` 补进 `LD_LIBRARY_PATH`（env.sh 漏了它，libgalaxyhip.so.5 在此）+ MIOPEN 缓存指向持久卷。

## 已知非故障现象（先看这里再报警）

- 页面「文字识别服务离线」徽章在服务恢复后不自动翻转，**强刷（Ctrl+Shift+R）后 15s 内恢复**（前端健康轮询 15s/次、连续 3 次失败才置灰）。
- 前端 preview 启动期 vite 代理报 `ECONNREFUSED 127.0.0.1:8000` 是后端起序问题，无害。
- GPU 探针一律 `python3 /opt/dtk-25.04.2/bin/hy_smi.py -i`，本机没有 nvidia-smi/libcuda。
- 日志按服务分文件在 `/root/redaction/cloud-deploy/logs/`（tmux 窗口内 `tee -a` 追加）。

## 上线版本对齐（preview2.0.0）

实例 `/root/redaction` 是代码快照（非 git 仓库）。升级到新构建的流程：

```bash
# 本地：导出分支代码 → rsync（先备份实例 src）
git archive origin/preview backend/app backend/config | tar -x -C /tmp/rel
rsync -a --delete /tmp/rel/backend/app  scnet-main:/root/redaction/DataInfra-RedactionEverything/backend/
rsync -a --delete /tmp/rel/backend/config scnet-main:/root/redaction/DataInfra-RedactionEverything/backend/
# 前端：rsync src 后在实例重建 dist，再 bash $CTL restart backend frontend
```

验收前先 `/usr/bin/grep -l <功能标识> dist/assets/*.js` 确认部署版本；部署后浏览器必须强刷。
