"""
高德地图工具模块

该模块提供高德地图的API调用功能，
用于获取地图上的地点信息、路线规划等

中间件开发（技术路线）
"""
import logging
from http.client import responses
from json import JSONDecodeError
from typing import Dict, Any, Optional, Literal, Union
from dataclasses import dataclass
from urllib3 import Retry
from requests.adapters import HTTPAdapter
import requests
import json
import os
from dotenv import load_dotenv
from tools.redis_tool import cache

load_dotenv()

# Python对数据处理有两种方式的验证
# 1.静态检查：（程序员以及编译器） 并且不会在运行的时候做约束：类型约束  typing
# 2.动态约束：（在程序运行的时候对数据做校验）运行时产生的：动态约束：pydantic库

PathInputModel = Literal["1", "2", "3"]  # 外部用
PathModel = Literal["walking", "electrobike", "driving"]  # 内部


# 路径模式转换工具
class PathModeConverter:
    """路径模式转换工具类"""

    # 映射关系  外部输入的路径模式 -> 内部使用的路径模式
    MODE_MAPPING = {
        "1": "walking",
        "2": "electrobike",
        "3": "driving",

    }

    @classmethod
    def to_mode(cls, mode_input: Union[PathInputModel]) -> PathModel:
        """将输入的模式转换为内部使用的模式"""

        if mode_input in cls.MODE_MAPPING:
            return cls.MODE_MAPPING[mode_input]
        else:
            raise ValueError(f"不支持的路径模式: {mode_input}，支持的模式: {list(cls.MODE_MAPPING.keys())}")


@dataclass  # 快速的对对象做一些赋值(重复工作少做）
class AmapConfig:
    AMAP_API_KEY: str = os.getenv("AMAP_API_KEY")
    RESTAURANT_ADDRESS: str = os.getenv("RESTAURANT_ADDRESS", "北京市昌平区宏福科技园")
    MERCHANT_LONGITUDE: str = os.getenv("MERCHANT_LONGITUDE")
    MERCHANT_LATITUDE: str = os.getenv("MERCHANT_LATITUDE")
    DELIVERY_RADIUS: int = int(os.getenv("DELIVERY_RADIUS"))
    DEFAULT_PATH_MODE = os.getenv("DEFAULT_PATH_MODE")

    def __post_init__(self):
        """自动调用"""
        if self.AMAP_API_KEY is None:
            raise ValueError("AMAP_API_KEY不存在")


# 创建配置实例
config = AmapConfig()


def create_session_with_retries():
    """创建带重试机制的requests会话"""

    # 1.创建session对象
    session = requests.Session()

    # 2.定义重试机制（规则）
    retry_strategy = Retry(
        total=3,  # 总共重试次数（不包括含第一次请求）
        backoff_factor=1,  # 退避因子(backoff_factor)*(2^重试次数-1)   第一次：1s 第二次：2 第三次：4s
        status_forcelist=[429, 500, 502, 503, 504, 505]  # # 429:请求过快  5xx系列的状态码都是服务内部出现各种问题
    )

    # 3.创建HttpAdapter(自定义Http请求的行为)
    adapter = HTTPAdapter(max_retries=retry_strategy)

    # 4.将适配器挂载到session中
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session


