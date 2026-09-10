# [Perf] 端到端处理速度慢：各阶段瓶颈分析与调优方向

## 现象

单文件多页扫描件 / 多文件批量任务的端到端耗时远高于模型纯推理时间，GPU 利用率低（大量时间在排队与串行等待），吞吐上不去。

## 流水线与各阶段瓶颈（附代码证据）

整条链路：上传解析 → 逐页渲染 → OCR（PP-StructureV3，8082 微服务）→ HaS Text NER（语义识别）→ LocateAnything 视觉特征 → 合并/回写/导出。批量任务再套一层 `SimpleTaskQueue`（JOB_CONCURRENCY 个 worker × 每文件页级并发）。

### 1. 全局 GPU 串行闸门：所有模型调用排队过一根单杆

`backend/app/core/gpu_inference_gate.py:22-45`：`shared_gpu_inference_slot` 默认信号量大小 = `HAS_NER_GLOBAL_MAX_INFLIGHT = 1`（`config.py:382`）。HaS Text 的每一趟 NER（`has_service.py:314`、`vision/has_text_analysis.py:175/451/555`，含 AMOUNT 值收窄、bridge NER 等小请求）都要抢这 1 个槽。

**瓶颈本质**：即使部署了 vLLM（服务端支持 continuous batching），后端也只放行 1 个 in-flight 请求，GPU 空转等网络往返；页级并发（`BATCH_RECOGNITION_PAGE_CONCURRENCY=2`）形同虚设——两个页的 NER 在闸门前重新串行。

**调优**：vLLM 多实例部署放开到实例数×2~3（双卡 5090 生产实测可到 6）；单卡小显存保持 1 但应配合页级并发=1 避免排队雪崩。

### 2. 双流水线默认串行：OCR+HaS 与 LocateAnything 顺序执行

`vision_service.py:427-447`：`VISION_DUAL_PIPELINE_PARALLEL: bool = False`（`config.py:317`），`detect_with_dual_pipeline` 里两条召回通道逐个 `await factory()`。每页先跑完 OCR+HaS 再跑 LA，页耗时 = 两者之和而非 max。

代码注释已写明历史原因（同卡 VL+MoonViT 抢显存 OOM），但也注明**VL 识别已外置、LA 降到 1280 后同卡并行已安全**——开关却仍默认关。

**调优**：默认评估打开 `VISION_DUAL_PIPELINE_PARALLEL=true`；OCR 与 LA 分卡部署的实例应直接开启。

### 3. OCR 微服务内部全局锁：整进程一次只推理一页

`backend/scripts/ocr_server.py:39`：`_infer_lock = asyncio.Lock()`——/structure 推理全程持锁，多页并发请求在服务内重新排队。页级并发对 OCR 阶段无收益。

同文件 `:199-206` 注释显示已知问题：VL 块级识别默认串行「~10s/page」，需 `vl_rec_max_concurrency + use_queues=True` 才能批量化；该优化只做了 VL 路径，而默认路径 `OCR_VL_ENABLED=false` 走的 PP-StructureV3 仍受 `_infer_lock` 全串行。

**调优**：PP-StructureV3 支持批处理时改锁为信号量（大小=显存可容纳批数），或 OCR 服务多副本 + 后端轮询。

### 4. 页级并发默认 2，且被 GPU 饱和度压到 1

`config.py:303`：`BATCH_RECOGNITION_PAGE_CONCURRENCY = 2`（上限 8）；`config.py:307`：视觉合并 pass 默认 1；`config.py:314`：`GPU_SATURATION_RATIO = 0.90`——静态显存占用本就高的多服务共卡部署（注释自述双卡生产 idle ~80%）极易被判饱和，页并发被强制压回 1，逐页串行。

**调优**：空闲显存充足的实例调页并发至 4~8；共卡部署把饱和阈值上调到 idle 占用 + 余量。

