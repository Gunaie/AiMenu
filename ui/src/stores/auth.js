import { reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { ElMessage } from 'element-plus'
import { authAPI } from '../api/index.js'

export const useAuthStore = defineStore('auth', () => {
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

  /** 登录或注册；登录成功返回 true，供调用方继续加载菜单等数据 */
  const handleAuth = async () => {
    if (!authForm.username || !authForm.password) {
      ElMessage.warning('请填写完整信息')
      return false
    }
    authLoading.value = true
    try {
      if (isLoginMode.value) {
        const res = await authAPI.login(authForm.username, authForm.password)
        currentUser.value = { username: res.username, nickname: res.nickname }
        ElMessage.success(`欢迎回来，${res.nickname}！`)
        showLogin.value = false
        return true
      }

      const res = await authAPI.register(authForm)
      if (res.success) {
        ElMessage.success(res.message)
        isLoginMode.value = true
      } else {
        ElMessage.error(res.message)
      }
      return false
    } catch (e) {
      ElMessage.error(e.message || '操作失败')
      return false
    } finally {
      authLoading.value = false
    }
  }

  const logout = () => {
    authAPI.logout()
    currentUser.value = null
    ElMessage.info('已退出登录')
  }

  return {
    currentUser, showLogin, isLoginMode, authLoading, authForm,
    handleAuth, logout
  }
})
