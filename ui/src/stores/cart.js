import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ElMessage } from 'element-plus'
import { useMenuStore } from './menu.js'

export const useCartStore = defineStore('cart', () => {
  const cart = ref([])
  const showCart = ref(false)

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
    const menu = useMenuStore()
    const dish = menu.menuItems.find(d => d.id.toString() === id.toString())
    if (dish) {
      addToCart(dish)
    }
  }

  const removeFromCart = (id) => {
    cart.value = cart.value.filter(i => i.id !== id)
  }

  const clearCart = () => {
    cart.value = []
  }

  return {
    cart, showCart, cartCount, totalPrice,
    addToCart, addById, removeFromCart, clearCart
  }
})
