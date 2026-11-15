<template>
  <el-main class="chat-main">
    <!-- 配送状态横幅 -->
    <div class="delivery-status-banner" :class="{ 'is-active': delivery.deliveryResponse }">
      <div class="banner-content" @click="delivery.showDeliveryInput = true">
        <el-icon class="status-icon">
          <Location v-if="!delivery.deliveryResponse" />
          <CircleCheck v-else-if="delivery.deliveryResponse.in_range" />
          <Warning v-else />
        </el-icon>
        <div class="status-info">
          <template v-if="!delivery.deliveryResponse">
            <span class="status-text">未设置配送地址</span>
            <span class="status-hint">点击设置以确认配送范围</span>
          </template>
          <template v-else>
            <span class="status-text">{{ delivery.deliveryResponse.formatted_address }}</span>
            <span class="status-detail">
              {{ delivery.deliveryResponse.in_range ? '在配送范围内' : '超出配送范围' }}
              <template v-if="delivery.deliveryResponse.distance">
                | {{ delivery.deliveryResponse.distance.toFixed(2) }}km | ￥{{ delivery.deliveryResponse.delivery_fee }}配送费 | 约{{ Math.floor(delivery.deliveryResponse.duration/60) }}分钟
              </template>
            </span>
          </template>
        </div>
        <el-button type="primary" link @click.stop="delivery.showDeliveryInput = true">
          {{ delivery.deliveryResponse ? '修改' : '去设置' }}
        </el-button>
      </div>
    </div>

    <!-- 对话窗口区域 -->
    <div class="chat-window" :ref="el => (chat.chatWindow = el)">
      <div class="welcome-msg" v-if="chat.chatHistory.length === 0">
        <el-icon size="48" color="#409EFF"><ChatDotRound /></el-icon>
        <h2>欢迎使用智能点餐助手</h2>
        <p>为了给您提供更好的服务，请先设置配送地址</p>
        <div class="quick-queries">
          <el-button type="primary" round @click="delivery.showDeliveryInput = true">设置配送地址</el-button>
          <el-button round size="small" @click="chat.chatQuery = '有什么不辣的推荐吗？'">不辣的菜品</el-button>
          <el-button round size="small" @click="chat.chatQuery = '我想点一些川菜'">推荐川菜</el-button>
        </div>
      </div>

      <div v-for="(msg, index) in chat.chatHistory" :key="index" :class="['message', msg.role]">
        <div class="avatar">
          <el-icon v-if="msg.role === 'assistant'"><Service /></el-icon>
          <el-icon v-else><User /></el-icon>
        </div>
        <div class="message-content-wrapper">
          <div class="message-content">
            <div v-if="msg.role === 'assistant'">
              <div v-if="msg.content === '' && chat.chatLoading" class="loading-dots">
                <span>.</span><span>.</span><span>.</span>
              </div>
              <div v-else v-html="chat.renderMarkdown(msg.content)" class="markdown-body"></div>
            </div>
            <div v-else>{{ msg.content }}</div>
          </div>
          <!-- AI推荐菜品快捷添加 -->
          <div v-if="msg.role === 'assistant' && msg.menu_ids && msg.menu_ids.length > 0" class="recommendation-quick-add">
            <div v-for="dishId in msg.menu_ids" :key="dishId" class="quick-add-item">
              <span class="quick-dish-name">{{ menu.getDishNameById(dishId) }}</span>
              <el-button type="primary" size="small" link @click="cart.addById(dishId)">+ 加入</el-button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 输入区域 -->
    <div class="input-area">
      <transition name="el-zoom-in-bottom">
        <div class="delivery-bar-wrapper" v-if="delivery.showDeliveryInput">
          <div class="delivery-bar-card">
            <div class="card-header">
              <span>设置配送地址</span>
              <el-button link @click="delivery.showDeliveryInput = false"><el-icon><Close /></el-icon></el-button>
            </div>
            <div class="card-body">
              <el-autocomplete
                v-model="delivery.deliveryAddress"
                :fetch-suggestions="delivery.querySearchTips"
                placeholder="请输入详细配送地址..."
                class="addr-input"
                @select="delivery.handleSelectTip"
                clearable
              >
                <template #prefix><el-icon><Location /></el-icon></template>
                <template #default="{ item }">
                  <div class="tip-item">
                    <div class="tip-name">{{ item.name }}</div>
                    <div class="tip-addr">{{ item.district }}{{ item.address }}</div>
                  </div>
                </template>
              </el-autocomplete>
              <div class="delivery-options">
                <el-select v-model="delivery.travelMode" placeholder="出行方式" style="width: 120px">
                  <el-option label="步行" value="1" />
                  <el-option label="骑行" value="2" />
                  <el-option label="驾车" value="3" />
                </el-select>
                <el-button type="primary" @click="delivery.checkDelivery" :loading="delivery.deliveryLoading" block>开始校验</el-button>
              </div>
            </div>
          </div>
        </div>
      </transition>

      <div class="chat-input-wrapper">
        <el-button
          circle
          @click="delivery.showDeliveryInput = !delivery.showDeliveryInput"
          :type="delivery.deliveryResponse ? (delivery.deliveryResponse.in_range ? 'success' : 'warning') : ''"
        >
          <el-icon><Location /></el-icon>
        </el-button>
        <el-input
          v-model="chat.chatQuery"
          placeholder="在此输入您的需求..."
          @keyup.enter="chat.sendChatQuery()"
          :disabled="chat.chatLoading"
        >
          <template #append>
            <el-button @click="chat.sendChatQuery()" :loading="chat.chatLoading">发送</el-button>
          </template>
        </el-input>
      </div>
    </div>
  </el-main>
</template>

<script setup>
import { watch } from 'vue'
import { useChatStore } from '../stores/chat.js'
import { useDeliveryStore } from '../stores/delivery.js'
import { useCartStore } from '../stores/cart.js'
import { useMenuStore } from '../stores/menu.js'

const chat = useChatStore()
const delivery = useDeliveryStore()
const cart = useCartStore()
const menu = useMenuStore()

watch(() => chat.chatHistory, chat.scrollToBottom, { deep: true })
</script>
