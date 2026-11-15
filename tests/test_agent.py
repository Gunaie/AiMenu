"""Agent 单测：状态机路由、最终回复与菜品 ID 提取、订单查询的越权防护。

所有用例均使用 mock，不依赖真实 LLM 与数据库。
"""
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.langgraph_agent import SmartRestaurantGraph
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
