import { ref } from 'vue'
import { defineStore } from 'pinia'
import { ElLoading, ElMessage, ElMessageBox } from 'element-plus'
import { orderAPI } from '../api/index.js'
import { useAuthStore } from './auth.js'
import { useCartStore } from './cart.js'
import { useDeliveryStore } from './delivery.js'

export const useOrderStore = defineStore('order', () => {
  const showOrders = ref(false)
  const ordersList = ref([])
  const ordersLoading = ref(false)

  const openOrders = () => {
    showOrders.value = true
    loadOrders()
  }

  const loadOrders = async () => {
    const auth = useAuthStore()
    if (!auth.currentUser) {
      ElMessage.warning('请先登录以查看订单历史')
      auth.showLogin = true
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

  const handlePayment = async (orderNo) => {
    const cart = useCartStore()
    const loading = ElLoading.service({ text: '正在模拟支付...' })
    try {
      const res = await orderAPI.payOrder(orderNo)
      loading.close()
      if (res.success) {
        ElMessage.success('支付成功！餐厅正在为您准备菜品')
        cart.clearCart()
        cart.showCart = false
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

  const checkout = async () => {
    const cart = useCartStore()
    const delivery = useDeliveryStore()

    // 下单拦截机制：强制配送范围校验
    if (!delivery.deliveryResponse) {
      ElMessage.warning('请先设置并校验配送地址')
      delivery.showDeliveryInput = true
      return
    }

    if (!delivery.deliveryResponse.in_range) {
      ElMessage.error('当前地址超出配送范围，无法下单')
      return
    }

    const totalWithDelivery = (parseFloat(cart.totalPrice) + (delivery.deliveryResponse.delivery_fee || 0)).toFixed(2)

    try {
      await ElMessageBox.confirm(
        `确认下单吗？\n菜品：¥${cart.totalPrice}\n配送费：¥${delivery.deliveryResponse.delivery_fee}\n总计金额：¥${totalWithDelivery}`,
        '订单确认',
        { confirmButtonText: '立即下单', cancelButtonText: '取消', type: 'success' }
      )

      const loading = ElLoading.service({ text: '正在提交订单...' })

      const orderData = {
        total_amount: parseFloat(cart.totalPrice),
        delivery_fee: delivery.deliveryResponse.delivery_fee,
        actual_amount: parseFloat(totalWithDelivery),
        delivery_address: delivery.deliveryResponse.formatted_address,
        travel_mode: delivery.travelMode,
        items_detail: cart.cart.map(item => ({
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

  const reset = () => {
    ordersList.value = []
  }

  return {
    showOrders, ordersList, ordersLoading,
    openOrders, loadOrders, checkout, handlePayment,
    getOrderStatusTag, getOrderStatusText, formatDate, reset
  }
})
