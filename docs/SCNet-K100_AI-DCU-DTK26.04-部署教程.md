# SCNet K100_AI (DCU) 部署教程 — DataInfra-RedactionEverything

> 实例镜像：DCU 版（DTK 26.04 + torch 2.9.0 + Python 3.11.9 + Ubuntu 22.04）
> 实例规格：**K100_AI 单卡 64GB**（HIP 6.3）/ 255 核 / 1TB 内存 / 14T 盘（7.6T 可用）
> 部署日期：2026-09-08 ｜ 方式：源码部署（无 Docker，`cloud-deploy/*_dtk.sh`）
> 结果：**5 服务 all_online=true**；NER/LA GPU 推理实测通过；OCR CPU 模式就绪

---

## 一、拓扑与 L20 版的差异

```
tmux 会话 redaction（6 窗口）
├── ner             8080  ner_transformers_server.py（transformers 自包 OpenAI 兼容, DCU, bf16, eager）
├── locateanything  8090  locate_anything_server.py（HF 模式, DCU, bf16）
├── ocr             8082  ocr_server.py（PP-StructureV3, CPU 模式）
├── backend         8000  uvicorn app.main:app
└── frontend        3000  vite preview（/api /health /uploads → 8000）
```

与 L20（NVIDIA）版的三个结构差异：
1. **NER 不用 vLLM**：pip 装 vLLM 会拖 CUDA 版 torch 覆盖 DTK 栈。改用自包的
   OpenAI 兼容服务（`ner_transformers_server.py`），backend 的 `HAS_BASE_URL` 契约零改动。
2. **venv-nl 用 `--system-site-packages`** 复用镜像里的 DTK torch（绝不 pip 装 torch）。
3. **模型零下载**：`backend/models` 软链到平台持久卷（L20 存的 8.4G 权重跨实例直接复用）。

| venv/目录 | 内容 |
|---|---|
| `~/.venvs/app` | backend requirements + paddlepaddle 3.2.2 CPU（OCR 同栈） |
| `~/.venvs/nl` | transformers 4.57.1 / accelerate / safetensors / fastapi（system-site-packages） |
| `~/.venvs/locateanything-hf-deps` | `--target` 只装 4 个本体：peft / opencv-headless / decord / lmdb + 官方 DTK torchvision |

## 二、连接与初始化

```bash
# ~/.ssh/config（本机已配好）
Host <dcu实例>
    HostName <SSH网关地址>
    Port <实例端口>          # 每个新实例端口会变, 平台控制台拿
    User root
    IdentityFile ~/.ssh/id_ed25519
```

实例 `.bashrc` 已追加（交互 shell 生效；**非交互 ssh 不加载，脚本里必须显式 export**）：
```bash
export http_proxy='http://<user>:<pass>@<代理地址:端口>'
export https_proxy=...  ftp_proxy=...  no_proxy='localhost,127.0.0.1,0.0.0.0'
source /opt/dtk/env.sh
```

## 三、部署流程（换新实例重做按此序）

```bash
# 本机: 打包上传(pack_upload.sh 已排除 .env/模型/权重; 代码包 ~4.2MB)
cd ~/workspace/Desensitization/cloud-deploy
tar 打包(见 pack_upload.sh) && scp 代码包 <dcu实例>:~/redaction/ && 远端 tar xzf

# 实例: 一次性安装(~10min, 幂等)
apt 换 aliyun 源装 tmux   # setup_dtk.sh 会自动做
bash ~/redaction/cloud-deploy/setup_dtk.sh

# 持久卷模型软链(新实例必做一次, setup 不含此步)
ln -sfn /root/private_data/redaction-persist/backend-models \
        ~/redaction/DataInfra-RedactionEverything/backend/models

# 启动(串行拉起+健康等待; OCR 首启经代理下 PaddleX 模型 ~5min, 已落持久卷则秒级)
bash ~/redaction/cloud-deploy/start_dtk.sh
# 验证: curl -s http://127.0.0.1:8000/health/services | grep all_online  → true
```

