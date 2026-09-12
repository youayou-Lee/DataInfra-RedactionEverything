"""HaS Text 0.6B 的 transformers 推理服务 —— OpenAI /v1/chat/completions 最小兼容实现。

用途：DTK 等 vLLM 不可用的环境作为 NER 运行时。backend 的 has_client 只使用
choices[0].message.content 与 choices[0].finish_reason，本服务按该契约返回。

用法:
    python ner_transformers_server.py --model <HaS模型目录> --host 0.0.0.0 --port 8080
    # 显存规划: bf16 0.6B ≈ 1.5-2GB；与 Docker ner(vLLM) 行为对齐: bf16 + trust_remote_code

Issue #23 轴B（退守路线）: 服务端攒批 batch generate。
    --batch-window-ms 250   # >0 启用: 请求入队, worker 在窗口内收集多个请求
                            # 一次左 padding batch forward（摊薄逐 token 权重读取）。
    默认 0 = 原串行行为（零行为变更）。batch 异常自动回退为逐个单请求执行。
    采样参数不同的请求（贪心 vs self-consistency 采样趟）按 key 分桶各自成批。
"""
import argparse
import queue
import threading
import time

import torch
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer

DEFAULT_MAX_NEW_TOKENS = 2048  # 对齐 compose ner 的 max-model-len 4096 内的安全生成上限

app = FastAPI(title="has-text-transformers")
_s: dict = {}


def _stop_token_ids(tok) -> list[int]:
    # 坑(实测): 该模型 generation_config.eos_token_id 指向 <|endoftext|>(151643), 而对话
    # 实际以 <|im_end|>(151645) 结束 → 不显式指定则永远等不到停止符, 每次生成拉满
    # max_tokens(实测 660-1450 tokens/次, 且内容复读)。两者都加入停止集后 22 tokens 收敛
    im_end = tok.convert_tokens_to_ids("<|im_end|>")
    if im_end is not None and im_end == getattr(tok, "unk_token_id", None):
        im_end = None  # 词表无此 token 时返回 unk id, 误作停止符会在首个 <unk> 处截断
    return [i for i in {tok.eos_token_id, im_end} if i is not None]


def load_model(model_dir: str, device: str) -> None:
    tok = AutoTokenizer.from_pretrained(model_dir, trust_remote_code=True)
    # DTK(海光DCU) 实测: SDPA 的 flash 后端分发找不到 flash_attn_2_cuda*.so(CUDA 专属库,
    # DTK 不提供) → 推理即 RuntimeError。禁用 flash/mem_efficient 两个 SDPA 后端 + 模型
    # 强制 eager 注意力(0.6B 模型无感), math/eager 路径纯 torch 算子, 国产卡可跑
    torch.backends.cuda.enable_flash_sdp(False)
    torch.backends.cuda.enable_mem_efficient_sdp(False)
    model = AutoModelForCausalLM.from_pretrained(
        model_dir,
        trust_remote_code=True,
        dtype=torch.bfloat16,
        attn_implementation="eager",
    ).to(device)
    model.eval()
    # DTK 实测: 服务重启后首个真实请求会吃 ~60s 的算子 JIT/初始化(此后 5-6s/次)。
    # 启动期预热把它挪到加载阶段, 用户请求不再踩雷
    try:
        warm = tok("预热", return_tensors="pt").to(device)
        with torch.no_grad():
            model.generate(**warm, max_new_tokens=8, do_sample=False, pad_token_id=tok.eos_token_id)
        print(f"[has-transformers] warmup done on {device}", flush=True)
    except Exception as exc:  # 预热失败不阻断服务
        print(f"[has-transformers] warmup skipped: {exc}", flush=True)
    _s.update(
        tok=tok, model=model, device=device, lock=threading.Lock(), model_dir=model_dir,
        batch_queue=None, batch_window_ms=0.0, max_batch=8,
    )


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    model: str | None = None  # 接受但忽略，同 vLLM 单模型服务


