import os
import json
import logging
from typing import Annotated, TypedDict, List, Dict, Any, Union

from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import add_messages

# 导入底层工具函数（直接使用逻辑函数，而不是包装好的tool）
from smart_diancan.tools.chroma_tool import search_menu_items_with_ids
from smart_diancan.tools.db_tool import get_all_menu_items
from smart_diancan.tools.amap_tool import check_delivery_range, PathInputModel

logger = logging.getLogger(__name__)

# 定义 Agent 状态
class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    intent: str           # 用户意图 (general, menu, delivery, chat)
    search_query: str     # 重写后的检索词
    retrieved_data: Any   # 检索到的原始数据
    final_response: str   # 最终回复
    menu_ids: List[str]   # 推荐的菜品ID

class SmartRestaurantGraph:
    def __init__(self):
        self.dashscope_api_key = os.getenv("DASHSCOPE_API_KEY")
        self.model_name = os.getenv("DASHSCOPE_MODEL_NAME", "qwen3.7-flash")
        self.api_base = os.getenv("DASHSCOPE_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1/")
        
        self.llm = ChatOpenAI(
            api_key=self.dashscope_api_key,
            model=self.model_name,
            base_url=self.api_base,
            temperature=0.7
        )
        
        self.workflow = self._create_workflow()
        self.memory = MemorySaver()
        self.app = self.workflow.compile(checkpointer=self.memory)

    def _create_workflow(self):
        graph = StateGraph(AgentState)
        
        graph.add_node("intent_analyzer", self.intent_analyzer)
        graph.add_node("data_retriever", self.data_retriever)
        graph.add_node("responder", self.responder)
        
        graph.set_entry_point("intent_analyzer")
        
        # 意图分析 -> 数据检索 (如果需要) 或 直接回复
        graph.add_conditional_edges(
            "intent_analyzer",
            lambda x: "retrieve" if x["intent"] != "chat" else "respond",
            {
                "retrieve": "data_retriever",
                "respond": "responder"
            }
        )
        
        graph.add_edge("data_retriever", "responder")
        graph.add_edge("responder", END)
        
        return graph

    def intent_analyzer(self, state: AgentState):
        """意图分析：确定意图并重写搜索词"""
        logger.info("LangGraph Node: intent_analyzer")
        
        system_msg = """你是一个智能餐厅助手的调度中心。请分析用户输入并决定意图。
        
        **意图分类：**
        1. menu: 涉及菜品推荐、查询、口味咨询。
        2. delivery: 涉及配送范围、距离查询。
        3. general: 涉及餐厅营业时间、电话、地址等基本信息。
        4. chat: 纯闲聊、礼貌性回复或不涉及上述内容的简单对话。

        **任务：搜索词重写 (Search Query Rewriting)**
        如果用户的问题依赖上下文（如“再推荐几个”、“还有吗”），请将其重写为独立的搜索关键词。
        
        请严格返回JSON：{"intent": "意图", "search_query": "重写后的搜索词"}
        """
        
        messages = [SystemMessage(content=system_msg)] + state['messages'][-10:]
        
        try:
            response = self.llm.invoke(messages)
            res_content = response.content.strip()
            if "```json" in res_content:
                res_content = res_content.split("```json")[1].split("```")[0].strip()
            
            res = json.loads(res_content)
            return {
                "intent": res.get("intent", "chat"),
                "search_query": res.get("search_query", "")
            }
        except Exception as e:
            logger.error(f"意图分析失败: {e}")
            return {"intent": "chat", "search_query": ""}

    def data_retriever(self, state: AgentState):
        """数据检索：从不同数据源获取原始信息"""
        intent = state["intent"]
        query = state["search_query"]
        logger.info(f"LangGraph Node: data_retriever -> {intent} for '{query}'")
        
        retrieved_data = None
        menu_ids = []
        
        try:
            if intent == "menu":
                # 增加检索数量 (top_k=5)，为多轮对话提供更多候选，由 responder 进行二次筛选
                similar_result = search_menu_items_with_ids(query, top_k=5)
                retrieved_data = similar_result.get("contents", [])
                menu_ids = similar_result.get("ids", [])
            elif intent == "delivery":
                # 配送校验 (假设 query 就是地址)
                retrieved_data = check_delivery_range(query, "2")
            elif intent == "general":
                # 简单处理：可以从文件或配置读取，这里模拟检索
                retrieved_data = "本餐厅营业时间为 9:00 - 22:00，地址位于北京市海淀区XX路，联系电话：010-12345678。"
            
            return {
                "retrieved_data": retrieved_data,
                "menu_ids": menu_ids
            }
        except Exception as e:
            logger.error(f"数据检索失败: {e}")
            return {"retrieved_data": "数据获取失败"}

    def responder(self, state: AgentState):
        """生成最终回复：结合历史、检索数据和用户原始提问"""
        logger.info("LangGraph Node: responder")
        
        intent = state["intent"]
        data = state["retrieved_data"]
        
        system_msg = f"""你是一个亲切专业的智能餐厅点餐助手。
        你的任务是根据提供的【参考数据】和【对话历史】，回答用户的【当前提问】。
        
        **回复准则：**
        1. 保持口语化、亲切，像真人店小二一样，多使用“客官”、“小店”等词汇。
        2. **多轮对话处理**：
           - 务必承接上文。如果用户说“再推荐几个”，你应该基于之前的推荐进一步挖掘参考数据中的其他项。
           - 避免重复推荐上一轮已经提到过的菜品。
        3. **数据筛选**：
           - 参考数据中包含多道菜品，请根据用户的特定要求（如“更辣”、“素食”、“便宜点”）从中筛选最符合的进行详细描述。
           - 如果参考数据里确实没有符合“更辣”要求的菜品，再告知用户并建议模拟调整口味。
        4. 推荐时要详细描述口感和推荐理由，并引导用户下单。
        5. 不要复读参考数据，要进行二次创作。

        **参考数据（来自向量数据库）：**
        {json.dumps(data, ensure_ascii=False) if data else "无特定参考数据，请根据常识回答"}
        """
        
        messages = [SystemMessage(content=system_msg)] + state['messages']
        
        try:
            response = self.llm.invoke(messages)
            final_text = response.content
            
            return {
                "final_response": final_text,
                "messages": [AIMessage(content=final_text)]
            }
        except Exception as e:
            logger.error(f"生成回复失败: {e}")
            return {"final_response": "抱歉，我现在有点忙，请稍后再试。"}

    async def astream_run(self, user_query: str, thread_id: str = "default"):
        """异步流式运行"""
        config = {"configurable": {"thread_id": thread_id}}
        
        # 使用 astream_events 来获取详细的流式事件
        async for event in self.app.astream_events(
            {"messages": [HumanMessage(content=user_query)]},
            config=config,
            version="v2"
        ):
            kind = event["event"]
            if kind == "on_chat_model_stream":
                # 只在 responder 阶段流式输出文本
                # 注意：这里需要根据具体的 trace 逻辑判断当前是否在 responder 节点
                # 简单起见，我们直接检查 content
                content = event["data"]["chunk"].content
                if content:
                    yield {"type": "content", "content": content}
            
            elif kind == "on_chain_end":
                # 当整个 graph 结束时，提取最终状态中的 menu_ids
                if event["name"] == "LangGraph":
                    final_output = event["data"]["output"]
                    if "menu_ids" in final_output:
                        yield {"type": "metadata", "menu_ids": final_output["menu_ids"]}

    def run(self, user_query: str, thread_id: str = "default"):
        """运行流程"""
        config = {"configurable": {"thread_id": thread_id}}
        # 初始状态只传入新消息
        final_state = self.app.invoke(
            {"messages": [HumanMessage(content=user_query)]}, 
            config=config
        )
        
        return {
            "response": final_state.get("final_response", "抱歉，没能帮到您。"),
            "menu_ids": final_state.get("menu_ids", [])
        }

restaurant_agent = SmartRestaurantGraph()
