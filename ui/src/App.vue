<template>
  <div class="app-wrapper">
    <!-- 用户登录状态条 (顶置) -->
    <div class="user-status-bar">
      <div v-if="currentUser" class="user-logged-in">
        <el-dropdown trigger="click" @command="handleUserCommand">
          <div class="user-profile">
            <el-avatar :size="24" icon="UserFilled" />
            <span class="nickname">{{ currentUser.nickname }}</span>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="logout">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
      <div v-else class="user-guest">
        <el-button type="primary" link size="small" @click="showLogin = true">登录 / 注册</el-button>
      </div>
    </div>

    <el-container class="main-container" :class="{ 'is-blurred': !currentUser }">
      <!-- 侧边栏：菜品展示 -->
      <el-aside width="400px" class="menu-sidebar">
        <div class="sidebar-header">
          <div class="logo-wrapper">
            <div class="logo">
              <el-icon><Food /></el-icon>
              <span>{{ restaurantInfo.name || '智能点餐系统' }}</span>
            </div>
            <div class="restaurant-address" v-if="restaurantInfo.address">
              <el-icon><Location /></el-icon>
              <span>{{ restaurantInfo.address }}</span>
            </div>
          </div>
          <div class="header-actions">
            <el-button type="primary" link @click="openOrders" class="order-history-btn">
              <el-icon :size="20"><List /></el-icon>
              <span class="btn-text">我的订单</span>
            </el-button>
            <el-badge :value="cartCount" :hidden="cartCount === 0" class="cart-badge">
              <el-button type="primary" link @click="showCart = true">
                <el-icon size="20"><ShoppingCart /></el-icon>
              </el-button>
            </el-badge>
            <el-button
              type="primary"
              link
              @click="loadMenuItems"
              :loading="menuLoading"
            >
              <el-icon><Refresh /></el-icon>
            </el-button>
          </div>
        </div>

        <div class="menu-filter">
          <el-radio-group v-model="activeCategory" size="small">
            <el-radio-button v-for="cat in categories" :key="cat" :label="cat" />
          </el-radio-group>
        </div>

        <div class="menu-list-container">
          <div v-if="filteredMenu.length > 0" class="menu-list">
            <div
              v-for="item in filteredMenu"
              :key="item.id"
              class="menu-card"
              :class="{ 'is-recommended': highlightedItems.includes(item.id.toString()) }"
              :id="'menu-' + item.id"
            >
              <div class="menu-card-image" v-if="item.dish_name">
                <el-image 
                  :src="item.image_url || getDishImageUrl(item.dish_name)" 
                  fit="cover"
                  lazy
                >
                  <template #placeholder>
                    <div class="image-slot">加载中...</div>
                  </template>
                </el-image>
              </div>
              <div class="menu-card-header">
                <span class="dish-name">{{ item.dish_name }}</span>
                <span class="dish-price">¥{{ item.price }}</span>
              </div>
              <p class="dish-desc">{{ item.description }}</p>
              <div class="dish-tags">
                <el-tag size="small" effect="plain">{{ item.category }}</el-tag>
                <el-tag size="small" :type="getSpiceType(item.spice_level)" effect="light">
                  {{ item.spice_text }}
                </el-tag>
                <el-tag v-if="item.is_vegetarian" size="small" type="success" effect="light">素食</el-tag>
                <el-badge v-if="highlightedItems.includes(item.id.toString())" value="推荐" class="recommend-badge" />
              </div>
              <div class="menu-card-footer">
                <el-button type="primary" size="small" @click="addToCart(item)">加入购物车</el-button>
              </div>
            </div>
          </div>
          <el-empty v-else-if="!menuLoading" description="暂无相关菜品" />
          <div v-if="menuLoading" class="loading-state">
            <el-skeleton :rows="3" animated v-for="i in 3" :key="i" style="margin-bottom: 20px" />
          </div>
        </div>
      </el-aside>

      <el-container>
        <el-main class="chat-main">
          <!-- 配送状态横幅 -->
          <div class="delivery-status-banner" :class="{ 'is-active': deliveryResponse }">
            <div class="banner-content" @click="showDeliveryInput = true">
              <el-icon class="status-icon">
                <Location v-if="!deliveryResponse" />
                <CircleCheck v-else-if="deliveryResponse.in_range" />
                <Warning v-else />
              </el-icon>
              <div class="status-info">
                <template v-if="!deliveryResponse">
                  <span class="status-text">未设置配送地址</span>
                  <span class="status-hint">点击设置以确认配送范围</span>
                </template>
                <template v-else>
                  <span class="status-text">{{ deliveryResponse.formatted_address }}</span>
                  <span class="status-detail">
                    {{ deliveryResponse.in_range ? '在配送范围内' : '超出配送范围' }}
                    <template v-if="deliveryResponse.distance">
                      | {{ deliveryResponse.distance.toFixed(2) }}km | ￥{{ deliveryResponse.delivery_fee }}配送费 | 约{{ Math.floor(deliveryResponse.duration/60) }}分钟
                    </template>
                  </span>
                </template>
              </div>
              <el-button type="primary" link @click.stop="showDeliveryInput = true">
                {{ deliveryResponse ? '修改' : '去设置' }}
              </el-button>
            </div>
          </div>

          <!-- 对话窗口区域 -->
          <div class="chat-window" ref="chatWindow">
            <div class="welcome-msg" v-if="chatHistory.length === 0">
              <el-icon size="48" color="#409EFF"><ChatDotRound /></el-icon>
              <h2>欢迎使用智能点餐助手</h2>
              <p>为了给您提供更好的服务，请先设置配送地址</p>
              <div class="quick-queries">
                <el-button type="primary" round @click="showDeliveryInput = true">设置配送地址</el-button>
                <el-button round size="small" @click="chatQuery = '有什么不辣的推荐吗？'">不辣的菜品</el-button>
                <el-button round size="small" @click="chatQuery = '我想点一些川菜'">推荐川菜</el-button>
              </div>
            </div>

            <div v-for="(msg, index) in chatHistory" :key="index" :class="['message', msg.role]">
              <div class="avatar">
                <el-icon v-if="msg.role === 'assistant'"><Service /></el-icon>
                <el-icon v-else><User /></el-icon>
              </div>
              <div class="message-content-wrapper">
                <div class="message-content">
                  <div v-if="msg.role === 'assistant'">
                    <div v-if="msg.content === '' && chatLoading" class="loading-dots">
                      <span>.</span><span>.</span><span>.</span>
                    </div>
                    <div v-else v-html="renderMarkdown(msg.content)" class="markdown-body"></div>
                  </div>
                  <div v-else>{{ msg.content }}</div>
                </div>
                <!-- AI推荐菜品快捷添加 -->
                <div v-if="msg.role === 'assistant' && msg.menu_ids && msg.menu_ids.length > 0" class="recommendation-quick-add">
                  <div v-for="dishId in msg.menu_ids" :key="dishId" class="quick-add-item">
                    <span class="quick-dish-name">{{ getDishNameById(dishId) }}</span>
                    <el-button type="primary" size="small" link @click="addById(dishId)">+ 加入</el-button>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 输入区域 -->
          <div class="input-area">
            <transition name="el-zoom-in-bottom">
              <div class="delivery-bar-wrapper" v-if="showDeliveryInput">
                <div class="delivery-bar-card">
                  <div class="card-header">
                    <span>设置配送地址</span>
                    <el-button link @click="showDeliveryInput = false"><el-icon><Close /></el-icon></el-button>
                  </div>
                  <div class="card-body">
                    <el-autocomplete
                      v-model="deliveryAddress"
                      :fetch-suggestions="querySearchTips"
                      placeholder="请输入详细配送地址..."
                      class="addr-input"
                      @select="handleSelectTip"
                      clearable
                    >
                      <template #prefix><el-icon><Location /></el-icon></template>
                      <template #default="{ item }">
                        <div class="tip-item">
                          <div class="tip-name">{{ item.name }}</div>
                          <div class="tip-addr">{{ item.district }}{{ item.address }}</div>
                        </div>
                      </template>
                    </el-autocomplete>
                    <div class="delivery-options">
                      <el-select v-model="travelMode" placeholder="出行方式" style="width: 120px">
                        <el-option label="步行" value="1" />
                        <el-option label="骑行" value="2" />
                        <el-option label="驾车" value="3" />
                      </el-select>
                      <el-button type="primary" @click="checkDelivery" :loading="deliveryLoading" block>开始校验</el-button>
                    </div>
                  </div>
                </div>
              </div>
            </transition>
            
            <div class="chat-input-wrapper">
              <el-button 
                circle 
                @click="showDeliveryInput = !showDeliveryInput" 
                :type="deliveryResponse ? (deliveryResponse.in_range ? 'success' : 'warning') : ''"
              >
                <el-icon><Location /></el-icon>
              </el-button>
              <el-input
                v-model="chatQuery"
                placeholder="在此输入您的需求..."
                @keyup.enter="sendChatQuery"
                :disabled="chatLoading"
              >
                <template #append>
                  <el-button @click="sendChatQuery" :loading="chatLoading">发送</el-button>
                </template>
              </el-input>
            </div>
          </div>
        </el-main>
      </el-container>
    </el-container>

    <!-- 购物车抽屉 -->
    <el-drawer
      v-model="showCart"
      title="您的购物车"
      direction="rtl"
      size="350px"
    >
      <div class="cart-container">
        <div v-if="cart.length > 0" class="cart-items">
          <div v-for="item in cart" :key="item.id" class="cart-item">
            <div class="cart-item-info">
              <div class="cart-item-name">{{ item.dish_name }}</div>
              <div class="cart-item-price">¥{{ item.price }}</div>
            </div>
            <div class="cart-item-actions">
              <el-input-number v-model="item.quantity" :min="1" size="small" />
              <el-button type="danger" icon="Delete" circle size="small" @click="removeFromCart(item.id)" />
            </div>
          </div>
          <div class="cart-total">
            <span>总计:</span>
            <span class="total-amount">¥{{ totalPrice }}</span>
          </div>
          <el-button type="primary" class="checkout-btn" @click="checkout">提交订单</el-button>
        </div>
        <el-empty v-else description="购物车还是空的" />
      </div>
    </el-drawer>

    <!-- 订单历史抽屉 -->
    <el-drawer
      v-model="showOrders"
      title="订单历史"
      direction="rtl"
      size="450px"
    >
      <div class="orders-container" v-loading="ordersLoading">
        <div v-if="ordersList.length > 0" class="orders-list">
          <div v-for="order in ordersList" :key="order.id" class="order-card">
            <div class="order-header">
              <span class="order-no">{{ order.order_no }}</span>
              <el-tag :type="getOrderStatusTag(order.order_status)" size="small">
                {{ getOrderStatusText(order.order_status) }}
              </el-tag>
            </div>
            <div class="order-time">{{ formatDate(order.created_at) }}</div>
            <div class="order-items">
              <div v-for="item in order.items_detail" :key="item.id" class="order-item-row">
                <span>{{ item.dish_name }} x {{ item.quantity }}</span>
                <span>¥{{ (item.price * item.quantity).toFixed(2) }}</span>
              </div>
            </div>
            <div class="order-footer">
              <div class="order-address">
                <el-icon><Location /></el-icon>
                <span>{{ order.delivery_address }}</span>
              </div>
              <div class="order-total">
                总额: <span class="amount">¥{{ order.actual_amount }}</span>
              </div>
            </div>
            <div class="order-actions" v-if="order.payment_status === 'unpaid'">
              <el-button type="primary" size="small" @click="handlePayment(order.order_no)">立即支付</el-button>
            </div>
          </div>
        </div>
        <el-empty v-else description="暂无订单记录" />
      </div>
    </el-drawer>

    <!-- 登录/注册对话框 (强制登录) -->
    <el-dialog
      v-model="showLogin"
      :title="isLoginMode ? '欢迎回来' : '开启美食之旅'"
      width="380px"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      :show-close="!!currentUser"
      append-to-body
      class="auth-dialog"
    >
      <div class="auth-form-container">
        <el-form :model="authForm" label-position="top">
          <el-form-item label="用户名">
            <el-input v-model="authForm.username" placeholder="请输入用户名" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="authForm.password" type="password" placeholder="请输入密码" show-password />
          </el-form-item>
          <template v-if="!isLoginMode">
            <el-form-item label="昵称">
              <el-input v-model="authForm.nickname" placeholder="给小二留个称呼吧" />
            </el-form-item>
            <el-form-item label="手机号">
              <el-input v-model="authForm.phone" placeholder="用于配送联系" />
            </el-form-item>
          </template>
        </el-form>
        <div class="auth-actions">
          <el-button type="primary" @click="handleAuth" :loading="authLoading" class="submit-btn">
            {{ isLoginMode ? '立即登录' : '立即注册' }}
          </el-button>
          <div class="switch-mode">
            <span>{{ isLoginMode ? '还没有账号？' : '已有账号？' }}</span>
            <el-button link type="primary" @click="isLoginMode = !isLoginMode">
              {{ isLoginMode ? '去注册' : '去登录' }}
            </el-button>
          </div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script>
