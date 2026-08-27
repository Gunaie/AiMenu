<template>
  <div class="app-wrapper">
    <el-container class="main-container">
      <!-- 侧边栏：菜品展示 -->
      <el-aside width="400px" class="menu-sidebar">
        <div class="sidebar-header">
          <div class="logo">
            <el-icon><Food /></el-icon>
            <span>智能点餐系统</span>
          </div>
          <div class="header-actions">
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
            <el-radio-button label="全部" />
            <el-radio-button label="川菜" />
            <el-radio-button label="粤菜" />
            <el-radio-button label="浙菜" />
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
          <!-- 对话窗口区域 -->
          <div class="chat-window" ref="chatWindow">
            <div class="welcome-msg" v-if="chatHistory.length === 0">
              <el-icon size="48" color="#409EFF"><ChatDotRound /></el-icon>
              <h2>欢迎使用智能点餐助手</h2>
              <p>您可以问我关于菜品推荐、口味咨询或配送范围的问题</p>
              <div class="quick-queries">
                <el-button round size="small" @click="chatQuery = '有什么不辣的推荐吗？'">不辣的菜品</el-button>
                <el-button round size="small" @click="chatQuery = '我想点一些川菜'">推荐川菜</el-button>
                <el-button round size="small" @click="chatQuery = '配送范围是多少？'">查询配送</el-button>
              </div>
            </div>

            <div v-for="(msg, index) in chatHistory" :key="index" :class="['message', msg.role]">
              <div class="avatar">
                <el-icon v-if="msg.role === 'assistant'"><Service /></el-icon>
                <el-icon v-else><User /></el-icon>
              </div>
              <div class="message-content-wrapper">
                <div class="message-content">
                  <div v-if="msg.role === 'assistant'" v-html="renderMarkdown(msg.content)" class="markdown-body"></div>
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
            
            <div v-if="chatLoading" class="message assistant">
              <div class="avatar"><el-icon><Service /></el-icon></div>
              <div class="message-content loading-dots">
                <span>.</span><span>.</span><span>.</span>
              </div>
            </div>
          </div>

          <!-- 输入区域 -->
          <div class="input-area">
            <div class="delivery-bar" v-if="showDeliveryInput">
              <el-input v-model="deliveryAddress" placeholder="输入配送地址..." class="addr-input">
                <template #prepend><el-icon><Location /></el-icon></template>
              </el-input>
              <el-select v-model="travelMode" style="width: 120px">
                <el-option label="步行" value="1" />
                <el-option label="骑行" value="2" />
                <el-option label="驾车" value="3" />
              </el-select>
              <el-button type="success" @click="checkDelivery" :loading="deliveryLoading">校验</el-button>
              <el-button circle @click="showDeliveryInput = false"><el-icon><Close /></el-icon></el-button>
            </div>
            
            <div class="chat-input-wrapper">
              <el-button circle @click="showDeliveryInput = !showDeliveryInput" :type="showDeliveryInput ? 'success' : ''">
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
            
            <!-- 配送结果小浮层 -->
            <transition name="el-zoom-in-bottom">
              <div v-if="deliveryResponse" class="delivery-toast">
                <el-alert
                  :title="deliveryResponse.message"
                  :type="deliveryResponse.in_range ? 'success' : 'warning'"
                  show-icon
                  @close="deliveryResponse = null"
                >
                  <template #default v-if="deliveryResponse.distance">
                    <span>{{ deliveryResponse.distance.toFixed(2) }}km | 约{{ Math.floor(deliveryResponse.duration/60) }}分钟</span>
                  </template>
                </el-alert>
              </div>
            </transition>
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
  </div>
</template>

<script>
import { ref, onMounted, computed, nextTick, watch } from 'vue'
import { chatAPI, deliveryAPI, menuAPI } from './api/index.js'
import { ElMessage, ElMessageBox } from 'element-plus'

export default {
  name: 'App',
  setup() {
    const sessionId = ref('session-' + Math.random().toString(36).substring(2, 9))
    const chatQuery = ref('')
    const chatHistory = ref([])
    const chatLoading = ref(false)
    const chatWindow = ref(null)

    const deliveryAddress = ref('')
    const travelMode = ref("2")
    const deliveryResponse = ref(null)
    const deliveryLoading = ref(false)
    const showDeliveryInput = ref(false)

    const menuItems = ref([])
    const menuLoading = ref(false)
    const highlightedItems = ref([])
    const activeCategory = ref('全部')

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

    const checkout = () => {
      ElMessageBox.confirm(
        `确认下单吗？总计金额：¥${totalPrice.value}`,
        '订单确认',
        { confirmButtonText: '确认', cancelButtonText: '取消', type: 'success' }
      ).then(() => {
        ElMessage.success('下单成功！')
        cart.value = []
        showCart.value = false
      })
    }

    const getDishNameById = (id) => {
      const dish = menuItems.value.find(d => d.id.toString() === id.toString())
      return dish ? dish.dish_name : '未知菜品'
    }

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
      return text
        .replace(/#{3} (.*?)$/gm, '<h3>$1</h3>')
        .replace(/#{2} (.*?)$/gm, '<h2>$1</h2>')
        .replace(/#{1} (.*?)$/gm, '<h1>$1</h1>')
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/^- (.*?)$/gm, '<li>$1</li>')
        .replace(/(<li>.*?<\/li>)+/gs, '<ul>$&</ul>')
        .replace(/\n/g, '<br/>')
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

      chatHistory.value.push({ role: 'user', content: query })
      chatQuery.value = ''
      chatLoading.value = true

      // 创建一个占位的 AI 回复
      const assistantMsg = { 
        role: 'assistant', 
        content: '', 
        menu_ids: [] 
      }
      chatHistory.value.push(assistantMsg)

      try {
        await chatAPI.sendMessageStream(query, sessionId.value, (chunk) => {
          if (chunk.type === 'content') {
            assistantMsg.content += chunk.content
          } else if (chunk.type === 'metadata') {
            if (chunk.menu_ids) {
              assistantMsg.menu_ids = chunk.menu_ids
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
      } catch (error) {
        deliveryResponse.value = { success: false, in_range: false, message: '校验失败' }
      } finally {
        deliveryLoading.value = false
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

    const getSpiceType = (level) => {
      return ['', 'success', 'warning', 'danger'][level] || 'info'
    }

    onMounted(loadMenuItems)

    return {
      chatQuery, chatHistory, chatLoading, chatWindow,
      deliveryAddress, travelMode, deliveryResponse, deliveryLoading, showDeliveryInput,
      menuItems, menuLoading, highlightedItems, activeCategory, filteredMenu,
      sendChatQuery, checkDelivery, loadMenuItems, getSpiceType, renderMarkdown,
      showCart, cart, cartCount, totalPrice, addToCart, addById, removeFromCart, checkout, getDishNameById
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
}

.main-container {
  height: 100%;
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
  gap: 10px;
}

.cart-badge {
  margin-right: 5px;
}

.logo {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 18px;
  font-weight: bold;
  color: var(--primary-color);
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
  padding: 15px;
  margin-bottom: 15px;
  transition: all 0.3s;
}

.menu-card:hover {
  box-shadow: 0 4px 12px rgba(0,0,0,0.08);
  transform: translateY(-2px);
}

.menu-card.is-recommended {
  border-color: #f56c6c;
  background: #fffbfa;
}

.menu-card-header {
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
}

.chat-window {
  flex: 1;
  overflow-y: auto;
  padding: 40px 10% 20px;
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

.message.user .message-content {
  background: var(--primary-color);
  color: white;
  border-bottom-right-radius: 2px;
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
