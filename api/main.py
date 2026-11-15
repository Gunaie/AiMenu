"""
智能点餐助手主程序 FastAPI 接口
1.定义FastAPI应用实例
2.提供三个主要接口：
2.1 POST /chat - 智能对话接口
2.2 POST /delivery - 配送查询接口
2.3 GET /menu/list - 菜品列表接口
"""
from  fastapi import  FastAPI, HTTPException, BackgroundTasks, Depends, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from typing import List,Optional, Dict, Any
from tools.amap_tool import PathInputModel
from tools.auth_tool import (
    get_password_hash, verify_password, create_access_token, decode_access_token
)
from tools.db_tool import get_user_by_username, create_user
import  logging
import  time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# OAuth2 配置
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

async def get_current_user(token: str = Depends(oauth2_scheme)):
    """获取当前登录用户的依赖项 (强制登录)"""
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
    user = get_user_by_username(username)
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
        return get_user_by_username(username)
    except:
        return None

def send_notification(order_no: str):
    """模拟异步发送订单通知（如邮件或微信推送）"""
    logger.info(f"开始为订单 {order_no} 准备通知推送...")
    time.sleep(3)  # 模拟耗时操作
    logger.info(f"订单 {order_no} 通知推送成功！")

app=FastAPI(title="智能点餐助手的API接口",description="智能点餐应用主要暴露三个接口分别为智能对话接口、配送查询接口、菜品列表接口")

@app.on_event("startup")
async def startup_event():
    from tools.db_tool import init_db
    init_db()
    
    # 同步向量数据库
    try:
        from tools.chroma_tool import chroma_db
        # 如果集合为空，则进行初始化同步
        if not chroma_db.collection:
            chroma_db.initialize_connection()
        
        if chroma_db.collection:
            # 检查是否需要同步数据 (如果 ids 为空)
            results = chroma_db.collection.get(limit=1)
            if not results['ids']:
                logger.info("向量数据库为空，开始初始化同步...")
                chroma_db.upsert_menu_data(clear_existing=True)
            else:
                logger.info("向量数据库已就绪")
        else:
            logger.error("向量数据库连接初始化失败，跳过同步")
    except Exception as e:
        logger.error(f"启动时同步向量数据库失败: {e}")



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
    address: str
    travel_mode: PathInputModel = "2"  # 1=步行, 2=骑电动车, 3=驾车


# 定义对话的请求模型
class ChatRequest(BaseModel):
    """智能对话请求"""
    query: str
    session_id: Optional[str] = "default"





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
    id: int
    dish_name: str
    price: float
    quantity: int

# 定义提交订单请求模型
# total_amount / delivery_fee / actual_amount 仅用于展示，服务端会依据数据库价格与配送距离重新计算
class OrderSubmitRequest(BaseModel):
    total_amount: Optional[float] = None
    delivery_fee: Optional[float] = None
    actual_amount: Optional[float] = None
    delivery_address: str
    travel_mode: PathInputModel = "2"
    items_detail: List[OrderItem]

# 定义通用操作响应模型
class ActionResponse(BaseModel):
    success: bool
    message: str
    order_no: Optional[str] = None

# ====================== 认证相关接口 ======================

class UserRegister(BaseModel):
    username: str
    password: str
    nickname: Optional[str] = None
    phone: Optional[str] = None

class Token(BaseModel):
    access_token: str
    token_type: str
    username: str
    nickname: str

@app.post("/auth/register", response_model=ActionResponse)
async def register(user_in: UserRegister):
    """用户注册"""
    existing_user = get_user_by_username(user_in.username)
    if existing_user:
        return ActionResponse(success=False, message="用户名已存在")
    
    user_data = {
        "username": user_in.username,
        "password_hash": get_password_hash(user_in.password),
        "nickname": user_in.nickname or user_in.username,
        "phone": user_in.phone
    }
    
    if create_user(user_data):
        return ActionResponse(success=True, message="注册成功，请登录")
    return ActionResponse(success=False, message="注册失败，请稍后再试")

@app.post("/auth/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """用户登录"""
    user = get_user_by_username(form_data.username)
    if not user or not verify_password(form_data.password, user['password_hash']):
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
async def delivery_tips_endpoint(keywords: str, current_user: dict = Depends(get_current_user)):
    """获取地址输入建议 (需要登录)"""
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
async def chat_endpoint(request: ChatRequest, current_user: dict = Depends(get_current_user)):
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
async def chat_stream_endpoint(request: ChatRequest, current_user: dict = Depends(get_current_user)):
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
async def menu_list_endpoint(current_user: dict = Depends(get_current_user)):
    """菜品列表区域展示 (需要登录)"""

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
async def delivery_endpoint(request: DeliveryRequest, current_user: dict = Depends(get_current_user)):
    """
    配送查询接口 (需要登录)
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
        return DeliveryResponse(
            success=False,
            message=f"配送范围查询失败{e}"
        )


@app.post("/order/submit", response_model=ActionResponse)
async def order_submit_endpoint(
    request: OrderSubmitRequest, 
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """提交订单接口 (需要登录)"""
    from service.diancan_service import submit_order
    order_data = request.model_dump()
    order_data['user_id'] = current_user['id']  # 绑定当前用户

    result = submit_order(order_data)
    
    # 添加后台任务：发送通知
    if result.get("success") and result.get("order_no"):
        background_tasks.add_task(send_notification, result["order_no"])
        
    return ActionResponse(**result)


@app.post("/order/pay/{order_no}", response_model=ActionResponse)
async def order_pay_endpoint(order_no: str, current_user: dict = Depends(get_current_user)):
    """模拟支付接口 (需要登录，且只能支付自己的订单)"""
    from service.diancan_service import pay_order
    result = pay_order(order_no, user_id=current_user['id'])
    if result.get("forbidden"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=result["message"])
    return ActionResponse(**{k: v for k, v in result.items() if k != "forbidden"})


@app.get("/order/list")
async def order_list_endpoint(limit: int = 10, current_user: dict = Depends(get_current_user)):
    """获取当前用户的订单历史列表"""
    from tools.db_tool import get_all_orders
    orders = get_all_orders(user_id=current_user['id'], limit=limit)
    return {"success": True, "orders": orders}