import { ref, onMounted, computed, nextTick, watch, reactive } from 'vue'
import { chatAPI, deliveryAPI, menuAPI, restaurantAPI, orderAPI, authAPI } from './api/index.js'
import { ElMessage, ElMessageBox, ElLoading } from 'element-plus'
import { 
  Food, Location, ShoppingCart, Refresh, ChatDotRound, 
  Service, User, Close, List, Warning, CircleCheck, Delete, UserFilled
} from '@element-plus/icons-vue'

export default {
  name: 'App',
  components: {
    Food, Location, ShoppingCart, Refresh, ChatDotRound,
    Service, User, Close, List, Warning, CircleCheck, Delete, UserFilled
  },
  setup() {
    // 用户认证相关
    const currentUser = ref(JSON.parse(localStorage.getItem('user') || 'null'))
    const showLogin = ref(false)
    const isLoginMode = ref(true)
    const authLoading = ref(false)
    const authForm = reactive({
      username: '',
      password: '',
      nickname: '',
      phone: ''
    })

    const handleAuth = async () => {
      if (!authForm.username || !authForm.password) {
        ElMessage.warning('请填写完整信息')
        return
      }
      authLoading.value = true
      try {
        if (isLoginMode.value) {
          const res = await authAPI.login(authForm.username, authForm.password)
          currentUser.value = { username: res.username, nickname: res.nickname }
          ElMessage.success(`欢迎回来，${res.nickname}！`)
          showLogin.value = false
          
          // 登录后加载必要数据
          await Promise.all([
            loadMenuItems(),
            loadRestaurantInfo()
          ])
          
          // 登录后自动发送个性化招呼语
          await sendInitialGreeting()
        } else {
          const res = await authAPI.register(authForm)
          if (res.success) {
            ElMessage.success(res.message)
            isLoginMode.value = true
          } else {
            ElMessage.error(res.message)
          }
        }
      } catch (e) {
        ElMessage.error(e.message || '操作失败')
      } finally {
        authLoading.value = false
      }
    }

    const handleUserCommand = (command) => {
      if (command === 'logout') {
        authAPI.logout()
        currentUser.value = null
        ElMessage.info('已退出登录')
        ordersList.value = []
      }
    }

    const sendInitialGreeting = async () => {
      const greeting = `小二，我来啦！`
      chatHistory.value.push({ role: 'user', content: greeting })
      await sendChatQuery(greeting)
    }

    const sessionId = ref('session-' + Math.random().toString(36).substring(2, 9))
    const chatQuery = ref('')
    const chatHistory = ref([])
    const chatLoading = ref(false)
    const chatWindow = ref(null)

    const restaurantInfo = reactive({
      name: '',
      address: '',
      longitude: 0,
      latitude: 0,
      delivery_radius: 0
    })

    const deliveryAddress = ref('')
    const travelMode = ref("2")
    const deliveryResponse = ref(null)
    const deliveryLoading = ref(false)
    const showDeliveryInput = ref(false)

    const menuItems = ref([])
    const menuLoading = ref(false)
    const highlightedItems = ref([])
    const activeCategory = ref('全部')

    // 订单历史相关
    const showOrders = ref(false)
    const ordersList = ref([])
    const ordersLoading = ref(false)

    // 购物车相关
    const showCart = ref(false)
    const cart = ref([])
    const cartCount = computed(() => cart.value.reduce((sum, item) => sum + item.quantity, 0))
    const totalPrice = computed(() => cart.value.reduce((sum, item) => sum + item.price * item.quantity, 0).toFixed(2))

    const addToCart = (item) => {
      const existing = cart.value.find(i => i.id === item.id)
      if (existing) {
        existing.quantity++
      } else {
        cart.value.push({ ...item, quantity: 1 })
      }
      ElMessage.success(`已添加 ${item.dish_name}`)
    }

    const addById = (id) => {
      const dish = menuItems.value.find(d => d.id.toString() === id.toString())
      if (dish) {
        addToCart(dish)
      }
    }

    const removeFromCart = (id) => {
      cart.value = cart.value.filter(i => i.id !== id)
    }

    const checkout = async () => {
      // 下单拦截机制：强制配送范围校验
      if (!deliveryResponse.value) {
        ElMessage.warning('请先设置并校验配送地址')
        showDeliveryInput.value = true
        return
      }
      
      if (!deliveryResponse.value.in_range) {
        ElMessage.error('当前地址超出配送范围，无法下单')
        return
      }

      const totalWithDelivery = (parseFloat(totalPrice.value) + (deliveryResponse.value.delivery_fee || 0)).toFixed(2)

      try {
        await ElMessageBox.confirm(
          `确认下单吗？\n菜品：¥${totalPrice.value}\n配送费：¥${deliveryResponse.value.delivery_fee}\n总计金额：¥${totalWithDelivery}`,
          '订单确认',
          { confirmButtonText: '立即下单', cancelButtonText: '取消', type: 'success' }
        )

        const loading = ElLoading.service({ text: '正在提交订单...' })
        
        const orderData = {
          total_amount: parseFloat(totalPrice.value),
          delivery_fee: deliveryResponse.value.delivery_fee,
          actual_amount: parseFloat(totalWithDelivery),
          delivery_address: deliveryResponse.value.formatted_address,
          travel_mode: travelMode.value,
          items_detail: cart.value.map(item => ({
            id: item.id,
            dish_name: item.dish_name,
            price: item.price,
            quantity: item.quantity
          }))
        }

        const res = await orderAPI.submitOrder(orderData)
        loading.close()

        if (res.success) {
          const orderNo = res.order_no
          await ElMessageBox.confirm(
            `订单已生成！单号：${orderNo}\n是否立即模拟支付？`,
            '待支付',
            { confirmButtonText: '模拟支付', cancelButtonText: '稍后支付', type: 'info' }
          )
          
          await handlePayment(orderNo)
        } else {
          ElMessage.error(res.message || '订单提交失败')
        }
      } catch (error) {
        if (error !== 'cancel') {
          console.error('下单流程失败:', error)
          ElMessage.error('系统繁忙，请稍后再试')
        }
      }
    }

    const handlePayment = async (orderNo) => {
      const loading = ElLoading.service({ text: '正在模拟支付...' })
      try {
        const res = await orderAPI.payOrder(orderNo)
        loading.close()
        if (res.success) {
          ElMessage.success('支付成功！餐厅正在为您准备菜品')
          cart.value = []
          showCart.value = false
          // 如果订单列表已打开，刷新列表
          if (showOrders.value) loadOrders()
        } else {
          ElMessage.error(res.message || '支付失败')
        }
      } catch (error) {
        loading.close()
        ElMessage.error('支付处理异常')
      }
    }

    const openOrders = () => {
      showOrders.value = true
      loadOrders()
    }

    const loadOrders = async () => {
      if (!currentUser.value) {
        ElMessage.warning('请先登录以查看订单历史')
        showLogin.value = true
        return
      }
      ordersLoading.value = true
      try {
        const res = await orderAPI.getOrderList()
        if (res.success) {
          ordersList.value = res.orders
        }
      } catch (error) {
        console.error('获取订单列表失败:', error)
      } finally {
        ordersLoading.value = false
      }
    }

    const getOrderStatusTag = (status) => {
      const tags = {
        'pending': 'warning',
        'paid': 'success',
        'completed': 'info',
        'cancelled': 'danger'
      }
      return tags[status] || 'info'
    }

    const getOrderStatusText = (status) => {
      const texts = {
        'pending': '待支付',
        'paid': '制作中',
        'completed': '已完成',
        'cancelled': '已取消'
      }
      return texts[status] || status
    }

    const formatDate = (dateStr) => {
      if (!dateStr) return ''
      const date = new Date(dateStr)
      return date.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
      })
    }

    const getDishNameById = (id) => {
      const dish = menuItems.value.find(d => d.id.toString() === id.toString())
      return dish ? dish.dish_name : '未知菜品'
    }

    // 分类来自后端菜品数据，避免与菜单实际分类不一致
    const categories = computed(() => {
      const names = menuItems.value.map(item => item.category).filter(Boolean)
      return ['全部', ...new Set(names)]
    })

    const filteredMenu = computed(() => {
      if (activeCategory.value === '全部') return menuItems.value
      return menuItems.value.filter(item => item.category === activeCategory.value)
    })

    const scrollToBottom = () => {
      nextTick(() => {
        if (chatWindow.value) {
          chatWindow.value.scrollTop = chatWindow.value.scrollHeight
        }
      })
    }

    watch(chatHistory, scrollToBottom, { deep: true })

    const renderMarkdown = (text) => {
      if (!text) return ''
      // 简单 Markdown 渲染逻辑优化
      let html = text
        .replace(/#{3} (.*?)$/gm, '<h3>$1</h3>')
        .replace(/#{2} (.*?)$/gm, '<h2>$1</h2>')
        .replace(/#{1} (.*?)$/gm, '<h1>$1</h1>')
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/^- (.*?)$/gm, '<li>$1</li>')
      
      // 合并连续的 <li> 到 <ul>
      html = html.replace(/(<li>.*?<\/li>\n?)+/gs, (match) => {
        return `<ul>${match.trim()}</ul>`
      })
      
      // 只有不在标签内部的换行才转为 <br/>
      html = html.replace(/\n/g, '<br/>')

      // 仅在正在生成的最新一条 AI 回复末尾添加光标
      const lastMsg = chatHistory.value[chatHistory.value.length - 1]
      if (chatLoading.value && lastMsg && lastMsg.role === 'assistant' && lastMsg.content === text) {
        html += '<span class="cursor">|</span>'
      }
      return html
    }

    const highlightRecommendedItems = (menuIds) => {
      if (!menuIds || !Array.isArray(menuIds)) {
        highlightedItems.value = []
        return
      }
      highlightedItems.value = menuIds.map(id => id.toString())
      
      if (highlightedItems.value.length > 0) {
        const firstId = highlightedItems.value[0]
        nextTick(() => {
          const el = document.getElementById('menu-' + firstId)
          if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        })
      }
    }

    const sendChatQuery = async () => {
      const query = chatQuery.value.trim()
      if (!query || chatLoading.value) return

      chatHistory.value = [...chatHistory.value, { role: 'user', content: query }]
      chatQuery.value = ''
      chatLoading.value = true

      // 创建一个占位的 AI 回复
      const assistantMsg = { 
        role: 'assistant', 
        content: '', 
        menu_ids: [] 
      }
      chatHistory.value = [...chatHistory.value, assistantMsg]
      const assistantIndex = chatHistory.value.length - 1

      try {
        await chatAPI.sendMessageStream(query, sessionId.value, (chunk) => {
          if (chunk.type === 'content') {
            // 直接替换整个数组项，确保 Vue 3 完美触发渲染
            const updatedMsg = { ...chatHistory.value[assistantIndex] }
            updatedMsg.content += chunk.content
            chatHistory.value[assistantIndex] = updatedMsg
          } else if (chunk.type === 'metadata') {
            if (chunk.menu_ids) {
              const updatedMsg = { ...chatHistory.value[assistantIndex] }
              updatedMsg.menu_ids = chunk.menu_ids
              chatHistory.value[assistantIndex] = updatedMsg
              highlightRecommendedItems(chunk.menu_ids)
            }
          }
        })
      } catch (error) {
        console.error('流式对话失败:', error)
        assistantMsg.content = '服务响应异常，请稍后再试。'
      } finally {
        chatLoading.value = false
      }
    }

    const checkDelivery = async () => {
      if (!deliveryAddress.value.trim()) return
      deliveryLoading.value = true
      try {
        deliveryResponse.value = await deliveryAPI.checkRange(deliveryAddress.value, travelMode.value)
        if (deliveryResponse.value.success) {
          showDeliveryInput.value = false // 校验成功后自动收起
          ElMessage.success(deliveryResponse.value.in_range ? '地址校验通过' : '超出配送范围')
        } else {
          // 处理特定错误，如 INVALID_USER_IP
          const errorMsg = deliveryResponse.value.message || '校验失败'
          if (errorMsg.includes('INVALID_USER_IP')) {
            ElMessageBox.alert(
              '配送校验功能目前不可用，原因：高德 API 网络 IP 校验未通过。请检查高德开发者控制台的 Key 设置（白名单或服务类型）。',
              '服务受限',
              { type: 'error' }
            )
          } else {
            ElMessage.error(errorMsg)
          }
        }
      } catch (error) {
        deliveryResponse.value = { success: false, in_range: false, message: '校验失败' }
      } finally {
        deliveryLoading.value = false
      }
    }

    const querySearchTips = async (queryString, cb) => {
      if (!queryString) {
        cb([])
        return
      }
      try {
        const response = await deliveryAPI.getTips(queryString)
        if (response.success && response.tips) {
          // 格式化为 el-autocomplete 需要的格式
          const results = response.tips.map(tip => ({
            value: tip.name, // 必须包含 value 字段用于显示在输入框
            ...tip
          }))
          cb(results)
        } else {
          // 如果是由于 IP 校验失败导致的，显示具体原因
          if (response.message && response.message.includes('INVALID_USER_IP')) {
            ElMessage.error({
              message: '地址建议功能受限：高德 API 网络 IP 校验未通过。请联系管理员检查 API Key 的白名单设置。',
              duration: 5000
            })
          }
          cb([])
        }
      } catch (error) {
        console.error('获取提示失败:', error)
        cb([])
      }
    }

    const handleSelectTip = (item) => {
      deliveryAddress.value = (item.district || '') + (item.address || '') + item.name
      if (item.location) {
        // 如果有坐标，直接调用校验
        checkDelivery()
      } else {
        // 如果没有坐标，提示用户手动补充详细地址
        ElMessage.info('请补充详细门牌号以便精准校验')
      }
    }

    const loadMenuItems = async () => {
      menuLoading.value = true
      try {
        const response = await menuAPI.getMenuList()
        menuItems.value = response.menu_items || []
      } finally {
        menuLoading.value = false
      }
    }

    const loadRestaurantInfo = async () => {
      try {
        const response = await restaurantAPI.getInfo()
        if (response.success) {
          Object.assign(restaurantInfo, response)
        }
      } catch (error) {
        console.error('获取餐厅信息失败:', error)
      }
    }

    const getSpiceType = (level) => {
      return ['', 'success', 'warning', 'danger'][level] || 'info'
    }

    const getDishImageUrl = (name) => {
      if (!name) return ''
      // 使用本地图片路径，图片放在 public/images/ 目录下
      return `/images/${name}.png`
    }

    onMounted(async () => {
      // 监听未授权事件
      window.addEventListener('unauthorized', () => {
        currentUser.value = null
        showLogin.value = true
        ElMessage.warning('请先登录')
      })

      if (currentUser.value) {
        try {
          // 验证 Token 是否依然有效
          await authAPI.getMe()
          await Promise.all([
            loadMenuItems(),
            loadRestaurantInfo()
          ])
        } catch (e) {
          // getMe 失败会自动触发 unauthorized 事件，由拦截器处理
          console.log('Token 验证失败，已清除登录态')
        }
      } else {
        showLogin.value = true
      }
    })

    return {
      currentUser, showLogin, isLoginMode, authLoading, authForm,
      handleAuth, handleUserCommand,
      chatQuery, chatHistory, chatLoading, chatWindow,
      deliveryAddress, travelMode, deliveryResponse, deliveryLoading, showDeliveryInput,
      menuItems, menuLoading, highlightedItems, activeCategory, categories, filteredMenu,
      restaurantInfo,
      sendChatQuery, checkDelivery, loadMenuItems, getSpiceType, renderMarkdown,
      querySearchTips, handleSelectTip,
      showCart, cart, cartCount, totalPrice, addToCart, addById, removeFromCart, checkout, getDishNameById,
      getDishImageUrl,
      showOrders, ordersList, ordersLoading, openOrders, getOrderStatusTag, getOrderStatusText, formatDate
    }
  }
}
</script>

