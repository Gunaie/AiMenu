"""Agent 单测：状态机路由、最终回复与菜品 ID 提取、订单查询的越权防护。

所有用例均使用 mock，不依赖真实 LLM 与数据库。
"""
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.langgraph_agent import (
    DELIVERY_ADDRESS_SLOT,
    DIET_CONSTRAINTS_SLOT,
    SmartRestaurantGraph,
)
from agent.tools import query_order, search_menu


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


def test_query_order_requires_login(monkeypatch):
    """缺少 user_id 时必须直接拒答，不能退化为查询全表"""

    def _forbid_connection():
        raise AssertionError("未登录时不应访问数据库")

    monkeypatch.setattr("agent.tools.DataBaseConnection", _forbid_connection)

    result = query_order.func(order_no="ORD100")

    assert "登录" in result


@pytest.mark.asyncio
async def test_call_model_uses_structured_items(graph):
    """只高亮回复里真的提到的菜品，ID 来自结构化 items 而非正则反查"""
    tool_payload = json.dumps(
        {
            "success": True,
            "contents": [
                "菜品ID:4|菜品名称:回锅肉|价格:¥45.00",
                "菜品ID:5|菜品名称:鱼香肉丝|价格:¥38.00",
            ],
            "items": [
                {"dish_id": "4", "dish_name": "回锅肉"},
                {"dish_id": "5", "dish_name": "鱼香肉丝"},
            ],
            "menu_ids": ["4", "5"],
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

    assert result["menu_ids"] == ["4"]


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


def test_patch_search_calls_injects_session_constraints(graph):
    """会话槽位里的辣度/预算/素食必须覆盖模型自己传的参数"""
    message = AIMessage(
        content="",
        tool_calls=[
            {"name": "search_menu", "args": {"query": "川菜", "max_spice": 3}, "id": "call_s"},
            {"name": "query_order", "args": {"order_no": "ORD100"}, "id": "call_q"},
        ],
    )

    patched = graph._patch_search_calls(
        [message], {"max_spice": 0, "max_price": 50.0, "vegetarian": True}
    )

    search_args = patched[-1].tool_calls[0]["args"]
    assert search_args == {
        "query": "川菜",
        "max_spice": 0,
        "max_price": 50.0,
        "vegetarian": True,
    }
    # 其他工具不受影响
    assert patched[-1].tool_calls[1]["args"] == {"order_no": "ORD100"}


def test_describe_constraints(graph):
    text = graph._describe_constraints(
        {"max_spice": 1, "max_price": 50.0, "vegetarian": True}
    )

    assert "微辣" in text
    assert "¥50" in text
    assert "素食" in text


@pytest.mark.asyncio
async def test_run_tools_persists_then_reuses_search_constraints(graph):
    """首轮约束写入槽位，次轮未提及也继续注入检索参数"""
    fake = _FakeToolNode()
    graph.tool_node = fake
    graph.memory_store = _FakeMemorySlots()
    config = {"configurable": {"thread_id": "t2"}}

    first_call = AIMessage(
        content="",
        tool_calls=[{"name": "search_menu", "args": {"query": "川菜"}, "id": "call_1"}],
    )
    await graph._run_tools(
        {
            "messages": [HumanMessage(content="来点不辣的，预算50元以内"), first_call],
            "user_info": {"id": 9},
        },
        config,
    )

    assert graph.memory_store.slots[("t2", DIET_CONSTRAINTS_SLOT, 9)] == {
        "max_spice": 0,
        "max_price": 50.0,
    }
    first_args = fake.calls[-1][0]["messages"][-1].tool_calls[0]["args"]
    assert first_args["max_spice"] == 0
    assert first_args["max_price"] == 50.0

    second_call = AIMessage(
        content="",
        tool_calls=[{"name": "search_menu", "args": {"query": "再推荐几个"}, "id": "call_2"}],
    )
    await graph._run_tools(
        {
            "messages": [HumanMessage(content="再推荐几个"), second_call],
            "user_info": {"id": 9},
        },
        config,
    )

    second_args = fake.calls[-1][0]["messages"][-1].tool_calls[0]["args"]
    assert second_args["max_spice"] == 0
    assert second_args["max_price"] == 50.0


def test_search_menu_verifies_existing_dish_ids(monkeypatch):
    """检索结果必须对账数据库：下架菜品被剔除，菜名以数据库为准"""
    monkeypatch.setattr(
        "agent.tools.search_menu_items_with_ids",
        lambda *args, **kwargs: {
            "items": [
                {"dish_id": "4", "dish_name": "回锅肉(旧名)", "content": "菜品ID:4"},
                {"dish_id": "99", "dish_name": "幽灵菜", "content": "菜品ID:99"},
            ],
            "contents": ["菜品ID:4", "菜品ID:99"],
            "answerable": True,
            "top_score": 0.9,
        },
    )
    monkeypatch.setattr(
        "agent.tools.get_menu_items_by_ids",
        lambda ids: [{"id": 4, "dish_name": "回锅肉", "price": 45.0}],
    )

    result = search_menu.func("川菜")

    assert result["menu_ids"] == ["4"]
    assert [item["dish_name"] for item in result["items"]] == ["回锅肉"]
    assert result["contents"] == ["菜品ID:4"]


def test_search_menu_marks_unanswerable_when_all_dishes_missing(monkeypatch):
    monkeypatch.setattr(
        "agent.tools.search_menu_items_with_ids",
        lambda *args, **kwargs: {
            "items": [{"dish_id": "99", "dish_name": "幽灵菜", "content": "菜品ID:99"}],
            "contents": ["菜品ID:99"],
            "answerable": True,
            "top_score": 0.9,
        },
    )
    monkeypatch.setattr("agent.tools.get_menu_items_by_ids", lambda ids: [])

    result = search_menu.func("幽灵菜")

    assert result["items"] == []
    assert result["answerable"] is False


def test_search_menu_fails_open_when_verification_unavailable(monkeypatch):
    monkeypatch.setattr(
        "agent.tools.search_menu_items_with_ids",
        lambda *args, **kwargs: {
            "items": [{"dish_id": "4", "dish_name": "回锅肉", "content": "菜品ID:4"}],
            "contents": ["菜品ID:4"],
            "answerable": True,
            "top_score": 0.9,
        },
    )

    def _db_down(ids):
        raise RuntimeError("db down")

    monkeypatch.setattr("agent.tools.get_menu_items_by_ids", _db_down)

    result = search_menu.func("川菜")

    assert result["menu_ids"] == ["4"]


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
