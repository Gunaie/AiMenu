"""智能对话路由（普通与 SSE 流式）。"""
from fastapi import APIRouter, Depends, HTTPException

from api import deps
from api.schemas import ChatRequest, ChatResponse

router = APIRouter(tags=["对话"])


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, current_user: dict = Depends(deps.get_chat_user)):
    """智能对话接口 (强制登录)"""
    try:
        from service.diancan_service import smart_chat

        result = await smart_chat(request.query, request.session_id, user_info=current_user)

        if isinstance(result, dict) and "recommendation" in result and "menu_ids" in result:
            return ChatResponse(
                success=True,
                query=request.query,
                recommendation=result["recommendation"],
                menu_ids=result["menu_ids"],
            )
        return ChatResponse(success=True, query=request.query, response=str(result))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"智能对话服务失败: {str(e)}")


@router.post("/chat/stream")
async def chat_stream_endpoint(request: ChatRequest, current_user: dict = Depends(deps.get_chat_user)):
    """智能对话接口 (流式输出，强制登录)"""
    import json

    from fastapi.responses import StreamingResponse

    from agent.assistant import chat_with_assistant_stream

    async def event_generator():
        async for chunk in chat_with_assistant_stream(
            request.query, request.session_id, user_info=current_user
        ):
            data = json.dumps(chunk, ensure_ascii=False)
            yield f"data: {data}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
