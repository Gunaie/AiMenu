import os
import json
import logging
from typing import Annotated, TypedDict, List, Dict, Any, Union

from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from smart_diancan.agent.mcp import general_inquiry, menu_inquiry, delivery_check_tool

logger = logging.getLogger(__name__)

# 定义 Agent 状态
class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], "The messages in the conversation"]
    next_step: str
    tool_params: Dict[str, Any]
    tool_result: Union[str, Dict[str, Any]]
    final_response: str
    menu_ids: List[str]

class SmartRestaurantGraph:
    def __init__(self):
        self.dashscope_api_key = os.getenv("DASHSCOPE_API_KEY")
        self.model_name = os.getenv("DASHSCOPE_MODEL_NAME", "qwen3.7-flash")
        self.api_base = os.getenv("DASHSCOPE_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1/")
        
        # 初始化 LLM
        self.llm = ChatOpenAI(
            api_key=self.dashscope_api_key,
            model=self.model_name,
            base_url=self.api_base,
            temperature=0.7
        )
        
        # 初始化工具映射
        self.tools = {
            "general_inquiry": general_inquiry,
            "menu_inquiry": menu_inquiry,
            "delivery_check_tool": delivery_check_tool
        }
        
        # 构建图
        self.workflow = self._create_workflow()
        self.memory = MemorySaver()
        self.app = self.workflow.compile(checkpointer=self.memory)

    def _create_workflow(self):
        graph = StateGraph(AgentState)
        
        # 添加节点
        graph.add_node("intent_analyzer", self.intent_analyzer)
        graph.add_node("tool_executor", self.tool_executor)
        
        # 设置入口
        graph.set_entry_point("intent_analyzer")
        
        # 添加边
        graph.add_conditional_edges(
            "intent_analyzer",
            self.should_continue,
            {
                "continue": "tool_executor",
                "end": END
            }
        )
        
        graph.add_edge("tool_executor", END)
        
        return graph

    def intent_analyzer(self, state: AgentState):
        """意图分析节点"""
        logger.info("LangGraph Node: intent_analyzer")
        
        system_msg = """你是一个智能餐厅助手的意图分析器。
        请根据对话历史和用户最新问题，选择最合适的工具：
        1. general_inquiry: 餐厅常规咨询（时间、地址、电话、活动等）
        2. menu_inquiry: 菜品推荐、介绍、点餐咨询等
        3. delivery_check_tool: 配送范围检查（需包含地址信息）
        
        你必须严格返回JSON格式：{"tool_name": "工具名", "format_query": "优化后的问题"}
        如果不需要工具，请返回：{"tool_name": "none", "format_query": ""}
        """
        
        messages = [SystemMessage(content=system_msg)] + state['messages']
        
        try:
            response = self.llm.invoke(messages)
            res_content = response.content.strip()
            # 兼容处理 Markdown 代码块
            if "```json" in res_content:
                res_content = res_content.split("```json")[1].split("```")[0].strip()
            
            structured_res = json.loads(res_content)
            
            tool_name = structured_res.get("tool_name", "none")
            format_query = structured_res.get("format_query", "")
            
            return {
                "next_step": tool_name,
                "tool_params": {"query": format_query}
            }
        except Exception as e:
            logger.error(f"意图分析失败: {e}")
            return {"next_step": "none"}

    def should_continue(self, state: AgentState):
        """条件路由"""
        if state["next_step"] == "none" or state["next_step"] not in self.tools:
            return "end"
        return "continue"

    def tool_executor(self, state: AgentState):
        """工具执行节点"""
        tool_name = state["next_step"]
        params = state["tool_params"]
        logger.info(f"LangGraph Node: tool_executor -> {tool_name}")
        
        try:
            tool_func = self.tools[tool_name]
            # 特殊处理配送工具的参数提取（LangGraph 改进：可以在意图分析中提取更多结构化参数）
            if tool_name == "delivery_check_tool":
                # 简单实现，实际可由 LLM 在 intent_analyzer 中提取 address 和 travel_mode
                result = tool_func.invoke({"address": params.get("query"), "travel_mode": "2"})
            else:
                result = tool_func.invoke({"query": params.get("query")})
            
            # 解析结果
            final_response = ""
            menu_ids = []
            
            if isinstance(result, dict):
                final_response = result.get("recommendation", "")
                menu_ids = result.get("menu_ids", [])
            else:
                final_response = str(result)
            
            return {
                "final_response": final_response,
                "menu_ids": menu_ids,
                "messages": state['messages'] + [AIMessage(content=final_response)]
            }
        except Exception as e:
            err_msg = f"工具执行出错: {str(e)}"
            return {
                "final_response": err_msg,
                "messages": state['messages'] + [AIMessage(content=err_msg)]
            }

    def run(self, user_query: str, thread_id: str = "default"):
        """运行图流程"""
        config = {"configurable": {"thread_id": thread_id}}
        initial_state = {
            "messages": [HumanMessage(content=user_query)],
            "next_step": "",
            "tool_params": {},
            "tool_result": "",
            "final_response": "",
            "menu_ids": []
        }
        
        final_state = self.app.invoke(initial_state, config=config)
        
        return {
            "response": final_state.get("final_response", "抱歉，我暂时无法回答。"),
            "menu_ids": final_state.get("menu_ids", [])
        }

# 单例模式
restaurant_agent = SmartRestaurantGraph()
