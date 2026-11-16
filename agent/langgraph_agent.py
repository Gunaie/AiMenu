import os
import json
import logging
import re
from typing import Annotated, TypedDict, List, Dict, Any, Union, Optional

from langchain_openai import ChatOpenAI
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

# 导入自定义工具
from agent.tools import restaurant_tools
from tools.observability import record_usage
from agent.session_memory import RedisSessionMemory

logger = logging.getLogger(__name__)

# 会话级槽位名：记录本会话已确认过的配送地址，供后续「那配送费多少」这类追问复用
DELIVERY_ADDRESS_SLOT = "delivery_address"

# 确定性输入护栏：在进入 LLM 之前拦掉两类请求，不依赖模型自觉
# 1) 只有标点/空白，没有任何文字或数字（模型容易自说自话编菜品）
MEANINGLESS_QUERY_RE = re.compile(r"^[\W_]+$")
# 2) 索要他人/全部用户订单或隐私信息（越权类请求）
PRIVILEGE_QUERY_PATTERNS = (
    re.compile(r"(所有|全部|其他|其它|他人|别人).{0,8}(用户|客官|顾客|客人|人)?.{0,8}(订单|单号|信息|密码|手机号|地址)"),
    re.compile(r"(用户|客官|顾客|客人).{0,6}(密码|身份证|手机号)"),
    # 第三人称持有：他的订单 / 她手机号 / 对方住址（不含「他家地址能送到吗」这类正常配送问题）
    re.compile(r"(他|她|ta|TA|对方|那个人).{0,6}(订单|单号|手机号|电话|密码|身份证|住址)"),
)

# 定义 Agent 状态
class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    menu_ids: List[str]   # 推荐的菜品ID（用于前端高亮）
    final_response: str   # 最终聚合的回复内容
    user_info: Optional[Dict[str, Any]] # 当前用户信息
    guard_blocked: bool   # 是否被确定性输入护栏拦截（拦截时不进入 LLM/工具）

