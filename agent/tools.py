import logging
from typing import Dict, Any, List, Optional
from langchain_core.tools import tool
from tools.chroma_tool import search_menu_items_with_ids
from tools.amap_tool import check_delivery_range as amap_check
from tools.db_tool import DataBaseConnection, get_menu_items_by_ids

logger = logging.getLogger(__name__)


def _verify_menu_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """菜名存在性校验：以数据库为准核对检索出的 dish_id 与菜名

    向量索引可能落后于菜单更新（下架/改名/删档），这里过滤掉已不存在的菜品，
    并用数据库里的标准菜名覆盖，避免模型推荐不存在的菜。
    数据库不可用时 fail-open，不影响正常检索。
    """
    dish_ids = []
    for item in items:
        try:
            dish_ids.append(int(item.get("dish_id")))
        except (TypeError, ValueError):
            continue
    if not dish_ids:
        return items

    try:
        rows = get_menu_items_by_ids(dish_ids)
    except Exception as exc:
        logger.warning(f"菜名存在性校验失败，跳过校验: {exc}")
        return items

    available = {int(row["id"]): row["dish_name"] for row in rows}
    verified = []
    for item in items:
        try:
            dish_id = int(item.get("dish_id"))
        except (TypeError, ValueError):
            continue
        if dish_id not in available:
            logger.info(f"过滤已下架/不存在的菜品: dish_id={dish_id}")
            continue
        canonical_name = available[dish_id]
        if item.get("dish_name") and item["dish_name"] != canonical_name:
            logger.info(f"菜名以数据库为准: {item['dish_name']} -> {canonical_name}")
        verified.append({**item, "dish_name": canonical_name})
    return verified


@tool
def search_menu(query: str, max_spice: Optional[int] = None,
                max_price: Optional[float] = None, vegetarian: Optional[bool] = None) -> Dict[str, Any]:
    """
    搜索餐厅的菜品信息。
    当你需要了解菜品的特色、口感、价格、分类（如川菜、浙菜）或推荐菜品时，请使用此工具。
    支持模糊搜索和语义搜索。
    max_spice/max_price/vegetarian 是本会话已确认的辣度、预算、素食约束，
    由系统自动注入；客官本轮没有明确提出新约束时不要自行改写。
    """
    try:
        logger.info(f"AI Tool Calling: search_menu for '{query}' "
                    f"(max_spice={max_spice}, max_price={max_price}, vegetarian={vegetarian})")
        result = search_menu_items_with_ids(
            query,
            top_k=5,
            max_spice=max_spice,
            max_price=max_price,
            vegetarian=vegetarian,
        )
        original_items = result.get("items") or []
        items = _verify_menu_items(original_items)

        if items:
            contents = [item.get("content", "") for item in items]
        elif original_items:
            # 检索有结果但全部不存在于数据库：视为无答案，避免推荐幽灵菜品
            contents = []
        else:
            contents = result.get("contents", [])

        answerable = result.get("answerable")
        if original_items and not items:
            answerable = False

        return {
            "success": True,
            "contents": contents,
            # 结构化条目：dish_id / dish_name / price / category / spice_level / is_vegetarian
            "items": items,
            "menu_ids": [item["dish_id"] for item in items],
            # answerable=False 表示菜单里没有足够相关的菜品，模型应如实拒答而非硬推
            "answerable": answerable,
            "top_score": result.get("top_score"),
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
    user_id 由系统自动注入，是查询的必要条件：没有 user_id 时不会查询任何订单。
    """
    try:
        if not user_id:
            return "客官，查询订单需要先登录哦～登录后小二才能帮您查自己的订单。"

        target_order_no = order_no

        with DataBaseConnection() as db:
            if not target_order_no:
                logger.info(f"AI Tool Calling: query_order (no order_no, user_id={user_id})")
                db.cursor.execute(
                    "SELECT order_no FROM orders WHERE user_id = %s ORDER BY created_at DESC LIMIT 1",
                    (user_id,),
                )
                row = db.cursor.fetchone()
                if row:
                    target_order_no = row['order_no']
                else:
                    return "客官，小店还没查到您的下单记录呢，要不您先点几个菜试试？"

            logger.info(f"AI Tool Calling: query_order for '{target_order_no}' (user_id={user_id})")
            # user_id 必须参与过滤，防止越权查询他人订单
            db.cursor.execute(
                "SELECT * FROM orders WHERE order_no = %s AND user_id = %s",
                (target_order_no, user_id),
            )
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
