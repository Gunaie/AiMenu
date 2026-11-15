import { nextTick, ref } from 'vue'
import { defineStore } from 'pinia'
import { chatAPI } from '../api/index.js'
import { useMenuStore } from './menu.js'

export const useChatStore = defineStore('chat', () => {
  const sessionId = ref('session-' + Math.random().toString(36).substring(2, 9))
  const chatQuery = ref('')
  const chatHistory = ref([])
  const chatLoading = ref(false)
  const chatWindow = ref(null)

  const scrollToBottom = () => {
    nextTick(() => {
      if (chatWindow.value) {
        chatWindow.value.scrollTop = chatWindow.value.scrollHeight
      }
    })
  }

  const escapeHtml = (text) =>
    text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;')

  const renderMarkdown = (text) => {
    if (!text) return ''
    // 先转义 HTML 再做 Markdown 替换，避免模型回显用户输入时注入标签
    let html = escapeHtml(text)
      .replace(/#{3} (.*?)$/gm, '<h3>$1</h3>')
      .replace(/#{2} (.*?)$/gm, '<h2>$1</h2>')
      .replace(/#{1} (.*?)$/gm, '<h1>$1</h1>')
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/^- (.*?)$/gm, '<li>$1</li>')

    // 合并连续的 <li> 到 <ul>
    html = html.replace(/(<li>.*?<\/li>\n?)+/gs, (match) => {
      return `<ul>${match.trim()}</ul>`
    })

    // 只有不在标签内部的换行才转为 <br/>
    html = html.replace(/\n/g, '<br/>')

    // 仅在正在生成的最新一条 AI 回复末尾添加光标
    const lastMsg = chatHistory.value[chatHistory.value.length - 1]
    if (chatLoading.value && lastMsg && lastMsg.role === 'assistant' && lastMsg.content === text) {
      html += '<span class="cursor">|</span>'
    }
    return html
  }

  /** 发送消息；不传参时读取输入框内容（兼容登录后的问候语触发） */
  const sendChatQuery = async (overrideQuery) => {
    const query = (overrideQuery ?? chatQuery.value).trim()
    if (!query || chatLoading.value) return

    chatHistory.value = [...chatHistory.value, { role: 'user', content: query }]
    if (overrideQuery === undefined) {
      chatQuery.value = ''
    }
    chatLoading.value = true

    // 创建一个占位的 AI 回复
    chatHistory.value = [...chatHistory.value, { role: 'assistant', content: '', menu_ids: [] }]
    const assistantIndex = chatHistory.value.length - 1

    try {
      await chatAPI.sendMessageStream(query, sessionId.value, (chunk) => {
        if (chunk.type === 'content') {
          // 直接替换整个数组项，确保 Vue 3 完美触发渲染
          const updatedMsg = { ...chatHistory.value[assistantIndex] }
          updatedMsg.content += chunk.content
          chatHistory.value[assistantIndex] = updatedMsg
        } else if (chunk.type === 'metadata') {
          if (chunk.menu_ids) {
            const updatedMsg = { ...chatHistory.value[assistantIndex] }
            updatedMsg.menu_ids = chunk.menu_ids
            chatHistory.value[assistantIndex] = updatedMsg
            useMenuStore().highlightRecommendedItems(chunk.menu_ids)
          }
        }
      })
    } catch (error) {
      console.error('流式对话失败:', error)
      chatHistory.value[assistantIndex] = {
        ...chatHistory.value[assistantIndex],
        content: '服务响应异常，请稍后再试。'
      }
    } finally {
      chatLoading.value = false
    }
  }

  const sendInitialGreeting = async () => {
    const greeting = '小二，我来啦！'
    chatHistory.value.push({ role: 'user', content: greeting })
    await sendChatQuery(greeting)
  }

  return {
    sessionId, chatQuery, chatHistory, chatLoading, chatWindow,
    scrollToBottom, renderMarkdown, sendChatQuery, sendInitialGreeting
  }
})
