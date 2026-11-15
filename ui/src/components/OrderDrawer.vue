<template>
  <el-drawer
    v-model="order.showOrders"
    title="订单历史"
    direction="rtl"
    size="450px"
  >
    <div class="orders-container" v-loading="order.ordersLoading">
      <div v-if="order.ordersList.length > 0" class="orders-list">
        <div v-for="item in order.ordersList" :key="item.id" class="order-card">
          <div class="order-header">
            <span class="order-no">{{ item.order_no }}</span>
            <el-tag :type="order.getOrderStatusTag(item.order_status)" size="small">
              {{ order.getOrderStatusText(item.order_status) }}
            </el-tag>
          </div>
          <div class="order-time">{{ order.formatDate(item.created_at) }}</div>
          <div class="order-items">
            <div v-for="detail in item.items_detail" :key="detail.id" class="order-item-row">
              <span>{{ detail.dish_name }} x {{ detail.quantity }}</span>
              <span>¥{{ (detail.price * detail.quantity).toFixed(2) }}</span>
            </div>
          </div>
          <div class="order-footer">
            <div class="order-address">
              <el-icon><Location /></el-icon>
              <span>{{ item.delivery_address }}</span>
            </div>
            <div class="order-total">
              总额: <span class="amount">¥{{ item.actual_amount }}</span>
            </div>
          </div>
          <div class="order-actions" v-if="item.payment_status === 'unpaid'">
            <el-button type="primary" size="small" @click="order.handlePayment(item.order_no)">立即支付</el-button>
          </div>
        </div>
      </div>
      <el-empty v-else description="暂无订单记录" />
    </div>
  </el-drawer>
</template>

<script setup>
import { useOrderStore } from '../stores/order.js'

const order = useOrderStore()
</script>
