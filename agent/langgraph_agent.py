import os
import json
import logging
from typing import Annotated, TypedDict, List, Dict, Any, Union

from langchain_openai import ChatOpenAI
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import add_messages

# 导入底层工具函数（直接使用逻辑函数，而不是包装好的tool）
from tools.chroma_tool import search_menu_items_with_ids
from tools.db_tool import get_all_menu_items
from tools.amap_tool import check_delivery_range, PathInputModel

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
            temperature=0.7,
            streaming=True
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

    async def intent_analyzer(self, state: AgentState, config: RunnableConfig):
        """意图分析：确定意图并重写搜索词"""
        import time
        start_t = time.time()
        logger.info("LangGraph Node: intent_analyzer - Start")
        
        user_msg = state['messages'][-1].content
        # 快速判断：简单问候直接跳过 LLM
        greetings = ["你好", "您好", "在吗", "hi", "hello", "哈喽"]
        if user_msg.strip().lower() in greetings:
            logger.info(f"LangGraph Node: intent_analyzer - Fast path, duration: {time.time()-start_t:.4f}s")
            return {"intent": "chat", "search_query": ""}

        system_msg = """分析用户意图并重写搜索词。
意图分类：
1. menu: 菜品推荐、查询、口味。
2. delivery: 配送范围、距离。
3. general: 餐厅基本信息。
4. chat: 闲聊。

如果依赖上下文（如“再推荐几个”），重写为独立搜索词。
只返回 JSON: {"intent": "...", "search_query": "..."}"""
        
        messages = [SystemMessage(content=system_msg)] + state['messages'][-5:] # 减少历史消息数量以提速
        
        try:
            # 传递 config 以便 callbacks 传播
            response = await self.llm.ainvoke(messages, config=config)
            res_content = response.content.strip()
            # 兼容处理
            if "```json" in res_content:
                res_content = res_content.split("```json")[1].split("```")[0].strip()
            elif "{" in res_content and "}" in res_content:
                # 提取第一个 { 和 最后一个 } 之间的内容
                start = res_content.find("{")
                end = res_content.rfind("}") + 1
                res_content = res_content[start:end]
            
            res = json.loads(res_content)
            logger.info(f"LangGraph Node: intent_analyzer - Done, intent: {res.get('intent')}, duration: {time.time()-start_t:.4f}s")
            return {
                "intent": res.get("intent", "chat"),
                "search_query": res.get("search_query", "")
            }
        except Exception as e:
            logger.error(f"意图分析失败: {e}")
            return {"intent": "chat", "search_query": ""}

    async def data_retriever(self, state: AgentState):
        """数据检索：从不同数据源获取原始信息"""
        import time
        start_t = time.time()
        intent = state["intent"]
        query = state["search_query"]
        logger.info(f"LangGraph Node: data_retriever - Start -> {intent} for '{query}'")
        
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
            
            logger.info(f"LangGraph Node: data_retriever - Done, duration: {time.time()-start_t:.4f}s")
            return {
                "retrieved_data": retrieved_data,
                "menu_ids": menu_ids
            }
        except Exception as e:
            logger.error(f"数据检索失败: {e}")
            return {"retrieved_data": "数据获取失败"}

    async def responder(self, state: AgentState, config: RunnableConfig):
        """生成最终回复：结合历史、检索数据和用户原始提问"""
        import time
        import re
        start_t = time.time()
        logger.info("LangGraph Node: responder - Start")
        
        intent = state.get("intent", "chat")
        data = state.get("retrieved_data", [])
        
        system_msg = f"""你是一个亲切专业的智能餐厅助手（小二）。
任务：根据【参考数据】和【对话历史】，回答用户的【当前提问】。

准则：
1. 口语化，使用“客官”、“小店”。
2. **多轮对话**：承接上文，不重复推荐。
3. **数据筛选**：从数据中选出最符合要求的（如“更辣”、“素食”）。若无，则如实告知。
4. **必须明确提及菜品名称**：在推荐时，请务必使用【参考数据】中提供的完整菜品名称，不要随意缩减或更改。
5. 描述口感和理由，引导下单。

参考数据：
{json.dumps(data, ensure_ascii=False) if data else "无特定数据"}
"""
        
        messages = [SystemMessage(content=system_msg)] + state['messages']
        
        try:
            # 关键：tags 应该放在 config 中，而不是作为独立参数传递给 astream
            final_text = ""
            stream_config = config.copy() if config else {}
            if "tags" not in stream_config:
                stream_config["tags"] = []
            if "responder_llm" not in stream_config["tags"]:
                stream_config["tags"].append("responder_llm")

            async for chunk in self.llm.astream(messages, config=stream_config):
                final_text += chunk.content
            
            # 根据生成的文本内容，动态筛选真正推荐的菜品 ID
            recommended_ids = []
            if data and isinstance(data, list):
                for item_str in data:
                    # 尝试从内容字符串中提取 ID 和名称
                    # 格式参考: "菜品ID:1|菜品名称:东坡肉|..."
                    id_match = re.search(r"菜品ID:(\d+)", item_str)
                    name_match = re.search(r"菜品名称:([^|]+)", item_str)
                    
                    if id_match and name_match:
                        dish_id = id_match.group(1)
                        dish_name = name_match.group(1).strip()
                        # 如果模型回复中提到了该菜品名称，则认为是被选中的推荐
                        if dish_name in final_text:
                            recommended_ids.append(dish_id)
            
            logger.info(f"LangGraph Node: responder - Done, filtered menu_ids: {recommended_ids}, duration: {time.time()-start_t:.4f}s")
            return {
                "final_response": final_text,
                "messages": [AIMessage(content=final_text)],
                "menu_ids": recommended_ids
            }
        except Exception as e:
            logger.error(f"生成回复失败: {e}")
            return {"final_response": "抱歉，我现在有点忙，请稍后再试。", "menu_ids": []}

    async def astream_run(self, user_query: str, thread_id: str = "default"):
        """异步流式运行"""
        config = {"configurable": {"thread_id": thread_id}}
        
        logger.info(f"LangGraph astream_run start: {user_query}")
        has_sent_content = False
        
        try:
            async for event in self.app.astream_events(
                {"messages": [HumanMessage(content=user_query)]},
                config=config,
                version="v2"
            ):
                kind = event["event"]
                tags = event.get("tags", [])
                
                # 1. 处理流式文本输出
                if kind == "on_chat_model_stream":
                    if "responder_llm" in tags or not tags: 
                        chunk = event["data"].get("chunk")
                        if chunk:
                            content = getattr(chunk, 'content', '')
                            if content:
                                has_sent_content = True
                                yield {"type": "content", "content": content}
                
                # 2. 处理节点结束事件
                elif kind == "on_chain_end":
                    if event["name"] == "LangGraph":
                        final_state = event["data"]["output"]
                        # 如果直到最后都没有流式输出内容（可能是报错了或节点直接返回了非流式内容）
                        if not has_sent_content and "final_response" in final_state:
                            yield {"type": "content", "content": final_state["final_response"]}
                        
                        if isinstance(final_state, dict) and "menu_ids" in final_state:
                            menu_ids = final_state["menu_ids"]
                            logger.info(f"LangGraph astream_run - Yielding menu_ids: {menu_ids}")
                            yield {"type": "metadata", "menu_ids": menu_ids}
        except Exception as e:
            logger.error(f"LangGraph astream_run error: {e}")
            yield {"type": "error", "content": "对话系统暂时不可用，请稍后再试。"}

    async def run(self, user_query: str, thread_id: str = "default"):
        """运行流程 (异步)"""
        config = {"configurable": {"thread_id": thread_id}}
        # 初始状态只传入新消息
        final_state = await self.app.ainvoke(
            {"messages": [HumanMessage(content=user_query)]}, 
            config=config
        )
        
        return {
            "response": final_state.get("final_response", "抱歉，没能帮到您。"),
            "menu_ids": final_state.get("menu_ids", [])
        }

restaurant_agent = SmartRestaurantGraph()
