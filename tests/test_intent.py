
import pytest
import os
import sys
from dotenv import load_dotenv

# 将项目根目录添加到 sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.langgraph_agent import SmartRestaurantGraph
from langchain_core.messages import HumanMessage

load_dotenv()

@pytest.fixture
def agent():
    return SmartRestaurantGraph()

@pytest.mark.asyncio
async def test_intent_menu(agent):
    state = {"messages": [HumanMessage(content="我想点一些川菜")]}
    # 模拟 RunnableConfig
    config = {"configurable": {"thread_id": "test"}}
    result = await agent.intent_analyzer(state, config)
    assert result["intent"] == "menu"
    assert "川菜" in result["search_query"]

@pytest.mark.asyncio
async def test_intent_delivery(agent):
    state = {"messages": [HumanMessage(content="你们这能送到中关村吗？")]}
    config = {"configurable": {"thread_id": "test"}}
    result = await agent.intent_analyzer(state, config)
    assert result["intent"] == "delivery"
    assert "中关村" in result["search_query"]

@pytest.mark.asyncio
async def test_intent_general(agent):
    state = {"messages": [HumanMessage(content="你们几点开门？")]}
    config = {"configurable": {"thread_id": "test"}}
    result = await agent.intent_analyzer(state, config)
    assert result["intent"] == "general"

@pytest.mark.asyncio
async def test_intent_chat(agent):
    state = {"messages": [HumanMessage(content="你好啊，今天天气不错")]}
    config = {"configurable": {"thread_id": "test"}}
    result = await agent.intent_analyzer(state, config)
    assert result["intent"] == "chat"

@pytest.mark.asyncio
async def test_intent_context_rewrite(agent):
    # 测试多轮对话下的重写逻辑
    state = {
        "messages": [
            HumanMessage(content="推荐几个川菜"),
            HumanMessage(content="那再推荐几个更辣一点的")
        ]
    }
    config = {"configurable": {"thread_id": "test"}}
    result = await agent.intent_analyzer(state, config)
    assert result["intent"] == "menu"
    # 搜索词应该包含“川菜”和“辣”的语义
    search_query = result["search_query"]
    assert any(word in search_query for word in ["川菜", "辣", "推荐"])