<style>
:root {
  --primary-color: #409EFF;
  --bg-light: #f5f7fa;
  --text-main: #303133;
  --text-sec: #909399;
  --border-color: #ebeef5;
}

body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  background-color: var(--bg-light);
}

.app-wrapper {
  height: 100vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* 用户状态栏样式 */
.user-status-bar {
  background: white;
  padding: 4px 20px;
  display: flex;
  justify-content: flex-end;
  border-bottom: 1px solid var(--border-color);
  font-size: 12px;
  z-index: 100;
}

.user-profile {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 2px 8px;
  border-radius: 12px;
  transition: background 0.2s;
}

.user-profile:hover {
  background: var(--bg-light);
}

.user-profile .nickname {
  font-weight: 600;
  color: var(--text-main);
}

/* 登录弹窗样式 */
.auth-form-container {
  padding: 0 10px;
}

.auth-actions {
  margin-top: 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.submit-btn {
  width: 100%;
  height: 40px;
  font-size: 16px;
}

.switch-mode {
  display: flex;
  justify-content: center;
  align-items: center;
  font-size: 14px;
  color: var(--text-sec);
}

.main-container {
  flex: 1;
  overflow: hidden;
  transition: filter 0.3s ease;
}

.main-container.is-blurred {
  filter: blur(8px);
  pointer-events: none;
}

/* Sidebar Styling */
.menu-sidebar {
  background: white;
  border-right: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
}

.sidebar-header {
  padding: 20px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid var(--border-color);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.order-history-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--primary-color);
  font-weight: 600;
  padding: 4px 8px;
  border-radius: 4px;
  transition: background 0.2s;
}

.order-history-btn:hover {
  background: rgba(64, 158, 255, 0.1);
}

.order-history-btn .btn-text {
  font-size: 14px;
}

.cart-badge {
  margin-right: 5px;
}

.logo-wrapper {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.logo {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 18px;
  font-weight: bold;
  color: var(--primary-color);
}

.restaurant-address {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--text-sec);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 250px;
}

.menu-filter {
  padding: 10px 20px;
  background: #fafafa;
}

.menu-list-container {
  flex: 1;
  overflow-y: auto;
  padding: 15px;
}

.menu-card {
  background: #fff;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 0; /* 修改 padding */
  margin-bottom: 15px;
  transition: all 0.3s;
  overflow: hidden; /* 裁剪图片圆角 */
}

.menu-card-image {
  width: 100%;
  height: 150px;
  background: #f0f2f5;
}

.menu-card-image .el-image {
  width: 100%;
  height: 100%;
}

.image-slot {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100%;
  color: var(--text-sec);
  font-size: 12px;
}

.menu-card-header, .dish-desc, .dish-tags, .menu-card-footer {
  padding: 0 15px;
}

.menu-card-header {
  margin-top: 12px;
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
}

.menu-card-footer {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}

.dish-name {
  font-weight: 600;
  font-size: 16px;
}

.dish-price {
  color: #f56c6c;
  font-weight: bold;
}

.dish-desc {
  font-size: 13px;
  color: var(--text-sec);
  line-height: 1.4;
  margin-bottom: 12px;
}

.dish-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  align-items: center;
}