def safe_request(base_url: str, params: dict) -> Optional[Dict]:
    """安全的HTTP请求，处理重试和SSL降级"""
    
    # 1. 获取到带重试机制的session对象
    session = create_session_with_retries()
    
    # 增强请求头，模拟合法的 Web 服务请求
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://lbs.amap.com/"  # 模拟来自高德官方文档或合法站点的引用
    }
    
    try:
        # 2.发送请求
        response = session.get(url=base_url, params=params, headers=headers, timeout=10)
        
        # 高德 API 即使 IP 校验失败也会返回 200 OK，但内容包含错误码
        data = response.json()
        
        # 统一拦截并翻译高德原始错误码
        if data.get('status') == '0':
            info = data.get('info', '')
            infocode = data.get('infocode', '')
            
            friendly_messages = {
                'INVALID_USER_IP': "网络环境不稳定，小店暂时没法定位您的位置（IP验证失败），请检查网络后重试哦。",
                'ENGINE_RESPONSE_DATA_ERROR': "小二在查询路线时，地图系统开了个小差（引擎数据异常），建议您输入更精准的地标试试。",
                'USER_DAILY_QUERY_OVER_LIMIT': "今天查地址的客官实在太多啦，小店的地图流量用完啦，请明天再来吧。",
                'INVALID_USER_KEY': "小店的地图授权过期了，请客官联系老板修复（Key无效）。",
                'INVALID_PARAMS': "参数格式好像不太对，小二有点糊涂了，请客官再说一遍地点试试？"
            }
            
            error_msg = friendly_messages.get(info) or friendly_messages.get(infocode) or f"地图服务暂时不可用（{info}）"
            
            logging.error(f"高德 API 报错: {info} ({infocode})")
            return {
                "status": "0",
                "info": info,
                "infocode": infocode,
                "detail_message": error_msg
            }
        
        response.raise_for_status()
        return data
    except requests.exceptions.SSLError as e:
        # 2.发送请求
        try:
            http_request_url = base_url.replace("https://", "http://")
            response = session.get(url=http_request_url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"HTTP协议的请求发送失败,原因是{e}")
            raise requests.exceptions.RequestException(f"HTTP协议的请求发送失败,原因:{e}")

    except requests.exceptions.RequestException as e:
        logging.error(f"HTTPS协议的请求发送失败,原因是{e}")
        raise requests.exceptions.RequestException(f"HTTPS协议的请求发送失败,原因:{e}")

    except json.decoder.JSONDecodeError as e:
        logging.error("解析响应结果失败,原因:{e}")
        raise JSONDecodeError(f"反序列化失败，原因{e}")


def geocode_address(address: str) -> Dict[str, Any]:
    """
    地理编码功能 将地址转换为坐标

    Args:
       address: 用户输入要查询的地址
    Returns:
        Dict: 地理编码结果 包含格式化后的地址、经纬度
   """
    # 尝试从缓存获取
    cache_key = f"geocode:{address}"
    cached_data = cache.get(cache_key)
    if cached_data:
        logging.info(f"Redis 命中 [地理编码]: {address}")
        return cached_data

    try:
        # 1.构建请求URL
        request_url = "https://restapi.amap.com/v3/geocode/geo"

        # 2.构建请求param
        params = {
            "address": address,
            "key": config.AMAP_API_KEY,
            "city": "武汉"  # 限制在武汉市，防止搜到西藏或重庆去
        }

        # 3.发送请求,得到响应
        response = safe_request(request_url, params)

        # 4.根据响应，解析结果
        # 4.1 失败
        if response.get('status') != "1":
            return {
                "success": False,
                "message": response.get('detail_message') or response.get('info')
            }

        # 4.2 成功 # 提取地址编码信息列表
        if not response.get('geocodes'):
            return {
                "success": False,
                "message": "未能识别该地址，请客官输入更详细的地点（例如：光谷步行街、武汉大学正门）。"
            }
        geocodes = response['geocodes'][0]
        result = {
            "formatted_address": geocodes['formatted_address'],
            "location": geocodes['location'],
            "success": True,
        }
        
        # 写入缓存 (有效期 24 小时)
        cache.set(cache_key, result, expire=86400)
        return result

    except Exception as e:
        logging.error(f"调用高德地图进行地理位置编码失败,原因:{e}")
        raise e


