<template>
  <div class="user-status-bar">
    <div v-if="auth.currentUser" class="user-logged-in">
      <el-dropdown trigger="click" @command="handleCommand">
        <div class="user-profile">
          <el-avatar :size="24" icon="UserFilled" />
          <span class="nickname">{{ auth.currentUser.nickname }}</span>
        </div>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="logout">退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
    <div v-else class="user-guest">
      <el-button type="primary" link size="small" @click="auth.showLogin = true">登录 / 注册</el-button>
    </div>
  </div>
</template>

<script setup>
import { useAuthStore } from '../stores/auth.js'
import { useOrderStore } from '../stores/order.js'

const auth = useAuthStore()
const order = useOrderStore()

const handleCommand = (command) => {
  if (command === 'logout') {
    auth.logout()
    order.reset()
  }
}
</script>
