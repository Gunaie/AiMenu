import os
import json
import logging
import re
from typing import Annotated, TypedDict, List, Dict, Any, Union

from langchain_openai import ChatOpenAI
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

# 导入自定义工具
from agent.tools import restaurant_tools

logger = logging.getLogger(__name__)

# 定义 Agent 状态
class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    menu_ids: List[str]   # 推荐的菜品ID（用于前端高亮）
    final_response: str   # 最终聚合的回复内容

class SmartRestaurantGraph:
    def __init__(self):
        self.dashscope_api_key = os.getenv("DASHSCOPE_API_KEY")
        self.model_name = os.getenv("DASHSCOPE_MODEL_NAME", "qwen-plus")
        self.api_base = os.getenv("DASHSCOPE_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1/")
        
        # 核心 LLM
        self.llm = ChatOpenAI(
            api_key=self.dashscope_api_key,
            model=self.model_name,
            base_url=self.api_base,
            temperature=0.7,
            streaming=True
        )
        
        # 绑定工具到 LLM
        self.llm_with_tools = self.llm.bind_tools(restaurant_tools)
        
        # 构建图
        self.workflow = self._create_workflow()
        self.memory = MemorySaver()
        self.app = self.workflow.compile(checkpointer=self.memory)

    def _create_workflow(self):
        graph = StateGraph(AgentState)
        
        # 添加节点
        graph.add_node("agent", self.call_model)
        graph.add_node("tools", ToolNode(restaurant_tools))
        
        # 设置入口
        graph.set_entry_point("agent")
        
        # 条件边：根据 LLM 是否调用工具来决定下一步
        graph.add_conditional_edges(
            "agent",
            self.should_continue,
            {
                "continue": "tools",
                "end": END
            }
        )
        
        # 工具执行完后回到 agent 进行总结回复
        graph.add_edge("tools", "agent")
        
        return graph

    def should_continue(self, state: AgentState):
        """判断是否需要继续调用工具"""
        messages = state['messages']
        last_message = messages[-1]
        # 如果最后一条消息包含工具调用，则继续
        if last_message.tool_calls:
            return "continue"
        return "end"

    async def call_model(self, state: AgentState, config: RunnableConfig):
        """调用 LLM 决策或生成回复"""
        import time
        start_t = time.time()
        logger.info("LangGraph Node: agent - Start")
        
        # 系统提示词：定义角色和行为规范
        system_prompt = """你是一个亲切专业的智能餐厅助手（小二）。
任务：利用提供的【工具】获取信息，并以热情、地道的口吻回答客官的问题。

准则：
1. **角色定位**：自称“小二”，称呼用户为“客官”。语气亲切，适当使用“好嘞”、“客官您慢看”等表达。
2. **工具使用优先级（极其重要）**：
   - **查订单状态/进度/我的单子**：必须调用 `query_order`。即使客官只说了“我的订单怎么样了”，也要直接调用它，工具会自动识别最近的订单。**严禁此时调用配送校验工具**。
   - **查配送范围/能否送达**：仅当客官询问“能否送到某地”或“配送费多少”且明确提到了一个【地点】时，才调用 `check_delivery`。如果没提地点，请礼貌询问地点。
   - **寻找菜品/推荐/口味**：使用 `search_menu`。
3. **意图区分示例**：
   - 用户：“我刚才的订单怎么样了” -> **调用 `query_order()`**。
   - 用户：“光谷地铁站能送到吗” -> **调用 `check_delivery(address='光谷地铁站')`**。
   - 用户：“这里能送到吗” -> 回复：“客官，请告诉小二一个具体的地点，小二马上帮您核实”。
4. **多轮对话**：承接上文，不重复啰嗦。
5. **菜品高亮**：当你向客官推荐或提到具体菜品时，请务必直接说出【参考数据】中的完整菜名。
6. **逻辑闭环**：如果获取到的数据为空或失败，如实告知客官并提供备选建议。
"""
        
        # 构建消息列表
        messages = [SystemMessage(content=system_prompt)] + state['messages']
        
        # 调用 LLM（带工具绑定）
        # 针对流式回复，只有在非工具调用时才打标 responder_llm 标签
        response = await self.llm_with_tools.ainvoke(messages, config=config)
        
        # 提取推荐的菜品 ID (基于正则匹配回复内容)
        # 注意：这里需要根据 LLM 的最终 AIMessage 来提取
        recommended_ids = []
        if not response.tool_calls:
            # 只有在最终回复阶段才进行 ID 提取
            # 逻辑：从 ToolMessage 的结果中反查菜品 ID
            for msg in state['messages']:
                if isinstance(msg, ToolMessage) and "search_menu" in msg.name:
                    try:
                        data = json.loads(msg.content)
                        if data.get("success") and data.get("contents"):
                            for i, content in enumerate(data["contents"]):
                                # 简单正则匹配内容中的菜名是否出现在最终回复中
                                name_match = re.search(r"菜品名称:([^|]+)", content)
                                if name_match:
                                    dish_name = name_match.group(1).strip()
                                    if dish_name in response.content:
                                        # 假设 contents 和 menu_ids 顺序一致
                                        if i < len(data.get("menu_ids", [])):
                                            recommended_ids.append(str(data["menu_ids"][i]))
                    except:
                        continue
        
        logger.info(f"LangGraph Node: agent - Done, duration: {time.time()-start_t:.4f}s")
        return {
            "messages": [response],
            "menu_ids": recommended_ids,
            "final_response": response.content if not response.tool_calls else ""
        }

    async def astream_run(self, user_query: str, thread_id: str = "default"):
        """异步流式运行"""
        config = {"configurable": {"thread_id": thread_id}}
        
        logger.info(f"LangGraph (Tool-Calling) astream_run start: {user_query}")
        has_sent_content = False
        
        try:
            async for event in self.app.astream_events(
                {"messages": [HumanMessage(content=user_query)]},
                config=config,
                version="v2"
            ):
                kind = event["event"]
                
                # 1. 处理 LLM 流式输出 (仅在最终回复阶段)
                if kind == "on_chat_model_stream":
                    # 注意：在 Tool Calling 模式下，第一次调用通常是调用工具，不会有 content
                    # 第二次调用（tools 之后）才是回复内容
                    chunk = event["data"].get("chunk")
                    if chunk and not getattr(chunk, 'tool_calls', None):
                        content = getattr(chunk, 'content', '')
                        if content:
                            has_sent_content = True
                            yield {"type": "content", "content": content}
                
                # 2. 处理元数据 (菜品 ID)
                elif kind == "on_chain_end":
                    if event["name"] == "LangGraph":
                        try:
                            final_state = event["data"].get("output")
                            if isinstance(final_state, dict) and "menu_ids" in final_state:
                                menu_ids = final_state["menu_ids"]
                                if menu_ids:
                                    logger.info(f"LangGraph astream_run - Yielding menu_ids: {menu_ids}")
                                    yield {"type": "metadata", "menu_ids": menu_ids}
                        except Exception as meta_e:
                            logger.error(f"Error extracting metadata: {meta_e}")
                                
        except Exception as e:
            logger.error(f"LangGraph astream_run error: {e}")
            yield {"type": "error", "content": f"小二忙不过来了，请客官稍后再试。(错误详情: {str(e)})"}

    async def run(self, user_query: str, thread_id: str = "default"):
        """同步运行流程"""
        config = {"configurable": {"thread_id": thread_id}}
        final_state = await self.app.ainvoke(
            {"messages": [HumanMessage(content=user_query)]}, 
            config=config
        )
        
        return {
            "response": final_state.get("final_response", "客官久等了，小二没能听清您的需求。"),
            "menu_ids": final_state.get("menu_ids", [])
        }

restaurant_agent = SmartRestaurantGraph()