/* Chat Main Styling */
.chat-main {
  display: flex;
  flex-direction: column;
  padding: 0 !important;
  background: var(--bg-light);
  position: relative;
}

.delivery-status-banner {
  background: white;
  padding: 12px 20px;
  border-bottom: 1px solid var(--border-color);
  box-shadow: 0 2px 4px rgba(0,0,0,0.02);
  z-index: 10;
  transition: all 0.3s ease;
}

.delivery-status-banner.is-active {
  background: #f0f9eb; /* 浅绿色背景 */
  border-bottom-color: #e1f3d8;
}

.banner-content {
  display: flex;
  align-items: center;
  gap: 15px;
  cursor: pointer;
}

.status-icon {
  font-size: 24px;
  color: var(--text-sec);
}

.is-active .status-icon {
  color: #67c23a;
}

.status-info {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.status-text {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-main);
}

.status-hint {
  font-size: 12px;
  color: var(--text-sec);
}

.status-detail {
  font-size: 12px;
  color: #67c23a;
}

.chat-window {
  flex: 1;
  overflow-y: auto;
  padding: 20px 10% 20px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.welcome-msg {
  text-align: center;
  margin-top: 10vh;
  color: var(--text-sec);
}

.quick-queries {
  margin-top: 20px;
  display: flex;
  justify-content: center;
  gap: 10px;
}

.message {
  display: flex;
  gap: 12px;
  max-width: 80%;
}

.message.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}