class _Task:
    """一个待生成请求：prompt 已模板化，worker 填充 result 后 set event。"""

    def __init__(self, prompt: str, max_new: int, gen_kwargs: dict):
        self.prompt = prompt
        self.max_new = max_new
        self.gen_kwargs = gen_kwargs  # 采样相关子集（do_sample/temperature/top_p）
        self.event = threading.Event()
        self.new_tokens: list[int] | None = None
        self.prompt_len = 0
        self.error: Exception | None = None


def _sampling_key(gen_kwargs: dict) -> tuple:
    return (gen_kwargs.get("do_sample", False), gen_kwargs.get("temperature"), gen_kwargs.get("top_p"))


def _generate_batch(tasks: list[_Task]) -> None:
    """左 padding batch generate，结果写回每个 task；出错时抛给调用方整体回退。"""
    tok, model = _s["tok"], _s["model"]
    prompts = [t.prompt for t in tasks]
    tok.padding_side = "left"
    inputs = tok(prompts, return_tensors="pt", padding=True).to(model.device)
    width = inputs["input_ids"].shape[1]
    gen_kwargs = dict(tasks[0].gen_kwargs)
    gen_kwargs.update({
        "max_new_tokens": max(t.max_new for t in tasks),
        "pad_token_id": tok.eos_token_id,
        "eos_token_id": _stop_token_ids(tok),
    })
    with torch.no_grad():
        out = model.generate(**inputs, **gen_kwargs)
    eos_set = set(gen_kwargs["eos_token_id"])
    for i, task in enumerate(tasks):
        task.prompt_len = int(inputs["input_ids"].shape[1])
        tail = out[i][width:].tolist()
        # batch 内先结束的序列被 pad 填充；截到首个停止符（与单请求语义一致）
        for pos, token in enumerate(tail):
            if token in eos_set:
                tail = tail[:pos]
                break
        task.new_tokens = tail


def _batch_worker() -> None:
    q: "queue.Queue[_Task]" = _s["batch_queue"]
    window = _s["batch_window_ms"] / 1000.0
    max_batch = int(_s["max_batch"])
    while True:
        first = q.get()
        if first is None:  # shutdown（测试用）
            return
        pending = [first]
        deadline = time.perf_counter() + window
        while len(pending) < max_batch:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            try:
                pending.append(q.get(timeout=remaining))
            except queue.Empty:
                break
        # 采样参数不同不能混批（generate 的 do_sample/temp/top_p 是批级参数），按 key 分桶
        buckets: dict[tuple, list[_Task]] = {}
        for task in pending:
            buckets.setdefault(_sampling_key(task.gen_kwargs), []).append(task)
        for bucket in buckets.values():
            try:
                _generate_batch(bucket)
            except Exception as exc:  # batch 失败回退逐个单跑（单 prompt 无 padding）
                print(f"[has-transformers] batch failed ({exc}), fallback to serial", flush=True)
                for task in bucket:
                    try:
                        _generate_batch([task])
                        continue
                    except Exception as serial_exc:
                        task.error = serial_exc
        for bucket in buckets.values():
            for task in bucket:
                task.event.set()


def _start_batch_worker(window_ms: float, max_batch: int) -> None:
    _s["batch_queue"] = queue.Queue()
    _s["batch_window_ms"] = float(window_ms)
    _s["max_batch"] = int(max_batch)
    threading.Thread(target=_batch_worker, daemon=True).start()
    print(f"[has-transformers] batching on: window={window_ms}ms max_batch={max_batch}", flush=True)


def _gen_kwargs_for(req: ChatRequest) -> tuple[int, dict]:
    max_new = min(int(req.max_tokens or DEFAULT_MAX_NEW_TOKENS), DEFAULT_MAX_NEW_TOKENS)
    temperature = 0.0 if req.temperature is None else float(req.temperature)
    kwargs: dict = {"do_sample": temperature > 0}
    if kwargs["do_sample"]:
        kwargs["temperature"] = temperature
        kwargs["top_p"] = 1.0 if req.top_p is None else float(req.top_p)
    return max_new, kwargs


