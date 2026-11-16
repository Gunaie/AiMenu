import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
import bcrypt
from dotenv import load_dotenv

# 以 .env 为准：避免系统中残留的旧 Key 覆盖项目配置
load_dotenv(override=True)

logger = logging.getLogger(__name__)

# JWT 配置
_config_secret = os.getenv("JWT_SECRET_KEY")
if not _config_secret or _config_secret in ("please-change-me", "aimenu_secret_key_2024_trae"):
    # 没有配置（或仍是示例值）时生成进程级随机密钥：宁可重启后旧令牌失效，
    # 也不能用仓库里公开的固定字符串签发票据（否则任何人都能伪造登录态）。
    _config_secret = secrets.token_urlsafe(48)
    logger.warning("JWT_SECRET_KEY 未配置或仍为示例值，已生成本次进程的随机密钥；"
                   "生产环境请在 .env 中设置随机长字符串，否则重启后令牌会全部失效")
SECRET_KEY = _config_secret
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 令牌有效期 24 小时

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    try:
        # bcrypt.checkpw 要求 bytes
        return bcrypt.checkpw(
            plain_password.encode('utf-8'), 
            hashed_password.encode('utf-8')
        )
    except (ValueError, TypeError):
        return False

def get_password_hash(password: str) -> str:
    """密码哈希加密"""
    # bcrypt.hashpw 要求 bytes，并返回 bytes
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """创建 JWT 访问令牌"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "iat": datetime.utcnow()})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str):
    """解析 JWT 令牌"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
