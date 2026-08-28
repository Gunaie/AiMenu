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
