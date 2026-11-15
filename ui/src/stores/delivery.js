import { ref } from 'vue'
import { defineStore } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import { deliveryAPI } from '../api/index.js'

export const useDeliveryStore = defineStore('delivery', () => {
  const deliveryAddress = ref('')
  const travelMode = ref('2')
  const deliveryResponse = ref(null)
  const deliveryLoading = ref(false)
  const showDeliveryInput = ref(false)

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

  return {
    deliveryAddress, travelMode, deliveryResponse, deliveryLoading, showDeliveryInput,
    checkDelivery, querySearchTips, handleSelectTip
  }
})
