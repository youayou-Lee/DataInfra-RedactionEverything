# Issue #23 设计：NER 拆请求 + batch 推理（单页 NER 31s → 5s 级）

> 分支：`perf/issue23-ner-batch`（基于 main）。
> 状态：设计稿，待用户确认后进入开发（七道门 ①→②）。
> 上游依据：Issue #23、Issue #11 实测数据（`docs/issue-10-processing-performance.md`）。

## 0. 需求背景与产品确认（门⓪结论引用）

以下结论已在 2026-09-08~09 与用户对齐（战略层），本设计全部继承，不再重新讨论：

1. **目标**：在国产 DCU 平台上把 NER 阶段从单页 31.4s（K100_AI）/ 51.5s（Vega20 2x16g 实例）
   压到 ≤10s（K100_AI 折算），配合已完成的 OCR-GPU（14.2→3.4s）使单页端到端进入 10~15s 量级。
2. **方向**：拆请求（按类型分组，本文档主体）+ 服务端真 batch + 推理引擎更换
   （torch eager → llama.cpp HIP），可选模型更换（HaS 0.6B → 通用 Qwen2.5 1.5B/3B + Prompt）。
   用户战略原话方向：「本地部署小参数量（可稍大）LLM，用 Prompt 达到 NER/HaS_Text 效果」。
3. **分工承诺**：用户出 OCR-GPU（已完成）与多卡（2x16g 已到位）；拆请求/batch 改造由本仓库负责。
4. **量化档位**（换 llama.cpp/GGUF 路线时）：0.6B 只用 Q8_0；1.5B 用 Q6_K~Q5_K_M（Q4 是悬崖）；
   3B+ 可 Q4_K_M。核心策略「更大模型+更狠量化」优于「小模型+轻量化」。
5. **质量闸门（一票否决，用户确认）**：证件/电话/账号等数字实体**逐字符精确匹配率 100%**
   （错一位=漏脱敏）；实体级召回 ≥ HaS_Text 基线 −1pp（matcher 只兜精确率，召回无人兜底）。
6. **路线风险已确认**：llama.cpp 在 DTK 26.04 编译未验证 → 退守 transformers batch generate
   （预期收益减半，仍值得做）。

## 1. 现状与瓶颈分解

### 1.1 现有形态（2026-09-09 实测于 scnet-2x16g，Vega20/gfx906）

| 层 | 现状 | 文件 |
|---|---|---|
| 模型 | HaS_Text_0209_0.6B（Qwen2 架构，bf16，trust_remote_code） | `backend/models/has/HaS_Text_0209_0.6B` |
| 服务 | transformers 自包 OpenAI 兼容服务，**进程内 `threading.Lock` 串行推理**，eager attention | `cloud-deploy/ner_transformers_server.py` |
| 契约 | `POST /v1/chat/completions`，单条 user 消息（无 system），输出严格 JSON `{类型名: [实体]}` | `backend/app/services/has_client.py:159-247` |
| 请求粒度 | **整页文本 + 9 类型 = 单请求**，输出 200~300 token，逐 token 解码 | `backend/app/services/has_service.py:111-160` |
| 并发闸门 | `HAS_NER_GLOBAL_MAX_INFLIGHT=1`（跨服务 GPU 闸门）+ 服务端 Lock 双重串行 | `backend/app/core/gpu_inference_gate.py` |

瓶颈本质：**decode 阶段是访存带宽瓶颈**。单请求逐 token 生成时每步都要读全部权重，
batch 摊薄每 token 的权重读取，理论接近线性加速直到 compute 饱和（区别于 #11 实测的
「多进程多实例」——单卡 compute 饱和时多实例互相拖慢 <2x；单进程 batch 共享权重无此问题）。

### 1.2 已有的可复用基础设施

- **类型多批执行已存在**：`has_service.py` 的 `_iter_ner_type_batches()` 按 token 预算
  （`HAS_NER_TYPE_BATCH_TARGET_TOKENS=8072`）+ 类型数上限把类型切多批，
  `extract_entities()`（:269）已用 `asyncio.Semaphore`（`HAS_NER_MAX_PARALLEL_REQUESTS`）
  并发执行批次并合并。9 类型默认场景因 token 预算宽裕走单批——**拆分组只需在批次生成
  处加语义分组路径，并发/合并/失败处理全部复用**。
