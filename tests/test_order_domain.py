"""订单域单元测试：状态机、幂等键透传、非法跃迁返回 409

这些用例全部打桩，不连接真实 MySQL / 高德接口。
"""
import pytest
from fastapi.testclient import TestClient

from api.main import app, get_current_user
from service.diancan_service import submit_order
from tools.db_tool import (
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_COMPLETED,
    ORDER_STATUS_PAID,
    ORDER_STATUS_PENDING,
    InvalidOrderTransition,
    OrderError,
    can_transition_order_status,
)

client = TestClient(app)
USER = {"id": 1, "username": "admin", "nickname": "店小二", "phone": None}


@pytest.fixture
def authed_user():
    app.dependency_overrides[get_current_user] = lambda: USER
    yield USER
    app.dependency_overrides.clear()


def _stub_menu_and_delivery(monkeypatch):
    monkeypatch.setattr(
        "tools.db_tool.get_menu_item_by_id",
        lambda dish_id: {"id": dish_id, "dish_name": "回锅肉", "price": 45.0, "is_available": 1},
    )
    monkeypatch.setattr(
        "tools.amap_tool.check_delivery_range",
        lambda address, mode: {
            "status": "success",
            "in_range": True,
            "delivery_fee": 3.0,
            "formatted_address": "湖北省武汉市洪山区",
            "message": "",
        },
    )


# ---------------- 状态机 ----------------

def test_pending_order_can_be_paid_or_cancelled():
    assert can_transition_order_status(ORDER_STATUS_PENDING, ORDER_STATUS_PAID)
    assert can_transition_order_status(ORDER_STATUS_PENDING, ORDER_STATUS_CANCELLED)


def test_paid_order_can_only_complete():
    assert can_transition_order_status(ORDER_STATUS_PAID, ORDER_STATUS_COMPLETED)
    assert not can_transition_order_status(ORDER_STATUS_PAID, ORDER_STATUS_PENDING)
    assert not can_transition_order_status(ORDER_STATUS_PAID, ORDER_STATUS_CANCELLED)


def test_terminal_status_is_frozen():
    assert not can_transition_order_status(ORDER_STATUS_COMPLETED, ORDER_STATUS_PAID)
    assert not can_transition_order_status(ORDER_STATUS_CANCELLED, ORDER_STATUS_PAID)


def test_unknown_status_is_rejected():
    assert not can_transition_order_status("pending", "shipped")


# ---------------- 下单业务异常 ----------------

def test_submit_order_returns_business_error_when_stock_short(monkeypatch):
    _stub_menu_and_delivery(monkeypatch)

    def _boom(order_data):
        raise OrderError("「回锅肉」库存不足，仅剩 0 份")

    monkeypatch.setattr("tools.db_tool.create_order", _boom)

    result = submit_order({
        "user_id": 1,
        "delivery_address": "洪山区",
        "travel_mode": "2",
        "items_detail": [{"id": 1, "dish_name": "回锅肉", "price": 45.0, "quantity": 1}],
    })

    assert result["success"] is False
    assert "库存不足" in result["message"]


# ---------------- 接口层 ----------------

def test_submit_order_passes_idempotency_key(authed_user, monkeypatch):
    captured = {}
    _stub_menu_and_delivery(monkeypatch)
    monkeypatch.setattr(
        "tools.db_tool.create_order",
        lambda order_data: captured.update(order_data) or "ORD-IDEM",
    )
    monkeypatch.setattr("api.main.send_notification", lambda order_no: None)

    response = client.post(
        "/order/submit",
        headers={"Idempotency-Key": "req-abc-123"},
        json={
            "delivery_address": "洪山区",
            "travel_mode": "2",
            "items_detail": [{"id": 1, "dish_name": "回锅肉", "price": 45.0, "quantity": 1}],
        },
    )

    assert response.status_code == 200
    assert response.json()["order_no"] == "ORD-IDEM"
    assert captured["idempotency_key"] == "req-abc-123"


def test_pay_cancelled_order_returns_409(authed_user, monkeypatch):
    monkeypatch.setattr(
        "tools.db_tool.get_order",
        lambda order_no: {
            "order_no": order_no,
            "user_id": 1,
            "order_status": ORDER_STATUS_CANCELLED,
            "payment_status": "unpaid",
        },
    )

    def _reject(order_no, status, payment_status=None):
        raise InvalidOrderTransition("订单当前状态为 cancelled，不能变更为 paid")

    monkeypatch.setattr("tools.db_tool.update_order_status", _reject)

    response = client.post("/order/pay/ORD-CANCELLED")

    assert response.status_code == 409
    assert "不能变更为 paid" in response.json()["detail"]
