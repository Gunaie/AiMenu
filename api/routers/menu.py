"""菜品与餐厅信息路由。"""
from fastapi import APIRouter, Depends

from api import deps
from api.schemas import MenuListResponse, RestaurantInfoResponse

router = APIRouter(tags=["菜单"])


@router.get("/menu/list", response_model=MenuListResponse)
def menu_list_endpoint(current_user: dict = Depends(deps.get_current_user)):
    """菜品列表区域展示 (需要登录)；DB/缓存为同步调用，由 FastAPI 线程池执行"""
    from service.diancan_service import get_menu

    menu_items = get_menu()
    if not menu_items:
        return MenuListResponse(success=False, menu_items=[], count=0, message="暂无菜品列表可用")

    return MenuListResponse(
        success=True,
        menu_items=menu_items,
        count=len(menu_items),
        message=f"成功查询到{len(menu_items)}道菜品信息",
    )


@router.get("/restaurant/info", response_model=RestaurantInfoResponse)
async def restaurant_info_endpoint(current_user: dict = Depends(deps.get_current_user)):
    """获取餐厅基本信息 (需要登录)"""
    from tools.amap_tool import config

    return RestaurantInfoResponse(
        success=True,
        name="智能餐厅",
        address=config.RESTAURANT_ADDRESS,
        longitude=float(config.MERCHANT_LONGITUDE),
        latitude=float(config.MERCHANT_LATITUDE),
        delivery_radius=float(config.DELIVERY_RADIUS),
    )
