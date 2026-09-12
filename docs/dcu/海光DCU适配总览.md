# 海光 DCU 适配总览（SCNet 云实例）

> 分支：`feat/dcu-adaptation` ｜ 定位：海光 DCU 平台适配的**唯一权威文档**
> 后续性能调优开发（Issue #23 NER 拆请求/batch 等）一律基于本分支切出。
> 最后更新：2026-09-10 ｜ 全部结论均有实例实测背书

## 0. 一句话结论

**生产组合 = SCNet "BW1000+DTK+vLLM 开发环境" 镜像（DTK 25.04.2）+ 异构加速卡AI（CC 9.2 / 64G）**：
vLLM NER 0.53s/次 + GPU OCR 1.2s/页，五服务全栈 all_online，真实案卷 E2E 跑通。

## 1. SCNet 云实例平台要点

| 项 | 值 |
|---|---|
| 平台 | SCNet 异构云（曙光），控制台 ksai.scnet.cn |
| 区域 | 华东一区（本文所有结论）；持久卷**同账号同区域**共享 |
| 实例形态 | k8s CRD Notebook 容器；SSH `ssh -p <port> root@ksai.scnet.cn`（端口随实例分配） |
| 网络 | 无直连外网，必须走平台代理 `http://<user>:<pass>@10.15.100.43:3120`（凭据见运维原件，不入库） |
| 持久卷 | `/root/private_data/redaction-persist/`——venv/模型缓存/恢复 kit 跨实例继承 |
| 卡型选择 | "异构加速卡AI"（64G）= **卡池调度，分到哪张用哪张**；16G 配置 = Vega20 固定卡型 |
| 镜像 | 平台公共镜像 / 自建 Dockerfile 构建（可指定驱动）/ 运行实例"保存镜像"为私有 |

## 2. 镜像矩阵（实测过的四套）

| 镜像 | DTK | Python | vLLM | torch | 用途 |
|---|---|---|---|---|---|
| vllm0.9（jupyter-vllm0.9.2:ubuntu22.04-dtk25.04.1-829） | 25.04.1 | 3.10 only | 0.9.2+das.opt1.beta.dtk25041 | 2.5.1+das.opt1.dtk25041 | vLLM 可用，**paddle GPU 全灭** |
| **BW1000+DTK+vLLM 开发环境** | **25.04.2** | 3.10.12 | 0.9.2+das.opt1.dtk25042 | 2.5.1+das.opt1.dtk25042 | **生产镜像**：vLLM + paddle GPU 双活 |
| （scnet-paddle 型） | 25.04.2 | 3.10 | 无 | — | paddle GPU 历史验证镜像 |
| DTK26.04 基础镜像 | 26.04 | 3.11.9 | 无（pip 装 vLLM 会毁栈） | 2.9.0+das.opt1.dtk2604 | 早期验证用；OCR 走 CPU |

**py3.10-only 镜像通用适配三件套**（见 §7 kit）：系统级 UTC 垫片、tmux（apt）、`--system-site-packages` venv。

## 3. 卡型矩阵（torch.cuda 报告值）

| torch 报告 | CC | 架构 | 显存 | vLLM | paddle GPU | 备注 |
|---|---|---|---|---|---|---|
| K100_AI / K500SM_AI | 9.2 | gfx926 | 64G | **✓** bf16+cudagraph 正常 | 25.04.1✗ / **25.04.2✓** | 卡池随机发；生产首选 |
| Vega 20 (MI50) | 9.0 | gfx906 | 16G | **✗** 内核级乱码（bf16/fp16 同乱码）+ cudagraph 崩 | 25.04.1✗ / 25.04.2✓（历史） | 只能 transformers NER |
| BW | 9.3 | gfx936 | — | 未测 | ✗ fatbin 缺 ISA（SIGABRT） | 换 DTK 也救不了 |

查卡命令：`source /opt/dtk/env.sh; python3 -c "import torch; p=torch.cuda.get_device_properties(0); print(p.name, p.major, p.minor)"`

## 4. DTK × paddle 兼容性（终局结论）

