"""Agent 单测：状态机路由、最终回复与菜品 ID 提取、订单查询的越权防护。

所有用例均使用 mock，不依赖真实 LLM 与数据库。
"""
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.langgraph_agent import DELIVERY_ADDRESS_SLOT, SmartRestaurantGraph
from agent.tools import query_order


@pytest.fixture
def graph():
    return SmartRestaurantGraph()


class _FakeLLM:
    """替身：直接返回预设的 AIMessage"""

    def __init__(self, response):
        self.response = response

    async def ainvoke(self, messages, config=None):
        return self.response


def test_should_continue_with_tool_calls(graph):
    message = AIMessage(
        content="",
        tool_calls=[{"name": "search_menu", "args": {"query": "川菜"}, "id": "call_1"}],
    )
    assert graph.should_continue({"messages": [message]}) == "continue"


def test_should_continue_without_tool_calls(graph):
    assert graph.should_continue({"messages": [AIMessage(content="客官您好")]}) == "end"


@pytest.mark.asyncio
async def test_call_model_extracts_recommended_menu_ids(graph):
    tool_payload = json.dumps(
        {
            "success": True,
            "contents": ["菜品ID:4|菜品名称:回锅肉|价格:¥45.00"],
            "menu_ids": ["4"],
        },
        ensure_ascii=False,
    )
    state = {
        "messages": [
            HumanMessage(content="推荐点川菜"),
            ToolMessage(content=tool_payload, tool_call_id="call_1", name="search_menu"),
        ],
        "user_info": {"id": 1, "username": "admin", "nickname": "店小二"},
    }
    graph.llm_with_tools = _FakeLLM(AIMessage(content="给客官推荐回锅肉"))

    result = await graph.call_model(state, {})

    assert result["final_response"] == "给客官推荐回锅肉"
    assert result["menu_ids"] == ["4"]


@pytest.mark.asyncio
async def test_call_model_returns_empty_response_when_calling_tool(graph):
    state = {"messages": [HumanMessage(content="推荐点川菜")]}
    graph.llm_with_tools = _FakeLLM(
        AIMessage(
            content="",
            tool_calls=[{"name": "search_menu", "args": {"query": "川菜"}, "id": "call_1"}],
        )
    )

    result = await graph.call_model(state, {})

    assert result["final_response"] == ""
    assert result["menu_ids"] == []


class _FakeCursor:
    def __init__(self):
        self.calls = []
        self._row = {
            "order_no": "ORD100",
            "order_status": "paid",
            "items_detail": json.dumps([{"dish_name": "回锅肉", "quantity": 2}], ensure_ascii=False),
            "actual_amount": 93.0,
            "delivery_address": "武汉市洪山区",
        }

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return dict(self._row)


class _FakeDB:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


def test_query_order_only_queries_current_user(monkeypatch):
    cursor = _FakeCursor()
    monkeypatch.setattr("agent.tools.DataBaseConnection", lambda: _FakeDB(cursor))

    result = query_order.func(order_no="ORD100", user_id=7)

    assert "ORD100" in result
    sql, params = cursor.calls[-1]
    assert "user_id" in sql
    assert params == ("ORD100", 7)


def test_inject_user_id_overrides_model_arguments(graph):
    message = AIMessage(
        content="",
        tool_calls=[{"name": "query_order", "args": {"order_no": "ORD100", "user_id": 1}, "id": "call_q"}],
    )

    injected = graph._inject_user_id([message], 7)

    assert injected[-1].tool_calls[0]["args"] == {"order_no": "ORD100", "user_id": 7}


def test_inject_user_id_leaves_other_tools_untouched(graph):
    message = AIMessage(
        content="",
        tool_calls=[{"name": "search_menu", "args": {"query": "川菜"}, "id": "call_s"}],
    )

    assert graph._inject_user_id([message], 7) == [message]


def test_inject_user_id_requires_login(graph):
    message = AIMessage(content="", tool_calls=[{"name": "query_order", "args": {}, "id": "call_q"}])

    assert graph._inject_user_id([message], None) == [message]


class _FakeToolNode:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, payload, config=None):
        self.calls.append((payload, config))
        return {"messages": payload["messages"]}


@pytest.mark.asyncio
async def test_run_tools_passes_config_and_injects_user(graph):
    """工具节点必须透传 config，否则新版 LangGraph 会直接抛错"""
    fake = _FakeToolNode()
    graph.tool_node = fake
    config = {"configurable": {"thread_id": "t1"}}
    message = AIMessage(
        content="",
        tool_calls=[{"name": "query_order", "args": {"order_no": "ORD100"}, "id": "call_q"}],
    )

    await graph._run_tools({"messages": [message], "user_info": {"id": 9}}, config)

    payload, received_config = fake.calls[0]
    assert received_config is config
    assert payload["messages"][-1].tool_calls[0]["args"]["user_id"] == 9


def test_patch_delivery_calls_fills_missing_address(graph):
    message = AIMessage(
        content="",
        tool_calls=[{"name": "check_delivery", "args": {"address": ""}, "id": "call_d"}],
    )

    patched, address = graph._patch_delivery_calls([message], "光谷广场")

    assert patched[-1].tool_calls[0]["args"]["address"] == "光谷广场"
    # 补齐的是历史地址，不算本轮新给出的地点
    assert address is None


def test_patch_delivery_calls_keeps_explicit_address(graph):
    message = AIMessage(
        content="",
        tool_calls=[{"name": "check_delivery", "args": {"address": "武昌火车站"}, "id": "call_d"}],
    )

    patched, address = graph._patch_delivery_calls([message], "光谷广场")

    assert patched == [message]
    assert address == "武昌火车站"


def test_patch_delivery_calls_ignores_other_tools(graph):
    message = AIMessage(
        content="",
        tool_calls=[{"name": "search_menu", "args": {"query": "川菜"}, "id": "call_s"}],
    )

    patched, address = graph._patch_delivery_calls([message], "光谷广场")

    assert patched == [message]
    assert address is None


class _FakeMemorySlots:
    """替身：只用字典记录槽位，不依赖 Redis"""

    def __init__(self):
        self.slots = {}

    def get_slot(self, session_id, name, user_id=None):
        return self.slots.get((session_id, name, user_id))

    def set_slot(self, session_id, name, value, user_id=None):
        self.slots[(session_id, name, user_id)] = value


@pytest.mark.asyncio
async def test_run_tools_saves_then_reuses_delivery_address(graph):
    """第一轮确认的地点写入槽位，第二轮追问漏传地址时自动复用"""
    fake = _FakeToolNode()
    graph.tool_node = fake
    graph.memory_store = _FakeMemorySlots()
    config = {"configurable": {"thread_id": "t1"}}

    first = AIMessage(
        content="",
        tool_calls=[{"name": "check_delivery", "args": {"address": "光谷广场"}, "id": "call_1"}],
    )
    await graph._run_tools({"messages": [first], "user_info": {"id": 9}}, config)
    assert graph.memory_store.slots[("t1", DELIVERY_ADDRESS_SLOT, 9)] == "光谷广场"

    second = AIMessage(
        content="",
        tool_calls=[{"name": "check_delivery", "args": {}, "id": "call_2"}],
    )
    await graph._run_tools({"messages": [second], "user_info": {"id": 9}}, config)

    assert fake.calls[-1][0]["messages"][-1].tool_calls[0]["args"]["address"] == "光谷广场"
