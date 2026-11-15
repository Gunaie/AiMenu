import redis
import json
import logging
from typing import Optional, Any
import os
from dotenv import load_dotenv

# 以 .env 为准：避免系统中残留的旧配置覆盖项目配置
load_dotenv(override=True)

# 日志配置
logger = logging.getLogger(__name__)

class RedisCache:
    def __init__(self):
        host = os.getenv("REDIS_HOST", "localhost")
        port = int(os.getenv("REDIS_PORT", 6379))
        db = int(os.getenv("REDIS_DB", 0))
        password = os.getenv("REDIS_PASSWORD", None)
        
        try:
            self.client = redis.Redis(
                host=host,
                port=port,
                db=db,
                password=password,
                decode_responses=True,
                socket_timeout=2
            )
            # 测试连接
            self.client.ping()
            logger.info(f"Redis 连接成功: {host}:{port}")
        except Exception as e:
            logger.error(f"Redis 连接失败: {e}")
            self.client = None

    def get(self, key: str) -> Optional[Any]:
        if not self.client:
            return None
        try:
            data = self.client.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            logger.error(f"Redis GET 失败 [{key}]: {e}")
            return None

    def set(self, key: str, value: Any, expire: int = 3600):
        if not self.client:
            return
        try:
            self.client.set(key, json.dumps(value, ensure_ascii=False), ex=expire)
        except Exception as e:
            logger.error(f"Redis SET 失败 [{key}]: {e}")

    def delete(self, key: str):
        if not self.client:
            return
        try:
            self.client.delete(key)
        except Exception as e:
            logger.error(f"Redis DELETE 失败 [{key}]: {e}")

    def incr_with_ttl(self, key: str, expire: int = 60) -> Optional[int]:
        """原子自增并设置过期时间，用于限流计数；Redis 不可用时返回 None（调用方放行）"""
        if not self.client:
            return None
        try:
            value = self.client.incr(key)
            if value == 1:
                self.client.expire(key, expire)
            return int(value)
        except Exception as e:
            logger.error(f"Redis INCR 失败 [{key}]: {e}")
            return None

# 全局单例
cache = RedisCache()
