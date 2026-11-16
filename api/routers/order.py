"""下单、支付与订单列表路由。"""
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, status

from api import deps
from api.schemas import ActionResponse, OrderSubmitRequest

router = APIRouter(tags=["订单"])


@router.post("/order/submit", response_model=ActionResponse)
def order_submit_endpoint(
    request: OrderSubmitRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(deps.get_current_user),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key", max_length=64),
):
    """提交订单接口 (需要登录)

    客户端可带 Idempotency-Key 请求头：重复提交（重试/双击）会返回同一笔订单，
    不会重复扣库存。
    """
    from service.diancan_service import submit_order

    order_data = request.model_dump()
    order_data["user_id"] = current_user["id"]
    order_data["idempotency_key"] = idempotency_key

    result = submit_order(order_data)
    if result.get("success") and result.get("order_no"):
        background_tasks.add_task(deps.send_notification, result["order_no"])

    return ActionResponse(**result)


@router.post("/order/pay/{order_no}", response_model=ActionResponse)
def order_pay_endpoint(order_no: str, current_user: dict = Depends(deps.get_current_user)):
    """模拟支付接口 (需要登录，且只能支付自己的订单)"""
    from service.diancan_service import pay_order

    result = pay_order(order_no, user_id=current_user["id"])
    if result.get("forbidden"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=result["message"])
    if result.get("conflict"):
        # 已取消/已完成的订单不能再支付，状态机拒绝非法跃迁
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result["message"])
    return ActionResponse(**{k: v for k, v in result.items() if k != "forbidden"})


@router.get("/order/list")
def order_list_endpoint(
    limit: int = Query(10, ge=1, le=100),
    current_user: dict = Depends(deps.get_current_user),
):
    """获取当前用户的订单历史列表"""
    from tools.db_tool import get_all_orders

    orders = get_all_orders(user_id=current_user["id"], limit=limit)
    return {"success": True, "orders": orders}
