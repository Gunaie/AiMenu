"""并发下单验证：库存扣减不超卖 + 幂等键去重

用法：.venv\\Scripts\\python.exe scripts/verify_oversell.py

验证内容：
1. 超卖防护：选一道菜把库存置为 5，20 个线程同时各下单 1 份；
   期望恰好 5 单成功、15 单因库存不足失败，最终库存为 0，且不存在负库存。
2. 幂等去重：把库存置回 5，10 个线程用同一个 Idempotency-Key 并发下单；
   期望只落 1 笔订单、库存只扣 1 份，10 个请求拿到的订单号一致。
3. 清理本次产生的订单并还原原始库存；任一项断言失败时退出码为 1。

注意：脚本会临时改写所选菜品的 stock 字段，结束后恢复。
"""
import json
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.db_tool import DataBaseConnection, OrderError, create_order  # noqa: E402

INITIAL_STOCK = 5
ORDER_CONCURRENCY = 20
IDEMPOTENCY_CONCURRENCY = 10


def _pick_dish():
    """挑一道菜做实验（只在脚本内改库存，结束后还原）"""
    with DataBaseConnection() as db:
        db.cursor.execute("SELECT id, dish_name, stock FROM menu_items ORDER BY id LIMIT 1")
        return db.cursor.fetchone()


def _set_stock(dish_id: int, stock):
    with DataBaseConnection() as db:
        db.cursor.execute("UPDATE menu_items SET stock = %s WHERE id = %s", (stock, dish_id))
        db.commit()


def _get_stock(dish_id: int):
    with DataBaseConnection() as db:
        db.cursor.execute("SELECT stock FROM menu_items WHERE id = %s", (dish_id,))
        row = db.cursor.fetchone()
        if row is None or row["stock"] is None:
            return None
        return int(row["stock"])


def _count_orders_with_key(idempotency_key: str) -> int:
    with DataBaseConnection() as db:
        db.cursor.execute(
            "SELECT COUNT(*) AS cnt FROM orders WHERE idempotency_key = %s",
            (idempotency_key,),
        )
        return int(db.cursor.fetchone()["cnt"])


def _delete_orders(order_nos):
    if not order_nos:
        return
    with DataBaseConnection() as db:
        db.cursor.executemany(
            "DELETE FROM orders WHERE order_no = %s",
            [(order_no,) for order_no in set(order_nos)],
        )
        db.commit()


def _payload(dish, idempotency_key=None):
    return {
        "user_id": None,
        "total_amount": 1.0,
        "delivery_fee": 0.0,
        "actual_amount": 1.0,
        "delivery_address": "并发验证地址",
        "items_detail": [{
            "id": dish["id"],
            "dish_name": dish["dish_name"],
            "price": 1.0,
            "quantity": 1,
        }],
        "idempotency_key": idempotency_key,
    }


def _place_order(dish, idempotency_key=None):
    """返回 (order_no, error_kind)；error_kind 为 stock / other / None"""
    try:
        return create_order(_payload(dish, idempotency_key)), None
    except OrderError as exc:
        kind = "stock" if "库存不足" in str(exc) else "other"
        return None, f"{kind}:{exc}"
    except Exception as exc:  # 非业务异常也要记录，避免脚本把故障当成「卖完了」
        return None, f"other:{exc}"


def _run_concurrently(times: int, fn):
    with ThreadPoolExecutor(max_workers=times) as pool:
        return list(pool.map(lambda _: fn(), range(times)))


def main() -> int:
    dish = _pick_dish()
    if not dish:
        print(json.dumps({"passed": False, "reason": "menu_items 为空，无法验证"}, ensure_ascii=False))
        return 1

    original_stock = dish["stock"]
    created_orders = []
    report = {
        "dish": {"id": dish["id"], "dish_name": dish["dish_name"], "original_stock": original_stock},
        "oversell": {},
        "idempotency": {},
    }
    passed = False

    try:
        # ---------- 1. 超卖防护 ----------
        _set_stock(dish["id"], INITIAL_STOCK)
        outcomes = _run_concurrently(ORDER_CONCURRENCY, lambda: _place_order(dish))
        success_orders = [order_no for order_no, _ in outcomes if order_no]
        created_orders.extend(success_orders)
        stock_failures = [err for _, err in outcomes if err and err.startswith("stock:")]
        other_failures = [err for _, err in outcomes if err and not err.startswith("stock:")]
        stock_after = _get_stock(dish["id"])

        oversell_pass = (
            len(success_orders) == INITIAL_STOCK
            and len(stock_failures) == ORDER_CONCURRENCY - INITIAL_STOCK
            and stock_after == 0
            and not other_failures
        )
        report["oversell"] = {
            "initial_stock": INITIAL_STOCK,
            "concurrency": ORDER_CONCURRENCY,
            "success": len(success_orders),
            "rejected_out_of_stock": len(stock_failures),
            "unexpected_errors": other_failures[:3],
            "stock_after": stock_after,
            "passed": oversell_pass,
        }

        # ---------- 2. 幂等键去重 ----------
        _set_stock(dish["id"], INITIAL_STOCK)
        idempotency_key = f"verify-{uuid.uuid4().hex}"
        outcomes = _run_concurrently(
            IDEMPOTENCY_CONCURRENCY,
            lambda: _place_order(dish, idempotency_key),
        )
        returned_orders = [order_no for order_no, _ in outcomes if order_no]
        created_orders.extend(returned_orders)
        idem_errors = [err for _, err in outcomes if err]
        distinct_orders = sorted(set(returned_orders))
        stock_after_idem = _get_stock(dish["id"])
        rows_with_key = _count_orders_with_key(idempotency_key)

        idempotency_pass = (
            len(returned_orders) == IDEMPOTENCY_CONCURRENCY
            and len(distinct_orders) == 1
            and rows_with_key == 1
            and stock_after_idem == INITIAL_STOCK - 1
            and not idem_errors
        )
        report["idempotency"] = {
            "concurrency": IDEMPOTENCY_CONCURRENCY,
            "responses": len(returned_orders),
            "distinct_order_no": distinct_orders,
            "orders_in_db": rows_with_key,
            "stock_after": stock_after_idem,
            "errors": idem_errors[:3],
            "passed": idempotency_pass,
        }

        passed = oversell_pass and idempotency_pass
    finally:
        # 无论断言是否通过，都清理测试订单并还原库存
        try:
            _delete_orders(created_orders)
            _set_stock(dish["id"], original_stock)
            report["cleanup"] = {"deleted_orders": len(set(created_orders)),
                                 "stock_restored_to": original_stock}
        except Exception as exc:
            report["cleanup"] = {"error": str(exc)}

    report["passed"] = passed
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
