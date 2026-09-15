---
title: scnet-main 实例环境清单
tags:
  - deploy/scnet
aliases:
  - 环境清单
  - ENVIRONMENT
---

> [!tip] 相关文档
> 实例地址/账号/历史实例：[[SSH-云实例信息]] · 验收入口：[[手动验收-Issue33-会话保持]] · 设计背景：[[issue-33-playground-session-restore]]（merge 后生效）

# scnet-main 实例环境清单（权威版）

> 2026-09-15 换新实例重新部署后更新（原 2026-09-13 冷启动排障定稿）。**实例重启/换人/再排障时先读这份**，不要按仓库里 `start_cloud.sh` 的名字猜——那是 NVIDIA 拓扑的历史脚本，本实例不用它。
> 本文档同步存放：仓库 `docs/` + 实例 `/root/ENVIRONMENT.md`（持久）。
> 2026-09-15 实录：ksai:10451 新实例（镜像=2026-09-10 私有镜像快照），bundle 传 preview2.0.0 → bootstrap.sh 补软链 → `scnet-main-ctl.sh start`。唯一坑：新克隆代码树缺 `backend/models` 软链（不入库），bootstrap 自修。

## 一、唯一启动入口

```bash
bash /root/redaction/cloud-deploy/scnet-main-ctl.sh start    # 仓库脚本（preview 分支 cloud-deploy/），start/stop/restart/status/logs
```

冷启动全程约 **13 分钟**（瓶颈是模型加载，不是出错）：NER 138s → LA 630s → backend 15s → frontend 3s（构建 dist 时另加 1-3 分钟）。脚本自带健康等待与状态汇总。单服务重启秒级（backend ~20s）。

## 二、服务拓扑（tmux 会话 redaction）

| 窗口 | 服务 | 端口 | 进程/venv | 备注 |
|---|---|---|---|---|
| ner | **NER = transformers 自包服务**（不是 vllm！） | 8080 | `/root/.venvs/nl/bin/python` 跑 `cloud-deploy/ner_transformers_server.py` | 模型 HaS_Text_0209_0.6B，卡0 |
| locateanything | LocateAnything-3B（HF 模式） | 8090 | 同上 nl venv | 系统 DCU torch 2.5.1（venv --system-site-packages 继承） |
| backend | FastAPI | 8000 | `/root/.venvs/app/bin/python`（uvicorn；调优参数已内联在 ctl 脚本 backend 段） | |
| frontend | vite preview（dist 已构建） | 10800 | node 在 `/root/.local/node-v22/bin` | SCNet 端口映射只能 8000+，本地访问走 ssh -L 隧道 |
| ocr | PP-StructureV3 | 8082 | `/root/.venvs/paddle-25041` | paddle-25041 冒烟通过后才起；GPU 化需同样补 hip/lib 环境 |

`.env`（`/root/redaction/DataInfra-RedactionEverything/.env`）：backend 软链指向它；已补 `VLLM_VENV_DIR` / `LOCATE_ANYTHING_DEPS`（仅为兼容旧脚本，start_core.sh 不需要）。

## 三、venv 真相表（踩过坑的都标了）

| venv | 用途 | 状态 |
|---|---|---|
| `/root/.venvs/app` | backend | ✅ 正役（python3.10，也装了 dtk 版 vllm 0.9.2 但不用） |
| `/root/.venvs/nl` | NER + LA | ✅ 正役（python3.10，**继承系统 dist-packages 的 DCU torch**） |
| `/root/.venvs/paddle-25041` | OCR | ✅ 正役（目录名是创建时代的旧叫法，现役 DTK 25.04.2 下使用，与 25.04.1 无关） |
| `/root/.venvs/vllm` | ~~CUDA 版 torch2.6/vllm0.8.5~~ | ⚠️ **废件，勿用**。2026-09-13 曾误救活（python3.12 链到 `/root/.local/py-runtime`），留着无害但不要拿它起服务 |

## 四、GPU 与环境

### 4.1 当前实例（2026-09-15 起）

1. **GPU 是海光 DCU（K500SM_AI 64GB，gfx928，300W 功耗帽；rocminfo/hy_smi.py 实测），一切探针用 DCU 生态**：
   ```bash
   python3 /opt/dtk-25.04.2/bin/hy_smi.py -i      # 设备在位
   # 绝不用 nvidia-smi / libcuda / /proc/driver/nvidia —— 本机没有这些，误判过一次
   ```
   宿主：Hygon C86 7185 32 核 / 503G 内存 / Ubuntu 22.04.5（旧实例 7490×2/128核/1007G；核数为旧 1/4、内存减半）。
2. **DTK 环境必须补一行**（`/opt/dtk/env.sh` 导出的 LD_LIBRARY_PATH **漏了 `hip/lib`**，海光 `libgalaxyhip.so.5` 在那里；已修进 scnet-main-ctl.sh 的 DTK_EXPORT，手动起服务时必须带上）：
   ```bash
   source /opt/dtk/env.sh
   export LD_LIBRARY_PATH=/opt/dtk/hip/lib:$LD_LIBRARY_PATH
   ```

