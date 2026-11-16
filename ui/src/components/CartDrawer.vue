<template>
  <el-drawer
    v-model="cart.showCart"
    title="您的购物车"
    direction="rtl"
    size="350px"
  >
    <div class="cart-container">
      <div v-if="cart.cart.length > 0" class="cart-items">
        <div v-for="item in cart.cart" :key="item.id" class="cart-item">
          <div class="cart-item-info">
            <div class="cart-item-name">{{ item.dish_name }}</div>
            <div class="cart-item-price">¥{{ item.price }}</div>
          </div>
          <div class="cart-item-actions">
            <el-input-number v-model="item.quantity" :min="1" size="small" />
            <el-button type="danger" icon="Delete" circle size="small" @click="cart.removeFromCart(item.id)" />
          </div>
        </div>
        <div class="cart-total">
          <span>总计:</span>
          <span class="total-amount">¥{{ cart.totalPrice }}</span>
        </div>
        <el-button type="primary" class="checkout-btn" @click="order.checkout" :loading="order.submitting">提交订单</el-button>
      </div>
      <el-empty v-else description="购物车还是空的" />
    </div>
  </el-drawer>
</template>

<script setup>
import { useCartStore } from '../stores/cart.js'
import { useOrderStore } from '../stores/order.js'

const cart = useCartStore()
const order = useOrderStore()
</script>
