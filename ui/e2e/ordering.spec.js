import { test, expect, request as playwrightRequest } from '@playwright/test'

const USER = { username: 'admin', password: 'password123' }
const API_BASE = 'http://127.0.0.1:8001'

/** 走真实登录接口登录，并等待菜单数据加载完成 */
async function loginViaUI(page) {
  await page.goto('/')

  const dialog = page.locator('.el-dialog', { hasText: '欢迎回来' })
  await expect(dialog).toBeVisible()
  await dialog.locator('input[placeholder="请输入用户名"]').fill(USER.username)
  await dialog.locator('input[placeholder="请输入密码"]').fill(USER.password)
  await dialog.getByRole('button', { name: '立即登录' }).click()

  await expect(dialog).toBeHidden({ timeout: 30_000 })
  await expect(page.locator('.user-logged-in .nickname')).toBeVisible()
  await expect(page.locator('.menu-card').first()).toBeVisible({ timeout: 30_000 })
}

test('未登录时被登录墙拦截，登录后进入点餐界面', async ({ page }) => {
  await page.goto('/')

  // 未登录：主区域模糊 + 强制登录弹窗，且没有关闭按钮
  await expect(page.locator('.main-container')).toHaveClass(/is-blurred/)
  const dialog = page.locator('.el-dialog', { hasText: '欢迎回来' })
  await expect(dialog).toBeVisible()
  await expect(dialog.locator('.el-dialog__headerbtn')).toHaveCount(0)

  await dialog.locator('input[placeholder="请输入用户名"]').fill(USER.username)
  await dialog.locator('input[placeholder="请输入密码"]').fill(USER.password)
  await dialog.getByRole('button', { name: '立即登录' }).click()

  await expect(dialog).toBeHidden({ timeout: 30_000 })
  await expect(page.locator('.main-container')).not.toHaveClass(/is-blurred/)
  await expect(page.locator('.user-logged-in .nickname')).not.toBeEmpty()
  await expect(page.locator('.menu-card').first()).toBeVisible({ timeout: 30_000 })
})

test('真实流式对话：回复增量渲染并给出可加购的推荐菜品', async ({ page }) => {
  test.setTimeout(300_000)
  await loginViaUI(page)

  // 登录后会先发一条招呼语，等它流式结束（输入框解除禁用）再发正式问题
  const input = page.locator('input[placeholder="在此输入您的需求..."]')
  await expect(input).toBeEnabled({ timeout: 240_000 })

  // 记录流式过程中回复文本长度的变化，用来证明是增量渲染而不是一次性返回
  await page.evaluate(() => {
    window.__streamLengths = []
    const observer = new MutationObserver(() => {
      const nodes = document.querySelectorAll('.message.assistant .markdown-body')
      if (!nodes.length) return
      const text = nodes[nodes.length - 1].textContent || ''
      if (text) window.__streamLengths.push(text.length)
    })
    observer.observe(document.body, { childList: true, subtree: true, characterData: true })
    window.__streamObserver = observer
  })

  await input.fill('有什么清淡不辣的汤推荐？')
  await page.getByRole('button', { name: '发送' }).click()

  const lastReply = page.locator('.message.assistant .markdown-body').last()
  await expect(lastReply).toBeVisible({ timeout: 180_000 })
  await expect(lastReply).not.toBeEmpty({ timeout: 180_000 })

  const quickAdd = page.locator('.recommendation-quick-add').last()
  await expect(quickAdd).toBeVisible({ timeout: 180_000 })

  const dishName = ((await quickAdd.locator('.quick-dish-name').first().textContent()) || '').trim()
  expect(dishName.length).toBeGreaterThan(0)

  const streamLengths = await page.evaluate(() => {
    window.__streamObserver?.disconnect()
    return window.__streamLengths
  })
  // 至少观察到 2 个不同的长度，说明前端是边收边渲染
  expect(new Set(streamLengths).size).toBeGreaterThan(1)

  // 推荐卡片可直接加购
  await quickAdd.getByRole('button', { name: /加入/ }).first().click()
  await expect(page.locator('.cart-badge .el-badge__content')).toHaveText(/[1-9]/)
})

test('下单：重复点击提交只生成一笔订单', async ({ page }) => {
  test.setTimeout(180_000)

  // 用后端接口取订单基线，最终以数据库里的订单数为准
  const apiCtx = await playwrightRequest.newContext({ baseURL: API_BASE })
  const loginResp = await apiCtx.post('/auth/login', { form: USER })
  const { access_token } = await loginResp.json()
  const authHeaders = { Authorization: `Bearer ${access_token}` }
  const beforeCount = (await (await apiCtx.get('/order/list?limit=50', { headers: authHeaders })).json()).orders.length

  await loginViaUI(page)

  // 真实调用高德配送校验：地址取餐厅（武汉光谷）附近的在范围地址
  await page.locator('.delivery-status-banner').getByRole('button', { name: /去设置|修改/ }).click()
  await page.locator('.addr-input input').fill('武汉市洪山区光谷步行街')
  await page.getByRole('button', { name: '开始校验' }).click()
  await expect(page.locator('.delivery-status-banner')).toContainText('在配送范围内', { timeout: 30_000 })

  await page.locator('.menu-card').first().getByRole('button', { name: '加入购物车' }).click()
  await page.locator('.cart-badge button').click()

  const drawer = page.locator('.el-drawer', { hasText: '您的购物车' })
  await expect(drawer).toBeVisible()
  await expect(drawer.locator('.cart-item')).toHaveCount(1)

  const submitRequests = []
  page.on('request', (req) => {
    if (req.method() === 'POST' && req.url().includes('/order/submit')) {
      submitRequests.push(req.headers())
    }
  })

  const checkoutBtn = drawer.getByRole('button', { name: '提交订单' })
  // 同一帧内连点两次，模拟用户双击
  await checkoutBtn.evaluate((el) => {
    el.click()
    el.click()
  })

  const confirmBox = page.locator('.el-message-box', { hasText: '订单确认' })
  await expect(confirmBox).toBeVisible()
  await expect(page.locator('.el-message-box', { hasText: '订单确认' })).toHaveCount(1)

  await confirmBox.getByRole('button', { name: '立即下单' }).click()

  const payBox = page.locator('.el-message-box', { hasText: '待支付' })
  await expect(payBox).toBeVisible({ timeout: 30_000 })
  await payBox.getByRole('button', { name: '稍后支付' }).click()
  await expect(payBox).toBeHidden()

  // 双击只发一次请求，且请求头带幂等键
  expect(submitRequests.length).toBe(1)
  expect(submitRequests[0]['idempotency-key']).toBeTruthy()

  const afterCount = (await (await apiCtx.get('/order/list?limit=50', { headers: authHeaders })).json()).orders.length
  expect(afterCount).toBe(beforeCount + 1)

  await apiCtx.dispose()
})
