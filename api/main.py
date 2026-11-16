"""智能点餐助手 FastAPI 入口。

职责收窄为「应用装配」：创建 app、注册中间件、启动检查/预热、挂载各业务路由。
具体接口实现见 ``api/routers/``，请求/响应模型见 ``api/schemas.py``，
登录鉴权与限流等公共依赖见 ``api/deps.py``。
"""
import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api import deps
from tools.observability import new_trace_id, set_trace_id, setup_logging

setup_logging()
logger = logging.getLogger(__name__)

# 跨域白名单：前后端分开部署时按需配置（逗号分隔）；留空表示只允许同源
CORS_ALLOW_ORIGINS = [o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()]
# 单次请求体上限，避免超大 JSON 占内存
MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", str(1024 * 1024)))


def _startup_checks():
    """启动时只做连接与就绪检查，向量入库属于离线初始化（setup_db.py）"""
    from tools.db_tool import init_db

    init_db()

    # 放到启动阶段会多 worker 重复执行并互相争抢 Chroma 写锁，这里只检查可用性。
    try:
        from tools.chroma_tool import chroma_db

        if not chroma_db.collection and not chroma_db.initialize_connection():
            logger.error("向量数据库连接初始化失败，请检查 chroma_db 目录")
        elif chroma_db.collection and chroma_db.collection.count() == 0:
            logger.warning("向量数据库为空，请先执行 python setup_db.py 完成菜品向量入库")
        else:
            logger.info("向量数据库已就绪")
    except Exception as e:
        logger.error(f"启动时检查向量数据库失败: {e}")

    _warmup_rerank_in_background()
    _warmup_agent_in_background()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _startup_checks()
    yield


def _warmup_agent_in_background():
    """首个对话请求还要承担 LangChain / LangGraph 的模块导入与 Agent 构建（实测 ~17s）

    接口里保留懒导入，这里在后台线程提前触发一次，避免第一位用户被卡住。
    """

    def _warmup():
        try:
            started = time.perf_counter()
            from agent.assistant import chat_with_assistant_stream  # noqa: F401

            logger.info("Agent 模块预热完成，耗时 %.1fs", time.perf_counter() - started)
        except Exception as exc:
            logger.warning("Agent 模块预热失败，将在首次对话时重试: %s", exc)

    threading.Thread(target=_warmup, name="agent-warmup", daemon=True).start()


def _warmup_rerank_in_background():
    """本地 Rerank 权重加载需要十几秒到一分钟，放后台线程预热

    否则第一个检索请求要自己承担冷启动，实测首次对话被拖慢 100s+。
    云端 Rerank 是一次 HTTP 调用，不需要预热。
    """
    if os.getenv("RERANK_PROVIDER", "dashscope").strip().lower() != "local":
        return
    if os.getenv("USE_RERANK", "true").lower() != "true":
        return

    def _warmup():
        try:
            from tools.local_reranker import get_local_reranker

            get_local_reranker().warmup()
        except Exception as exc:
            logger.warning("本地 Rerank 预热异常: %s", exc)

    threading.Thread(target=_warmup, name="rerank-warmup", daemon=True).start()


app = FastAPI(
    title="智能点餐助手的API接口",
    description="智能点餐应用主要暴露智能对话、配送查询、菜品列表等接口",
    lifespan=lifespan,
)


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    """把一次请求内所有日志串到同一个 trace_id 上

    优先复用上游传入的 X-Trace-ID（便于和网关/前端日志对齐），没有就新建，
    并回写到响应头，出问题时可以直接拿这个 ID 去搜日志。
    """
    trace_id = request.headers.get("X-Trace-ID") or new_trace_id()
    set_trace_id(trace_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = (time.perf_counter() - started) * 1000
        logger.exception(
            "http_request 失败 path=%s duration_ms=%.1f error=%s",
            request.url.path,
            duration_ms,
            exc,
        )
        raise
    duration_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Trace-ID"] = trace_id
    logger.info(
        "http_request method=%s path=%s status=%s duration_ms=%.1f",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


if CORS_ALLOW_ORIGINS:
    # 默认只允许同源（前端经 Vite/Nginx 代理访问）；分域部署时由 CORS_ALLOW_ORIGINS 显式放行
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ALLOW_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """基础安全响应头 + 请求体大小限制

    API 不输出 HTML，但仍拒绝被 iframe 嵌套与内容类型嗅探；
    体积上限先于路由/鉴权判断，避免超大请求占用内存。
    """
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_REQUEST_BODY_BYTES:
        return JSONResponse(status_code=413, content={"detail": "请求体过大"})
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


@app.get("/")
def hello_world():
    """测试项目根路径访问是否可用"""
    return {"hello": "world"}


@app.get("/healthy")
def healthy():
    """测试项目请求路径访问是否可用"""
    return {"message": "请求路径访问健康"}


from api.routers import auth, chat, delivery, menu, metrics, order  # noqa: E402

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(menu.router)
app.include_router(delivery.router)
app.include_router(order.router)
app.include_router(metrics.router)

# 兼容旧引用：测试与外部代码可从 api.main 取到公共依赖
get_current_user = deps.get_current_user
get_optional_user = deps.get_optional_user
get_chat_user = deps.get_chat_user
send_notification = deps.send_notification

__all__ = [
    "app",
    "get_current_user",
    "get_optional_user",
    "get_chat_user",
    "send_notification",
]
