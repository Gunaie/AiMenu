"""Agent 会话记忆持久化。

原实现使用 LangGraph 的 MemorySaver，会话只存在于进程内存：服务重启后上下文丢失，
多实例部署时同一会话落到不同进程也读不到历史。这里把「用户提问 + 最终回复」写入 Redis，
每次调用先按 session_id 读取历史再拼装 messages，从而实现跨重启、跨实例的多轮记忆。

只持久化 Human/AI 的纯文本消息：工具调用消息带有 tool_call_id，脱离当次调用链后
重新注入会破坏工具调用协议，因此不入库（工具结果本身也会拼进最终回复）。
"""
import logging
import os
from typing import List

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from tools.redis_tool import cache

logger = logging.getLogger(__name__)

KEY_PREFIX = "aimenu:agent:history:"


class RedisSessionMemory:
    """基于 Redis 的多轮对话记忆"""

    def __init__(self, ttl: int = None, max_turns: int = None):
        self.ttl = ttl if ttl is not None else int(os.getenv("AGENT_MEMORY_TTL", "86400"))
        self.max_turns = max_turns if max_turns is not None else int(os.getenv("AGENT_MEMORY_TURNS", "6"))

    @staticmethod
    def _key(session_id: str, user_id: int = None) -> str:
        """会话 key 必须带上用户维度

        session_id 由客户端生成并传入，如果只按它分键，任何登录用户拿到他人的
        session_id 就能把自己接进对方的历史。这里统一按 user_id 隔离。
        """
        owner = str(user_id) if user_id is not None else "anonymous"
        return f"{KEY_PREFIX}{owner}:{session_id}"

    def load(self, session_id: str, user_id: int = None) -> List[BaseMessage]:
        """读取会话历史并还原为 LangChain 消息"""
        records = cache.get(self._key(session_id, user_id)) or []
        messages: List[BaseMessage] = []
        for record in records:
            content = (record or {}).get("content")
            if not content:
                continue
            if record.get("role") == "human":
                messages.append(HumanMessage(content=content))
            elif record.get("role") == "ai":
                messages.append(AIMessage(content=content))
        return messages

    def append_turn(self, session_id: str, user_query: str, answer: str,
                    user_id: int = None) -> None:
        """追加一轮问答，并按窗口截断旧历史（TTL 每轮续期）"""
        if not user_query or not answer:
            return
        records = cache.get(self._key(session_id, user_id)) or []
        records.append({"role": "human", "content": user_query})
        records.append({"role": "ai", "content": answer})
        records = records[-self.max_turns * 2:]
        cache.set(self._key(session_id, user_id), records, expire=self.ttl)

    def clear(self, session_id: str, user_id: int = None) -> None:
        """清空指定会话历史（供“重新开始”之类操作使用）"""
        cache.delete(self._key(session_id, user_id))
