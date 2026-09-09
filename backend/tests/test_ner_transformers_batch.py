"""Issue #23 轴B（退守路线）：ner_transformers_server.py 攒批 worker 编排单测。

部署脚本不在 app 包、且本机无 torch/DTK：用 fake 模块注入后按文件路径加载，
只测纯编排语义（窗口收集、采样分桶、失败回退、结果分发、finish_reason），
tensor 级 batch generate 留给实例冒烟（cloud-deploy/e2e）。
"""
from __future__ import annotations

import importlib.util
import sys
import threading
import types
from pathlib import Path

import pytest

SERVER_PATH = Path(__file__).resolve().parents[2] / "cloud-deploy" / "ner_transformers_server.py"


class _FakeModule(types.ModuleType):
    def __getattr__(self, name):
        # 未显式定义的属性一律给 no-op（torch.backends.cuda.* 等）
        return _FakeModule(name, lambda *a, **k: None) if False else types.SimpleNamespace()


def _install_fakes():
    torch = types.ModuleType("torch")
    torch.backends = types.SimpleNamespace(
        cuda=types.SimpleNamespace(enable_flash_sdp=lambda *a: None,
                                   enable_mem_efficient_sdp=lambda *a: None))
    torch.no_grad = lambda: types.SimpleNamespace(__enter__=lambda s: None, __exit__=lambda s, *a: None)

    fastapi = types.ModuleType("fastapi")
    fastapi.FastAPI = lambda *a, **k: _FakeRouter()
    fastapi.get = lambda *a, **k: (lambda f: f)
    fastapi.post = lambda *a, **k: (lambda f: f)

    transformers = types.ModuleType("transformers")
    transformers.AutoModelForCausalLM = types.SimpleNamespace(from_pretrained=lambda *a, **k: None)
    transformers.AutoTokenizer = types.SimpleNamespace(from_pretrained=lambda *a, **k: None)

    for name, mod in (("torch", torch), ("fastapi", fastapi), ("transformers", transformers)):
        sys.modules.setdefault(name, mod)


class _FakeRouter:
    def get(self, *a, **k):
        return lambda f: f

    def post(self, *a, **k):
        return lambda f: f


_install_fakes()
spec = importlib.util.spec_from_file_location("ner_transformers_server_under_test", SERVER_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _start_worker(monkeypatch, generate_fn, window_ms=50.0, max_batch=8):
    mod._s.update(batch_queue=None)
    monkeypatch.setattr(mod, "_generate_batch", generate_fn)
    mod._s["batch_queue"] = __import__("queue").Queue()
    mod._s["batch_window_ms"] = window_ms
    mod._s["max_batch"] = max_batch
    thread = threading.Thread(target=mod._batch_worker, daemon=True)
    thread.start()
    return mod._s["batch_queue"]


def test_batch_worker_collects_window_and_dispatches(monkeypatch):
    """窗口内多个请求合成一批，结果各自分发。"""
    seen_batches = []

    def fake_generate(tasks):
        seen_batches.append([t.prompt for t in tasks])
        for t in tasks:
            t.prompt_len = 10
            t.new_tokens = [5, 6]

    q = _start_worker(monkeypatch, fake_generate, window_ms=300.0)
    tasks = [mod._Task(f"p{i}", 64, {"do_sample": False}) for i in range(3)]
    for t in tasks:
        q.put(t)
    for t in tasks:
        assert t.event.wait(timeout=5)
    assert seen_batches == [["p0", "p1", "p2"]]  # 同采样参数合成一批
    assert all(t.new_tokens == [5, 6] for t in tasks)


def test_batch_worker_splits_sampling_buckets(monkeypatch):
    """贪心与采样请求不能混批（generate 的采样参数是批级），按 key 分桶。"""
    seen = []

    def fake_generate(tasks):
        seen.append([t.gen_kwargs.get("do_sample") for t in tasks])

    q = _start_worker(monkeypatch, fake_generate, window_ms=300.0)
    greedy = mod._Task("greedy", 64, {"do_sample": False})
    sampled = mod._Task("sampled", 64, {"do_sample": True, "temperature": 0.7, "top_p": 0.6})
    q.put(greedy)
    q.put(sampled)
    assert greedy.event.wait(timeout=5) and sampled.event.wait(timeout=5)
    assert sorted(map(len, seen)) == [1, 1]  # 两批各一


def test_batch_worker_falls_back_to_serial_on_batch_error(monkeypatch):
    """batch 异常时逐个单请求重跑；单请求也失败则 task.error 置位。"""
    calls = []

    def flaky_generate(tasks):
        calls.append(len(tasks))
        if len(tasks) > 1:
            raise RuntimeError("oom")
        tasks[0].prompt_len = 3
        tasks[0].new_tokens = [1]

    q = _start_worker(monkeypatch, flaky_generate, window_ms=300.0)
    ok_tasks = [mod._Task(f"ok{i}", 64, {"do_sample": False}) for i in range(2)]
    for t in ok_tasks:
        q.put(t)
    for t in ok_tasks:
        assert t.event.wait(timeout=5)
    assert calls == [2, 1, 1]  # 一批失败 → 两次单跑
    assert all(t.new_tokens == [1] for t in ok_tasks)


def test_finish_reason_semantics():
    # 截到停止符（无论是否顶满预算）= stop；顶满预算且无停止符 = length
    assert mod._finish_reason([9, 9, 9], max_new=3, eos_ids=[9]) == "stop"
    assert mod._finish_reason([1, 2, 3], max_new=3, eos_ids=[9]) == "length"
    assert mod._finish_reason([1, 2], max_new=3, eos_ids=[9]) == "stop"


def test_gen_kwargs_greedy_vs_sampling():
    greedy = mod.ChatRequest(messages=[{"role": "user", "content": "x"}])
    max_new, kwargs = mod._gen_kwargs_for(greedy)
    assert max_new == mod.DEFAULT_MAX_NEW_TOKENS and kwargs == {"do_sample": False}
    sampled = mod.ChatRequest(messages=[{"role": "user", "content": "x"}],
                              temperature=0.7, top_p=0.6, max_tokens=128)
    max_new, kwargs = mod._gen_kwargs_for(sampled)
    assert max_new == 128
    assert kwargs == {"do_sample": True, "temperature": 0.7, "top_p": 0.6}