def calculate_distance(origin_location: str, destination_location: str,
                       path_mode_input: PathInputModel or None) -> Dict[str, Any]:
    """
    不同的路径模式计算两个地点之间的距离和预计时间

    Args:
        origin_location: 起点经纬度
        destination_location:  终点经纬度
        path_mode_input:  路径模式，1:步行，2:骑行，3:驾车

    Returns:
        Dict: 路径结果，包含路径模式、距离、预计时间等

    """
    # 尝试从缓存获取
    cache_key = f"dist:{origin_location}:{destination_location}:{path_mode_input}"
    cached_data = cache.get(cache_key)
    if cached_data:
        logging.info(f"Redis 命中 [距离计算]: {origin_location} -> {destination_location}")
        return cached_data

    try:
        # 1.计算高德的API_KEY
        if config.AMAP_API_KEY is None:
            raise ValueError("AMAP_API_KEY 不存在")

        # 2.将外部路径模式转换为内部用的
        inner_model = PathModeConverter.to_mode(path_mode_input)

        # 3.构建请求的URL
        path_endpoint = {
            "walking": "https://restapi.amap.com/v5/direction/walking",
            "electrobike": "https://restapi.amap.com/v5/direction/electrobike",
            "driving": "https://restapi.amap.com/v5/direction/driving"

        }

        # 4.构建param
        params = {
            "key": config.AMAP_API_KEY,
            "origin": origin_location,
            "destination": destination_location
        }
        if inner_model == "driving":
            params["show_fields"] = "cost"

        # 5.发送请求获取响应
        response = safe_request(path_endpoint[inner_model], params)
        # 6.解析响应结果
        # 6.1 失败
        if response.get('status') != "1":
            return {
                "success": False,
                "message": response.get('detail_message') or response.get('info')
            }

        # 6.2
        path = response['route']["paths"][0]
        duration = path['duration'] if inner_model == "electrobike" else path['cost']['duration']
        result = {
            "distance": int(path["distance"]),  # 两点之间距离
            "duration": duration,  # 两点之间某一种路径规划下的时间
            "success": True  # 状态
        }
        
        # 写入缓存 (有效期 1 小时)
        cache.set(cache_key, result, expire=3600)
        return result
    except Exception as e:
        logging.error(f"调用高德地图进行路径规划失败,原因:{e}")
        raise e


def get_input_tips(keywords: str) -> Dict[str, Any]:
    """获取地址输入提示

    Args:
        keywords: 输入的关键字
    Returns:
        提示结果列表
    """
    # 尝试从缓存获取
    cache_key = f"tips:{keywords}"
    cached_data = cache.get(cache_key)
    if cached_data:
        logging.info(f"Redis 命中 [地址提示]: {keywords}")
        return cached_data

    try:
        request_url = "https://restapi.amap.com/v3/assistant/inputtips"
        params = {
            "key": config.AMAP_API_KEY,
            "keywords": keywords,
            # 暂时移除 location 参数以规避“无效IP”报错，高德 API 在没有 location 时会进行全国搜索
            # "location": f"{config.MERCHANT_LONGITUDE},{config.MERCHANT_LATITUDE}",
            "datatype": "all"
        }
        response = safe_request(request_url, params)

        if response.get('status') != "1":
            return {
                "success": False, 
                "message": response.get('detail_message') or response.get('info')
            }

        # 3.成功提示
        tips = []
        for tip in response.get('tips', []):
            # 兼容处理：高德 API 返回的 location 可能是空列表 [] 或 字符串
            loc = tip.get('location')
            if isinstance(loc, str) and loc:
                tips.append({
                    "name": tip.get('name'),
                    "district": tip.get('district'),
                    "address": tip.get('address'),
                    "location": loc
                })
            elif isinstance(loc, list) and len(loc) > 0:
                # 某些情况下返回的是坐标数组
                tips.append({
                    "name": tip.get('name'),
                    "district": tip.get('district'),
                    "address": tip.get('address'),
                    "location": f"{loc[0]},{loc[1]}"
                })
            else:
                # 即使没有经纬度，也返回给前端展示，只是无法直接校验
                tips.append({
                    "name": tip.get('name'),
                    "district": tip.get('district'),
                    "address": tip.get('address'),
                    "location": None
                })

        result = {
            "success": True,
            "tips": tips
        }
        # 写入缓存 (有效期 1 小时)
        cache.set(cache_key, result, expire=3600)
        return result
    except Exception as e:
        logging.error(f"获取输入提示失败,原因:{e}")
        return {"success": False, "message": str(e)}


