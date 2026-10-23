// Real Vue -> Vite proxy -> FastAPI -> SQLite/JWT. No HTTP route fixtures.
// Aggregation/ingestion are intentionally not run by this isolated test host.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { writeFile, mkdir } from 'node:fs/promises'
import path from 'node:path'
const require = createRequire(
  process.env.PLAYWRIGHT_NODE_MODULES
    ? path.join(process.env.PLAYWRIGHT_NODE_MODULES, '_test.cjs')
    : import.meta.url,
)
const { chromium } = require('playwright')
const base = process.env.FRONTEND_URL || 'http://127.0.0.1:5188'
const browser = await chromium.launch({
  headless: true,
  ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
})
const a = await browser.newContext(),
  b = await browser.newContext()
const pageA = await a.newPage(),
  pageB = await b.newPage()
const failures = []
for (const page of [pageA, pageB]) page.on('pageerror', (error) => failures.push(error.message))
const password = 'testPass123'
const names = ['publisher-' + Date.now(), 'worker-' + Date.now()]
async function register(page, name) {
  const response = await page.request.post(base + '/api/users/register', {
    data: { username: name, password },
  })
  assert.equal(response.status(), 200)
  await page.goto(base + '/login')
  await page.getByLabel('用户名', { exact: true }).fill(name)
  await page.getByLabel('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.waitForURL('**/dashboard')
}
try {
  await register(pageA, names[0])
  await register(pageB, names[1])
  await pageA.goto(base + '/rag-tasks/new')
  await pageA.getByLabel('任务标题（选填）').fill('双用户 HTTP 众包验收')
  await pageA.getByLabel(/任务问题/).fill('请整理传统木构建筑保护资料，注明来源和不确定性。')
  await pageA.getByLabel('所需工人数').fill('1')
  await pageA.getByRole('button', { name: '发布任务', exact: true }).click()
  await pageA.waitForURL(/\/rag-tasks\/rag-task-/)
  const taskId = new URL(pageA.url()).pathname.split('/').at(-1)
  await pageB.goto(base + '/rag-tasks?view=published')
  await pageB.getByText('暂无符合条件的任务', { exact: true }).waitFor()
  await pageB.goto(base + '/rag-tasks')
  await pageB.getByRole('link', { name: '双用户 HTTP 众包验收', exact: true }).click()
  await pageB.getByRole('button', { name: '接取任务', exact: true }).click()
  await pageB.getByText('接取成功。', { exact: true }).waitFor()
  await pageB.getByLabel('答案正文').fill('应记录榫卯结构、病害与修缮过程。来源需要进一步核实。')
  await pageB.getByRole('button', { name: '提交答案', exact: true }).click()
  await pageB.getByText('服务器已确认你提交的回答。', { exact: true }).waitFor()
  await pageB.reload()
  await pageB.getByText('服务器已确认你提交的回答。', { exact: true }).waitFor()
  assert.equal(await pageB.getByRole('button', { name: '提交答案', exact: true }).count(), 0)
  await pageB.goto(base + '/rag-tasks?view=accepted')
  await pageB.getByText('已提交回答', { exact: true }).waitFor()
  await pageA.goto(base + '/rag-tasks?view=published')
  await pageA.getByRole('link', { name: '双用户 HTTP 众包验收', exact: true }).click()
  await pageA.getByText('聚合处理中', { exact: true }).first().waitFor()
  const detail = await pageA.evaluate(
    async (id) =>
      (
        await fetch('/api/rag-tasks/' + id, {
          headers: { Authorization: 'Bearer ' + localStorage.getItem('token') },
        })
      ).json(),
    taskId,
  )
  assert.equal(detail.valid_worker_count, 1)
  assert.equal(detail.aggregation_status, 'pending')
  assert.equal(detail.result, null)
  assert.equal(detail.ingestion, null)
  // Create real isolated API records, then verify Vue pagination and filters.
  const created = await pageA.evaluate(async () => {
    const statuses = []
    for (let i = 0; i < 13; i++)
      statuses.push(
        (
          await fetch('/api/rag-tasks', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: 'Bearer ' + localStorage.getItem('token'),
            },
            body: JSON.stringify({
              title: '分页样例 ' + i,
              question: '整理文化遗产保护方法及来源 ' + i,
              worker_count: 1,
            }),
          })
        ).status,
      )
    return statuses
  })
  assert.ok(created.every((status) => status === 201))
  await pageB.goto(base + '/rag-tasks')
  await pageB.getByText('第 1 / 2 页 · 共 14 项', { exact: true }).waitFor()
  assert.equal(await pageB.locator('.task-item').count(), 12)
  await pageB.getByRole('button', { name: '下一页', exact: true }).click()
  await pageB.getByText('第 2 / 2 页 · 共 14 项', { exact: true }).waitFor()
  await pageB.waitForFunction(() => document.querySelectorAll('.task-item').length === 2)
  assert.equal(await pageB.locator('.task-item').count(), 2)
  await pageB.getByLabel('任务状态', { exact: true }).selectOption('completed')
  await pageB.getByText('暂无符合条件的任务', { exact: true }).waitFor()
  await pageB.getByLabel('任务状态', { exact: true }).selectOption('')
  await pageB.getByLabel('仅看可接取', { exact: true }).check()
  await pageB.getByText('第 1 / 2 页 · 共 13 项', { exact: true }).waitFor()
  await pageB.evaluate(() => {
    for (const key of Object.keys(localStorage))
      if (key.startsWith('heritage:crowd:')) localStorage.removeItem(key)
  })
  await pageB.goto(base + '/rag-tasks?view=accepted')
  await pageB.getByText('已提交回答', { exact: true }).waitFor()
  await mkdir('tests/artifacts', { recursive: true })
  await pageB.screenshot({ path: 'tests/artifacts/crowd-http-accepted.png', fullPage: true })
  const report = {
    passed: 1,
    taskId,
    scope: 'Real browser + HTTP + JWT + disposable SQLite',
    steps: [
      'A publishes',
      'B discovers through public GET',
      'B private published list is empty',
      'B accepts and submits',
      'GET restores submitted state after reload',
      'B accepted list',
      'A sees threshold and pending aggregation',
      '14 API-created tasks paginate 12/2',
      'status and available-only filters',
      'accepted state survives clearing local crowd receipts',
    ],
    unverified: ['MySQL writes/locking', 'real model aggregation', 'real Milvus ingestion'],
  }
  assert.deepEqual(failures, [])
  await writeFile('tests/artifacts/crowd-http-report.json', JSON.stringify(report, null, 2))
  console.log(JSON.stringify(report, null, 2))
} finally {
  await browser.close()
}
