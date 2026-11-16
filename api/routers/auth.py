"""认证相关路由：注册、登录、当前用户信息。"""
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import OAuth2PasswordRequestForm

from api import deps
from api.schemas import ActionResponse, Token, UserRegister
from tools.auth_tool import create_access_token, get_password_hash
from tools.db_tool import create_user

router = APIRouter(tags=["认证"])


@router.post("/auth/register", response_model=ActionResponse)
async def register(user_in: UserRegister):
    """用户注册"""
    existing_user = await run_in_threadpool(deps.get_user_by_username, user_in.username)
    if existing_user:
        return ActionResponse(success=False, message="用户名已存在")

    user_data = {
        "username": user_in.username,
        "password_hash": await run_in_threadpool(get_password_hash, user_in.password),
        "nickname": user_in.nickname or user_in.username,
        "phone": user_in.phone,
    }

    if await run_in_threadpool(create_user, user_data):
        return ActionResponse(success=True, message="注册成功，请登录")
    return ActionResponse(success=False, message="注册失败，请稍后再试")


@router.post("/auth/login", response_model=Token)
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    """用户登录（按「用户名 + IP」限流，防撞库与暴力破解）"""
    if deps.LOGIN_RATE_LIMIT_PER_MINUTE > 0:
        window = int(time.time() // 60)
        client_ip = request.client.host if request.client else "unknown"
        key = f"aimenu:ratelimit:login:{form_data.username}:{client_ip}:{window}"
        count = deps.cache.incr_with_ttl(key, expire=70)
        if count is not None and count > deps.LOGIN_RATE_LIMIT_PER_MINUTE:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="登录尝试过于频繁，请稍后再试",
                headers={"Retry-After": "60"},
            )

    user = await run_in_threadpool(deps.authenticate, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(data={"sub": user["username"]})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "username": user["username"],
        "nickname": user["nickname"] or user["username"],
    }


@router.get("/auth/me")
async def get_me(current_user: dict = Depends(deps.get_current_user)):
    """获取当前用户信息"""
    return {
        "success": True,
        "username": current_user["username"],
        "nickname": current_user["nickname"],
        "phone": current_user["phone"],
    }
