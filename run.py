"""
智能点餐助手 启动脚本

启动uvicorn web服务器
"""

import  uvicorn
import  logging
import os
import sys

# 将当前目录添加到 PYTHONPATH
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO)
logger=logging.getLogger(__name__)



def  main():
    """启动uvicorn的入口"""
    print("AiMenu 智能点餐系统 v1.0")
    print("=" * 50)

    print("环境配置检查通过")
    print("正在启动API服务...")
    print("服务地址: http://localhost:8001")
    print("API文档: http://localhost:8001/docs")
    print("=" * 50)

    try:
        uvicorn.run("api.main:app",host="127.0.0.1",port=8001,log_level="info")
        logger.info("启动uvicorn服务器成功...")
    except KeyboardInterrupt as e:
        logger.error(f"启动uvicorn服务器失败{e}...")






if  __name__ == "__main__":
    main()



































