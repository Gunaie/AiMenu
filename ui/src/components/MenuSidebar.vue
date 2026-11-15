<template>
  <el-aside width="400px" class="menu-sidebar">
    <div class="sidebar-header">
      <div class="logo-wrapper">
        <div class="logo">
          <el-icon><Food /></el-icon>
          <span>{{ menu.restaurantInfo.name || '智能点餐系统' }}</span>
        </div>
        <div class="restaurant-address" v-if="menu.restaurantInfo.address">
          <el-icon><Location /></el-icon>
          <span>{{ menu.restaurantInfo.address }}</span>
        </div>
      </div>
      <div class="header-actions">
        <el-button type="primary" link @click="order.openOrders" class="order-history-btn">
          <el-icon :size="20"><List /></el-icon>
          <span class="btn-text">我的订单</span>
        </el-button>
        <el-badge :value="cart.cartCount" :hidden="cart.cartCount === 0" class="cart-badge">
          <el-button type="primary" link @click="cart.showCart = true">
            <el-icon size="20"><ShoppingCart /></el-icon>
          </el-button>
        </el-badge>
        <el-button
          type="primary"
          link
          @click="menu.loadMenuItems"
          :loading="menu.menuLoading"
        >
          <el-icon><Refresh /></el-icon>
        </el-button>
      </div>
    </div>

    <div class="menu-filter">
      <el-radio-group v-model="menu.activeCategory" size="small">
        <el-radio-button v-for="cat in menu.categories" :key="cat" :label="cat" />
      </el-radio-group>
    </div>

    <div class="menu-list-container">
      <div v-if="menu.filteredMenu.length > 0" class="menu-list">
        <div
          v-for="item in menu.filteredMenu"
          :key="item.id"
          class="menu-card"
          :class="{ 'is-recommended': menu.highlightedItems.includes(item.id.toString()) }"
          :id="'menu-' + item.id"
        >
          <div class="menu-card-image" v-if="item.dish_name">
            <el-image
              :src="item.image_url || menu.getDishImageUrl(item.dish_name)"
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
            <el-tag size="small" :type="menu.getSpiceType(item.spice_level)" effect="light">
              {{ item.spice_text }}
            </el-tag>
            <el-tag v-if="item.is_vegetarian" size="small" type="success" effect="light">素食</el-tag>
            <el-badge v-if="menu.highlightedItems.includes(item.id.toString())" value="推荐" class="recommend-badge" />
          </div>
          <div class="menu-card-footer">
            <el-button type="primary" size="small" @click="cart.addToCart(item)">加入购物车</el-button>
          </div>
        </div>
      </div>
      <el-empty v-else-if="!menu.menuLoading" description="暂无相关菜品" />
      <div v-if="menu.menuLoading" class="loading-state">
        <el-skeleton :rows="3" animated v-for="i in 3" :key="i" style="margin-bottom: 20px" />
      </div>
    </div>
  </el-aside>
</template>

<script setup>
import { useMenuStore } from '../stores/menu.js'
import { useCartStore } from '../stores/cart.js'
import { useOrderStore } from '../stores/order.js'

const menu = useMenuStore()
const cart = useCartStore()
const order = useOrderStore()
</script>
