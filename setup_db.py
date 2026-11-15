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

def setup_menu_items():
    """初始化 12 道工业级菜品数据"""
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
            db.cursor.execute(create_table_sql)
            
            # 2. 准备 12 道菜品数据
            dishes = [
                ("东坡肉", 68.00, "经典浙菜，精选五花肉长时间炖煮，色泽红亮，肥而不腻，入口即化。", "热炒", 0, "咸甜", "五花肉、绍兴黄酒、冰糖", "炖", False, "肉类"),
                ("佛跳墙", 288.00, "闽菜之王，汇集鲍鱼、海参、鱼翅、花胶等名贵食材，汤浓味厚，极具滋补功效。", "汤品", 0, "鲜香", "鲍鱼、海参、花胶、瑶柱", "煨", False, "海鲜"),
                ("剁椒鱼头", 88.00, "湘菜经典，选用新鲜雄鱼头，覆盖秘制剁椒清蒸而成，肉质细嫩，鲜辣过瘾。", "热炒", 3, "鲜辣", "雄鱼头、湖南剁椒、姜蒜", "蒸", False, "鱼类"),
                ("回锅肉", 45.00, "川菜之魂，五花肉先煮后炒，搭配青蒜和豆瓣酱，酱香浓郁，下饭神菜。", "川菜", 2, "酱香微辣", "五花肉、青蒜、郫县豆瓣", "炒", False, "肉类"),
                ("地三鲜", 32.00, "东北家常名菜，选用土豆、茄子、青椒三种地里新鲜食材，咸鲜适口，老少皆宜。", "热炒", 0, "咸鲜", "土豆、茄子、青椒", "炒", True, "无"),
                ("宫保鸡丁", 38.00, "经典川菜，鸡丁滑嫩，花生米酥脆，口味酸甜微辣，层次分明。", "川菜", 1, "糊辣荔枝味", "鸡胸肉、花生米、干辣椒", "炒", False, "禽类、花生"),
                ("广式肠粉", 15.00, "广东特色传统早餐，粉皮晶莹剔透，嫩滑细腻，淋上特制豉油，清爽可口。", "主食", 0, "清淡咸鲜", "大米浆、猪肉末、鸡蛋", "蒸", False, "蛋类"),
                ("清蒸鲈鱼", 78.00, "选用新鲜鲈鱼，仅用葱姜清蒸，最大程度保留鱼肉的鲜美与营养，口感极佳。", "热炒", 0, "鲜美", "新鲜鲈鱼、葱、姜、豉油", "蒸", False, "鱼类"),
                ("番茄炒蛋", 22.00, "国民级家常菜，酸甜可口，汤汁拌饭尤为绝妙，是每个家庭餐桌的常客。", "热炒", 0, "酸甜", "鸡蛋、番茄、青椒", "炒", True, "蛋类"),
                ("鱼香肉丝", 36.00, "经典川菜，虽无鱼肉却有鱼香味，口味咸甜酸辣兼具，肉丝滑嫩，笋丝脆爽。", "川菜", 1, "鱼香味", "猪里脊肉、木耳、笋丝", "炒", False, "肉类"),
                ("麻婆豆腐", 28.00, "川菜名片，豆腐嫩滑入味，集麻、辣、烫、香、酥、嫩、鲜七味于一体。", "川菜", 3, "麻辣", "嫩豆腐、牛肉末、花椒面", "烩", False, "肉类、豆制品"),
                ("龙井虾仁", 98.00, "杭帮菜代表，选用清明前后的龙井新茶与鲜活虾仁烹制，虾肉玉白，茶香四溢。", "热炒", 0, "清香鲜嫩", "新鲜虾仁、龙井茶叶", "炒", False, "虾类")
            ]
            
            # 3. 插入数据 (如果不存在)
            insert_sql = """
            INSERT IGNORE INTO menu_items 
            (dish_name, price, description, category, spice_level, flavor, main_ingredients, cooking_method, is_vegetarian, allergens)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
