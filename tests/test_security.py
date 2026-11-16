"""安全加固回归：登录限流、安全响应头、请求体上限、输入约束与 CORS。

这些用例只走接口层，不访问真实数据库、Redis 与外部 API。
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


def test_security_headers_present():
    response = client.get("/healthy")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_oversized_body_rejected():
    payload = b"x" * (1024 * 1024 + 1)

    response = client.post(
        "/auth/login",
        content=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 413


def test_login_rate_limited(monkeypatch):
    monkeypatch.setattr("api.deps.LOGIN_RATE_LIMIT_PER_MINUTE", 10)
    monkeypatch.setattr("api.deps.cache.incr_with_ttl", lambda key, expire=60: 11)

    response = client.post("/auth/login", data={"username": "admin", "password": "wrong"})

    assert response.status_code == 429
    assert "频繁" in response.json()["detail"]


def test_login_failure_message_does_not_leak_account(monkeypatch):
    """用户不存在与密码错误返回同一句话，避免用户名枚举"""
    monkeypatch.setattr("api.deps.LOGIN_RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setattr("api.deps.get_user_by_username", lambda username: None)

    response = client.post("/auth/login", data={"username": "nobody", "password": "whatever"})

    assert response.status_code == 401
    assert response.json()["detail"] == "用户名或密码错误"


def test_jwt_secret_not_repository_default():
    from tools import auth_tool

    assert auth_tool.SECRET_KEY not in ("aimenu_secret_key_2024_trae", "please-change-me")


def test_chat_query_too_long_rejected(authed_user):
    response = client.post("/chat", json={"query": "菜" * 501})

    assert response.status_code == 422


def test_order_list_limit_is_bounded(authed_user):
    response = client.get("/order/list", params={"limit": 1000})

    assert response.status_code == 422


def test_register_rejects_weak_password():
    response = client.post("/auth/register", json={"username": "newuser", "password": "123"})

    assert response.status_code == 422


def test_order_items_must_not_be_empty(authed_user):
    response = client.post(
        "/order/submit",
        json={"delivery_address": "洪山区", "travel_mode": "2", "items_detail": []},
    )

    assert response.status_code == 422


def test_cors_preflight_allows_configured_origin():
    response = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
