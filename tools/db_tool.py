"""
数据库查询工具模块

该模块提供MySQL数据库连接和查询功能，
专门用于查询menu数据库中的menu_items表的全部信息
"""
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
import mysql.connector
import json
from datetime import datetime
from dotenv import load_dotenv
from typing import Dict, Any, List, Optional
from tools.redis_tool import cache

# 以 .env 为准：避免系统中残留的旧配置覆盖项目配置
load_dotenv(override=True)


class DataBaseConnection:
    """数据库管理相关的操作"""

    def __init__(self):
        """初始化数据库配置信息"""
        self.host = os.getenv("MYSQL_HOST", "localhost")
        self.port = os.getenv("MYSQL_PORT", "3306")
        self.user = os.getenv("MYSQL_USER_NAME", "root")
        self.password = os.getenv("MYSQL_USER_PASSWORD", "root")
        self.db_name = os.getenv("MYSQL_DB_NAME", "menu")
        self.connection = None
        self.cursor = None

    def initialize_connection(self) -> bool:
        """初始化数据库连接对象和游标对象"""
        try:
            self.connection = mysql.connector.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.db_name,
                charset="utf8mb4")
            self.cursor = self.connection.cursor(dictionary=True)
            return True
        except mysql.connector.Error as e:
            logger.error(f"数据库{self.db_name}连接失败: {e}")
            return False

    def disconnect_connection(self) -> bool:
        """关闭数据库资源"""
        try:
            if self.cursor:
                self.cursor.close()
            if self.connection and self.connection.is_connected():
                self.connection.close()
            return True
        except mysql.connector.Error as e:
            logger.error(f"关闭数据库资源失败: {e}")
            return False

    def commit(self):
        """提交事务"""
        if self.connection:
            self.connection.commit()

    def __enter__(self):
        if self.initialize_connection():
            return self
        else:
            raise Exception("数据库初始化失败")

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect_connection()
        return False


