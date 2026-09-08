"""HaS Text 0.6B 的 transformers 推理服务 —— OpenAI /v1/chat/completions 最小兼容实现。

用途：DTK 等 vLLM 不可用的环境作为 NER 运行时。backend 的 has_client 只使用
choices[0].message.content 与 choices[0].finish_reason，本服务按该契约返回。

用法:
    python ner_transformers_server.py --model <HaS模型目录> --host 0.0.0.0 --port 8080
    # 显存规划: bf16 0.6B ≈ 1.5-2GB；与 Docker ner(vLLM) 行为对齐: bf16 + trust_remote_code
"""
import argparse
import threading
import time

import torch
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer

DEFAULT_MAX_NEW_TOKENS = 2048  # 对齐 compose ner 的 max-model-len 4096 内的安全生成上限

app = FastAPI(title="has-text-transformers")
_s: dict = {}


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
    _s.update(tok=tok, model=model, device=device, lock=threading.Lock(), model_dir=model_dir)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    model: str | None = None  # 接受但忽略，同 vLLM 单模型服务


@app.get("/health")
def health():
    return {"ready": bool(_s.get("model")), "runtime": "transformers"}


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
    inputs = tok(prompt, return_tensors="pt").to(model.device)
    max_new = min(int(req.max_tokens or DEFAULT_MAX_NEW_TOKENS), DEFAULT_MAX_NEW_TOKENS)
    temperature = 0.0 if req.temperature is None else float(req.temperature)
    # 坑(实测): 该模型 generation_config.eos_token_id 指向 <|endoftext|>(151643), 而对话
    # 实际以 <|im_end|>(151645) 结束 → 不显式指定则永远等不到停止符, 每次生成拉满
    # max_tokens(实测 660-1450 tokens/次, 且内容复读)。两者都加入停止集后 22 tokens 收敛
    _im_end = tok.convert_tokens_to_ids("<|im_end|>")
    if _im_end is not None and _im_end == getattr(tok, "unk_token_id", None):
        _im_end = None  # 词表无此 token 时返回 unk id, 误作停止符会在首个 <unk> 处截断
    _eos_ids = [i for i in {tok.eos_token_id, _im_end} if i is not None]
    gen_kwargs = {
        "max_new_tokens": max_new,
        "do_sample": temperature > 0,
        "pad_token_id": tok.eos_token_id,
        "eos_token_id": _eos_ids,
    }
    if gen_kwargs["do_sample"]:
        gen_kwargs["temperature"] = temperature
        gen_kwargs["top_p"] = 1.0 if req.top_p is None else float(req.top_p)

    started = time.perf_counter()
    with _s["lock"]:  # 串行推理，与 backend HAS_NER_MAX_PARALLEL_REQUESTS=1 一致
        with torch.no_grad():
            out = model.generate(**inputs, **gen_kwargs)
    new_tokens = out[0][inputs["input_ids"].shape[1]:]
    content = tok.decode(new_tokens, skip_special_tokens=True)
    # EOS 恰好落在 cap 上时 HF 会把它包含进输出, 此时是自然停止而非截断
    _hit_eos = bool(new_tokens) and int(new_tokens[-1]) in _eos_ids
    finish_reason = "length" if (len(new_tokens) >= max_new and not _hit_eos) else "stop"
    return {
        "id": f"chatcmpl-has-{int(started)}",
        "object": "chat.completion",
        "model": req.model or "HaS_Text_0209_0.6B",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": finish_reason,
        }],
        "usage": {
            "prompt_tokens": int(inputs["input_ids"].shape[1]),
            "completion_tokens": int(len(new_tokens)),
            "total_tokens": int(inputs["input_ids"].shape[1] + len(new_tokens)),
        },
    }


if __name__ == "__main__":
    import uvicorn

    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True, help="HaS_Text_0209_0.6B 模型目录")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--device", default="cuda:0", help="DTK 以 cuda 设备暴露; CPU 传 cpu")
    args = p.parse_args()

    print(f"[has-transformers] loading {args.model} on {args.device} (bf16)...", flush=True)
    load_model(args.model, args.device)
    print(f"[has-transformers] ready on :{args.port}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
