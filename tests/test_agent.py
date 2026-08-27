
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from langchain_core.messages import HumanMessage, AIMessage
from agent.langgraph_agent import SmartRestaurantGraph

@pytest.fixture
def agent():
    return SmartRestaurantGraph()

@pytest.mark.asyncio
async def test_agent_full_flow_menu(agent):
    # 模拟 LLM 响应
    mock_intent_response = AIMessage(content='{"intent": "menu", "search_query": "川菜"}')
    mock_responder_response = AIMessage(content="为您推荐麻婆豆腐。")
    
    # 模拟 astream 以支持 responder 节点
    async def mock_astream(*args, **kwargs):
        yield AIMessage(content="为您推荐麻婆豆腐。")

    # 模拟 ainvoke
    async def mock_ainvoke(*args, **kwargs):
        # 第一次调用是 intent_analyzer
        return mock_intent_response

    with patch("langchain_openai.ChatOpenAI.ainvoke", new_callable=AsyncMock) as mock_inv:
        mock_inv.return_value = mock_intent_response
        
        with patch("langchain_openai.ChatOpenAI.astream", side_effect=mock_astream):
            # 运行 Agent
            result = await agent.run("我想吃川菜")
            
            assert "麻婆豆腐" in result["response"]
            assert isinstance(result["menu_ids"], list)

@pytest.mark.asyncio
async def test_agent_chat_flow(agent):
    mock_intent_response = AIMessage(content='{"intent": "chat", "search_query": ""}')
    
    async def mock_astream(*args, **kwargs):
        yield AIMessage(content="您好！很高兴为您服务。")

    with patch("langchain_openai.ChatOpenAI.ainvoke", new_callable=AsyncMock) as mock_inv:
        mock_inv.return_value = mock_intent_response
        
        with patch("langchain_openai.ChatOpenAI.astream", side_effect=mock_astream):
            result = await agent.run("你好")
            assert "很高兴为您服务" in result["response"]
