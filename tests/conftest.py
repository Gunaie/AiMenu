"""测试全局配置：注入最小可用的环境变量，保证无 .env 时也能导入模块。"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 仅在未配置时使用占位值，本地 .env 中的真实配置不会被覆盖
os.environ.setdefault("AMAP_API_KEY", "test-amap-key")
os.environ.setdefault("DELIVERY_RADIUS", "10000")
os.environ.setdefault("DEFAULT_PATH_MODE", "2")
os.environ.setdefault("MERCHANT_LONGITUDE", "114.397745")
os.environ.setdefault("MERCHANT_LATITUDE", "30.466352")
os.environ.setdefault("DASHSCOPE_API_KEY", "test-dashscope-key")
os.environ.setdefault("DASHSCOPE_MODEL_NAME", "qwen-plus")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