def _finish_reason(new_tokens: list[int], max_new: int, eos_ids: list[int]) -> str:
    eos_set = set(eos_ids)
    _hit_eos = bool(new_tokens) and new_tokens[-1] in eos_set
    # new_tokens 已截到首个停止符：含停止符即自然停止，未含且达到预算为截断
    return "length" if (len(new_tokens) >= max_new and not _hit_eos) else "stop"


def _render(task: _Task, req: ChatRequest, started: float) -> dict:
    tok = _s["tok"]
    eos_ids = _stop_token_ids(tok)
    content = tok.decode(task.new_tokens or [], skip_special_tokens=True)
    return {
        "id": f"chatcmpl-has-{int(started)}",
        "object": "chat.completion",
        "model": req.model or "HaS_Text_0209_0.6B",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": _finish_reason(task.new_tokens or [], task.max_new, eos_ids),
        }],
        "usage": {
            "prompt_tokens": task.prompt_len,
            "completion_tokens": len(task.new_tokens or []),
            "total_tokens": task.prompt_len + len(task.new_tokens or []),
        },
    }


@app.get("/health")
def health():
    return {
        "ready": bool(_s.get("model")),
        "runtime": "transformers",
        "batching": bool(_s.get("batch_queue")),
    }


@app.get("/v1/models")
def models():
    return {"data": [{"id": _s.get("model_dir", "HaS_Text_0209_0.6B"), "object": "model"}]}


@app.post("/v1/chat/completions")
def chat_completions(req: ChatRequest):
    tok, model = _s["tok"], _s["model"]
    prompt = tok.apply_chat_template(
        [m.model_dump() for m in req.messages],
        tokenize=False,
        add_generation_prompt=True,
    )
    max_new, sampling = _gen_kwargs_for(req)
    started = time.perf_counter()

    batch_queue = _s.get("batch_queue")
    if batch_queue is not None:  # 攒批路径
        task = _Task(prompt, max_new, sampling)
        batch_queue.put(task)
        task.event.wait()
        if task.error is not None:
            raise RuntimeError(f"generation failed: {task.error}")
        return _render(task, req, started)

    # 原串行路径（默认，与历史行为逐字节一致）
    inputs = tok(prompt, return_tensors="pt").to(model.device)
    gen_kwargs = dict(sampling)
    gen_kwargs.update({
        "max_new_tokens": max_new,
        "pad_token_id": tok.eos_token_id,
        "eos_token_id": _stop_token_ids(tok),
    })
    with _s["lock"]:  # 串行推理，与 backend HAS_NER_MAX_PARALLEL_REQUESTS=1 一致
        with torch.no_grad():
            out = model.generate(**inputs, **gen_kwargs)
    task = _Task(prompt, max_new, sampling)
    task.prompt_len = int(inputs["input_ids"].shape[1])
    task.new_tokens = out[0][inputs["input_ids"].shape[1]:].tolist()
    return _render(task, req, started)


if __name__ == "__main__":
    import uvicorn

    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True, help="HaS_Text_0209_0.6B 模型目录")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--device", default="cuda:0", help="DTK 以 cuda 设备暴露; CPU 传 cpu")
    p.add_argument("--batch-window-ms", type=float, default=0.0,
                   help="Issue #23 攒批窗口；0 = 串行现状（默认）")
    p.add_argument("--max-batch", type=int, default=8, help="攒批单次 forward 的最大请求数")
    args = p.parse_args()

    print(f"[has-transformers] loading {args.model} on {args.device} (bf16)...", flush=True)
    load_model(args.model, args.device)
    if args.batch_window_ms > 0:
        _start_batch_worker(args.batch_window_ms, args.max_batch)
    print(f"[has-transformers] ready on :{args.port}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
