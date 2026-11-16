"""配送范围与地址输入提示路由。"""
from fastapi import APIRouter, Depends, Query

from api import deps
from api.schemas import DeliveryRequest, DeliveryResponse

router = APIRouter(tags=["配送"])


@router.get("/delivery/tips")
def delivery_tips_endpoint(
    keywords: str = Query(..., min_length=1, max_length=50),
    current_user: dict = Depends(deps.get_current_user),
):
    """获取地址输入建议 (需要登录)；同步 HTTP 调用由 FastAPI 线程池执行"""
    from tools.amap_tool import get_input_tips

    return get_input_tips(keywords)


@router.post("/delivery", response_model=DeliveryResponse)
def delivery_endpoint(request: DeliveryRequest, current_user: dict = Depends(deps.get_current_user)):
    """配送查询接口 (需要登录)；高德接口为同步 requests 调用，由 FastAPI 线程池执行"""
    import logging

    logger = logging.getLogger(__name__)
    try:
        from service.diancan_service import check_delivery_range

        result = check_delivery_range(request.address, request.travel_mode)
        if result["status"] == "fail":
            return DeliveryResponse(
                success=False,
                in_range=False,
                distance=0.0,
                formatted_address=request.address,
                duration=0.0,
                message=result["message"],
                travel_mode=request.travel_mode,
                input_address=request.address,
            )

        return DeliveryResponse(
            success=True,
            in_range=result["in_range"],
            distance=result["distance"],
            formatted_address=result["formatted_address"],
            duration=result["duration"],
            delivery_fee=result.get("delivery_fee", 0.0),
            message=result["message"],
            travel_mode=request.travel_mode,
            input_address=request.address,
        )
    except Exception as e:
        logger.error(f"配送范围查询失败: {e}")
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
            input_address=request.address,
        )
