# SCNet L20 部署教程 — DataInfra-RedactionEverything

> 实例镜像：`jupyterlab-pytorch:2.7.0-py3.12-cuda11.8-ubuntu22.04-devel`
> 实例规格：NVIDIA L20 48GB / 88 核 / 2TB 内存 / Ubuntu 22.04（容器）
> 部署日期：2026-09-08 ｜ 部署方式：源码部署（无 Docker，B 方案）
> 部署结果：5 服务 `all_online`，GPU 占用 15.8G/46G

---

## 一、部署架构总览

```
tmux 会话 redaction（6 窗口，按依赖顺序拉起）
├── vllm-ner         8080  vLLM 0.8.5 serve HaS_Text_0209_0.6B（GPU, 显存配额 0.18 ≈ 8.3G）
├── locateanything   8090  locate_anything_server.py（HF 模式, GPU ≈ 7.5G）
├── ocr              8082  ocr_server.py（PP-StructureV3, CPU 模式）
├── backend          8000  uvicorn app.main:app（FastAPI）
└── frontend         3000  vite preview（构建产物 + /api /health /uploads 代理 → 8000）
```

**两套 venv**（vLLM 的 torch 栈与 Paddle 必须隔离，上游文档要求）：

| venv | 内容 | 说明 |
|---|---|---|
| `~/.venvs/app` | backend/requirements.txt + **CPU 版 paddlepaddle 3.2.2** | 跑 backend + OCR |
| `~/.venvs/vllm` | **vllm==0.8.5**（自带 torch 2.6.0+cu124）+ transformers==4.51.3 | 跑 vLLM NER |
| `~/.venvs/locateanything-hf-deps` | transformers 4.57.1 / peft / accelerate 等（`pip --target` 隔离目录） | LA 服务专用，与 venv-vllm 共用 torch |

**模型权重**（`backend/models/`，共 ~9.8G）：
- `has/HaS_Text_0209_0.6B`（1.2G）— hf-mirror 下载
- `locateanything/LocateAnything-3B-HF`（7.3G）— **ModelScope `nv-community/LocateAnything-3B` 直下**（与 HF 版同源同结构，shard 一致）

**数据存储分层**（理解这个才能理解关机迁移，见第七节）：
- 容器可写层：代码 + `.env` + 构建产物（关机保存镜像时有 15GB 限额）
- `/root/private_data/`：平台持久卷（网络存储），模型/venv/缓存常驻于此

---

## 二、前置：连接实例

平台创建实例后拿到 SSH 命令（形如 `ssh -p <port> root@<SSH网关地址>` + 密码）。

```bash
# 1. 上传本机公钥实现免密（scp 公钥过去追加，别在命令行里二次读 stdin，会读到空）
scp -P <port> ~/.ssh/id_ed25519.pub root@<SSH网关地址>:/tmp/mykey.pub
ssh -p <port> root@<SSH网关地址> 'cat /tmp/mykey.pub >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'

# 2. 写入 ~/.ssh/config（此后 ssh <nvidia实例> 直连）
Host scnet
    HostName <SSH网关地址>
    Port <port>
    User root
    IdentityFile ~/.ssh/id_ed25519
    ServerAliveInterval 30
    Compression yes
```

> ⚠️ 平台 SSH 网关偶发 `Connection reset by peer`（kex 阶段），等几秒重试即可，不是配置问题。

## 三、关键坑 ①：容器不能直接出公网

**现象**：容器内访问 pypi / hf-mirror / npmmirror / 任何外网全部超时；apt 默认源也失败（还叠加 IPv6 不通）。

**根因**：SCNet 的 K8s 容器网络隔离，出网必须走平台认证代理。

**解决**：
```bash
# 平台提供的代理（控制台获取，账密形式）
export http_proxy='http://<user>:<pass>@<代理地址:端口>'
export https_proxy='http://<user>:<pass>@<代理地址:端口>'
export ftp_proxy='http://<user>:<pass>@<代理地址:端口>'
# 写入 ~/.bashrc；注意非交互 ssh 执行命令时不加载 .bashrc，需显式 export
```

验证：pypi-tuna / hf-mirror / npmmirror / aliyun-apt 全部 HTTP 200。

**apt 源修正**：`/etc/apt/sources.list` 平台模板配的是 noble(24.04) 但系统是 jammy(22.04)，且默认源不通。统一替换：
```bash
sed -i -e "s|security.ubuntu.com/ubuntu|mirrors.aliyun.com/ubuntu|g" \
       -e "s|<其他ubuntu域名>|mirrors.aliyun.com/ubuntu|g" -e "s/noble/jammy/g" /etc/apt/sources.list
apt-get update && apt-get install -y tmux curl
```

