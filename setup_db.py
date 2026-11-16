import json
import os
import mysql.connector
from dotenv import load_dotenv
import logging
from tools.db_tool import DataBaseConnection
from tools.chroma_tool import chroma_db

# 以 .env 为准：避免系统中残留的旧配置覆盖项目配置
load_dotenv(override=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MENU_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "menu_items.json")

def load_menu_items(path: str = MENU_DATA_PATH) -> list:
    """从 data/menu_items.json 读取菜品数据并转换为插入用的元组列表"""
    with open(path, "r", encoding="utf-8") as fp:
        items = json.load(fp)

    dishes = []
    for item in items:
        dishes.append((
            item["dish_name"],
            item["price"],
            item["description"],
            item["category"],
            int(item["spice_level"]),
            item["flavor"],
            item["main_ingredients"],
            item["cooking_method"],
            bool(item["is_vegetarian"]),
            item.get("allergens") or "无",
        ))
    logger.info(f"从 {path} 读取到 {len(dishes)} 道菜品")
    return dishes

def setup_menu_items():
    """初始化菜品数据（数据源：data/menu_items.json）"""
    try:
        with DataBaseConnection() as db:
            # 1. 创建 menu_items 表
            create_table_sql = """
            CREATE TABLE IF NOT EXISTS menu_items (
                id INT AUTO_INCREMENT PRIMARY KEY,
                dish_name VARCHAR(100) NOT NULL UNIQUE,
                price DECIMAL(10, 2) NOT NULL,
                description TEXT,
                category VARCHAR(50),
                spice_level INT DEFAULT 0, -- 0:不辣, 1:微辣, 2:中辣, 3:重辣
                flavor VARCHAR(100),
                main_ingredients TEXT,
                cooking_method VARCHAR(100),
                is_vegetarian BOOLEAN DEFAULT FALSE,
                allergens VARCHAR(255),
                is_available BOOLEAN DEFAULT TRUE,
                stock INT DEFAULT NULL, -- NULL 表示不限量；整数表示剩余可售份数
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
            db.cursor.execute(create_table_sql)

            # 平滑升级：老库缺 stock 列时补上（默认 NULL = 不限量，不影响存量菜品）
            from tools.db_tool import _ensure_column
            if _ensure_column(db.cursor, "menu_items", "stock",
                              "stock INT DEFAULT NULL AFTER is_available"):
                logger.info("平滑升级：已为 menu_items 表增加 stock 列")
                db.commit()
            
            # 2. 读取菜品数据
            dishes = load_menu_items()

            # 3. 插入数据（已存在则更新，保证可重复执行）
            insert_sql = """
            INSERT INTO menu_items 
            (dish_name, price, description, category, spice_level, flavor, main_ingredients, cooking_method, is_vegetarian, allergens)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                price = VALUES(price),
                description = VALUES(description),
                category = VALUES(category),
                spice_level = VALUES(spice_level),
                flavor = VALUES(flavor),
                main_ingredients = VALUES(main_ingredients),
                cooking_method = VALUES(cooking_method),
                is_vegetarian = VALUES(is_vegetarian),
                allergens = VALUES(allergens)
            """
            db.cursor.executemany(insert_sql, dishes)
            db.commit()
            logger.info(f"成功初始化 {len(dishes)} 道菜品数据")
            
            # 4. 同步到 ChromaDB
            logger.info("开始同步向量数据库...")
            if chroma_db.upsert_menu_data(clear_existing=True):
                logger.info("向量数据库同步成功")
            else:
                logger.error("向量数据库同步失败")
                
    except Exception as e:
        logger.error(f"初始化数据库失败: {e}")

def setup_default_user():
    """创建默认测试账号"""
    try:
        from tools.auth_tool import get_password_hash
        from tools.db_tool import get_user_by_username, create_user
        
        username = "admin"
        if not get_user_by_username(username):
            user_data = {
                "username": username,
                "password_hash": get_password_hash("password123"),
                "nickname": "店小二",
                "phone": "13800138000"
            }
            if create_user(user_data):
                logger.info(f"默认用户 {username} 创建成功 (密码: password123)")
        else:
            logger.info(f"默认用户 {username} 已存在")
    except Exception as e:
        logger.error(f"创建默认用户失败: {e}")

if __name__ == "__main__":
    from tools.db_tool import init_db
    init_db()  # 先初始化 users 和 orders 表
    setup_menu_items()
    setup_default_user()
