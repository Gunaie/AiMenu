"""接口层公共依赖：登录鉴权、限流与后台任务。

拆出独立模块后，路由与测试都以本模块为打桩边界：
例如 ``monkeypatch.setattr("api.deps.LOGIN_RATE_LIMIT_PER_MINUTE", 0)``。
"""
import logging
import os
import time
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import OAuth2PasswordBearer

from tools.auth_tool import decode_access_token, get_password_hash, verify_password
from tools.db_tool import get_user_by_username
from tools.redis_tool import cache

logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

# 对话接口限流：每个用户每分钟最多 N 次，0 表示关闭
CHAT_RATE_LIMIT_PER_MINUTE = int(os.getenv("CHAT_RATE_LIMIT_PER_MINUTE", "20"))
# 登录限流：同一「用户名 + 客户端 IP」每分钟最多尝试次数，防撞库/爆破；0 表示关闭
LOGIN_RATE_LIMIT_PER_MINUTE = int(os.getenv("LOGIN_RATE_LIMIT_PER_MINUTE", "20"))

_DUMMY_PASSWORD_HASH = get_password_hash("aimenu-dummy-password-for-timing")


def authenticate(username: str, password: str) -> Optional[dict]:
    """账号密码校验（bcrypt 校验是 CPU 密集操作，调用方需放线程池执行）"""
    user = get_user_by_username(username)
    if not user:
        # 用户不存在也跑一次 bcrypt，抹平「账号是否存在」的响应耗时差异
        verify_password(password, _DUMMY_PASSWORD_HASH)
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return user


async def get_current_user(token: str = Depends(oauth2_scheme)):
    """获取当前登录用户的依赖项 (强制登录)

    真正的数据库查询放到线程池执行，避免阻塞事件循环。
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="登录已过期，请重新登录",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception
    username: str = payload.get("sub")
    if username is None:
        raise credentials_exception
    user = await run_in_threadpool(get_user_by_username, username)
    if user is None:
        raise credentials_exception
    return user


async def get_optional_user(token: str = Depends(oauth2_scheme)) -> Optional[dict]:
    """获取当前登录用户的依赖项 (可选登录)"""
    try:
        payload = decode_access_token(token)
        if payload is None:
            return None
        username: str = payload.get("sub")
        if username is None:
            return None
        return await run_in_threadpool(get_user_by_username, username)
    except Exception as exc:
        logger.debug(f"可选登录态解析失败: {exc}")
        return None


async def get_chat_user(current_user: dict = Depends(get_current_user)) -> dict:
    """对话接口限流依赖

    按「用户 + 分钟」在 Redis 计数，避免单个账号刷爆 LLM 额度；
    Redis 不可用时放行，不阻断正常业务。
    """
    if CHAT_RATE_LIMIT_PER_MINUTE <= 0:
        return current_user
    window = int(time.time() // 60)
    key = f"aimenu:ratelimit:chat:{current_user['id']}:{window}"
    count = cache.incr_with_ttl(key, expire=70)
    if count is not None and count > CHAT_RATE_LIMIT_PER_MINUTE:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="客官问得有点急，请稍等片刻再试",
            headers={"Retry-After": "60"},
        )
    return current_user


def send_notification(order_no: str):
    """模拟异步发送订单通知（如邮件或微信推送）"""
    logger.info(f"开始为订单 {order_no} 准备通知推送...")
    time.sleep(3)  # 模拟耗时操作
    logger.info(f"订单 {order_no} 通知推送成功！")
