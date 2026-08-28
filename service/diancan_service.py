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



def  smart_chat(user_query:str, session_id: str = "default", user_info: dict = None):
    """对话接口"""
    from  agent.assistant import chat_with_assistant

    return chat_with_assistant(user_query, session_id, user_info=user_info)


def submit_order(order_data: dict):
    """提交订单"""
    from tools.db_tool import create_order
    order_no = create_order(order_data)
    if order_no:
        return {"success": True, "order_no": order_no, "message": "订单提交成功"}
    return {"success": False, "message": "订单提交失败"}


def pay_order(order_no: str):
    """模拟支付"""
    from tools.db_tool import update_order_status, get_order
    order = get_order(order_no)
    if not order:
        return {"success": False, "message": "订单不存在"}
    
    if order['payment_status'] == 'paid':
        return {"success": True, "message": "订单已支付，请勿重复支付"}
    
    # 模拟支付成功
    success = update_order_status(order_no, status='paid', payment_status='paid')
    if success:
        return {"success": True, "message": "支付成功", "order_no": order_no}
    return {"success": False, "message": "支付处理失败"}



