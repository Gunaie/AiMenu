import axios from 'axios'

// 创建axios实例
const api = axios.create({
  baseURL: '/api',  // 使用代理路径
  // timeout: 10000,  // 移除超时限制
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
api.interceptors.request.use(
  config => {
    const token = localStorage.getItem('token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    console.log('API请求:', config.method.toUpperCase(), config.url, config.data)
    return config
  },
  error => {
    console.error('请求错误:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器
api.interceptors.response.use(
  response => {
    console.log('API响应:', response.status, response.data)
    return response.data
  },
  error => {
    console.error('响应错误:', error.response?.status, error.response?.data)
    if (error.response?.status === 401) {
      // Token 过期或未登录
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      // 触发全局事件或在页面中处理重定向
      window.dispatchEvent(new Event('unauthorized'))
    }
    
    if (error.response?.status === 500) {
      throw new Error('处理中遇到问题，请稍后再试')
    } else if (error.response?.status === 404) {
      throw new Error('请求的资源不存在')
    } else {
      throw new Error(error.response?.data?.detail || '请求处理中，请稍候...')
    }
  }
)

// 认证API
export const authAPI = {
  register: async (userData) => {
    return await api.post('/auth/register', userData)
  },
  login: async (username, password) => {
    // 使用 URLSearchParams 发送表单数据
    const params = new URLSearchParams()
    params.append('username', username)
    params.append('password', password)
    
    const response = await api.post('/auth/login', params, {
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded'
      }
    })
    
    if (response.access_token) {
      localStorage.setItem('token', response.access_token)
      localStorage.setItem('user', JSON.stringify({
        username: response.username,
        nickname: response.nickname
      }))
    }
    return response
  },
  getMe: async () => {
    return await api.get('/auth/me')
  },
  logout: () => {
    localStorage.removeItem('token')
    localStorage.removeItem('user')
  }
}

// 智能对话API
export const chatAPI = {
  // 发送聊天消息
  sendMessage: async (query, session_id = 'default') => {
    return await api.post('/chat', { query, session_id })
  },
  // 流式发送消息
  sendMessageStream: async (query, session_id = 'default', onChunk) => {
    const token = localStorage.getItem('token')
    const headers = {
      'Content-Type': 'application/json',
    }
    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }

    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: headers,
      body: JSON.stringify({ query, session_id }),
    });

    if (!response.ok) {
      throw new Error('网络响应错误');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const dataStr = line.substring(6);
            // console.log('收到流式数据块:', dataStr);
            const data = JSON.parse(dataStr);
            onChunk(data);
          } catch (e) {
            console.error('解析流式数据失败:', e, '原始数据:', line);
          }
        }
      }
    }
  }
}

// 配送查询API
export const deliveryAPI = {
  // 检查配送范围
  checkRange: async (address, travel_mode = 3) => {
    return await api.post('/delivery', { 
      address, 
      travel_mode 
    })
  },
  // 获取输入提示
  getTips: async (keywords) => {
    return await api.get('/delivery/tips', { params: { keywords } })
  }
}

// 菜品API
export const menuAPI = {
  // 获取菜品列表
  getMenuList: async () => {
    return await api.get('/menu/list')
  }
}

// 餐厅API
export const restaurantAPI = {
  // 获取餐厅信息
  getInfo: async () => {
    return await api.get('/restaurant/info')
  }
}

// 订单API
export const orderAPI = {
  // 提交订单；idempotencyKey 透传给后端，网络重试或重复点击不会生成第二笔订单
  submitOrder: async (orderData, idempotencyKey) => {
    const config = idempotencyKey ? { headers: { 'Idempotency-Key': idempotencyKey } } : undefined
    return await api.post('/order/submit', orderData, config)
  },
  // 模拟支付
  payOrder: async (orderNo) => {
    return await api.post(`/order/pay/${orderNo}`)
  },
  // 获取订单列表
  getOrderList: async (limit = 10) => {
    return await api.get('/order/list', { params: { limit } })
  }
}

// 健康检查API
export const healthAPI = {
  // 检查服务状态
  checkHealth: async () => {
    return await api.get('/healthy')
  }
}

export default api 