- **llama.cpp 契约层本就支持**：`has_client.py` 即为 llama.cpp OpenAI 接口编写；
  启动器 `backend/scripts/start_has_python.py`（llama-cpp-python，chatml）；
  运行时配置 `HAS_TEXT_RUNTIME` / `ner_runtime.py` / `/ner-backend` API 已有 llamacpp 分支。
- **GGUF 模型可得**：官方 `xuanwulab/HaS_4.0_0.6B_GGUF`（Q4_K_M）；Q8_0 可本地用
  `convert_hf_to_gguf.py` + `llama-quantize` 自制（0.6B 按档位须 Q8+，见 §0.4）。
- **双实例 LB 经验**：#11 已验证 NER 双实例 + 异步 LB（`perf-work/ner_lb.py`），
  本期服务端 batch 是其替代方案（单副本内 batch 优于跨进程多实例）。

## 2. 方案设计

三个正交改造轴，各自带开关、独立回退，实验数据决定最终组合（见 §3 决策树）：

- **轴A 拆请求**（backend，模型无关）：类型语义分组并发。
- **轴B 服务端 batch + 引擎**（服务端）：llama.cpp HIP（主）/ transformers batch generate（退守）。
- **轴C 模型**（实验轴）：HaS 0.6B（基线/对照）vs Qwen2.5 1.5B/3B（用户战略候选）。

### 2.1 轴A：类型语义分组（`has_service.py`）

**分组映射**（默认 9 类固定三组，语义聚类 + 输出 token 均衡）：

| 组 | 类型（默认勾选集） | 依据 |
|---|---|---|
| G1 人员/组织 | 姓名、机构名称 | 高频长实体列表，同组互为消歧上下文 |
| G2 标识号码 | 身份证号、护照号、电话、银行卡号、邮箱 | 格式化短实体；**数字保真一票否决区**；格式特征互斥性强，同组利于模型区分相近号码类型 |
| G3 时空描述 | 地址、日期 | 长实体（地址）与短实体（日期）互补 |

预期整页 9 类型 200~300 输出 token → 每组 60~100，墙钟（batch 后）≈ 最长组 token 数 × 单 token 延迟。

**动态分组算法**（类型是用户可勾选的开放集合，68 预置 + 自定义，必须对任意集合工作）：

1. 用户勾选类型按 `NER_TYPE_GROUP_MAP`（id→组标签，G1/G2/G3）查表分配；
2. 未命中映射的类型（自定义/非默认勾选）→ 沿用现有 `_pack_ner_type_batches` 的
   token 预算分桶逻辑，作为第 4+ 批（不与固定组混合，避免语义污染）；
3. 组数上限 `HAS_NER_TYPE_GROUPS_MAX`（默认 3+兜底批）；单组类型数超
   `HAS_NER_MAX_TYPES_PER_REQUEST` 时组内再按现有逻辑拆；
4. 单元素组不单独成请求（并入兜底批），避免空转。

**执行与合并**：每组一个 `client.ner(text, group_types)` 调用，`asyncio.gather` +
既有信号量并发；合并 = 结果 dict 按 key update（组间类型集合构造上不相交，无冲突路径）。

**失败语义**（与现状等价，不静默丢类型）：
- 组级失败：走既有 `retry_sync`（2 次退避）→ 仍失败则**整页失败**（现状单请求失败同语义）；
- 组级截断（`finish_reason=length`）：既有「缺失类型补查 + 末桶残值丢弃」逻辑按组独立生效；
- 组间结果类型集合并后必须 ⊇ 请求类型集，否则按失败处理（防御性校验）。

**共指/coref 兼容性**：类型分组只改变 `ner()` 端点的调用粒度。hide/pair/seek 的输入是
NER **合并后**的完整结果（assistant 回填，`has_client.py:672-707`），无组概念；
pseudonym 的 coref 聚合、hybrid_ner 三阶段均消费合并后实体列表——均不受影响。
（Issue #23 风险项「类型拆分丢跨类型上下文」的实测验证见 §3 E4-A/B。）

