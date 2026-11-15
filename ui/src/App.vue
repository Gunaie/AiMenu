<template>
  <div class="app-wrapper">
    <!-- 用户登录状态条 (顶置) -->
    <UserStatusBar />

    <el-container class="main-container" :class="{ 'is-blurred': !auth.currentUser }">
      <!-- 侧边栏：菜品展示 -->
      <MenuSidebar />

      <el-container>
        <ChatWindow />
      </el-container>
    </el-container>

    <!-- 购物车抽屉 -->
    <CartDrawer />

    <!-- 订单历史抽屉 -->
    <OrderDrawer />

    <!-- 登录/注册对话框 (强制登录) -->
    <LoginDialog />
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { authAPI } from './api/index.js'
import { useAuthStore } from './stores/auth.js'
import { useMenuStore } from './stores/menu.js'
import UserStatusBar from './components/UserStatusBar.vue'
import MenuSidebar from './components/MenuSidebar.vue'
import ChatWindow from './components/ChatWindow.vue'
import CartDrawer from './components/CartDrawer.vue'
import OrderDrawer from './components/OrderDrawer.vue'
import LoginDialog from './components/LoginDialog.vue'

const auth = useAuthStore()
const menu = useMenuStore()

onMounted(async () => {
  // 监听未授权事件
  window.addEventListener('unauthorized', () => {
    auth.currentUser = null
    auth.showLogin = true
    ElMessage.warning('请先登录')
  })

  if (auth.currentUser) {
    try {
      // 验证 Token 是否依然有效
      await authAPI.getMe()
      await Promise.all([
        menu.loadMenuItems(),
        menu.loadRestaurantInfo()
      ])
    } catch (e) {
      // getMe 失败会自动触发 unauthorized 事件，由拦截器处理
      console.log('Token 验证失败，已清除登录态')
    }
  } else {
    auth.showLogin = true
  }
})
</script>