**Python**：镜像自带 `/opt/conda`（Python 3.12.7，torch 2.7.0+cu118 已内置），但 `python3` 不在默认 PATH —— 脚本里 `export PATH=/opt/conda/bin:$PATH`。

## 四、上传代码与权重

本地执行（脚本：`cloud-deploy/pack_upload.sh`）：
```bash
cd <工作区>/cloud-deploy
./pack_upload.sh <nvidia实例>    # 打包代码(4MB, 排除敏感案卷样本) + yoloe 权重(817MB) 并 scp
```
上传带宽实测 ~4.5MB/s（走平台网关）。LocateAnything 权重**不上传**，云端从 ModelScope 直下（约 19MB/s，5 分钟，远快于家宽上行）。

云端解包：
```bash
cd ~/redaction
tar xzf redaction-code.tar.gz && mkdir -p yoloe-service && tar xzf yoloe-weights.tar.gz -C yoloe-service
```

## 五、关键坑 ②~④：环境安装（setup_cloud.sh）

`setup_cloud.sh` 做的事：两个 venv → pip 依赖（清华源+代理）→ HaS 模型下载（hf-mirror）→ 生成 .env（随机 JWT）→ Node 22（npmmirror）→ 前端构建。可重复执行（幂等）。

### 坑 ②：requirements-locateanything.txt 的 numpy==1.25.0 装不上
- **现象**：`pip --target` 安装 LA 依赖时报 `Failed to build 'numpy'`
- **根因**：numpy 1.25.0 没有 Python 3.12 的 wheel（3.12 需 ≥1.26），pip 回退源码构建失败
- **修复**：`sed -i "s/numpy==1.25.0/numpy==1.26.4/"`；后续进一步直接删掉 target 里的 numpy（见坑③）

### 坑 ③：`pip --target` 把 torch 2.14 / numpy 1.26 也装进了隔离目录
- **现象**：LA 服务启动报 `ModuleNotFoundError: Could not import module 'AutoProcessor'`，真实异常是 `RuntimeError: operator torchvision::nms does not exist`
- **根因**：`--target` 目录装了全新 torch 2.14 + 配套 torchvision，与 venv-vllm 的 torch 2.6 混载（PYTHONPATH 的 target 优先）；删 torch 后 numpy 1.26 又与 venv 里 numpy2 编译的 scipy ABI 冲突
- **修复**：**target 目录里不能有 torch/numpy**，删掉后统一复用 venv-vllm 的 torch 2.6.0 + numpy 2.2.6：
  ```bash
  rm -rf ~/.venvs/locateanything-hf-deps/torch* ~/.venvs/locateanything-hf-deps/numpy*
  # 验证：
  PYTHONPATH=~/.venvs/locateanything-hf-deps ~/.venvs/vllm/bin/python -c \
    "import torch,transformers,peft,accelerate,cv2,decord; from transformers import AutoProcessor"
  ```

### 坑 ④：vLLM 版本与 transformers 的双重锁
- 镜像驱动 535（CUDA 12.2），最新 vLLM 的 cu129 torch 栈不兼容 → **钉 `vllm==0.8.5`**（依赖 torch 2.6.0+cu124，cu124 在 535 驱动上靠 CUDA minor version compatibility 可跑）
- vllm 0.8.5 装完后 pip 会带上最新 transformers（4.57+），新版删了 `all_special_tokens_extended` → vLLM 启动即崩 `AttributeError: Qwen2Tokenizer has no attribute ...`
- **修复**：venv-vllm 内 `pip install transformers==4.51.3`（LA 服务不受影响，它的 4.57.1 在 target 目录）

## 六、关键坑 ⑤~⑥：启动（start_cloud.sh）

### 坑 ⑤：tmux 服务器环境不继承 start 脚本的 .env
- **现象**：OCR 服务 FATAL `installed Paddle build has no CUDA support`
- **根因**：`start_cloud.sh` 里 `set -a; source .env` 只作用于脚本进程；tmux **服务器**环境在首次启动时定死（update-environment 白名单机制），后续 new-window 继承的是服务器旧环境 → `OCR_REQUIRE_GPU=false`、代理变量都传不进窗口 → OCR 走了 GPU 硬检查（venv-app 是 CPU 版 paddle）失败退出
- **修复**：spawn 命令里**显式传关键环境变量**：
  ```bash
  spawn ocr "... OCR_REQUIRE_GPU=false http_proxy=... https_proxy=... ..."
  ```
  OCR 首次启动还需经代理下载 PaddleX 模型（~7 分钟），代理变量必须带上

