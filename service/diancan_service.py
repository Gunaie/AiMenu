"""
智能点餐助手 服务类封装

封装三个核心功能：
- smart_chat: 调用 assistant.py 中的 chat_with_assistant 函数
- delivery_check: 调用 check_delivery_range 函数 获取配送范围展示
- get_menu: 调用 get_menu_items_list 函数 获取菜品区域数据的展示
"""

from  tools.amap_tool import  PathInputModel

def  get_menu():
    """获取菜品区域数据的展示"""
    from  tools.db_tool import  get_menu_items
    return get_menu_items()


def  check_delivery_range(address:str,model:PathInputModel):
    """获取配送范围展示"""
    from  tools.amap_tool import check_delivery_range
    return check_delivery_range(address,model)



async def  smart_chat(user_query:str, session_id: str = "default", user_info: dict = None):
    """对话接口"""
    from  agent.assistant import chat_with_assistant

    return await chat_with_assistant(user_query, session_id, user_info=user_info)


def submit_order(order_data: dict):
    """提交订单：价格与配送费全部由服务端重算，忽略客户端传入的金额"""
    from tools.db_tool import create_order, get_menu_item_by_id
    from tools.amap_tool import check_delivery_range

    items = order_data.get("items_detail") or []
    if not items:
        return {"success": False, "message": "订单中没有任何菜品"}

    total_amount = 0.0
    normalized_items = []
    for item in items:
        quantity = int(item.get("quantity") or 0)
        if quantity <= 0:
            return {"success": False, "message": "菜品数量不合法"}

        dish = get_menu_item_by_id(item.get("id"))
        if not dish or not dish.get("is_available"):
            return {"success": False, "message": f"菜品 {item.get('dish_name') or item.get('id')} 已下架"}

        price = float(dish["price"])
        total_amount += price * quantity
        normalized_items.append({
            "id": dish["id"],
            "dish_name": dish["dish_name"],
            "price": price,
            "quantity": quantity
        })

    delivery = check_delivery_range(
        order_data["delivery_address"],
        order_data.get("travel_mode", "2")
    )
    if delivery.get("status") == "fail":
        return {"success": False, "message": f"配送地址校验失败：{delivery.get('message')}"}
    if not delivery.get("in_range"):
        return {"success": False, "message": "当前地址超出配送范围，无法下单"}

    total_amount = round(total_amount, 2)
    delivery_fee = float(delivery.get("delivery_fee", 0.0))
    actual_amount = round(total_amount + delivery_fee, 2)

    order_no = create_order({
        "user_id": order_data.get("user_id"),
        "total_amount": total_amount,
        "delivery_fee": delivery_fee,
        "actual_amount": actual_amount,
        "delivery_address": delivery.get("formatted_address") or order_data["delivery_address"],
        "items_detail": normalized_items
    })
    if order_no:
        return {"success": True, "order_no": order_no, "message": "订单提交成功"}
    return {"success": False, "message": "订单提交失败"}


def pay_order(order_no: str, user_id: int = None):
    """模拟支付（校验订单归属，防止越权支付他人订单）"""
    from tools.db_tool import update_order_status, get_order
    order = get_order(order_no)
    if not order:
        return {"success": False, "message": "订单不存在"}

    if user_id is not None and order.get('user_id') != user_id:
        return {"success": False, "forbidden": True, "message": "无权操作该订单"}

    if order['payment_status'] == 'paid':
        return {"success": True, "message": "订单已支付，请勿重复支付"}
    
    # 模拟支付成功
    success = update_order_status(order_no, status='paid', payment_status='paid')
    if success:
        return {"success": True, "message": "支付成功", "order_no": order_no}
    return {"success": False, "message": "支付处理失败"}