.message.assistant {
  align-self: flex-start;
}

.avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: #eef5fe;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--primary-color);
  flex-shrink: 0;
}

.message.user .avatar {
  background: var(--primary-color);
  color: white;
}

.message-content-wrapper {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.message-content {
  padding: 12px 16px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.6;
  box-shadow: 0 2px 8px rgba(0,0,0,0.05);
}

.message.assistant .message-content {
  background: white;
  border-bottom-left-radius: 2px;
}

.cursor {
  display: inline-block;
  width: 2px;
  height: 1.2em;
  background-color: var(--primary-color);
  margin-left: 2px;
  animation: blink 1s infinite;
  vertical-align: middle;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

.message.user .message-content {
  background: var(--primary-color);
  color: white;
  border-bottom-right-radius: 2px;
}

.loading-dots {
  display: flex;
  gap: 4px;
  padding: 4px 0;
}

.loading-dots span {
  width: 6px;
  height: 6px;
  background-color: var(--primary-color);
  border-radius: 50%;
  animation: jump 1.4s infinite ease-in-out;
}

.loading-dots span:nth-child(2) { animation-delay: 0.2s; }
.loading-dots span:nth-child(3) { animation-delay: 0.4s; }

@keyframes jump {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
  40% { transform: scale(1.2); opacity: 1; }
}

.recommendation-quick-add {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.quick-add-item {
  background: #fff;
  border: 1px solid var(--border-color);
  padding: 4px 10px;
  border-radius: 20px;
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  box-shadow: 0 2px 4px rgba(0,0,0,0.05);
}

.quick-dish-name {
  color: var(--text-main);
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Input Area Styling */
.input-area {
  padding: 20px 10%;
  background: white;
  border-top: 1px solid var(--border-color);
  position: relative;
}

.chat-input-wrapper {
  display: flex;
  align-items: center;
  gap: 12px;
}

.delivery-bar-wrapper {
  position: absolute;
  bottom: 100%;
  left: 10%;
  right: 10%;
  padding-bottom: 10px;
  z-index: 100;
}

.delivery-bar-card {
  background: white;
  border-radius: 12px;
  box-shadow: 0 -4px 12px rgba(0,0,0,0.1);
  border: 1px solid var(--border-color);
  overflow: hidden;
}

.delivery-bar-card .card-header {
  padding: 10px 15px;
  background: #f8f9fa;
  border-bottom: 1px solid var(--border-color);
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 14px;
  font-weight: 600;
}

.delivery-bar-card .card-body {
  padding: 15px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.delivery-bar-card .addr-input {
  width: 100%;
}

.delivery-options {
  display: flex;
  gap: 10px;
}

.delivery-options .el-button {
  flex: 1;
}

/* 搜索建议样式 */
.tip-item {
  padding: 8px 0;
  line-height: 1.2;
}

.tip-name {
  font-weight: bold;
  font-size: 14px;
  color: var(--text-main);
}

.tip-addr {
  font-size: 12px;
  color: var(--text-sec);
  margin-top: 4px;
}

/* Cart Styling */
.cart-container {
  padding: 0 20px;
  height: 100%;
  display: flex;
  flex-direction: column;
}

.cart-items {
  flex: 1;
  overflow-y: auto;
}

.cart-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 15px 0;
  border-bottom: 1px solid var(--border-color);
}

.cart-item-name {
  font-weight: 600;
  margin-bottom: 4px;
}

.cart-item-price {
  color: #f56c6c;
  font-size: 13px;
}

.cart-item-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.cart-total {
  margin-top: 20px;
  padding: 20px 0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-top: 2px solid var(--border-color);
  font-weight: bold;
  font-size: 18px;
}

.total-amount {
  color: #f56c6c;
}

.checkout-btn {
  width: 100%;
  margin-top: 20px;
  padding: 12px;
}

/* Orders Styling */
.orders-container {
  padding: 0 15px;
  height: 100%;
}

.order-card {
  background: #fff;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 15px;
  margin-bottom: 20px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.02);
}

.order-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.order-no {
  font-family: monospace;
  font-size: 13px;
  color: var(--text-sec);
}

.order-time {
  font-size: 12px;
  color: #999;
  margin-bottom: 12px;
}

.order-items {
  background: #f9f9f9;
  padding: 10px;
  border-radius: 8px;
  margin-bottom: 12px;
}

.order-item-row {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  margin-bottom: 5px;
}

.order-footer {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12px;
}

.order-address {
  font-size: 12px;
  color: var(--text-sec);
  display: flex;
  align-items: center;
  gap: 4px;
  max-width: 60%;
}

.order-total {
  font-size: 14px;
  font-weight: 600;
}

.order-total .amount {
  color: #f56c6c;
}

.order-actions {
  display: flex;
  justify-content: flex-end;
  border-top: 1px solid #f0f0f0;
  padding-top: 12px;
}

/* 响应式布局：避免窄屏下右侧主区域被固定宽度侧边栏挤成 0 宽 */
.main-container > .el-container {
  min-width: 0;
}

@media (max-width: 1200px) {
  .menu-sidebar {
    width: 320px !important;
  }
}

@media (max-width: 900px) {
  .main-container {
    flex-direction: column;
    overflow-y: auto;
  }

  .menu-sidebar {
    width: 100% !important;
    max-height: 45vh;
    border-right: none;
    border-bottom: 1px solid var(--border-color);
  }

  .main-container > .el-container {
    flex: 1;
    min-height: 60vh;
  }
}
</style>