def init_db():
    """初始化数据库表结构"""
    try:
        with DataBaseConnection() as db:
            # 1. 创建用户表
            create_users_sql = """
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(50) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                nickname VARCHAR(50),
                phone VARCHAR(20),
                avatar VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
            db.cursor.execute(create_users_sql)
            
            # 2. 创建订单表 (增加 user_id 外键)
            create_orders_sql = """
            CREATE TABLE IF NOT EXISTS orders (
                id INT AUTO_INCREMENT PRIMARY KEY,
                order_no VARCHAR(50) NOT NULL UNIQUE,
                user_id INT,
                total_amount DECIMAL(10, 2) NOT NULL,
                delivery_fee DECIMAL(10, 2) NOT NULL,
                actual_amount DECIMAL(10, 2) NOT NULL,
                delivery_address TEXT NOT NULL,
                order_status VARCHAR(20) DEFAULT 'pending',
                payment_status VARCHAR(20) DEFAULT 'unpaid',
                items_detail JSON NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
            db.cursor.execute(create_orders_sql)
            
            # 检查 orders 表是否已有 user_id 列，没有则添加 (用于平滑升级)
            db.cursor.execute("SHOW COLUMNS FROM orders LIKE 'user_id'")
            if not db.cursor.fetchone():
                db.cursor.execute("ALTER TABLE orders ADD COLUMN user_id INT AFTER order_no")
                db.cursor.execute("ALTER TABLE orders ADD FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL")
                logger.info("平滑升级：已为 orders 表增加 user_id 列")

            db.commit()
            logger.info("数据库表结构初始化/检查成功")
    except Exception as e:
        logger.error(f"初始化数据库表失败: {e}")


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """根据用户名查询用户"""
    try:
        with DataBaseConnection() as db:
            sql = "SELECT * FROM users WHERE username = %s"
            db.cursor.execute(sql, (username,))
            return db.cursor.fetchone()
    except Exception as e:
        logger.error(f"查询用户失败: {e}")
        return None


def create_user(user_data: Dict[str, Any]) -> bool:
    """创建新用户"""
    try:
        with DataBaseConnection() as db:
            sql = "INSERT INTO users (username, password_hash, nickname, phone) VALUES (%s, %s, %s, %s)"
            db.cursor.execute(sql, (
                user_data['username'],
                user_data['password_hash'],
                user_data.get('nickname', user_data['username']),
                user_data.get('phone')
            ))
            db.commit()
            return True
    except Exception as e:
        logger.error(f"创建用户失败: {e}")
        return False


def create_order(order_data: Dict[str, Any]) -> Optional[str]:
    """创建新订单"""
    try:
        # 生成订单号
        import time
        import random
        order_no = f"ORD{int(time.time())}{random.randint(1000, 9999)}"

        with DataBaseConnection() as db:
            insert_sql = """
            INSERT INTO orders 
            (order_no, user_id, total_amount, delivery_fee, actual_amount, delivery_address, items_detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            db.cursor.execute(insert_sql, (
                order_no,
                order_data.get('user_id'),
                order_data['total_amount'],
                order_data['delivery_fee'],
                order_data['actual_amount'],
                order_data['delivery_address'],
                json.dumps(order_data['items_detail'], ensure_ascii=False)
            ))
            db.commit()
            logger.info(f"订单 {order_no} 创建成功")
            return order_no
    except Exception as e:
        logger.error(f"创建订单失败: {e}")
        return None


def update_order_status(order_no: str, status: str, payment_status: Optional[str] = None) -> bool:
    """更新订单状态"""
    try:
        with DataBaseConnection() as db:
            if payment_status:
                update_sql = "UPDATE orders SET order_status = %s, payment_status = %s WHERE order_no = %s"
                db.cursor.execute(update_sql, (status, payment_status, order_no))
            else:
                update_sql = "UPDATE orders SET order_status = %s WHERE order_no = %s"
                db.cursor.execute(update_sql, (status, order_no))
            db.commit()
            return True
    except Exception as e:
        logger.error(f"更新订单状态失败: {e}")
        return False


def get_order(order_no: str) -> Optional[Dict[str, Any]]:
    """查询订单详情"""
    try:
        with DataBaseConnection() as db:
            query_sql = "SELECT * FROM orders WHERE order_no = %s"
            db.cursor.execute(query_sql, (order_no,))
            order = db.cursor.fetchone()
            if order and 'items_detail' in order:
                order['items_detail'] = json.loads(order['items_detail'])
            return order
    except Exception as e:
        logger.error(f"查询订单失败: {e}")
        return None


def get_all_orders(user_id: Optional[int] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """查询订单列表（可选按用户过滤）"""
    try:
        with DataBaseConnection() as db:
            if user_id:
                query_sql = "SELECT * FROM orders WHERE user_id = %s ORDER BY created_at DESC LIMIT %s"
                db.cursor.execute(query_sql, (user_id, limit))
            else:
                query_sql = "SELECT * FROM orders ORDER BY created_at DESC LIMIT %s"
                db.cursor.execute(query_sql, (limit,))
            
            orders = db.cursor.fetchall()
            for order in orders:
                if 'items_detail' in order:
                    order['items_detail'] = json.loads(order['items_detail'])
            return orders
    except Exception as e:
        logger.error(f"查询订单列表失败: {e}")
        return []


def get_all_menu_items() -> str:
    """
    作用：查询menu_items中所有的菜品信息，并且对每一条菜品信息用\n连接，最终形成一个大字符串（向量化）
    :return:str
    """

    try:
        with DataBaseConnection() as db:
            # 1.定义SQL语句
            query_sql = """
              SELECT 
                    id, dish_name, price, description, category, 
                    spice_level, flavor, main_ingredients, cooking_method, 
                    is_vegetarian, allergens, is_available
                FROM menu_items 
                WHERE is_available = 1
                ORDER BY category, dish_name
            """
            # 2.执行SQL语句
            db.cursor.execute(query_sql)
            menu_items = db.cursor.fetchall()

            # 3.处理结果
            if not menu_items:
                logger.info("当前无可用的菜品信息")
                return "当前无可用的菜品信息"

            menu_items_strings = []
            for item in menu_items:
                # 1.格式化辣度级别
                spice_level_mapping = {0: "不辣", 1: "微辣", 2: "中辣", 3: "重辣"}
                format_spice_level = spice_level_mapping.get(item.get('spice_level'), "暂无辣度级别")
                # 2.格式化是否是素食
                format_is_vegetarian = "是" if item.get('is_vegetarian') else "否"
                # 3.格式化菜品描述
                format_description = item.get('description') if item.get('description', '').strip() else "暂无描述"
                # 4.格式化主要食材
                format_main_ingredients = item.get('main_ingredients') if item.get('main_ingredients',
                                                                                   '').strip() else "暂无主要食材"
                # 5.格式化过敏源
                format_allergens = item.get('allergens') if item.get('allergens', '').strip() else "暂无过敏源"

                # 拼接菜品结构为字符串
                menu_item_string = f"菜品ID:{item['id']}|菜品名称:{item['dish_name']}|价格:¥{item['price']:.2f}|菜品描述:{format_description}|分类:{item['category']}|辣度:{format_spice_level}|口味:{item['flavor']}|主要食材:{format_main_ingredients}|烹饪方法:{item['cooking_method']}|素食:{format_is_vegetarian}|过敏原:{format_allergens}"
                menu_items_strings.append(menu_item_string)

            logger.info(f"查询菜品信息成功，且菜品个数:{len(menu_items_strings)}")

            # 4.返回处理后的结果
            return "\n".join(menu_items_strings)

    except Exception as e:
        logger.error(f"查询所有菜品信息字符串结果失败: {e}")
        return "查询菜品信息失败"



def get_menu_item_by_id(dish_id: int) -> Optional[Dict[str, Any]]:
    """按 ID 查询单个菜品（用于下单时服务端重算价格）"""
    try:
        with DataBaseConnection() as db:
            db.cursor.execute(
                "SELECT id, dish_name, price, is_available FROM menu_items WHERE id = %s",
                (dish_id,)
            )
            return db.cursor.fetchone()
    except Exception as e:
        logger.error(f"查询菜品失败: {e}")
        return None


def get_menu_items() -> List[Dict[str, Any]]:
    """
    前端菜品区域展示
    :return: 字典列表（菜品列表）
    """
    # 尝试从缓存获取
    cache_key = "menu:all_items"
    cached_data = cache.get(cache_key)
    if cached_data:
        logger.info("Redis 命中 [菜单列表]")
        return cached_data

    try:
        with DataBaseConnection() as db:
            # 1.定义SQL语句
            query_sql = """
                            SELECT 
                            id, dish_name, price, description, category, 
                            spice_level, flavor, main_ingredients, cooking_method, 
                            is_vegetarian, allergens, is_available
                            FROM menu_items 
                            WHERE is_available = 1
                            ORDER BY category, dish_name
                            """

            # 2.执行SQL语句
            db.cursor.execute(query_sql)
            # 3.获取结果
            menu_items = db.cursor.fetchall()

            # 4.处理结果并返回
            if not  menu_items:
                logger.error("暂无可用菜品信息")
                return  []


            menu_items_list=[]

            for  item  in menu_items:
                # 辣度等级转换
                spice_levels = {0: "不辣", 1: "微辣", 2: "中辣", 3: "重辣"}
                spice_text = spice_levels.get(item['spice_level'], "未知")

                # 处理数据
                processed_item = {
                    "id": item['id'],
                    "dish_name": item['dish_name'],
                    "price": float(item['price']),
                    "formatted_price": f"¥{item['price']:.2f}",
                    "description": item['description'] or "暂无描述",
                    "category": item['category'],
                    "spice_level": item['spice_level'],
                    "spice_text": spice_text,
                    "flavor": item['flavor'] or "暂无口味",
                    "main_ingredients": item['main_ingredients'] or "暂无主要食材",
                    "cooking_method": item['cooking_method'] or "暂无烹饪方法",
                    "is_vegetarian": bool(item['is_vegetarian']),
                    "vegetarian_text": "是" if item['is_vegetarian'] else "否",
                    "allergens": item['allergens'] if item['allergens'] and item['allergens'].strip() else "暂无过敏原",
                    "is_available": bool(item['is_available']),
                    "image_url": f"/images/{item['dish_name']}.png"
                }
                menu_items_list.append(processed_item)

            logger.info(f"查询菜品结构化列表信息成功，且菜品个数:{len(menu_items_list)}")
            # 写入缓存 (有效期 10 分钟，菜单变化不频繁)
            cache.set(cache_key, menu_items_list, expire=600)
            return menu_items_list

    except Exception as e:
        logger.error(f"查询菜品结构化列表信息失败:{e}")
        return []


def update_menu_item(dish_id: int, update_data: Dict[str, Any]) -> bool:
    """
    更新菜品信息，并应用 Redis 缓存“延迟双删”策略保证一致性
    :param dish_id: 菜品ID
    :param update_data: 需要更新的字段字典
    :return: 是否更新成功
    """
    cache_key = "menu:all_items"
    
    try:
        # 1. 第一删：先删除缓存
        cache.delete(cache_key)
        logger.info(f"一致性策略 - 第一删成功: {cache_key}")

        # 2. 更新数据库
        with DataBaseConnection() as db:
            if not update_data:
                return False
            
            set_clause = ", ".join([f"{k} = %s" for k in update_data.keys()])
            values = list(update_data.values())
            values.append(dish_id)
            
            sql = f"UPDATE menu_items SET {set_clause} WHERE id = %s"
            db.cursor.execute(sql, tuple(values))
            db.commit()
            
            if db.cursor.rowcount == 0:
                logger.warning(f"未找到 ID 为 {dish_id} 的菜品，更新失败")
                return False

        logger.info(f"数据库更新成功: dish_id={dish_id}")

        # 3. 延迟双删：休眠一小段时间后再次删除缓存
        # 在高并发环境下，这能防止在数据库更新期间有其他请求将旧数据重新载入缓存
        import time
        import threading

        def delayed_delete():
            time.sleep(2)  # 延迟 2 秒
            cache.delete(cache_key)
            logger.info(f"一致性策略 - 第二删成功: {cache_key}")

        # 异步执行第二删，避免阻塞主流程
        threading.Thread(target=delayed_delete).start()

        return True

    except Exception as e:
        logger.error(f"更新菜品失败，原因: {e}")
        return False


def test_connection():
    with DataBaseConnection() as db:

        # 业务逻辑（定义SQL 执行SQL）
        db.cursor.execute("select 1")
        test_res = db.cursor.fetchone()  # 获取SQL语句的结果
        if test_res:
            print(f"测试数据库连接成功，且查询结果是:{test_res}")
        else:
            print(f"测试数据库连接失败")


if __name__ == "__main__":
    # print("\n1.测试数据库连接可用性:")
    # test_connection()

    # print("\n2.测试所有菜品信息的字符串:向量化的前置")
    # menu_item_str = get_all_menu_items()
    #
    # print(menu_item_str)

    print("\n3.测试所有菜品信息的列表结构:前端展示")
    menu_item_list=get_menu_items()

    for  index,item in enumerate(menu_item_list,1):
        print(f"当前是第{index}菜品，对应的结构:{item}")