### 坑 ⑥：`source .env` 吃掉 JSON 值的引号
- **现象**：backend 起不来，`SettingsError: error parsing value for field "CORS_ORIGINS"`
- **根因**：`.env` 里 `CORS_ORIGINS=["http://localhost:3000"]`，bash source 时把双引号吃掉 → `[http://localhost:3000]` 非法 JSON（Docker compose 传 env 不过 shell，所以 Docker 方式没这个问题）
- **修复**：backend spawn 显式传 `CORS_ORIGINS='["http://localhost:3000"]'`

### 其他启动参数修正（vLLM 0.8.5 不支持新版参数）
删除 `--kv-cache-memory-bytes` 和 `--default-chat-template-kwargs`，保留 `--gpu-memory-utilization 0.18 --enforce-eager --no-enable-prefix-caching`。

### 一次意外：运行中脚本被 scp 覆盖
start_cloud.sh 跑到一半（OCR 等待中）时 scp 推送了新版脚本 → bash 按文件偏移继续读 → 乱码语法错误 `syntax error near unexpected token '('`。**教训：改远端运行中脚本前先确认进程已停，或复制为新文件再切换。**

## 七、验证与运维

```bash
# 全链路验证（no_proxy 必须设，否则 curl 走代理打不到 127.0.0.1）
export no_proxy=localhost,127.0.0.1,0.0.0.0
curl -s http://127.0.0.1:8000/health/services | python3 -m json.tool | grep all_online
# 期望 "all_online": true；GPU used ≈ 15847 MiB

tmux attach -t redaction          # Ctrl+B 数字切窗口看实时日志
~/redaction/cloud-deploy/stop_cloud.sh
~/redaction/cloud-deploy/start_cloud.sh    # 幂等，含上述全部修复
tail -f ~/redaction/cloud-deploy/logs/vllm-ner.log
```

公网访问：平台控制台暴露 **3000 一个端口**即可（vite preview 在服务器端代理 `/api`，浏览器同源无 CORS 问题）。

## 八、关机保存与重启恢复（镜像 15GB 限额）

平台"保存环境"= 把**容器可写层** commit 成镜像，限额 15GB。大件数据（venv 14.5G + 模型 8.6G + 缓存 7.3G ≈ 31.4G）**常驻持久卷 `/root/private_data/redaction-persist/`**（不计入镜像），容器层用软链指过去 → 可写层只剩代码 + 软链 + node_modules（~200MB），远低于限额。

**开机引导脚本（唯一入口，幂等）**：`/root/private_data/redaction-persist/bootstrap.sh`（本地源：`cloud-deploy/bootstrap.sh`）。换任意新实例（镜像为本项目保存过的环境），开机后：

```bash
bash /root/private_data/redaction-persist/bootstrap.sh                # 校验+修复（软链/run_backend.sh/缺件自愈）
bash /root/private_data/redaction-persist/bootstrap.sh --services     # 无卡模式：拉起 OCR/backend/frontend 三件套
cd /root/redaction/cloud-deploy && bash start_cloud.sh                # 有卡模式：拉起全部 5 服务
```

bootstrap 做的事：① PATH/代理写入 .bashrc ② 缺 tmux/curl 自动装（aliyun jammy 源）③ 软链自检自修（venvs/models/cache/yoloe/node-v22；`.local` 保留平台 Jupyter 运行时只链 node-v22）④ 重建 run_backend.sh ⑤ 验证 python/node/模型/vite，缺 vite 自动 npm ci ⑥ 可选拉起 CPU 服务并自检。

**关机保存 Checklist**：确认服务已停（`tmux kill-server`）→ 平台勾选"保存环境"关机。镜像会带上：代码、软链、node_modules、run_backend.sh、tmux/curl、bashrc 配置。

### 坑 ⑦：node_modules 绝不能软链到持久卷
- **现象**：`vite preview` 崩 `ERR_MODULE_NOT_FOUND: Cannot find package 'picomatch' imported from /root/private_data/.../vite/dist/node/cli.js`（文件明明存在）
- **根因**：Node 按 **realpath** 解析依赖——cli.js 的真实路径已跳到 `/root/private_data/.../frontend-node-modules/`，向上查找名为 `node_modules` 的祖先目录时永远绕不回 `frontend/node_modules`，包解析断链
- **修复**：node_modules 保持**实体目录**（仅 ~194MB，放容器层完全够 15GB 限额）；bootstrap 检测缺 vite 时自动 `npm ci --registry=npmmirror` 自愈（注意 PATH 需带 node 目录，否则 npm 子进程报 `env: 'node': No such file or directory`）

