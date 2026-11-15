<template>
  <el-dialog
    v-model="auth.showLogin"
    :title="auth.isLoginMode ? '欢迎回来' : '开启美食之旅'"
    width="380px"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    :show-close="!!auth.currentUser"
    append-to-body
    class="auth-dialog"
  >
    <div class="auth-form-container">
      <el-form :model="auth.authForm" label-position="top">
        <el-form-item label="用户名">
          <el-input v-model="auth.authForm.username" placeholder="请输入用户名" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="auth.authForm.password" type="password" placeholder="请输入密码" show-password />
        </el-form-item>
        <template v-if="!auth.isLoginMode">
          <el-form-item label="昵称">
            <el-input v-model="auth.authForm.nickname" placeholder="给小二留个称呼吧" />
          </el-form-item>
          <el-form-item label="手机号">
            <el-input v-model="auth.authForm.phone" placeholder="用于配送联系" />
          </el-form-item>
        </template>
      </el-form>
      <div class="auth-actions">
        <el-button type="primary" @click="handleAuth" :loading="auth.authLoading" class="submit-btn">
          {{ auth.isLoginMode ? '立即登录' : '立即注册' }}
        </el-button>
        <div class="switch-mode">
          <span>{{ auth.isLoginMode ? '还没有账号？' : '已有账号？' }}</span>
          <el-button link type="primary" @click="auth.isLoginMode = !auth.isLoginMode">
            {{ auth.isLoginMode ? '去注册' : '去登录' }}
          </el-button>
        </div>
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { useAuthStore } from '../stores/auth.js'
import { useMenuStore } from '../stores/menu.js'
import { useChatStore } from '../stores/chat.js'

const auth = useAuthStore()
const menu = useMenuStore()
const chat = useChatStore()

// 登录成功后加载菜单与餐厅信息，并发送个性化招呼语
const handleAuth = async () => {
  const success = await auth.handleAuth()
  if (!success) return

  await Promise.all([
    menu.loadMenuItems(),
    menu.loadRestaurantInfo()
  ])
  await chat.sendInitialGreeting()
}
</script>
