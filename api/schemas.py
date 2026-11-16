"""接口层请求/响应模型（与路由实现分离，便于复用与契约测试）。"""
from typing import List, Optional

from pydantic import BaseModel, Field

from tools.amap_tool import PathInputModel


class DeliveryRequest(BaseModel):
    """配送范围查询请求"""

    address: str = Field(..., min_length=1, max_length=120)
    travel_mode: PathInputModel = "2"  # 1=步行, 2=骑电动车, 3=驾车


class ChatRequest(BaseModel):
    """智能对话请求（限制长度，避免超长输入把 LLM 成本与延迟放大）"""

    query: str = Field(..., max_length=500)
    session_id: Optional[str] = Field(default="default", max_length=64)


class DeliveryResponse(BaseModel):
    """配送查询响应"""

    success: bool
    in_range: bool
    distance: float
    formatted_address: str
    duration: float
    delivery_fee: Optional[float] = 0.0
    message: str
    travel_mode: PathInputModel
    input_address: str


class ChatResponse(BaseModel):
    """智能对话响应"""

    success: bool
    query: str
    response: Optional[str] = None
    recommendation: Optional[str] = None
    menu_ids: Optional[List[str]] = None


class MenuListResponse(BaseModel):
    """菜品列表响应"""

    success: bool
    menu_items: List[dict]
    count: int
    message: str


class RestaurantInfoResponse(BaseModel):
    """餐厅信息响应"""

    success: bool
    name: str
    address: str
    longitude: float
    latitude: float
    delivery_radius: float


class OrderItem(BaseModel):
    """订单明细条目"""

    id: int = Field(..., ge=1)
    dish_name: str = Field(..., max_length=100)
    price: float = Field(..., ge=0)
    quantity: int = Field(..., ge=1, le=99)


class OrderSubmitRequest(BaseModel):
    """提交订单请求

    total_amount / delivery_fee / actual_amount 仅用于展示，服务端会依据数据库价格
    与配送距离重新计算。
    """

    total_amount: Optional[float] = None
    delivery_fee: Optional[float] = None
    actual_amount: Optional[float] = None
    delivery_address: str = Field(..., min_length=1, max_length=200)
    travel_mode: PathInputModel = "2"
    items_detail: List[OrderItem] = Field(..., min_length=1, max_length=50)


class ActionResponse(BaseModel):
    """通用操作响应"""

    success: bool
    message: str
    order_no: Optional[str] = None


class UserRegister(BaseModel):
    """用户注册请求"""

    username: str = Field(..., pattern=r"^[A-Za-z0-9_]{3,32}$")
    password: str = Field(..., min_length=8, max_length=72)
    nickname: Optional[str] = Field(default=None, max_length=32)
    phone: Optional[str] = Field(default=None, max_length=20)


class Token(BaseModel):
    """登录成功返回的令牌与用户展示信息"""

    access_token: str
    token_type: str
    username: str
    nickname: str