class SmartRestaurantGraph:
    def __init__(self, model_name: Optional[str] = None):
        self.dashscope_api_key = os.getenv("DASHSCOPE_API_KEY")
        # 允许显式指定模型：评测做 A/B 时用独立实例，不受各工具模块 load_dotenv 的影响
        self.model_name = model_name or os.getenv("DASHSCOPE_MODEL_NAME", "qwen-plus")
        self.api_base = os.getenv("DASHSCOPE_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1/")
        
        # 核心 LLM
        # qwen3 系列默认开启思考模式，点餐场景不需要推理过程，且会拖慢流式首字响应
        extra_body = {}
        if "qwen3" in self.model_name.lower():
            enable_thinking = os.getenv("DASHSCOPE_ENABLE_THINKING", "false").lower() == "true"
            extra_body["enable_thinking"] = enable_thinking

        self.llm = ChatOpenAI(
            api_key=self.dashscope_api_key,
            model=self.model_name,
            base_url=self.api_base,
            temperature=0.7,
            streaming=True,
            timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
            max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
            # 流式模式下也返回 token 用量，便于统计成本
            stream_usage=True,
            extra_body=extra_body or None
        )
        
        # 绑定工具到 LLM
        self.llm_with_tools = self.llm.bind_tools(restaurant_tools)
        # 工具节点实例：执行前统一补齐用户身份
        self.tool_node = ToolNode(restaurant_tools)
        
        # 构建图
        self.workflow = self._create_workflow()
        # 会话历史持久化到 Redis：服务重启或多实例部署后仍能续上上下文
        self.memory_store = RedisSessionMemory()
        self.app = self.workflow.compile()

    def _create_workflow(self):
        graph = StateGraph(AgentState)
        
        # 添加节点
        graph.add_node("guard", self._input_guard)
        graph.add_node("agent", self.call_model)
        graph.add_node("tools", self._run_tools)
        
        # 设置入口：所有请求先过确定性护栏，再交给模型
        graph.set_entry_point("guard")
        graph.add_conditional_edges(
            "guard",
            self._after_guard,
            {
                "continue": "agent",
                "blocked": END
            }
        )
        
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

    @staticmethod
    def _input_guard(state: AgentState):
        """进入 LLM 前的确定性护栏：空输入/纯标点、越权索要他人订单直接拒答

        这类请求靠提示词约束并不可靠（评测里就出现过越权请求照样调了 query_order），
        所以放在图入口用规则拦掉：既省一次 LLM 调用，也保证行为稳定可测。
        """
        query = ""
        for message in reversed(state.get("messages", [])):
            if isinstance(message, HumanMessage):
                query = message.content if isinstance(message.content, str) else ""
                break

        text = (query or "").strip()
        reply = None
        if not text or MEANINGLESS_QUERY_RE.match(text):
            reply = (
                "抱歉客官，小二没太看明白您的意思～您是想吃点什么，还是想查订单、问配送？"
                "直接跟小二说一声就行。"
            )
        elif any(pattern.search(text) for pattern in PRIVILEGE_QUERY_PATTERNS):
            reply = (
                "抱歉客官，小二只能帮您查您自己的订单，其他客官的订单和账号信息是不能提供、也不会展示的。"
                "您把单号告诉小二，小二马上帮您查。"
            )

        if reply:
            logger.info(f"输入护栏拦截：{text[:30] or '<空输入>'}")
            return {
                "messages": [AIMessage(content=reply)],
                "final_response": reply,
                "menu_ids": [],
                "guard_blocked": True,
            }
        return {"guard_blocked": False}

    @staticmethod
    def _after_guard(state: AgentState):
        """护栏命中则直接结束，否则进入正常的 Agent 流程"""
        return "blocked" if state.get("guard_blocked") else "continue"


    def should_continue(self, state: AgentState):
        """判断是否需要继续调用工具"""
        messages = state['messages']
        last_message = messages[-1]
        # 如果最后一条消息包含工具调用，则继续
        if last_message.tool_calls:
            return "continue"
        return "end"

    @staticmethod
    def _inject_user_id(messages: List[BaseMessage], user_id) -> List[BaseMessage]:
        """把当前用户 ID 强制写进 query_order 的参数

        订单查询工具本身接受可选的 user_id，若模型漏传就会退化为不过滤查询。
        这里在工具执行前统一覆盖，保证只能查到自己名下的订单。
        """
        if not user_id or not messages:
            return messages
        last = messages[-1]
        if not getattr(last, "tool_calls", None):
            return messages
        patched = []
        changed = False
        for call in last.tool_calls:
            if call.get("name") == "query_order":
                call = {**call, "args": {**(call.get("args") or {}), "user_id": user_id}}
                changed = True
            patched.append(call)
        if not changed:
            return messages
        return messages[:-1] + [last.model_copy(update={"tool_calls": patched})]

    @staticmethod
    def _patch_delivery_calls(messages: List[BaseMessage], fallback_address: str = None):
        """补齐 check_delivery 的地址参数，并返回本轮出现的地址

        模型在「那配送费多少」这类追问里容易漏传地址（甚至干脆不调工具）。
        工具执行前把上一轮确认过的地址补进参数；同时把本轮明确给出的地址
        回传给调用方，用于更新会话槽位。
        """
        if not messages:
            return messages, None
        last = messages[-1]
        if not getattr(last, "tool_calls", None):
            return messages, None
        patched = []
        seen_address = None
        changed = False
        for call in last.tool_calls:
            if call.get("name") == "check_delivery":
                args = dict(call.get("args") or {})
                address = (args.get("address") or "").strip()
                if address:
                    seen_address = address
                elif fallback_address:
                    args["address"] = fallback_address
                    changed = True
                call = {**call, "args": args}
            patched.append(call)
        if changed:
            messages = messages[:-1] + [last.model_copy(update={"tool_calls": patched})]
        return messages, seen_address

    async def _run_tools(self, state: AgentState, config: RunnableConfig):
        """工具节点：补齐当前用户身份与上一轮已确认的配送地址"""
        user_info = state.get("user_info") or {}
        user_id = user_info.get("id")
        session_id = (config or {}).get("configurable", {}).get("thread_id")
        fallback_address = self.memory_store.get_slot(
            session_id, DELIVERY_ADDRESS_SLOT, user_id=user_id
        )

        messages, address = self._patch_delivery_calls(state["messages"], fallback_address)
        messages = self._inject_user_id(messages, user_id)
        if address:
            # 本轮明确说了地点，更新槽位；下一轮追问直接复用
            self.memory_store.set_slot(session_id, DELIVERY_ADDRESS_SLOT, address, user_id=user_id)
        # 必须透传 config：LangGraph 新版会校验节点运行所需的 config key，
        # 不传会直接抛 "Missing required config key"。
        return await self.tool_node.ainvoke({"messages": messages}, config=config)

    async def call_model(self, state: AgentState, config: RunnableConfig):
        """调用 LLM 决策或生成回复"""
        import time
        start_t = time.time()
        logger.info("LangGraph Node: agent - Start")
        
        user_info = state.get("user_info")
        user_context = ""
        if user_info:
            user_context = f"\n当前登录客官信息：\n- 昵称：{user_info.get('nickname')}\n- 用户ID：{user_info.get('id')}\n请在回复中优先使用昵称称呼客官，并表现出对老顾客的亲切感。"

        # 本会话已确认过的配送地址：追问配送费/时效时可直接复用，不必再问一遍
        session_id = (config or {}).get("configurable", {}).get("thread_id")
        known_address = self.memory_store.get_slot(
            session_id, DELIVERY_ADDRESS_SLOT, user_id=(user_info or {}).get("id")
        )
        address_slot = ""
        if known_address:
            address_slot = (
                f"\n【本会话已知配送地址】：{known_address}"
                f"\n客官本轮未给出新地点时，追问配送费、送达时长或能否送达，"
                f"请直接以这个地址调用 check_delivery，不要重复询问地址。"
            )

        # 系统提示词：定义角色和行为规范
        system_prompt = f"""你是一个亲切专业的智能餐厅助手（小二）。
任务：利用提供的【工具】获取信息，并以热情、地道的口吻回答客官的问题。
{user_context}{address_slot}

准则：
1. **角色定位**：自称“小二”，称呼用户为“客官”。语气亲切，适当使用“好嘞”、“客官您慢看”等表达。
2. **工具使用优先级（极其重要）**：
   - **查订单状态/进度/我的单子**：必须调用 `query_order`。调用时系统会自动处理权限，你只需在需要时传入 user_id 参数（如果已知）。
   - **查配送范围/配送费/送达时长**：客官给出明确地点时调用 `check_delivery(address=地点)`；
     如果客官本轮只追问配送费或时效、且【本会话已知配送地址】存在，直接用该地址调用 `check_delivery`，
     不要再让客官重复提供地址。
   - **寻找菜品/推荐/口味**：使用 `search_menu`。
3. **意图区分示例**：
   - 用户：“我刚才的订单怎么样了” -> **调用 `query_order()`**。
   - 用户：“光谷地铁站能送到吗” -> **调用 `check_delivery(address='光谷地铁站')`**。
   - 用户（上一轮已问过光谷地铁站）：“那配送费多少” -> **直接用已知地址调用 `check_delivery`**，不要反问地址。
4. **多轮对话**：承接上文，不重复啰嗦。
5. **菜品高亮**：当你向客官推荐或提到具体菜品时，请务必直接说出【参考数据】中的完整菜名。
6. **逻辑闭环**：如果获取到的数据为空或失败，如实告知客官并提供备选建议。
7. **无答案拒答**：`search_menu` 返回 `answerable: false` 时，说明菜单中没有相关菜品（例如客官问帝王蟹、松露），必须如实说明"小店暂时没有这道菜"，可推荐相近菜品，绝不能编造菜品或硬推不相关的结果。
8. **权限边界**：只能查询当前登录客官本人的订单。若客官索要他人或"全部用户"的订单、账号密码、手机号等隐私信息，
   必须明确拒绝，且不要调用 `query_order`，更不能编造数据。
"""
        
        # 针对 Tool Calling 注入 user_id
        # 如果有工具调用，我们手动为 query_order 注入 user_id
        # 这里通过 prompt 告诉模型 user_id，或者在 tools 节点处理
        # 简单起见，在 system_prompt 中明确告知 user_id
        if user_info:
            system_prompt += f"\n重要：调用 query_order 时，请务必传入参数 user_id={user_info.get('id')}。"

        # 构建消息列表
        messages = [SystemMessage(content=system_prompt)] + state['messages']
        
        # 调用 LLM（带工具绑定）
        response = await self.llm_with_tools.ainvoke(messages, config=config)
        
        # 提取推荐的菜品 ID
        recommended_ids = []
        if not response.tool_calls:
            for msg in state['messages']:
                if isinstance(msg, ToolMessage) and "search_menu" in msg.name:
                    try:
                        data = json.loads(msg.content)
                        if data.get("success") and data.get("contents"):
                            for i, content in enumerate(data["contents"]):
                                name_match = re.search(r"菜品名称:([^|]+)", content)
                                if name_match:
                                    dish_name = name_match.group(1).strip()
                                    if dish_name in response.content:
                                        if i < len(data.get("menu_ids", [])):
                                            recommended_ids.append(str(data["menu_ids"][i]))
                    except (json.JSONDecodeError, TypeError, AttributeError, IndexError) as parse_err:
                        logger.debug(f"解析工具返回的菜品信息失败: {parse_err}")
                        continue
        
        logger.info(f"LangGraph Node: agent - Done, duration: {time.time()-start_t:.4f}s")
        usage = getattr(response, "usage_metadata", None) or {}
        if usage:
            record_usage(self.model_name, usage)
            logger.info(
                "LLM token usage: model=%s prompt=%s completion=%s total=%s",
                self.model_name,
                usage.get("input_tokens"),
                usage.get("output_tokens"),
                usage.get("total_tokens"),
            )
        return {
            "messages": [response],
            "menu_ids": recommended_ids,
            "final_response": response.content if not response.tool_calls else "",
            "user_info": user_info
        }

    async def astream_run(self, user_query: str, session_id: str = "default", user_info: Dict[str, Any] = None):
        """异步流式运行"""
        config = {"configurable": {"thread_id": session_id}}
        user_id = (user_info or {}).get("id")

        # 先取出该会话的持久化历史，再拼上本轮提问
        history = self.memory_store.load(session_id, user_id=user_id)
        initial_state = {
            "messages": history + [HumanMessage(content=user_query)],
            "user_info": user_info
        }
        
        logger.info(f"LangGraph (Tool-Calling) astream_run start: {user_query} (user: {user_info.get('username') if user_info else 'Guest'}, history: {len(history)})")
        answer_parts: List[str] = []
        usage_totals = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        final_state: Dict[str, Any] = {}
        
        try:
            async for event in self.app.astream_events(
                initial_state,
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
                            answer_parts.append(content)
                            yield {"type": "content", "content": content}
                
                # 2. 汇总 token 用量（流式也要能统计成本），结束时随 SSE 一起下发
                elif kind == "on_chat_model_end":
                    output = event["data"].get("output")
                    usage = getattr(output, "usage_metadata", None) or {}
                    if usage:
                        usage_totals["calls"] += 1
                        usage_totals["input_tokens"] += int(usage.get("input_tokens") or 0)
                        usage_totals["output_tokens"] += int(usage.get("output_tokens") or 0)
                        usage_totals["total_tokens"] += int(usage.get("total_tokens") or 0)

                # 3. 处理元数据 (菜品 ID)
                elif kind == "on_chain_end":
                    if event["name"] == "LangGraph":
                        try:
                            output = event["data"].get("output")
                            if isinstance(output, dict):
                                final_state = output
                                menu_ids = output.get("menu_ids") or []
                                if menu_ids:
                                    logger.info(f"LangGraph astream_run - Yielding menu_ids: {menu_ids}")
                                    yield {"type": "metadata", "menu_ids": menu_ids}
                        except Exception as meta_e:
                            logger.error(f"Error extracting metadata: {meta_e}")
                                
        except Exception as e:
            logger.error(f"LangGraph astream_run error: {e}")
            yield {"type": "error", "content": f"小二忙不过来了，请客官稍后再试。(错误详情: {str(e)})"}
        else:
            # 被输入护栏拦截时没有 LLM 流式输出，这里补发整段回复，避免前端空白
            if not answer_parts and final_state.get("guard_blocked"):
                guarded_reply = final_state.get("final_response") or ""
                if guarded_reply:
                    answer_parts.append(guarded_reply)
                    yield {"type": "content", "content": guarded_reply}
            if usage_totals["total_tokens"]:
                # 成本已在 _call_llm 里按模型调用逐笔记账，这里只把汇总下发给前端
                yield {"type": "usage", "usage": usage_totals}
            # 只有完整跑完的轮次才写入记忆，避免把半截回复固化
            self.memory_store.append_turn(session_id, user_query, "".join(answer_parts), user_id=user_id)

    async def run(self, user_query: str, session_id: str = "default", user_info: Dict[str, Any] = None):
        """同步运行流程"""
        config = {"configurable": {"thread_id": session_id}}
        user_id = (user_info or {}).get("id")

        history = self.memory_store.load(session_id, user_id=user_id)
        initial_state = {
            "messages": history + [HumanMessage(content=user_query)],
            "user_info": user_info
        }
        
        final_state = await self.app.ainvoke(
            initial_state, 
            config=config
        )
        
        response = final_state.get("final_response", "客官久等了，小二没能听清您的需求。")
        self.memory_store.append_turn(session_id, user_query, response, user_id=user_id)
        return {
            "response": response,
            "menu_ids": final_state.get("menu_ids", [])
        }

restaurant_agent = SmartRestaurantGraph()