**开关与默认值**：`HAS_NER_TYPE_GROUPING`（`semantic` | `off`，**默认 off = 现状行为**）。
云实例验证通过后部署侧显式开启；保留一键回退。

**两链路共用**：文本链路（`hybrid_ner_service` chunk 后）与视觉链路
（`has_text_analysis.py` 整页聚合文本）都经由 `HaSService.extract_entities` → 同一分组逻辑生效，
验收覆盖两条链路。

**并发闸门联动**：分组并发后，`HAS_NER_MAX_PARALLEL_REQUESTS` 需 ≥ 组数（部署值 3~4）；
`HAS_NER_GLOBAL_MAX_INFLIGHT`（跨服务 GPU 闸门）在 batch 服务落地后由实验定值
（现状 1 是防 OCR/NER/LA 争抢的保守值，#11 双实例时曾验证 6 可行）。

### 2.2 轴B：服务端真 batch + 推理引擎

**路线 L（主）：llama.cpp HIP on DTK 26.04**

- 编译：`cmake -DGGML_HIP=ON -DAMDGPU_TARGETS=<gfx906|K100 target>`，产物 `llama-server`；
- 服务：`llama-server`（C++ 原生 continuous batching，`-np` parallel slots，`-c` 按副本
  KV 预算），OpenAI 兼容端点 → **backend 零改动**（`has_client` 直连）；
- chatml 模板：HaS 与 Qwen2.5 GGUF 均内嵌 Qwen 系模板，`--chat-template` 兜底 chatml；
- 停止符：`<|im_end|>`/`<|endoftext|>`（Qwen 词表，与现 transformers 服务处理一致）；
- 显存（Issue #23 分析）：3B Q4_K_M ≈2G 权重 + batch16×2k ctx KV <3G → 单副本 <6G，
  16G 卡富余、64G 卡可多副本；量化再减半。
- 现有 `start_has_python.py`（llama-cpp-python）保留为调试入口，生产用 `llama-server`。

**路线 T（退守）：transformers batch generate**

- `ner_transformers_server.py` 改造：入口队列 + ~250ms 攒批窗口 + left-padding
  batch `generate()`（2-3 组天然同长近似，padding 浪费小）+ 移除全局 `threading.Lock`；
- 预期收益减半（eager 算子效率低是根因，batch 只摊带宽；Issue #23 退守预期 8~10s）。

### 2.3 轴C：模型矩阵（实验轴，数据说话）

| 候选 | 量化 | 角色 |
|---|---|---|
| HaS_Text_0209_0.6B | bf16 transformers | **质量 + 性能双基线**（现状） |
| HaS_Text_0209_0.6B | GGUF Q8_0 | 换引擎对照：隔离「引擎收益」与「模型收益」 |
| Qwen2.5-1.5B-Instruct | Q6_K / Q5_K_M | 用户战略候选 |
| Qwen2.5-3B-Instruct | Q4_K_M | 用户战略甜点（≈2.5B-bf16 质量，同显存同速度优于 1.5B-Q8） |

通用 LLM prompt 起点 = HaS 模板原文（Qwen 同系迁移基础好，`has_client.py:494-501`
与模型卡逐字一致约定），temperature 0；合成语料上若差距 >1pp 再消融 few-shot。
**换模型不过质量闸 → 退回 HaS Q8 + llama.cpp（纯工程提速），功能不受损。**

## 3. 实验计划与决策树（需 GPU 实例）

| # | 实验 | 产出 | 失败分支 |
|---|---|---|---|
| E1 | llama.cpp HIP 编译（DTK 26.04，gfx906 与 K100 target） | 可用 `llama-server` | → 路线 T |
| E2 | HaS bf16 基线复测（单流 tok/s、分段耗时） | 折算系数（Vega20 vs K100_AI ≈ 51.5/31.4） | — |
| E3 | 性能矩阵：各模型 × 单流 / batch 4/8/16 tok/s、延迟 | 选型依据 | 收益不足 → 重估 batch 窗口 |
| E4-A | 拆分质量 A/B：HaS bf16 单请求 vs 拆 3 组（transformers 串行跑，隔离拆分影响） | 拆分召回差 | 差 >1pp → 调组/回退单请求 |
| E4-B | 全矩阵质量：合成语料 P/R + 数字逐字率 | 最终选型 | 见 §2.3 退守 |
| E5 | 端到端：文本型 + 扫描型 PDF 各一、分段计时、20 页批量 | 验收数据 | — |