**paddle-dcu 稳定源全部 7 个版本（3.0.0–3.3.1）在 DTK 25.04.1 上 conv 全崩 `MIOpen std::bad_alloc`，与卡无关**（gfx906/gfx926 双卡同死法；同镜像 torch conv 正常 = wheel↔运行时 ABI 不匹配）。
**DTK 25.04.2 上 3.2.1/3.3.1 GPU 全活**（conv/pool/PP-StructureV3）。唯一历史跑通组合此前为 25.04.2+3.2.1+gfx906（1.61s/页）；现已扩展到 gfx926。

CPU 兜底：纯 CPU wheel `paddlepaddle==3.2.2`（pip 普通源）全镜像可用——DCU wheel 的 CPU 模式有 onednn+PIR 静默 0 框 bug，不要用。

## 5. 实测速度汇总（生产组合）

| 项 | 数值 | 对照 |
|---|---|---|
| NER vLLM（HaS Qwen3-0.6B, bf16） | **0.53s/次** | transformers 2.5s（5 倍） |
| OCR GPU（PP-StructureV3）纯推理 | **1.2s/页**（首页 28s 含 MIOpen 首编） | CPU 128 核 14.9s/页（12 倍） |
| OCR 服务级（HTTP 含序列化） | 2.5–3s/页 | — |
| LA（LocateAnything, HF 模式） | ~10s/页 | vision 管道大头 |
| 单页 vision 全链路（合成样本） | 14.7s | LA 主导 |

### 5.1 真实案卷 E2E 逐案明细（2026-09-10，用户真实卷宗抽检 7 份）

| 文件 | 型 | 页 | vision | 执行 | 端到端 |
|---|---|---|---|---|---|
| 文书卷 | 文本 | 16 | 13.6s | 2.7s | 16.3s |
| 曾庆成文书卷 | 扫描 | 15 | 10.5s | 1.6s | 12.1s |
| 陈朝郁证据卷2 | 扫描 | 28 | 15.3s | 3.1s | 18.4s |
| 陈海新等人中院裁定 | 扫描 | 11 | 22.6s | 1.5s | 24.1s |
| 陈海新裁定 | 扫描 | 2 | 14.5s | 0.3s | 14.8s |
| 陈明飞一审判决书 | 文本 | 6 | 17.8s | 0.5s | 18.3s |
| 朱海军笔录 | 加密 | 20 | FAIL 404 | — | — |

**注意**：vision 是**逐页 API**（`POST /redaction/{id}/vision?page=N`，默认 page=1）——
上表 vision 耗时为"第 1 页"成本。整卷真实成本 ≈ 页数 × 单页管道（~10–23s/页，LA 主导），
如 28 页卷全跑 ≈ 6 分钟。复现脚本：`cloud-deploy/e2e_real_cases.py`。

### 5.2 单页 vision 管道分阶段拆解（backend.log JSON 行重建 + 微基准）

| 阶段 | 单页耗时 | 来源 |
|---|---|---|
| LA（视觉特征） | **~7–10s ← 大头** | elapsed 差值；历史热稳 10.7s |
| OCR（GPU） | 2.5–3.0s | 服务微基准（纯推理 1.2s/页） |
| NER（transformers 8080） | 1.4–4.3s × 2 次 | 日志 `HaS model request finished in 1425~4339ms` |
| 编排+合并 | ~1s | 差值 |
| **合计（实测）** | **10.4–22.6s/页** | `Vision detect elapsed=10.36~22.6s` |

### 5.3 NER 运行时对照（同模型/同模板/贪心，本实例实测）

| 运行时 | 延迟 | 备注 |
|---|---|---|
| **vLLM 0.9.2 (8081)** | **0.49–0.54s** | paged KV + cudagraph；backend 切换只需改 `HAS_BASE_URL` |
| transformers 自包 (8080) | 1.4–4.3s | 全卡可用（gfx906 唯一选项） |
| llama-server（GGUF） | 待实测 | 仓库已有 DTK 脚本（Issue #23 轴B） |

NER 切 vLLM 后单页管道预估 10–12s（LA 仍是大头）——性能优化主战场在 LA 与整卷并行。

## 6. 镜像构建（Dockerfile）

见 `cloud-deploy/Dockerfile.dtk-paddle-vllm`。要点：

