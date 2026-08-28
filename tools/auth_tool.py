import os
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
import bcrypt
from dotenv import load_dotenv

load_dotenv()

# JWT 配置
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "aimenu_secret_key_2024_trae")
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
    except Exception:
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
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str):
    """解析 JWT 令牌"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