**实例**：scnet-2x16g（当前离线）。开机后按《DCU极速部署指南》`deploy_fast.sh` 恢复全栈
（~5-6 分钟），实验代码 rsync 到实例跑；最终验收若需 K100_AI 绝对值，用 E2 折算系数换算并注明。

## 4. 验收标准（三部分，门③④⑥）

### 4.1 自动化测试（门③，本地）

- 单测（`backend/tests/`）：
  - 分组映射：默认 9 类三组划分；未知/自定义类型走兜底批；单元素组并批；组类型超限再拆；
  - 并发合并：多组结果合并正确、组间类型不相交、防御校验（合并后类型集 ⊇ 请求集）；
  - 失败传播：单组失败→整页失败；单组截断→组内补查不跨组污染；
  - 开关回归：`HAS_NER_TYPE_GROUPING=off` 行为与现状逐字节一致；
- 既有 10 个 NER 相关测试全绿（`test_has_text_ner_concurrency.py` 等）；
- lint / build 全绿。

### 4.2 云实例实测（门④，Issue #23 原文四条）

1. 同一 10 页合成语料，单页 NER 阶段耗时 ≤ 10s（K100_AI 卡型折算，注明折算系数）；
2. 实体级召回 ≥ HaS_Text 基线 −1pp，精确率不低于基线；
3. 数字实体逐字符精确匹配率 100%（一票否决）；
4. 批量 20 页任务 0 item 失败且吞吐 ≥ 2x 现基线（47.7s/页 → ≤23.8s/页）。

### 4.3 用户手动验收（门⑥）

- 用户以其真实卷宗样例处理一份文件，确认脱敏结果与提速体感；
- 操作文档《手动验收-Issue23-NER提速.md》（工作区，不入库）；
- 用户明确放行后才 `gh pr merge --squash`。

## 5. 风险与退守

| 风险 | 概率 | 退守 |
|---|---|---|
| llama.cpp DTK 26.04 编译失败/性能异常 | 中（未验证） | 路线 T transformers batch（收益减半；若仍 >10s 需用户拍板接受折算值或延期） |
| 换通用模型质量不过闸 | 中 | HaS Q8_0 + llama.cpp 纯工程路线（拆+batch+引擎，模型不动） |
| 类型拆分召回损失 >1pp | 低 | 调整分组映射 / 该类型回退并入单请求（保留 batch 收益） |
| Vega20 绝对值封顶误判 | 高（已知） | 全部指标注明折算系数；上线前在 K100_AI 复测一次 |
| batch 服务 OOM（KV 超预算） | 低（<6G/16G 卡） | 降 `-np`/`-c` 或 batch 窗口缩容 |

## 6. 交付物与里程碑

| 里程碑 | 内容 | 依赖实例 |
|---|---|---|
| M0 | 本设计文档 | 否 |
| M4 | 合成 ground truth 语料生成器 + 质量评测脚本（P/R + 数字逐字率，入库 `backend/tests/e2e/`） | 否 |
| M2 | 模型准备：HaS→GGUF Q8_0 本地转换、Qwen2.5 GGUF 下载 | 否（走本地代理） |
| M1/M3 | E1 编译 + E3/E4 性能与质量矩阵 | **是（当前阻塞：实例离线）** |
| M5 | backend 轴A 改造 + 单测（默认 off，零行为变更） | 否 |
| M6 | E5 端到端 + 批量验收（门④） | 是 |
| M7 | 独立 AI review（门⑤）+ 手动验收文档（门⑥）→ merge | 否 |

敏感数据纪律：合成语料为程序生成假数据，可入库；真实卷宗样例仅限用户手动验收，
不入库、不上传 GitHub。