持久卷布局（`/root/private_data/redaction-persist/`，**跨实例共享**，平台代理同账密）：
`backend-models/`（HaS 1.2G + LA 7.3G）、`paddlex-cache/`（382MB，经
`PADDLE_PDX_CACHE_HOME` 指入）、`dot-venvs/` 等 L20 遗产。

## 四、DTK 特有坑（全部实测踩过，脚本已含修复）

### 坑 ① 非 interactive shell 不加载 DTK 环境
`/etc/profile.d/env.sh` 只对登录 shell 生效。`ssh host cmd` / tmux 窗口里直接
`import torch` → `ImportError: libgalaxyhip.so.5`。**必须显式 `source /opt/dtk/env.sh`**。

### 坑 ② env.sh 与 set -u 冲突
env.sh 按 interactive 环境写，追加引用未定义的 `CMAKE_PREFIX_PATH`/`LD_LIBRARY_PATH` 等，
`set -u` 下当场炸。source 前后 `set +u` / `set -u` 包起来。

### 坑 ③ pip --target 隔离解析 → torch 雪崩
`pip install --target` 做依赖解析时**无视 venv/系统包**。requirements 里的
`peft` 依赖 torch → pip 拉 torch 2.14 + CUDA13 全家桶（cudnn 553MB...共 ~2G），
装完即被清理逻辑删除，纯浪费 15+ 分钟。**target 安装必须 `--no-deps`**。

### 坑 ④ 但整份 requirements 用 --no-deps 会撞编译后端
新版 pydantic 本体进 target，其编译后端 pydantic-core 还在 venv（旧版）→
`SystemError: pydantic-core version incompatible`。**target 只装 venv 真没有的本体包**
（peft/opencv/decord/lmdb，均无编译后端耦合），transformers 4.57.1 恰好 venv 已有。

### 坑 ⑤ LA 模型代码硬依赖 torchvision，DTK 镜像没有
trust_remote_code 加载时 `ImportError: torchvision`。PyPI 的 torchvision 是 CUDA 构建
（ABI 不配且拖 nvidia 全家桶）。解法：
- **首选**：光合社区官方 DTK 构建（平台方提供）：
  `https://download.sourcefind.cn:65024/file/4/vision/DAS1.8/torchvision-0.24.0%2Bdas.opt1.dtk2604.torch290-cp311-cp311-manylinux_2_28_x86_64.whl`
  （z-file 直链规则 `/file/<CategoryID>/<path>`，文件名 `+` 编码 `%2B`；版本串与镜像 torch
  同源：`+das.opt1.dtk2604`，`torch290`=配对 torch 2.9.0，cp311）
- **兜底**：aliyun CPU 版 `torchvision==0.24.0+cpu`（`-f https://mirrors.aliyun.com/pytorch-wheels/cpu/`），
  实测对 DTK torch ABI 兼容（LA 只用图像预处理面；io.read_video 仅视频路径）

### 坑 ⑥ NER 推理崩：SDPA flash 后端缺库
Qwen2 系走 `scaled_dot_product_attention` 时 `RuntimeError: No matching libraries found
for flash_attn_2_cuda*.so`（CUDA 专属库，DTK 不提供）。修法（已写入
`ner_transformers_server.py`）：
```python
torch.backends.cuda.enable_flash_sdp(False)
torch.backends.cuda.enable_mem_efficient_sdp(False)
AutoModelForCausalLM.from_pretrained(..., attn_implementation="eager")
```
0.6B 模型 eager 无感。

### 坑 ⑦ backend 读的 .env 是 backend/.env，不是仓库根
`config.py: BACKEND_DIR = Path(__file__).parents[2]` = `backend/`，env_file 指向
`backend/.env`。只生成根 `.env` 时 backend 全用默认值 → `VISUAL_FEATURES_BASE_URL`
默认 **9090**（实际 8090）→ LA 被判 offline（OCR 默认 8082 撞巧能通，has_ner 走本地
模型文件判定，都具有迷惑性）。**修复：`ln -sfn $UP/.env $UP/backend/.env`**（setup_dtk.sh 已含）。
L20 当时能过是因为 tmux 服务器恰好在 start 脚本后才首启、`.env` 的 export 顺进程进了窗口——侥幸不可复制。