### 4.2 卡型可能切换（换卡/换实例先查这张表）

SCNet 租用实例**卡型可能变化**（09-15 就从 K100_AI 换成了 K500SM_AI），新卡到手第一件事：`rocminfo | grep gfx` 看架构，再对表判断 wheel 兼容性。

| 卡 | gfx / 显存 | 实测结论 |
|---|---|---|
| **K500SM_AI** | gfx928 / 64G / 300W | **现役**（2026-09-15 起）：全栈 e2e 全绿，OCR GPU 1.27s/页 |
| K100_AI | gfx928 / 64G / 400W | 旧主力（09-10~09-14）：全栈验证最充分；与 K500SM_AI 同架构，wheel 全兼容 |
| BW | gfx936 / 64G / 1000W | **paddle 不能跑**（09-13 定案：eager 静默全零 + 推理 Eigen Slice SIGABRT），OCR 只能 CPU |
| Vega20 / MI50 系 | gfx906 / 16G×2 | 老架构验证机（2x16g，已关机）：transformers 可用、**vLLM 内核级乱码**、paddle GPU 可用但需 gflag 预设、性能差 |

### 4.3 DTK 版本记录（主力 25.04.2）

| 版本 | 状态 |
|---|---|
| **DTK 25.04.2** | **主力，唯一现役**。本清单全部结论基于它（vLLM 0.9.2 DAS 镜像、py3.10.12、torch 2.5.1） |
| DTK 25.04.1 | **明确 bug：OCR GPU 模式不可用（MIOpen 崩），25.04.2 正常** → 弃用不用 |
| DTK 26.x | 未选用。记载的问题：①无官方 paddle-GPU 轮子（OCR 只能 CPU，14s/页）；②gflag 坑 2026-09-09 在 26.04+gfx906 首录（paddle 3.2.1/3.2.2/3.3.1 同炸，见归档《多卡DCU部署记录-2x16g》坑⑨）；llama.cpp 26.04 编译未测 |

> 注意：**gflag 坑（OCR 静默 0 框）与 DTK 版本无强绑定**——26.04 首录、09-15 在 25.04.2 换环境后也复现（同 wheel）。结论：OCR spawn **一律**预设 `FLAGS_conv_workspace_size_limit=2000`，不赌环境。

## 五、已知坑（2026-09-13 冷启动实录；09-15 新实例复检更新）

- **OCR 静默 0 框（2026-09-15 新实例实锤）：spawn 必须预设 `FLAGS_conv_workspace_size_limit=2000`**。不预设时 paddle 3.2.1 建 GPU predictor 内部 `SetGflag` 该 flag 失败（analysis_predictor.cc 源码带环境守卫：env 已有值则跳过内部设置），init 异常被 ocr_server 吞掉 → `/health` 照常 ready 但 `/structure` 永远 `boxes:[]`。判据：`structure_ready` 字段 + 真图一发看 boxes 数。历史脚本 `perf-work/deploy_fast_v2.sh` 一直带此预设，preview2.0.0 的 ctl 脚本漏带，09-15 新实例冷启动即复现；已修回（fix/ocr-gflag-env）。
- **新克隆代码树缺 `backend/models` 软链**（软链不入库）：预检会报「缺少 HaS 模型 config.json」，跑一遍持久卷 `bootstrap.sh` 自修。
- **paddlex 把 gpu 归一化陷阱**：手动排障时给 OCR 换 `OCR_DEVICE=gpu:0` 反而会踩同一 gflag 坑（gpu 分支不设该 env），别用，统一 `dcu:0` + 上面的 env 预设。
- `/opt/conda` 是空壳（目录在但无 bin/python，09-15 新实例实测与 09-13 旧实例状态一致）；nl/app venv 均为 py3.10 自足，无实际影响。
- tmux 会话死掉 = **全部手工环境消失**，服务必须走 scnet-main-ctl.sh 重建，不要指望「重启前的窗口还在」。
- OCR venv 直用持久卷 `dot-venvs/paddle-25041`（ctl 脚本 PY_OCR 直指卷上，不拷贝）；`/root/.venvs` 本身是指向 `dot-venvs` 的软链。
- 本机 `grep` 是 ugrep 别名且 zsh 不分词：产物检索用 `/usr/bin/grep` 并给文件名加引号。
- 下载：本机直连 GitHub release 慢/超时，实例走平台代理（scnet-main-ctl.sh 读 `cloud-deploy/instance.env` 的 PROXY_URL）通常可行。
- `/auth/register` 等 POST 已加 CSRF 双提交校验：脚本调用先 GET `/api/v1/auth/status` 取 `csrf_token` cookie，POST 时带同名 cookie + `x-csrf-token` 头。

## 六、验收/调试常用

```bash
curl --noproxy '*' http://127.0.0.1:8000/health/services   # 后端视角的服务总健康
curl --noproxy '*' http://127.0.0.1:8080/health            # NER
ssh -N -L 18000:localhost:10800 scnet-main                    # 本地隧道（用户页面入口）
```