- DAS 定制 wheel（torch/vllm/vision）来自**构建内网源 `http://10.16.4.1:8000`**——运行实例不可达，仅 Dockerfile 构建环境可达；直链可从任一现成镜像的 `dist-packages/*dist-info/direct_url.json` 溯源。dtk25.04.2 实测直链：
  - vllm：`/debug/vllm/dtk25.04.2-rc1/vllm-torch251-tag-1025-4ba4b755/vllm-0.9.2+das.opt1.dtk25042-cp310-cp310-manylinux_2_28_x86_64.whl`（sha256=66e02107…）
  - torch：`/debug/pytorch/dtk25.04.2-rc1/torch251-tag/torch-2.5.1+das.opt1.dtk25042-cp310-cp310-manylinux_2_28_x86_64.whl`（sha256=c5064c26…）
- PyPI 的 vllm 会拖 CUDA torch **毁掉 DTK 栈**，绝对禁止 pip install vllm
- paddle 依赖顺序：bcebos safetensors 0.6.2 wheel 先行 → `paddlepaddle-dcu==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/dcu/` → `paddleocr[doc-parser]`
- DTK 需与宿主机驱动匹配（实测 Driver 60325.x 配 25.04.1/25.04.2 均可），构建时平台侧指定驱动

## 7. 快速部署：持久卷恢复 kit

`/root/private_data/redaction-persist/vllm-img-kit/`：`bootstrap_vllm_img.sh` 一键自愈（卡型报告→代码解包→UTC 垫片→venv 软链→npm 修复）+ 全部服务脚本 + README 实测结论。新实例流程：

```
bash …/vllm-img-kit/bootstrap_vllm_img.sh      # 自愈（秒级～2分钟）
bash /root/vllm_ner_25042.sh                   # vLLM NER 8081
bash /root/respawn_ocr_gpu_dcu.sh              # OCR GPU 8082
bash …/vllm-img-kit/start_core.sh              # NER8080/LA/backend/frontend
```

venv 绑 Python 小版本（3.10）不绑镜像——py3.10 镜像间通用，零重建。

## 8. 镜像与 venv 的交付（handoff）

### 8.1 镜像获取四条路（按推荐序）

| 路 | 做法 | 前提 |
|---|---|---|
| **A. 平台社区镜像** | 修好的实例"保存镜像"→ 平台发布/共享到镜像社区，他人直接选用 | 平台控制台支持共享（镜像属主操作） |
| **B. 指引选公共镜像** | "BW1000+DTK+vLLM 开发环境"本就是 SCNet 公共镜像，任何人开实例可选——他人缺的是"选哪个"的知识（本文 §2） | 无 |
| **C. Dockerfile 自建** | `cloud-deploy/Dockerfile.dtk-paddle-vllm` + 平台 Dockerfile 构建（可指定驱动），dtk25.04.2 torch/vllm wheel 直链已内置 | 平台账号有构建功能 |
| **D. 矩阵降级兜底** | 按 §3/§4 矩阵：DTK25.04.1 镜像 → OCR 退 CPU（14.9s/页）；无 vLLM 镜像 → NER 退 transformers（2.5s） | 无 |

**🚨 保存/共享镜像前清理红线（真实案件数据严禁入镜像）**：
`/root/cases/`（测试案卷）、`backend/uploads/` 与 `backend/data/`（上传原件+脱敏成品）、
`/root/e2e_real_report.json`、`/root/audit_scan.out`（审计日志含真实身份证/手机号）、tmux 日志。

### 8.2 venv 交付三种方式

| 方式 | 内容 | 适用 |
|---|---|---|
| **重建（主推）** | `cloud-deploy/venv/requirements-*.txt` ×4 + `cloud-deploy/build_venvs.sh`（过滤系统栈泄漏/paddle 依赖顺序/UTC 垫片全部内置），约 15–25 分钟 | 任何人的新实例 |
| venv tar 快照 | dot-venvs 打包（无敏感数据）放 release；py3.10+路径一致即用 | 跳过安装 |
| 持久卷 | SCNet 同账号同区域共享 | 仅自己账号（他人不可见） |

注意：持久卷**不同账号不共享**，"下载别人的 venv"在平台上走不通；重建是通用路径。
venv 只绑 Python 小版本（3.10）不绑镜像——快照/卷在任意 py3.10 镜像通用。

## 9. 已知问题（真实数据 E2E 发现，2026-09-10）

