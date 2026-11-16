"""本地 Rerank 预热单测。

覆盖：预热幂等（只加载一次）、加载失败不抛出、按 provider 决定是否预热。
用例不加载真实权重，不访问网络。
"""

import pytest

from tools.local_reranker import LocalReranker


class _FakeModel:
    def __init__(self):
        self.predict_calls = 0

    def predict(self, pairs, batch_size=16, show_progress_bar=False):
        self.predict_calls += 1
        return [0.5 for _ in pairs]


def test_warmup_loads_once_and_runs_first_inference(monkeypatch):
    reranker = LocalReranker()
    calls = []
    fake_model = _FakeModel()

    def fake_load():
        # 与真实实现一致：已加载则直接返回，不重复加载
        if reranker._model is not None:
            return reranker._model
        calls.append(1)
        reranker._model = fake_model
        return fake_model

    monkeypatch.setattr(reranker, "_load", fake_load)
    assert reranker.warmup() is True
    assert reranker.warmup() is True
    assert len(calls) == 1
    # 预热要跑一次真实推理，把 torch 首推理开销也挪到启动阶段
    assert fake_model.predict_calls == 1


def test_warmup_swallows_load_error(monkeypatch):
    reranker = LocalReranker()

    def boom():
        raise RuntimeError("model not found")

    monkeypatch.setattr(reranker, "_load", boom)
    # 预热失败只记日志，不能阻断服务启动
    assert reranker.warmup() is False
    assert reranker.loaded is False


@pytest.mark.parametrize(
    "provider,expected",
    [("local", True), ("dashscope", False), ("LOCAL", True)],
)
def test_warmup_thread_only_for_local_provider(monkeypatch, provider, expected):
    from api import main as api_main

    monkeypatch.setenv("RERANK_PROVIDER", provider)
    monkeypatch.setenv("USE_RERANK", "true")
    started = []
    monkeypatch.setattr(
        api_main.threading,
        "Thread",
        lambda *a, **kw: type("T", (), {"start": lambda self: started.append(kw.get("name"))})(),
    )
    api_main._warmup_rerank_in_background()
    assert bool(started) is expected


def test_agent_module_warmup_spawns_thread(monkeypatch):
    from api import main as api_main

    started = []
    monkeypatch.setattr(
        api_main.threading,
        "Thread",
        lambda *a, **kw: type("T", (), {"start": lambda self: started.append(kw.get("name"))})(),
    )
    api_main._warmup_agent_in_background()
    assert started == ["agent-warmup"]


def test_warmup_thread_skipped_when_rerank_disabled(monkeypatch):
    from api import main as api_main

    monkeypatch.setenv("RERANK_PROVIDER", "local")
    monkeypatch.setenv("USE_RERANK", "false")
    started = []
    monkeypatch.setattr(
        api_main.threading,
        "Thread",
        lambda *a, **kw: type("T", (), {"start": lambda self: started.append(kw.get("name"))})(),
    )
    api_main._warmup_rerank_in_background()
    assert started == []
