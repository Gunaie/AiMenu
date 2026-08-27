"""
智能点餐助手主程序 - 已升级为 LangGraph + Memory + Hybrid Search
"""
import logging
from agent.langgraph_agent import restaurant_agent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def chat_with_assistant_stream(user_query: str, session_id: str = "default"):
    """
    流式对话接口
    """
    try:
        logger.info(f"开始流式对话: query='{user_query}', session='{session_id}'")
        count = 0
        async for chunk in restaurant_agent.astream_run(user_query, thread_id=session_id):
            if chunk.get("type") == "content":
                count += 1
            yield chunk
        logger.info(f"流式对话结束，共发送 {count} 个内容数据块")
    except Exception as e:
        logger.error(f"流式 Agent 执行异常: {e}")
        yield {"type": "error", "content": str(e)}

async def chat_with_assistant(user_query: str, session_id: str = "default"):
    """
    和智能小助手对话 (已升级为 LangGraph 架构)
    
    Args:
        user_query: 用户输入的问题
        session_id: 会话ID，用于实现多轮对话记忆
        
    Returns:
        Union[str, dict]: 助手回复内容或包含推荐信息的字典
    """
    try:
        logger.info(f"开始对话: query='{user_query}', session='{session_id}'")
        
        # 调用升级后的 LangGraph Agent
        # 它内部会自动处理意图分析、工具执行以及对话历史（Memory）
        result = await restaurant_agent.run(user_query, thread_id=session_id)
        
        # 保持与旧版 API 的兼容性
        # 如果 Agent 返回了具体的菜品推荐 ID，则组装成旧版的字典格式
        if result.get("menu_ids"):
            return {
                "recommendation": result["response"],
                "menu_ids": result["menu_ids"]
            }
        
        # 否则直接返回回复字符串
        return result["response"]
        
    except Exception as e:
        logger.error(f"Agent 执行异常: {e}")
        raise Exception(f"服务内部出现故障，暂不可用：{str(e)}")

if __name__ == '__main__':
    # 简单的本地测试
    print(chat_with_assistant("你们餐厅在哪里？"))
    print(chat_with_assistant("推荐一些川菜"))
    print(chat_with_assistant("那再推荐几个更辣一点的")) # 测试多轮对话
