import logging
from typing import Dict, Any, List, Optional
from langchain_core.tools import tool
from tools.chroma_tool import search_menu_items_with_ids
from tools.amap_tool import check_delivery_range as amap_check, PathInputModel
from tools.db_tool import get_order, DataBaseConnection

logger = logging.getLogger(__name__)

@tool
def search_menu(query: str) -> Dict[str, Any]:
    """
    搜索餐厅的菜品信息。
    当你需要了解菜品的特色、口感、价格、分类（如川菜、浙菜）或推荐菜品时，请使用此工具。
    支持模糊搜索和语义搜索。
    """
    try:
        logger.info(f"AI Tool Calling: search_menu for '{query}'")
        result = search_menu_items_with_ids(query, top_k=5)
        return {
            "success": True,
            "contents": result.get("contents", []),
            "menu_ids": result.get("ids", [])
        }
    except Exception as e:
        logger.error(f"search_menu tool error: {e}")
        return {"success": False, "message": str(e)}

@tool
def check_delivery(address: str, travel_mode: str = "2") -> Dict[str, Any]:
    """
    检查指定地址是否在餐厅的配送范围内。
    参数 address 是详细的地点名称（如：光谷广场）。
    参数 travel_mode 是配送模式：1=步行, 2=骑行(默认), 3=驾车。
    """
    try:
        logger.info(f"AI Tool Calling: check_delivery for '{address}'")
        # 内部函数调用
        res = amap_check(address, travel_mode)
        return res
    except Exception as e:
        logger.error(f"check_delivery tool error: {e}")
        return {"success": False, "message": str(e)}

@tool
def query_order(order_no: str) -> Dict[str, Any]:
    """
    根据订单号查询订单状态和详情。
    当用户询问“我的订单到哪了”、“订单状态”或提供订单号时，请使用此工具。
    """
    try:
        logger.info(f"AI Tool Calling: query_order for '{order_no}'")
        order = get_order(order_no)
        if order:
            # 状态映射
            status_map = {
                "pending": "待处理",
                "paid": "已支付，正在准备中",
                "completed": "已完成",
                "cancelled": "已取消"
            }
            return {
                "success": True,
                "order_no": order['order_no'],
                "status": status_map.get(order['order_status'], order['order_status']),
                "total_amount": float(order['total_amount']),
                "items": order['items_detail']
            }
        return {"success": False, "message": "未找到该订单，请检查订单号是否正确。"}
    except Exception as e:
        logger.error(f"query_order tool error: {e}")
        return {"success": False, "message": str(e)}

# 导出工具列表供 Agent 使用
restaurant_tools = [search_menu, check_delivery, query_order]