def check_delivery_range(address: str, path_mode_input: PathInputModel = None) -> Dict[str, Any]:
    """检查地址是否在配送范围内

    Args:
        address: 用户输入的地址

        path_mode_input: 路径模式，支持 "1"(walking), "2"(electrobike), "3"(driving)。如果为None则使用配置的默认模式

    Returns:
          包含检查结果的 Dict 对象
    """
    try:
        # 1.获取终点的坐标
        geocode_result = geocode_address(address)

        if not geocode_result['success']:
            return {
                "status": "fail",
                "message": geocode_result['message']
            }

        # 2.调用距离函数
        # 2.1 起点坐标（餐厅地址）拼接经纬度
        origin_location = f"{config.MERCHANT_LONGITUDE},{config.MERCHANT_LATITUDE}"
        calculate_distance_result = calculate_distance(origin_location, geocode_result['location'],
                                                       path_mode_input=path_mode_input or config.DEFAULT_PATH_MODE)  # or 运算符的使用 返回第一个真值或者最后一个假值

        if not calculate_distance_result['success']:
            return {
                "status": "fail",
                "message": calculate_distance_result['message']
            }

        # 两点之间的距离 时间 是否在配送范围之类  格式化的地址 message=[当前地址+之间距离+时间+在（不在）范围之内]
        distance = calculate_distance_result['distance']  # 米
        in_range = distance <= config.DELIVERY_RADIUS
        
        # 配送费计算逻辑 (阶梯式)
        # 1km内免费，1-3km收3元，3km以上每公里加2元
        delivery_fee = 0.0
        dist_km = distance / 1000
        if dist_km <= 1.0:
            delivery_fee = 0.0
        elif dist_km <= 3.0:
            delivery_fee = 3.0
        else:
            delivery_fee = 3.0 + (dist_km - 3.0) * 2.0
        
        return {
            "status": "success",
            "in_range": in_range,  # 是否在配送范围之内
            "distance": round(dist_km, 2),  # 距离公里
            "duration": int(calculate_distance_result['duration']),
            "delivery_fee": round(delivery_fee, 1), # 配送费
            "formatted_address": geocode_result['formatted_address'],
            "message": (
                f"配送地址：{geocode_result['formatted_address']}\n"
                f"配送距离：{dist_km:.2f}公里\n"
                f"配送费：￥{delivery_fee:.1f}\n"
                f"配送状态：{'在配送范围内' if in_range else '超出配送范围'}"
            )
        }
    except Exception as  e:
        logging.error(f"配送范围服务查询失败，原因:{e}")
        raise e



if __name__ == '__main__':
    pass

    # print(geocode_address(address="武汉大学"))  # formatted_address:湖北省武汉市武昌区武汉大学 'location': '114.364514,30.536243'
    # print(geocode_address(address="清华大学"))  # formatted_address:北京市海淀区清华大学 'location': '116.326936,40.003213'
    # print(geocode_address(address="宏福科技园"))  # formatted_address:北京市昌平区宏福科技园(郑平路) 'location': '116.365533,40.102488'
    # print(geocode_address(address="北京市昌平区温都水城"))  # formatted_address:北京市北京市昌平区温都水城 'location': '116.376890,40.100204'

    # print(calculate_distance(origin_location="116.365533,40.102488",destination_location="116.376890,40.100204"))
    # print(calculate_distance(origin_location="116.365533,40.102488",destination_location="116.326936,40.003213"))
    # print(calculate_distance(origin_location="116.365533,40.102488",destination_location="114.364514,30.536243",path_mode_input="1"))
    # print(calculate_distance(origin_location="114.397745,30.466352",destination_location="114.364514,30.536243"))

    # 不同模式的使用
    pass
    # test_address = "北京市昌平区温都水城"  # 测试地址
    test_address = "海淀区清华大学"  # 测试地址
    print("\n=== 测试不同路径模式 ===")
    # 测试步行模式 (1)
    print("\n1. 步行模式测试:")
    result1 = check_delivery_range(test_address, "1")
    minutes = result1['duration'] // 60
    seconds = result1['duration'] % 60
    print(f"步行模式距离: {result1['distance']}公里 时间: {result1['duration']}秒 ({minutes}分{round(seconds, 2)}秒)")
    print(f"是否在配送范围内: {result1['message']}")

    # # 测试骑行模式 (2)
    print("\n2. 骑行模式测试:")
    result2 = check_delivery_range(test_address, "2")
    minutes = result1['duration'] // 60
    seconds = result1['duration'] % 60
    print(f"步行模式距离: {result2['distance']}公里 时间: {result2['duration']}秒 ({minutes}分{round(seconds, 2)}秒)")
    print(f"是否在配送范围内: {result2['message']}")
    #
    # # 测试驾车模式 (3)
    print("\n3. 驾车模式测试:")
    result3 = check_delivery_range(test_address, "3")
    minutes = result3['duration'] // 60
    seconds = result3['duration'] % 60
    print(f"步行模式距离: {result3['distance']}公里 时间: {result3['duration']}秒 ({minutes}分{round(seconds, 2)}秒)")
    print(f"是否在配送范围内: {result3['message']}")





