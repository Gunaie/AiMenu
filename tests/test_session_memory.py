"""会话记忆持久化单测：使用内存替身模拟 Redis，不依赖真实实例。"""
import pytest
from langchain_core.messages import AIMessage, HumanMessage

import agent.session_memory as session_memory
from agent.session_memory import RedisSessionMemory


class _FakeCache:
    def __init__(self):
        self.store = {}
        self.expires = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, expire=3600):
        self.store[key] = value
        self.expires[key] = expire

    def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def memory(monkeypatch):
    fake = _FakeCache()
    monkeypatch.setattr(session_memory, "cache", fake)
    return RedisSessionMemory(ttl=600, max_turns=2), fake


def test_load_empty_session_returns_empty_list(memory):
    store, _ = memory

    assert store.load("s1") == []


def test_append_turn_then_load_restores_messages(memory):
    store, fake = memory

    store.append_turn("s1", "有什么清淡的汤", "推荐丝瓜蛋汤")
    messages = store.load("s1")

    assert [type(m) for m in messages] == [HumanMessage, AIMessage]
    assert messages[0].content == "有什么清淡的汤"
    assert messages[1].content == "推荐丝瓜蛋汤"
    assert fake.expires["aimenu:agent:history:s1"] == 600


def test_append_turn_truncates_to_window(memory):
    store, _ = memory

    for i in range(4):
        store.append_turn("s1", f"问题{i}", f"回答{i}")

    messages = store.load("s1")
    assert len(messages) == 4  # max_turns=2 -> 保留最近 2 轮
    assert messages[0].content == "问题2"
    assert messages[-1].content == "回答3"


def test_append_turn_skips_empty_answer(memory):
    store, _ = memory

    store.append_turn("s1", "问题", "")

    assert store.load("s1") == []


def test_clear_removes_session(memory):
    store, _ = memory

    store.append_turn("s1", "问题", "回答")
    store.clear("s1")

    assert store.load("s1") == []