### 5. LocateAnything 逐类别 fan-out、多采样串行

- 每个勾选的视觉类别一次独立 /detect 请求（per-category fan-out）；`config.py:258`：`VISUAL_DETECT_BATCH_CATEGORIES = false`——合并请求仅在 vLLM prompt-embeds 模式有益（HF 模式实测 5.4s vs 3.3s），但 vLLM 部署也默认没开。
- `vision_service.py:1792-1794`：共识多采样 `LOCATE_ANYTHING_CONSENSUS_SAMPLES>1` 时 N 趟**串行**执行（避免同卡争抢），开销线性 ×N。
- `LOCATE_ANYTHING_MAX_NEW_TOKENS = 8192`（`config.py:276`）：开放式 grounding 生成预算极大，长生成直接拉高单请求延迟。

**调优**：vLLM 部署开启类别合并（单次 MoonViT 视觉编码共享）；多采样只在 LA 分卡或确实需要压波动时启用；按实际类别数收紧 max_new_tokens。

### 6. 批量层：JOB_CONCURRENCY=3、单 worker 逐项串行

`task_queue.py:65/460`：`SimpleTaskQueue(concurrency=JOB_CONCURRENCY=3)`（`config.py:510`，上限 16），每个 worker 一次处理一个文件项；页级处理 `task_queue_pipelines.py:248` 用信号量限流，页超时 `BATCH_RECOGNITION_PAGE_TIMEOUT=180s`。多 GPU 实例下 3 个并发不足以喂饱推理服务。

**调优**：按 (GPU 实例数 × 每实例可并发) 配置 JOB_CONCURRENCY 与页并发的乘积关系，避免两层并发互相抵消（当前页并发受闸门压制时，加 JOB_CONCURRENCY 比加页并发更有效）。

### 7. 超时与重试放大尾延迟

`OCR_TIMEOUT = 360s`（`config.py:293`）、`VISUAL_FEATURES_TIMEOUT = 240s`：单页一次失败重试即数分钟级排队堆积；冷启动首请求已知 ~60s JIT（见 #4）。无全局预算控制，慢页会长期占住 worker。

## 调优优先级建议

| 优先级 | 措施 | 预期收益 |
|---|---|---|
| P0 | vLLM 部署放开 `HAS_NER_GLOBAL_MAX_INFLIGHT` | NER 吞吐 ×实例并发数 |
| P0 | 打开 `VISION_DUAL_PIPELINE_PARALLEL`（OCR/LA 分卡或已外置 VL 时） | 每页耗时由和变 max，~减半 |
| P1 | OCR 服务 `_infer_lock` → 有界信号量 / 多副本 | OCR 阶段随页并发线性扩展 |
| P1 | 上调 `BATCH_RECOGNITION_PAGE_CONCURRENCY` + 校准 `GPU_SATURATION_RATIO` | 页间并行真正生效 |
| P2 | vLLM 模式开 `VISUAL_DETECT_BATCH_CATEGORIES`；收紧 LA max_new_tokens | 单页 LA 延迟下降 |
| P2 | JOB_CONCURRENCY 与页并发/闸门联动调参（给出容量公式与文档） | 批量吞吐可预期 |

## 实测数据（2026-09-08，DCU K100_AI 单卡 64G，DTK 26.04）

测试方法：合成 10 页扫描型 PDF（虚构 PII，120dpi，无文本层），逐页 `/redaction/{id}/vision?force=true`，
取 duration_ms 埋点分解。基线 9 页、调优后 4 页。

### 基线（默认参数：双流水线串行、NER 单实例、闸门=1）

| 指标 | 数值 |
|---|---|
| 单页端到端 | **68.9s** |
| ocr_has 阶段（串行） | 54.9s = OCR 14.2s + **HaS NER 31.4s（单个大请求）** + 匹配开销 |
| visual_features（LocateAnything） | 13.9s |