### 坑 ⑧ L20 坑⑤复发：OCR spawn 必须内联 OCR_REQUIRE_GPU=false
tmux 服务器环境首启冻结，`.env` source 的值进不了窗口。重写启动脚本时丢了这一个变量，
OCR 撞 GPU 硬检查 FATAL `installed Paddle build has no CUDA support`。已内联，并顺带
`PADDLE_PDX_CACHE_HOME=/root/private_data/redaction-persist/paddlex-cache` 把模型缓存落持久卷。

### 坑 ⑨ 运维自伤：pkill -f 自匹配
`pkill -f "xxx"` 的模式若以明文出现在同一条 ssh 命令的其他位置（如 tmux 拉起串），
会匹配到自己这条 bash → SSH 255 且后半段命令没跑。pattern 用 `[x]` 括号技巧并把
明文拆到另一条命令里。

### 坑 ⑩ NER 不停笔：generation_config 的 eos 指错 token
模型 `generation_config.eos_token_id=151643 (<|endoftext|>)`，但对话实际以
`<|im_end|>(151645)` 结束 → 自包 transformers 服务每次生成拉满 max_tokens
（管道里分块调用，每块 660-1450 tokens ≈ 30-60s，单页管道 82s 的最大头），且输出复读。
修法：gen_kwargs 显式 `eos_token_id=[eos, im_end]`（ner_transformers_server.py 已含）
→ 单块 1.2s、`finish:stop`。vLLM 不踩此坑（自己处理 chat stop），L20 未暴露的原因。

### 性能两件套（已内置）
- **启动预热**：NER 服务加载后自跑一次 generate，把首个请求的 ~60s 算子 JIT 挪到启动期
- **MIOpen 内核缓存**：`MIOPEN_USER_CACHE_PATH=/root/private_data/redaction-persist/miopen-cache`
  落持久卷，重启/换实例不重 JIT

## 五、验证结果（2026-09-08）

| 项 | 结果 |
|---|---|
| `/health/services` | **all_online: true**（paddle_ocr / has_ner / visual_features 全 online） |
| NER 真实推理 | ✓ 单发 **1.2s**（EOS 修复后），正确输出实体 JSON |
| LA /detect（GPU） | ✓ 首推 18.2s（含 JIT），热稳 **10.7s/次**（2 类别 640×400） |
| OCR | ✓ structure_ready，CPU 模式 /structure 7.4s/页，模型缓存已落持久卷 |
| 单页 vision 全管道 | **26.2s**（优化前 82.5s；构成: LA ~24s 共识 + OCR 7.4s + NER ~2s + 粘合） |
| 模型/缓存持久化 | models + paddlex-cache + miopen-cache 均在持久卷，实例保存/换机零重下 |

## 六、遗留事项
- 公网访问：平台控制台映射 **3000** 一个端口即可（vite preview 服务端代理 /api）；平台映射不通时的替代：SSH 隧道 `ssh -N -L 3000:localhost:3000 <dcu实例>`
- 纯 API 冒烟测试：`cloud-deploy/e2e_smoke_test.py`（实例上 venv-app 运行，9 步全链路含敏感串消除验证）
- **进一步提速选项**（当前 26.2s/页，均有质量代价，按需启用）：
  - `LOCATE_ANYTHING_GENERATION_MODE=fast`：LA 每类 3 次共识采样→1 次，-15s/页，代价：漏检风险↑（法律文书漏检=漏脱敏，慎用）
  - `LOCATE_ANYTHING_MAX_IMAGE_SIDE=1024`（现 1280）：-3~5s，小印章/手写识别略降
  - OCR 上 GPU（DTK 版 paddlepaddle，光合社区待找）：-5s
  - 管道并行（OCR 与 LA 并行，backend 代码改造）：-20% 量级
- 光合社区 z-file 还有 `torch251/torch271` 等其他配套 wheel，目录
  `https://download.sourcefind.cn:65024/4/main/vision/DAS1.8`（浏览需真浏览器，curl 是 SPA 壳）
- 商用许可提醒同 L20（Personal Use + LA 权重 NVIDIA 非商业 + PyMuPDF AGPL）
