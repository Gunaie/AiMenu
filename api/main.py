"""
智能点餐助手主程序 FastAPI 接口
1.定义FastAPI应用实例
2.提供三个主要接口：
2.1 POST /chat - 智能对话接口
2.2 POST /delivery - 配送查询接口
2.3 GET /menu/list - 菜品列表接口
"""
from  fastapi import  FastAPI, HTTPException, BackgroundTasks, Depends, Header, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from typing import List,Optional, Dict, Any
from tools.amap_tool import PathInputModel
from tools.auth_tool import (
    get_password_hash, verify_password, create_access_token, decode_access_token
)
from tools.db_tool import get_user_by_username, create_user
from tools.redis_tool import cache
from tools.observability import new_trace_id, set_trace_id, setup_logging, usage_snapshot
import  logging
import  os
import  threading
import  time

setup_logging()
logger = logging.getLogger(__name__)

# OAuth2 配置
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

async def get_current_user(token: str = Depends(oauth2_scheme)):
    """获取当前登录用户的依赖项 (强制登录)

    这里刻意保持 async：真正的数据库查询放到线程池执行，避免阻塞事件循环。
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="登录已过期，请重新登录",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception
    username: str = payload.get("sub")
    if username is None:
        raise credentials_exception
    user = await run_in_threadpool(get_user_by_username, username)
    if user is None:
        raise credentials_exception
    return user

async def get_optional_user(token: str = Depends(oauth2_scheme)) -> Optional[dict]:
    """获取当前登录用户的依赖项 (可选登录)"""
    try:
        payload = decode_access_token(token)
        if payload is None:
            return None
        username: str = payload.get("sub")
        if username is None:
            return None
        return await run_in_threadpool(get_user_by_username, username)
    except Exception as exc:
        logger.debug(f"可选登录态解析失败: {exc}")
        return None

# 对话接口限流：每个用户每分钟最多 N 次，0 表示关闭
CHAT_RATE_LIMIT_PER_MINUTE = int(os.getenv("CHAT_RATE_LIMIT_PER_MINUTE", "20"))
# 登录限流：同一「用户名 + 客户端 IP」每分钟最多尝试次数，防撞库/爆破；0 表示关闭
LOGIN_RATE_LIMIT_PER_MINUTE = int(os.getenv("LOGIN_RATE_LIMIT_PER_MINUTE", "20"))
# 跨域白名单：前后端分开部署时按需配置（逗号分隔）；留空表示只允许同源
CORS_ALLOW_ORIGINS = [o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()]
# 单次请求体上限，避免超大 JSON 占内存
MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", str(1024 * 1024)))


async def get_chat_user(current_user: dict = Depends(get_current_user)) -> dict:
    """对话接口限流依赖

    按「用户 + 分钟」在 Redis 计数，避免单个账号刷爆 LLM 额度；
    Redis 不可用时放行，不阻断正常业务。
    """
    if CHAT_RATE_LIMIT_PER_MINUTE <= 0:
        return current_user
    window = int(time.time() // 60)
    key = f"aimenu:ratelimit:chat:{current_user['id']}:{window}"
    count = cache.incr_with_ttl(key, expire=70)
    if count is not None and count > CHAT_RATE_LIMIT_PER_MINUTE:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="客官问得有点急，请稍等片刻再试",
            headers={"Retry-After": "60"},
        )
    return current_user

def send_notification(order_no: str):
    """模拟异步发送订单通知（如邮件或微信推送）"""
    logger.info(f"开始为订单 {order_no} 准备通知推送...")
    time.sleep(3)  # 模拟耗时操作
    logger.info(f"订单 {order_no} 通知推送成功！")

app=FastAPI(title="智能点餐助手的API接口",description="智能点餐应用主要暴露三个接口分别为智能对话接口、配送查询接口、菜品列表接口")


@app.middleware("http")
async def trace_middleware(request, call_next):
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
        logger.exception("http_request 失败 path=%s duration_ms=%.1f error=%s",
                         request.url.path, duration_ms, exc)
        raise
    duration_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Trace-ID"] = trace_id
    logger.info("http_request method=%s path=%s status=%s duration_ms=%.1f",
                request.method, request.url.path, response.status_code, duration_ms)
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
async def security_middleware(request, call_next):
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


@app.on_event("startup")
async def startup_event():
    from tools.db_tool import init_db
    init_db()

    # 只做连接与就绪检查：向量入库属于离线初始化（setup_db.py），
    # 放到启动阶段会在多 worker 下重复执行并互相争抢 Chroma 写锁。
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



@app.get("/")
def  hello_world():
    """测试项目根路径访问是否可用"""
    return {"hello":"world"}

@app.get("/healthy")
def  healthy():
    """测试项目请求路径访问是否可用"""
    return {"message":"请求路径访问健康"}


# 定义请求数据模型
# 定义配送范围查询的请求模型
class DeliveryRequest(BaseModel):
    """配送范围查询请求"""
    address: str = Field(..., min_length=1, max_length=120)
    travel_mode: PathInputModel = "2"  # 1=步行, 2=骑电动车, 3=驾车


# 定义对话的请求模型
class ChatRequest(BaseModel):
    """智能对话请求（限制长度，避免超长输入把 LLM 成本与延迟放大）"""
    query: str = Field(..., max_length=500)
    session_id: Optional[str] = Field(default="default", max_length=64)





# 响应数据模型
# 定义配送范围查询响应模型
class DeliveryResponse(BaseModel):
    """配送查询响应"""
    success: bool  # 成功(True) or 失败的标识（False）
    in_range: bool #  配送是否在配送范围内(True False)
    distance: float # 配送距离(公里 km)
    formatted_address: str # 格式化地址
    duration:float # 配送时间（秒）
    delivery_fee: Optional[float] = 0.0 # 配送费
    message: str  # (前端要展示的配送完整消息内容)
    travel_mode: PathInputModel # 配送模式 (1:步行 2:骑电动车 3:驾车)
    input_address: str # 输入原始内容

# 定义对话响应模型
class ChatResponse(BaseModel):
    """智能对话响应"""
    success: bool # 成功失败表示
    query: str # 原始查询内容
    response: Optional[str] = None # 响应内容
    recommendation: Optional[str] = None # 推荐内容
    menu_ids: Optional[List[str]] = None # 推荐的菜品id

# 定义菜品列表响应模型
class MenuListResponse(BaseModel):
    """菜品列表响应"""
    success: bool
    menu_items: List[dict] # 菜品列表
    count: int # 菜品数
    message: str # 响应消息提示

# 定义餐厅信息响应模型
class RestaurantInfoResponse(BaseModel):
    """餐厅信息响应"""
    success: bool
    name: str
    address: str
    longitude: float
    latitude: float
    delivery_radius: float

# 定义订单项模型
class OrderItem(BaseModel):
    id: int = Field(..., ge=1)
    dish_name: str = Field(..., max_length=100)
    price: float = Field(..., ge=0)
    quantity: int = Field(..., ge=1, le=99)

# 定义提交订单请求模型
# total_amount / delivery_fee / actual_amount 仅用于展示，服务端会依据数据库价格与配送距离重新计算
class OrderSubmitRequest(BaseModel):
    total_amount: Optional[float] = None
    delivery_fee: Optional[float] = None
    actual_amount: Optional[float] = None
    delivery_address: str = Field(..., min_length=1, max_length=200)
    travel_mode: PathInputModel = "2"
    items_detail: List[OrderItem] = Field(..., min_length=1, max_length=50)

# 定义通用操作响应模型
class ActionResponse(BaseModel):
    success: bool
    message: str
    order_no: Optional[str] = None

# ====================== 认证相关接口 ======================

class UserRegister(BaseModel):
    username: str = Field(..., pattern=r"^[A-Za-z0-9_]{3,32}$")
    password: str = Field(..., min_length=8, max_length=72)
    nickname: Optional[str] = Field(default=None, max_length=32)
    phone: Optional[str] = Field(default=None, max_length=20)

class Token(BaseModel):
    access_token: str
    token_type: str
    username: str
    nickname: str

_DUMMY_PASSWORD_HASH = get_password_hash("aimenu-dummy-password-for-timing")


def _authenticate(username: str, password: str) -> Optional[dict]:
    """账号密码校验（bcrypt 校验是 CPU 密集操作，放到线程池执行）"""
    user = get_user_by_username(username)
    if not user:
        # 用户不存在也跑一次 bcrypt，抹平「账号是否存在」的响应耗时差异
        verify_password(password, _DUMMY_PASSWORD_HASH)
        return None
    if not verify_password(password, user['password_hash']):
        return None
    return user


@app.post("/auth/register", response_model=ActionResponse)
async def register(user_in: UserRegister):
    """用户注册"""
    existing_user = await run_in_threadpool(get_user_by_username, user_in.username)
    if existing_user:
        return ActionResponse(success=False, message="用户名已存在")
    
    user_data = {
        "username": user_in.username,
        "password_hash": await run_in_threadpool(get_password_hash, user_in.password),
        "nickname": user_in.nickname or user_in.username,
        "phone": user_in.phone
    }
    
    if await run_in_threadpool(create_user, user_data):
        return ActionResponse(success=True, message="注册成功，请登录")
    return ActionResponse(success=False, message="注册失败，请稍后再试")

@app.post("/auth/login", response_model=Token)
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    """用户登录（按「用户名 + IP」限流，防撞库与暴力破解）"""
    if LOGIN_RATE_LIMIT_PER_MINUTE > 0:
        window = int(time.time() // 60)
        client_ip = request.client.host if request.client else "unknown"
        key = f"aimenu:ratelimit:login:{form_data.username}:{client_ip}:{window}"
        count = cache.incr_with_ttl(key, expire=70)
        if count is not None and count > LOGIN_RATE_LIMIT_PER_MINUTE:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="登录尝试过于频繁，请稍后再试",
                headers={"Retry-After": "60"},
            )

    user = await run_in_threadpool(_authenticate, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = create_access_token(data={"sub": user['username']})
    return {
        "access_token": access_token, 
        "token_type": "bearer",
        "username": user['username'],
        "nickname": user['nickname'] or user['username']
    }

@app.get("/metrics/cost")
async def cost_metrics(current_user: dict = Depends(get_current_user)):
    """LLM 成本面板：进程内累计的调用次数与 token 用量

    只统计当前 worker 进程，多 worker 部署时需要用 /metrics 聚合，
    或在网关侧汇总；单价由 COST_INPUT_PER_1K / COST_OUTPUT_PER_1K 配置，
    未配置时不估算金额（返回 null），避免用假价格糊弄。
    """
    return {"success": True, "usage": usage_snapshot()}


@app.get("/auth/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    """获取当前用户信息"""
    return {
        "success": True,
        "username": current_user['username'],
        "nickname": current_user['nickname'],
        "phone": current_user['phone']
    }

# ====================== 业务逻辑接口 ======================

@app.get("/delivery/tips")
def delivery_tips_endpoint(
    keywords: str = Query(..., min_length=1, max_length=50),
    current_user: dict = Depends(get_current_user)
):
    """获取地址输入建议 (需要登录)；同步 HTTP 调用，交给 FastAPI 线程池执行"""
    from tools.amap_tool import get_input_tips
    return get_input_tips(keywords)

@app.get("/restaurant/info", response_model=RestaurantInfoResponse)
async def restaurant_info_endpoint(current_user: dict = Depends(get_current_user)):
    """获取餐厅基本信息 (需要登录)"""
    from tools.amap_tool import config
    return RestaurantInfoResponse(
        success=True,
        name="智能餐厅",
        address=config.RESTAURANT_ADDRESS,
        longitude=float(config.MERCHANT_LONGITUDE),
        latitude=float(config.MERCHANT_LATITUDE),
        delivery_radius=float(config.DELIVERY_RADIUS)
    )

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, current_user: dict = Depends(get_chat_user)):
    """
    智能对话接口 (强制登录)
    """
    try:
        from  service.diancan_service import smart_chat
        
        # 调用智能对话服务，传入用户信息
        result = await smart_chat(request.query, request.session_id, user_info=current_user)

        # 处理不同类型的返回值
        if isinstance(result, dict) and "recommendation" in result and "menu_ids" in result:
            # 菜品推荐返回
            return ChatResponse(
                success=True,
                query=request.query,
                recommendation=result["recommendation"],  #菜品推荐的原因
                menu_ids=result["menu_ids"]
            )
        else:
            # 普通文本回复
            return ChatResponse(
                success=True,
                query=request.query,
                response=str(result)
            )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"智能对话服务失败: {str(e)}"
        )

@app.post("/chat/stream")
async def chat_stream_endpoint(request: ChatRequest, current_user: dict = Depends(get_chat_user)):
    """
    智能对话接口 (流式输出，强制登录)
    """
    from agent.assistant import chat_with_assistant_stream
    from fastapi.responses import StreamingResponse
    import json

    async def event_generator():
        async for chunk in chat_with_assistant_stream(request.query, request.session_id, user_info=current_user):
            # 以 Server-Sent Events (SSE) 格式发送
            data = json.dumps(chunk, ensure_ascii=False)
            yield f"data: {data}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/menu/list", response_model=MenuListResponse)
def menu_list_endpoint(current_user: dict = Depends(get_current_user)):
    """菜品列表区域展示 (需要登录)；DB/缓存为同步调用，交给线程池执行"""

    # 1.调用service
    from  service.diancan_service import get_menu

    # 2.调用方法
    menu_items=get_menu()


    # 3.封装结果返回
    if  not  menu_items:
        return MenuListResponse(
            success=False,
            menu_items=[],
            count=0,
            message="暂无菜品列表可用"
        )

    return MenuListResponse(
        success=True,
        menu_items=menu_items,
        count=len(menu_items),
        message=f"成功查询到{len(menu_items)}道菜品信息"
    )


@app.post("/delivery", response_model=DeliveryResponse)
def delivery_endpoint(request: DeliveryRequest, current_user: dict = Depends(get_current_user)):
    """
    配送查询接口 (需要登录)；高德接口为同步 requests 调用，交给线程池执行
    """

    # 1.导入service
    try:
        from  service.diancan_service import  check_delivery_range

        # 2.调用
        check_delivery_range_response=check_delivery_range(request.address, request.travel_mode)

        if  check_delivery_range_response['status']=="fail":

            return DeliveryResponse(

                success=False,
                in_range=False,
                distance=0.0,
                formatted_address=request.address,
                duration=0.0,
                message=check_delivery_range_response['message'],
                travel_mode=request.travel_mode,
                input_address=request.address

            )

        return DeliveryResponse(
            success=True,
            in_range=check_delivery_range_response['in_range'],
            distance=check_delivery_range_response['distance'],
            formatted_address=check_delivery_range_response['formatted_address'],
            duration=check_delivery_range_response['duration'],
            delivery_fee=check_delivery_range_response.get('delivery_fee', 0.0),
            message=check_delivery_range_response['message'],
            travel_mode=request.travel_mode,
            input_address=request.address
        )
    except Exception as e:
        logger.error(f"配送范围查询失败；{e}")
        # 必须回填全部必填字段，否则响应模型校验失败会变成 500
        return DeliveryResponse(
            success=False,
            in_range=False,
            distance=0.0,
            formatted_address=request.address,
            duration=0.0,
            delivery_fee=0.0,
            message=f"配送范围查询失败：{e}",
            travel_mode=request.travel_mode,
            input_address=request.address
        )


@app.post("/order/submit", response_model=ActionResponse)
def order_submit_endpoint(
    request: OrderSubmitRequest, 
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key", max_length=64)
):
    """提交订单接口 (需要登录)；DB 与高德调用均为同步，交给线程池执行

    客户端可带 Idempotency-Key 请求头：重复提交（重试/双击）会返回同一笔订单，
    不会重复扣库存。
    """
    from service.diancan_service import submit_order
    order_data = request.model_dump()
    order_data['user_id'] = current_user['id']  # 绑定当前用户
    order_data['idempotency_key'] = idempotency_key

    result = submit_order(order_data)
    
    # 添加后台任务：发送通知
    if result.get("success") and result.get("order_no"):
        background_tasks.add_task(send_notification, result["order_no"])
        
    return ActionResponse(**result)


@app.post("/order/pay/{order_no}", response_model=ActionResponse)
def order_pay_endpoint(order_no: str, current_user: dict = Depends(get_current_user)):
    """模拟支付接口 (需要登录，且只能支付自己的订单)"""
    from service.diancan_service import pay_order
    result = pay_order(order_no, user_id=current_user['id'])
    if result.get("forbidden"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=result["message"])
    if result.get("conflict"):
        # 已取消/已完成的订单不能再支付，状态机拒绝非法跃迁
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result["message"])
    return ActionResponse(**{k: v for k, v in result.items() if k != "forbidden"})


@app.get("/order/list")
def order_list_endpoint(limit: int = Query(10, ge=1, le=100), current_user: dict = Depends(get_current_user)):
    """获取当前用户的订单历史列表"""
    from tools.db_tool import get_all_orders
    orders = get_all_orders(user_id=current_user['id'], limit=limit)
    return {"success": True, "orders": orders}




