import { defineConfig, devices } from '@playwright/test'

// 前端端到端测试：默认复用已在 3000 端口运行的 Vite dev server，
// 后端需要先在本机 8001 启动（python run.py）。
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:3000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure'
  },
  projects: [
    {
      name: 'chromium',
      // 默认用 Playwright 自带的 Chromium；本机下载受限时可 PW_CHANNEL=msedge 复用系统 Edge
      use: { ...devices['Desktop Chrome'], channel: process.env.PW_CHANNEL || undefined }
    }
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://127.0.0.1:3000',
    reuseExistingServer: true,
    timeout: 120_000
  }
})