## 九、版本锁定清单（实测可用组合）

| 组件 | 版本 | 原因 |
|---|---|---|
| vllm | 0.8.5 | 驱动 535 不支持 cu129；0.8.5 = torch 2.6.0+cu124 可跑 |
| venv-vllm 内 transformers | **4.51.3** | 新版删 `all_special_tokens_extended`，vLLM 0.8.5 崩 |
| target 内 transformers | 4.57.1 | LA 服务（上游 requirements 钉的） |
| paddlepaddle | 3.2.2 CPU 版 | 与 Docker OCR 容器行为一致；GPU 版 wheel 需驱动 ≥575 |
| Node | v22.14.0（npmmirror） | 前端构建/preview |
| numpy | venv 2.2.6（target 内不装） | 见坑 ③ |

## 十、待执行优化：自定义镜像 + torch 复用（方案已定，未实施）

**背景**：镜像自带 torch 2.7.0+cu118（`/opt/conda`，`cuda_ok: True` 验证过）和 transformers 4.51.3，但本次部署 venv-vllm 里又装了一整套 torch 2.6.0+cu124（约 3-4GB）——因为 vllm 0.8.5 钉的是 torch 2.6.0，pip 默默重复下载，镜像自带 torch 全程闲置。**版本配对事实：vllm 0.8.5 → torch 2.6.0；vllm 0.9.x → torch 2.7.0**（与镜像自带版本正好配对）。

**优化方案**（做自定义镜像时一并执行）：

```bash
# 1. 用 --system-site-packages 重建 venv（继承基础 conda 的 torch 2.7.0 / transformers 4.51.3 / numpy 2.1.2）
python3 -m venv --system-site-packages ~/.venvs/vllm2

# 2. 装 vllm 0.9.x（torch 依赖由基础环境满足，零下载）
~/.venvs/vllm2/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple vllm==0.9.1
#    验证点①: 0.9.x 的 wheel 是 cu126 构建, 依赖驱动 ≥525(CUDA 12 minor compat), 535 驱动可用——
#    装完必须实际验证: nvidia-smi 正常 + vllm 能起

# 3. 确认 pip 没有升级/降级基础环境的共享包(重点看 transformers 是否仍 4.51.3)
~/.venvs/vllm2/bin/pip list | grep -E "torch|transformers|vllm"

# 4. LA 附加依赖 target 目录不变(它的 torch 解析走 venv→base 链)
# 5. 真机验证: vLLM serve HaS_Text → /v1/models 200 → 跑一次真实 NER 推理
# 6. 通过后再改 .env 的 VLLM_VENV_DIR 指向 vllm2, start_cloud.sh 全链路回归
```

**预期收益**：
- venv 体积 **14.5G → 约 10G**——15GB 镜像限额从贴线（14.9G）变从容（约 10.4G），自定义镜像方案才真正可行
- 全新部署的 pip 环节省 15 分钟里的约 5 分钟（torch 2.6 全家桶下载）

**注意**：
- venv 本身不能省（上游要求 vLLM 栈与 Paddle 栈隔离 + 保护基础 conda 环境不被 pip 污染），省的是重复 torch
- 0.9.x 与 0.8.5 的启动参数略有差异，切换时对照 vllm 0.9 文档检查 start_cloud.sh 的参数（0.8.5 剔除过的 `--kv-cache-memory-bytes` 在新版反而可用，但不必须）
- 现有 vllm 0.8.5 部署**保持不动**（已全链路验证），此优化只在构建自定义镜像时实施

## 十一、遗留事项

- yoloe 服务（8095）未部署（可选，不影响主平台）
- 公网端口映射由平台控制台操作（只需 3000）
- 若换新实例重做：按本教程四→八顺序执行约 1 小时；本地 `cloud-deploy/` 脚本已含全部修复
- 安全提示：NER/LA/OCR 三个模型服务无鉴权且绑定 0.0.0.0，当前仅因平台只映射 3000 而安全；切勿将其余端口暴露到公网，或前置网关鉴权后再暴露
- 商用提醒：项目为 Personal Use License + LA 权重 NVIDIA 非商业许可 + PyMuPDF AGPL，商用需单独授权（详见项目 LICENSE）
