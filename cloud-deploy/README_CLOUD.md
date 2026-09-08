# B 方案：无 Docker 云端源码部署

适用：ModelScope 免费 GPU 实例等**无法运行 Docker** 的环境。
本地机器只需要跑一次打包脚本，其余都在云上执行。

> 配套部署实录：[SCNet L20 (NVIDIA)](../docs/SCNet-L20-jupyterlab-pytorch-部署教程.md) ｜
> [SCNet K100_AI (海光 DCU/DTK)](../docs/SCNet-K100_AI-DCU-DTK26.04-部署教程.md)——DCU 适配用
> `setup_dtk.sh`/`start_dtk.sh`（transformers 自包 NER），本 README 描述的 NVIDIA 路径用
> `setup_cloud.sh`/`start_cloud.sh`（vLLM NER）。

> 凭据：平台代理经环境变量注入，运行前 `export SCNET_PROXY_URL='http://<user>:<pass>@<代理地址:端口>'`

## 部署的是什么

| 端口 | 进程 | 对应 Docker 容器 | 运行环境 |
|---|---|---|---|
| 8080 | vLLM serve HaS_Text_0209_0.6B | ner | venv-vllm（GPU） |
| 8090 | locate_anything_server.py（HF 模式） | visual-features | venv-vllm + hf-deps（GPU） |
| 8082 | ocr_server.py（PP-StructureV3，CPU） | ocr | venv-app（CPU，与 Docker 行为一致） |
| 8000 | uvicorn app.main:app | backend | venv-app |
| 3000 | vite preview（构建产物 + /api 代理） | frontend | Node 22 |

两个 venv（vLLM 的 torch 栈与 Paddle 冲突，必须分开——上游文档要求）：

- `~/.venvs/app`：backend/requirements.txt + **CPU 版 paddlepaddle 3.2.2**（替换掉 requirements.txt 里的 paddlepaddle-gpu cu129——cu129 wheel 需要驱动 ≥575，云机驱动不满足；且 Docker 里 OCR 本来就是 CPU 模式）
- `~/.venvs/vllm`：`pip install vllm` + `locateanything-hf-deps`（--target 隔离安装，避免版本锁冲突）

## 步骤

### ① 本地：打包 + 上传（约 10-30 分钟，取决于上行带宽）

```bash
cd /home/you/workspace/Desensitization
./pack_upload.sh user@服务器IP
```

脚本会打包并 scp 上去：
- `redaction-code.tar.gz`：代码 + 部署脚本（排除 node_modules / venv / 模型 / .env）
- `yoloe-weights.tar.gz`：可选，仅当本地 `yoloe-service/*.pt` 存在时才打包（不装不影响主平台）
- HaS 1.2GB / LocateAnything 7.3GB 不传：云上从 hf-mirror / ModelScope 直下（机房带宽远快于家用上行）

### ② 云端：一次性安装（约 20-40 分钟，取决于网速）

登录实例，解包后跑：

```bash
cd ~/redaction
tar xzf redaction-code.tar.gz   # yoloe 权重包存在时另行: tar xzf yoloe-weights.tar.gz -C yoloe-service
./setup_cloud.sh
```

setup 做的事：系统依赖 → 两个 venv → pip 依赖（清华源）→ 下载 HaS 模型（hf-mirror）→ 生成 .env（自动生成 JWT 密钥）→ Node 22（官方 tarball → ~/.local/node-v22）→ 前端构建。可重复执行（已装的会跳过）。

### ③ 云端：启动

```bash
./start_cloud.sh        # tmux 会话 redaction，5 个窗口按依赖顺序拉起 + 健康检查
```

vLLM 加载 + LocateAnything 加载约 2-4 分钟，脚本会逐个等服务就绪。

### ④ 本地：访问

安全组只开 SSH，浏览器走隧道（本地另开一个终端）：

```bash
ssh -N -L 3000:localhost:3000 user@服务器IP
# 然后本地浏览器打开 http://localhost:3000
```

### ⑤ 日常操作

```bash
tmux attach -t redaction        # 看各服务实时输出（Ctrl+B 数字键切窗口）
./stop_cloud.sh                 # 停止（杀 tmux 会话 + 进程兜底）
./start_cloud.sh                # 再次启动
tail -f logs/vllm-ner.log       # 直接看某个服务日志
```

## yoloe（可选）

源码方式下 yoloe 单独建 venv（它的 ultralytics 与 vLLM 同用 torch，但版本要求不同，隔离最稳）：

```bash
python3 -m venv ~/.venvs/yoloe
~/.venvs/yoloe/bin/pip install -r yoloe-service/requirements.txt torch
~/.venvs/yoloe/bin/pip install ultralytics paddleocr 2>/dev/null || true
cd yoloe-service && ~/.venvs/yoloe/bin/python server.py   # 端口 8095
```

不装不影响主平台，只是没有 YOLOE 视觉检测链路。

## 故障排查

| 症状 | 处理 |
|---|---|
| vLLM 起不来，报 CUDA/driver | `nvidia-smi` 看驱动版本；确认实例 GPU 可用（ModelScope 有时需排队） |
| vLLM 报 OOM | 显存 24GB 正常够用；若同时开了 yoloe 先关掉 |
| 8082 OCR 不 ready | 确认装的是 CPU 版 paddle：`pip show paddlepaddle`（不应是 -gpu）；`logs/ocr.log` 看首次 warmup（PP-StructureV3 首次初始化 1-2 分钟） |
| 3000 打不开 | `logs/frontend.log`；确认 build 成功（setup 日志） |
| /health/services 有服务 offline | 对照上表端口逐个查 `logs/` |
| 磁盘不足 | 模型+环境约 20GB，`df -h` 检查；ModelScope 免费实例盘一般 50GB+ |

## 额度与数据提醒

- 剩余额度（35h 量级）用尽/实例回收后，**系统盘数据会丢**：模型权重、venv、上传的案卷都在实例上。重开实例 = 重跑一遍 ①②③（约 1 小时）。
- 重要产出（脱敏结果、上传文件）定期 `tar + scp` 拉回本地。
- 代码和部署脚本在本地 Git/目录里永远有底，不会丢。
