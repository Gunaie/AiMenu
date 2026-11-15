import { computed, nextTick, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { menuAPI, restaurantAPI } from '../api/index.js'

export const useMenuStore = defineStore('menu', () => {
  const menuItems = ref([])
  const menuLoading = ref(false)
  const highlightedItems = ref([])
  const activeCategory = ref('全部')
  const restaurantInfo = reactive({
    name: '',
    address: '',
    longitude: 0,
    latitude: 0,
    delivery_radius: 0
  })

  // 分类来自后端菜品数据，避免与菜单实际分类不一致
  const categories = computed(() => {
    const names = menuItems.value.map(item => item.category).filter(Boolean)
    return ['全部', ...new Set(names)]
  })

  const filteredMenu = computed(() => {
    if (activeCategory.value === '全部') return menuItems.value
    return menuItems.value.filter(item => item.category === activeCategory.value)
  })

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

  const getDishNameById = (id) => {
    const dish = menuItems.value.find(d => d.id.toString() === id.toString())
    return dish ? dish.dish_name : '未知菜品'
  }

  const getSpiceType = (level) => {
    return ['', 'success', 'warning', 'danger'][level] || 'info'
  }

  const getDishImageUrl = (name) => {
    if (!name) return ''
    // 使用本地图片路径，图片放在 public/images/ 目录下
    return `/images/${name}.png`
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

  return {
    menuItems, menuLoading, highlightedItems, activeCategory, restaurantInfo,
    categories, filteredMenu,
    loadMenuItems, loadRestaurantInfo, highlightRecommendedItems,
    getDishNameById, getSpiceType, getDishImageUrl
  }
})
