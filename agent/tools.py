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
    注意：必须提供具体的地点名称，严禁传入空字符串或无意义的代词。
    """
    try:
        if not address or len(address.strip()) < 2:
            return {"success": False, "message": "客官，请告诉小二一个具体的地点（比如：光谷广场），不然小二没法帮您算距离呀。"}
            
        logger.info(f"AI Tool Calling: check_delivery for '{address}'")
        # 内部函数调用
        res = amap_check(address, travel_mode)
        return res
    except Exception as e:
        logger.error(f"check_delivery tool error: {e}")
        return {"success": False, "message": str(e)}

@tool
def query_order(order_no: str = None) -> Dict[str, Any]:
    """
    查询订单状态和详情。
    参数 order_no 是订单号（如：ORD172483...）。
    如果不提供订单号，工具会自动尝试查找客官最近的一笔订单。
    当用户询问“我的订单”、“订单状态”或“查一下我的单子”时，请使用此工具。
    """
    try:
        # 如果没有提供订单号，尝试从数据库获取最近一笔订单
        if not order_no:
            logger.info("AI Tool Calling: query_order (no order_no, fetching latest)")
            with DataBaseConnection() as db:
                db.cursor.execute("SELECT order_no FROM orders ORDER BY created_at DESC LIMIT 1")
                row = db.cursor.fetchone()
                if row:
                    order_no = row['order_no']
                    logger.info(f"Auto-detected latest order_no: {order_no}")
                else:
                    return {"success": False, "message": "客官，小店还没查到您的下单记录呢，要不您先点几个菜试试？"}

        logger.info(f"AI Tool Calling: query_order for '{order_no}'")
        order = get_order(order_no)
        if order:
            # 状态映射
            status_map = {
                "pending": "待处理（小二正在飞奔确认中）",
                "paid": "已支付（后厨大师傅火候正旺，请稍候）",
                "completed": "已完成（祝客官用餐愉快）",
                "cancelled": "已取消"
            }
            return {
                "success": True,
                "order_no": order['order_no'],
                "status": status_map.get(order['order_status'], order['order_status']),
                "total_amount": float(order['total_amount']),
                "items": order['items_detail'],
                "address": order['delivery_address']
            }
        return {"success": False, "message": f"客官，小店翻遍了账本也没找到单号为 {order_no} 的记录，请您核对一下单号是否正确。"}
    except Exception as e:
        logger.error(f"query_order tool error: {e}")
        return {"success": False, "message": str(e)}

# 导出工具列表供 Agent 使用
restaurant_tools = [search_menu, check_delivery, query_order]
