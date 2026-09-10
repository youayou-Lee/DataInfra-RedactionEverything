# venv 依赖快照（pip freeze, 2026-09-10, 生产镜像 DTK25.04.2 上导出）

| 文件 | venv | 用途 | 重建方式 |
|---|---|---|---|
| requirements-nl.txt | nl | NER（transformers 服务） | `--system-site-packages`，**torch/vllm 行必须过滤**（镜像自带） |
| requirements-app.txt | app | backend 全依赖 | 普通 venv |
| requirements-paddle-25041.txt | paddle-25041 | **GPU OCR**（paddle-dcu 3.2.1） | safetensors 先行 + DCU 源装 paddlepaddle-dcu |
| requirements-paddle-cpu.txt | paddle-cpu | CPU OCR 兜底（paddlepaddle 3.2.2） | PyPI 直装 |

## 注意

1. **不要直接 `pip install -r` 全量装**——nl 的 freeze 泄漏了镜像系统栈
   （torch/vllm/vision 带 `10.16.4.1:8000` 构建内网直链，运行实例不可达）。
   统一用 `../build_venvs.sh`，过滤与安装顺序已内置。
2. venv 绑 Python **3.10**——其他小版本镜像上需重建（本目录快照即可复现）。
3. 这些 10.16.4.1 直链保留在文件里作为**溯源记录**（构建内网源的 DTK 版本对应关系）。
