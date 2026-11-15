"""接口层单测：鉴权、支付越权拦截、下单金额服务端重算。

通过 override 依赖注入模拟登录用户，不触发启动事件，也不访问真实数据库与地图 API。
"""
import pytest
from fastapi.testclient import TestClient

from api.main import app, get_current_user

client = TestClient(app)

USER = {"id": 1, "username": "admin", "nickname": "店小二", "phone": None}


@pytest.fixture
def authed_user():
    app.dependency_overrides[get_current_user] = lambda: USER
    yield USER
    app.dependency_overrides.clear()


def test_chat_requires_login():
    response = client.post("/chat", json={"query": "你好"})

    assert response.status_code == 401


def test_menu_list_requires_login():
    response = client.get("/menu/list")

    assert response.status_code == 401


def test_pay_order_of_other_user_is_forbidden(authed_user, monkeypatch):
    monkeypatch.setattr(
        "tools.db_tool.get_order",
        lambda order_no: {"order_no": order_no, "user_id": 2, "payment_status": "unpaid"},
    )

    response = client.post("/order/pay/ORD100")

    assert response.status_code == 403
    assert "无权操作" in response.json()["detail"]


def test_submit_order_recalculates_amount_server_side(authed_user, monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "tools.db_tool.get_menu_item_by_id",
        lambda dish_id: {"id": dish_id, "dish_name": "回锅肉", "price": 45.0, "is_available": 1},
    )
    monkeypatch.setattr(
        "tools.amap_tool.check_delivery_range",
        lambda address, mode: {
            "status": "success",
            "in_range": True,
            "distance": 2.0,
            "duration": 600,
            "delivery_fee": 3.0,
            "formatted_address": "湖北省武汉市洪山区",
            "message": "",
        },
    )
    monkeypatch.setattr(
        "tools.db_tool.create_order",
        lambda order_data: captured.update(order_data) or "ORD123",
    )
    monkeypatch.setattr("api.main.send_notification", lambda order_no: None)

    response = client.post(
        "/order/submit",
        json={
            "total_amount": 0.01,
            "delivery_fee": 0.0,
            "actual_amount": 0.01,
            "delivery_address": "洪山区",
            "travel_mode": "2",
            "items_detail": [
                {"id": 1, "dish_name": "回锅肉", "price": 0.01, "quantity": 2}
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert captured["user_id"] == 1
    assert captured["total_amount"] == 90.0
    assert captured["delivery_fee"] == 3.0
    assert captured["actual_amount"] == 93.0
    assert captured["items_detail"][0]["price"] == 45.0


def test_submit_order_rejects_out_of_range_address(authed_user, monkeypatch):
    monkeypatch.setattr(
        "tools.db_tool.get_menu_item_by_id",
        lambda dish_id: {"id": dish_id, "dish_name": "回锅肉", "price": 45.0, "is_available": 1},
    )
    monkeypatch.setattr(
        "tools.amap_tool.check_delivery_range",
        lambda address, mode: {"status": "success", "in_range": False, "delivery_fee": 0.0},
    )
    monkeypatch.setattr("tools.db_tool.create_order", lambda order_data: "ORD-should-not-exist")

    response = client.post(
        "/order/submit",
        json={
            "delivery_address": "北京市天安门",
            "travel_mode": "2",
            "items_detail": [{"id": 1, "dish_name": "回锅肉", "price": 45.0, "quantity": 1}],
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert "超出配送范围" in response.json()["message"]
