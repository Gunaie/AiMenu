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
    该工具仅用于地理位置查询，如“配送费多少”、“能否送到某地”。
    参数 address 是详细的地点名称（如：光谷广场）。
    注意：严禁在查询“订单状态”时调用此工具。
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
def query_order(order_no: Optional[str] = None, user_id: Optional[int] = None) -> str:
    """
    查询订单状态和详情。
    参数 order_no 是可选的订单号。
    如果不提供订单号，工具会自动尝试查找该客官最近的一笔订单。
    user_id 由系统自动提供，用于确保只能查到客官自己的订单。
    """
    try:
        target_order_no = order_no
        
        with DataBaseConnection() as db:
            if not target_order_no:
                logger.info(f"AI Tool Calling: query_order (no order_no, user_id={user_id})")
                if user_id:
                    db.cursor.execute("SELECT order_no FROM orders WHERE user_id = %s ORDER BY created_at DESC LIMIT 1", (user_id,))
                else:
                    db.cursor.execute("SELECT order_no FROM orders ORDER BY created_at DESC LIMIT 1")
                
                row = db.cursor.fetchone()
                if row:
                    target_order_no = row['order_no']
                else:
                    return "客官，小店还没查到您的下单记录呢，要不您先点几个菜试试？"

            logger.info(f"AI Tool Calling: query_order for '{target_order_no}' (user_id={user_id})")
            # 增加 user_id 校验，防止越权查询
            if user_id:
                query_sql = "SELECT * FROM orders WHERE order_no = %s AND user_id = %s"
                db.cursor.execute(query_sql, (target_order_no, user_id))
            else:
                query_sql = "SELECT * FROM orders WHERE order_no = %s"
                db.cursor.execute(query_sql, (target_order_no,))
            
            order = db.cursor.fetchone()
        
        if order:
            if 'items_detail' in order and isinstance(order['items_detail'], str):
                import json
                order['items_detail'] = json.loads(order['items_detail'])
            
            status_map = {
                "pending": "待处理（小二正在飞奔确认中）",
                "paid": "已支付（后厨大师傅火候正旺，请稍候）",
                "completed": "已完成（祝客官用餐愉快）",
                "cancelled": "已取消"
            }
            status_text = status_map.get(order['order_status'], order['order_status'])
            
            items_str = ", ".join([f"{i['dish_name']}x{i['quantity']}" for i in order['items_detail']])
            res_text = (
                f"查到了！单号：{target_order_no}\n"
                f"当前状态：{status_text}\n"
                f"订单内容：{items_str}\n"
                f"总额：¥{float(order['actual_amount'])}\n"
                f"配送地址：{order['delivery_address']}"
            )
            return res_text
            
        return f"客官，小店翻遍了账本也没找到单号为 {target_order_no} 的记录，请您核对一下单号是否正确。"
    except Exception as e:
        logger.error(f"query_order tool error: {e}")
        return f"抱歉客官，查询订单时遇到了点技术小故障：{str(e)}"

# 导出工具列表供 Agent 使用
restaurant_tools = [search_menu, check_delivery, query_order]