1. **扫描件泄漏（结论已修正）**：成品逐页 OCR 审计发现 4 份扫描件中 3 份在 p2+ 残留身份证/手机号——根因是 **vision 为逐页 API（默认 page=1），E2E 脚本只调用了第 1 页**：p1 处理质量良好（无漏检），p2+ 属**未处理**而非漏检；文本型卷零残留因 execute 文本路径覆盖全文字层。**待验证**：整卷流程（逐页循环/batch 端点）的覆盖率与整卷耗时。审计要点：扫描件成品的泄漏审计必须 OCR（文本层提取是假阴性），工具 `cloud-deploy/audit_scan_output.py`。
2. **加密案卷**：真实卷宗存在 user-password 加密 PDF（上传后 vision 404 "document closed or encrypted"），需前置解密或明确报错引导。
3. vision 响应 `entities` 恒空、实体信息在 `bounding_boxes`（API 契约待理顺）。

## 10. 坑清单（15 条，按类）

**DTK/驱动**
1. 非交互 shell 必须显式 `source /opt/dtk/env.sh`，且 `set +u; source; set -u` 包裹（env.sh 引用未定义变量）
2. `export A=1 B=$A` 同语句先展开——代理变量分两行写
3. 空 `CUDA_VISIBLE_DEVICES=` 会让 DCU wheel 探卡失败（Hip error(100) SIGABRT）——删掉空值，别设空

**Python 环境**
4. py3.10-only 镜像：卷上 py3.11 venv 全废，需 `python3.10 -m venv --system-site-packages` 重建
5. `datetime.UTC`（py3.11）在 22 处——**系统级** sitecustomize 垫片（venv 内的被 /usr/lib/python3.10 遮蔽）
6. transformers 需 ≥4.57.1（4.55 不认 `dtype=` kwarg）
7. pip --target 解析陷阱：peft 拖 CUDA torch ~2G——`--no-deps` 但整份 requirements --no-deps 又会 pydantic 对撞，只补缺失本体包

**NER**
8. DTK 无 flash_attn_2_cuda：SDPA 禁 flash/mem_efficient + 模型 eager 注意力
9. HaS 模型 generation_config eos 指 `<|endoftext|>` 而对话以 `<|im_end|>` 结束——不显式指定则每次拉满 max_tokens 且复读
10. vLLM 在 gfx906（CC9.0）输出乱码（bf16/fp16 逐字相同，内核级）+ cudagraph 捕获必崩（`--enforce-eager` 只救启动不救数值）——**该卡型禁用 vLLM，退 transformers**
11. 16G 卡上 vLLM 与其他服务共卡：`--gpu-memory-utilization` 按整卡比例预算，需调大（0.25→0.5）

**OCR/paddle**
12. paddlex 平台连通性探测不走代理——`PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True`；DCU 模型白名单挡 PP-LCNet——`PADDLE_PDX_DISABLE_DEV_MODEL_WL=true`；模型补拉必须带代理
13. DCU wheel CPU 模式 onednn+PIR 静默 0 框——CPU 场景用纯 CPU wheel 3.2.2
14. paddle GPU 崩溃会在 cwd 留 2G/个 core dump——`ulimit -c 0` 禁用

**运维**
15. `pkill -f xxx` 若模式串在同条 ssh 命令明文出现会自杀（exit 255）；保存镜像**全程实例必须开机**（commit 中途关机=容器消失）

## 11. 相关文档

- `docs/SCNet-K100_AI-DCU-DTK26.04-部署教程.md`（PR #10，DTK26.04 时代教程）
- 运维原件（工作区 `docs/deploy/`，含真实凭据，不入库）：`SSH-云实例信息.md`、`云实例依赖配齐手册.md`、`DCU极速部署指南.md`、`多卡DCU部署记录-2x16g.md`、`Paddle-DCU-部署验证记录.md`、`SCNet K100_AI DCU DTK26.04 部署教程.md`
- 脚本：`cloud-deploy/respawn_ocr_gpu_dcu.sh`（OCR GPU）、`cloud-deploy/vllm_ner_25042.sh`（vLLM）、`cloud-deploy/paddle_conv_probe.sh`（paddle 判别器）、`cloud-deploy/e2e_real_cases.py` + `audit_scan_output.py`（真实案卷 E2E 与泄漏审计）