### 调优（`VISION_DUAL_PIPELINE_PARALLEL=true`，OCR 走 CPU 与 GPU 通道重叠）

| 指标 | 数值 | 变化 |
|---|---|---|
| 单页端到端 | **51.8s** | **-25%**（total 由「两通道之和」变「max」） |

### 进一步发现

1. **DCU 上 NER 每页是一个 ~31s 的单请求**（整页 ~1000 字符、9 个识别类型一次性进出），
   页内无并行可挖；多实例 NER 只提升跨页吞吐（批量路径）。
2. **uvicorn `--workers` 多进程在 DTK 上不稳定**：子进程反复静默崩溃（仅 2/4 就绪），
   请求会卡死。改用「N 个独立单进程实例 + 轮询 LB」（`cloud-deploy/ner_lb.py`，工作区部署脚本），
   2 实例 × ~2G 显存，64G 卡余量充足。
3. NER 31s 的根因是 0.6B 模型在 DTK 上只能跑 eager 注意力（flash/mem-efficient 后端缺
   CUDA 专属库），生成 ~200+ token 单次 ~30s——单页延迟的下界，需模型侧优化（量化/蒸馏/分类型
   拆请求）才能突破。
4. OCR 在 CPU（255 核）上 14s/页，与 GPU 通道天然可重叠——这正是双流水线并行收益明确的
   原因（DCU 拓扑下安全，无同卡显存争抢）。

### 批量路径 A/B（smart_batch，2 文件 × 10 页，同内容结构，NER 缓存关闭）

| 配置 | 总墙钟 | 吞吐/页 | 结果 |
|---|---|---|---|
| 默认（页并发2、串行双流水线、NER 闸门1） | **1104.7s** | 55.2s | 20/20 页完成，框数 500+473 |
| 调优 A：页并发4 | 182.4s | — | **失败**：OCR 14→26/41s、NER 排队，页耗时冲破 180s 页超时，item failed |
| 调优 B：JOB=2×页并发2（总在途4页） | 632.9s | — | **失败**：同超订，第二文件 7/10 页丢失 |
| **调优 C：JOB=2×页并发1 + 双流水线并行 + NER 双实例（闸门2）+ 页超时300s** | **953.9s** | **47.7s（1.16x）** | 20/20 页完成，框数 500+473 与基线**完全一致** |

关键结论：

1. **单卡 DCU 的在途页数上限 ≈ 2**（JOB_CONCURRENCY × BATCH_RECOGNITION_PAGE_CONCURRENCY）。
   超过即三处资源同时超订：CPU OCR（14→41s）、GPU NER 排队、LA 争抢，页耗时非线性上涨
   直至撞 180s 默认页超时——且失败是**静默丢页**（任务仍 awaiting_review，item failed）。
2. **NER 多实例受算力约束而非显存约束**：2 实例仅占 ~4G/64G，但单卡 compute 饱和，
   并发 generate 互相拖慢，吞吐提升远低于 2x。显存余量换不来线性加速。
3. 质量零回退：调优 C 的每页识别框数与默认配置完全一致（500/473），加速全部来自
   调度重叠（LA 与 OCR+NER 并行、页间流水），不牺牲任何召回。
4. 单页交互延迟（-25%）与批量吞吐（1.16x）收益不对称的原因：单页路径 LA 重叠
   收益大；批量路径瓶颈在 NER 串行生成（31s/页，页内单请求），需模型侧优化突破。


## 验收标准

- 提供一份端到端 profile 文档：单文件（10 页扫描件）与 100 文件批量在各阶段的耗时分解（利用现有 `duration_ms` / `pipeline_status` 埋点）。
- 双卡部署给出推荐并发参数组合，并附压测对比（`e2e/concurrent_users.py` 可复用）。
- 默认配置在单卡小显存下不回退（无 OOM、无 LA 丢框——参照 `vision_service.py:428` 注释中的历史教训）。
