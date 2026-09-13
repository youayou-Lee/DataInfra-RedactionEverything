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

> 2026-09-13 冷启动排障后定稿。**实例重启/换人/再排障时先读这份**，不要按仓库里 `start_cloud.sh` 的名字猜——那是 NVIDIA 拓扑的历史脚本，本实例不用它。
> 本文档同步存放：仓库 `docs/` + 实例 `/root/ENVIRONMENT.md`（持久）。

## 一、唯一启动入口

```bash
bash /root/start_core.sh        # 实例上的脚本，不在仓库里
```

冷启动全程约 **13 分钟**（瓶颈是模型加载，不是出错）：NER 138s → LA 630s → backend 15s → frontend 3s。脚本自带健康等待与状态汇总。OCR 窗口按注释单独处理（见下）。

## 二、服务拓扑（tmux 会话 redaction）

| 窗口 | 服务 | 端口 | 进程/venv | 备注 |
|---|---|---|---|---|
| ner | **NER = transformers 自包服务**（不是 vllm！） | 8080 | `/root/.venvs/nl/bin/python` 跑 `cloud-deploy/ner_transformers_server.py` | 模型 HaS_Text_0209_0.6B，卡0 |
| locateanything | LocateAnything-3B（HF 模式） | 8090 | 同上 nl venv | 系统 DCU torch 2.5.1（venv --system-site-packages 继承） |
| backend | FastAPI | 8000 | `/root/.venvs/app/bin/python`（uvicorn，调优参数在 `/root/respawn_backend_tuned.sh`） | |
| frontend | vite preview（dist 已构建） | 10800 | node 在 `/root/.local/node-v22/bin` | SCNet 端口映射只能 8000+，本地访问走 ssh -L 隧道 |
| ocr | PP-StructureV3 | 8082 | `/root/.venvs/paddle-25041` | paddle-25041 冒烟通过后才起；GPU 化需同样补 hip/lib 环境 |

`.env`（`/root/redaction/DataInfra-RedactionEverything/.env`）：backend 软链指向它；已补 `VLLM_VENV_DIR` / `LOCATE_ANYTHING_DEPS`（仅为兼容旧脚本，start_core.sh 不需要）。

## 三、venv 真相表（踩过坑的都标了）

| venv | 用途 | 状态 |
|---|---|---|
| `/root/.venvs/app` | backend | ✅ 正役（python3.10，也装了 dtk 版 vllm 0.9.2 但不用） |
| `/root/.venvs/nl` | NER + LA | ✅ 正役（python3.10，**继承系统 dist-packages 的 DCU torch**） |
| `/root/.venvs/paddle-25041` | OCR | ✅ 正役 |
| `/root/.venvs/vllm` | ~~CUDA 版 torch2.6/vllm0.8.5~~ | ⚠️ **废件，勿用**。2026-09-13 曾误救活（python3.12 链到 `/root/.local/py-runtime`），留着无害但不要拿它起服务 |

## 四、GPU 与环境（本机最重要的两条）

1. **GPU 是海光 DCU（K100_AI 64GB），一切探针用 DCU 生态**：
   ```bash
   python3 /opt/dtk-25.04.2/bin/hy_smi.py -i      # 设备在位
   # 绝不用 nvidia-smi / libcuda / /proc/driver/nvidia —— 本机没有这些，误判过一次
   ```
2. **DTK 环境必须补一行**（`/opt/dtk/env.sh` 导出的 LD_LIBRARY_PATH **漏了 `hip/lib`**，海光 `libgalaxyhip.so.5` 在那里；已修进 start_core.sh 的 DTK_EXPORT，手动起服务时必须带上）：
   ```bash
   source /opt/dtk/env.sh
   export LD_LIBRARY_PATH=/opt/dtk/hip/lib:$LD_LIBRARY_PATH
   ```

## 五、已知坑（2026-09-13 冷启动实录）

- **`/opt/conda` 在某次重启后只剩空壳**：曾提供 python3.12（vllm venv 链接目标）。该 venv 是 CUDA 废件所以无实际影响；如需 python3.12，运行时已备在 `/root/.local/py-runtime`（持久）。
- tmux 会话死掉 = 9月8日搭建时的**全部手工环境消失**，服务必须走 start_core.sh 重建，不要指望「重启前的窗口还在」。
- 本机 `grep` 是 ugrep 别名且 zsh 不分词：产物检索用 `/usr/bin/grep` 并给文件名加引号。
- 下载：本机直连 GitHub release 慢/超时，实例走平台代理（start_core.sh 里有）通常可行。

## 六、验收/调试常用

```bash
curl --noproxy '*' http://127.0.0.1:8000/health/services   # 后端视角的服务总健康
curl --noproxy '*' http://127.0.0.1:8080/health            # NER
ssh -N -L 18000:localhost:10800 scnet-main                    # 本地隧道（用户页面入口）
```
