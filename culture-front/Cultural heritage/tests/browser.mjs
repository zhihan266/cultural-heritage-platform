// Isolated browser acceptance: only test HTTP responses are substituted.
// The production frontend has no fixture adapter or synthetic task records.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const require = createRequire(
  process.env.PLAYWRIGHT_NODE_MODULES
    ? path.join(process.env.PLAYWRIGHT_NODE_MODULES, '_test.cjs')
    : import.meta.url,
)
const { chromium } = require('playwright')
const base = process.env.FRONTEND_URL || 'http://127.0.0.1:5175'
const artifacts = fileURLToPath(new URL('./artifacts/', import.meta.url))
await mkdir(artifacts, { recursive: true })
const browser = await chromium.launch({
  headless: true,
  ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
})
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
const page = await context.newPage()
const accounts = new Map([
  ['publisher', { id: 'account-publisher', username: 'publisher', score: 1 }],
  ['worker', { id: 'account-worker', username: 'worker', score: 1 }],
])
const agents = [
  {
    id: 'ai-worker',
    name: '验收回答 Agent',
    type: 'worker',
    enabled: true,
    available: true,
    score: 1,
    metadata: {},
  },
  {
    id: 'ai-judge',
    name: '验收聚合 Agent',
    type: 'judge',
    enabled: true,
    available: true,
    score: 1,
    metadata: {},
  },
]
const tasks = new Map(),
  questions = new Map(),
  qaReads = new Map(),
  participants = new Map(),
  answers = new Map()
const calls = [],
  consoleErrors = [],
  checks = []
let firstAnswerUnknown = true,
  failStatus = 0,
  failQaPoll = false,
  delayQaCreate = false,
  releaseQaCreate,
  pauseQa = false,
  ragOutcome = 'retrieved'
function qaTask(id, who, body) {
  return {
    id,
    user_id: who.id,
    question: body.question,
    answer_format: '普通文本',
    ai_ids: ['server-selected-worker-a', 'server-selected-worker-b', 'server-selected-judge'],
    required_worker_count: 0,
    accepted_worker_ids: [],
    status: 'waiting',
    mode: 'hcp_mad',
    answers: [],
    final_result: null,
    metadata: {
      hcp_mad: {
        use_rag: body.use_rag,
        rag_status: body.use_rag ? ragOutcome : 'disabled',
        rag_hit_count: body.use_rag && ragOutcome === 'retrieved' ? 2 : 0,
      },
    },
  }
}
page.on('pageerror', (error) => consoleErrors.push(error.message))
page.on('console', (message) => {
  if (message.type() === 'error') console.error('Browser console:', message.text())
})
page.on('requestfailed', (request) => {
  if (!new URL(request.url()).pathname.startsWith('/api/'))
    console.error('Resource failed:', request.url(), request.failure()?.errorText)
})
function snapshot(id, who) {
  const value = structuredClone(tasks.get(id)),
    joined = participants.get(id)?.has(who.id)
  const content = answers.get(id + ':' + who.id)
  value.is_publisher = value.publisher_id === who.id
  delete value.publisher_id
  value.created_at = '2026-10-23T12:00:00Z'
  value.my_participation = joined
    ? {
        status: content ? 'submitted' : 'accepted',
        accepted_at: value.created_at,
        submitted_at: content ? value.created_at : null,
      }
    : null
  value.my_answer = content || null
  value.can_accept =
    value.status === 'collecting' &&
    !joined &&
    value.accepted_worker_count < value.required_worker_count
  value.can_submit = value.status === 'collecting' && !!joined && !content
  value.threshold_reached = value.valid_worker_count >= value.required_worker_count
  value.result_available = !!value.result
  value.ingestion_status = value.ingestion?.status || null
  value.retry = {
    allowed: value.is_publisher && value.status === 'failed',
    requires_acknowledgement: value.jobs.some((x) => x.status === 'uncertain'),
    phase: 'aggregate',
  }
  return value
}
const task = (id, publisher, body) => ({
  task_id: id,
  publisher_id: publisher,
  title: body.title || null,
  question: body.question,
  answer_format: body.answer_format,
  status: 'collecting',
  required_worker_count: body.worker_count,
  accepted_worker_count: 0,
  valid_worker_count: 0,
  aggregation_status: 'pending',
  jobs: [],
  result: null,
  ingestion: null,
})
await context.route('**/api/**', async (route) => {
  const request = route.request(),
    url = new URL(request.url())
  if (!url.pathname.startsWith('/api/')) return route.continue()
  const pathname = url.pathname.slice(4)
  const method = request.method(),
    body = request.postDataJSON()
  const auth = request.headers().authorization
  const who = [...accounts.values()].find((account) => auth === `Bearer test.${account.id}.token`)
  const reply = (status, data) =>
    route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })
  calls.push({ method, pathname, body, auth })
  if (pathname === '/users/login') {
    const account = accounts.get(body.username)
    return account
      ? reply(200, { access_token: `test.${account.id}.token`, token_type: 'bearer', account })
      : reply(401, { detail: 'Invalid credentials' })
  }
  if (pathname === '/users/logout') return reply(200, { msg: 'logout success' })
  if (failStatus) {
    const status = failStatus
    failStatus = 0
    return reply(status, {
      detail:
        status === 422
          ? [{ msg: '测试参数校验失败', loc: ['body', 'question'] }]
          : 'Traceback: test-only-private-stack',
    })
  }
  if (!who) return reply(401, { detail: 'Not authenticated' })
  if (pathname === '/ais' && method === 'GET') return reply(200, agents)
  if (pathname === '/ais' && method === 'POST') {
    assert.ok(['worker', 'judge'].includes(body.type))
    const agent = {
      ...agents[0],
      ...body,
      id: 'ai-new',
      score: body.initial_score,
      available: false,
      last_checked_at: null,
    }
    agents.push(agent)
    return reply(200, { ai: agent })
  }
  if (/^\/ais\/[^/]+\/health-check$/.test(pathname))
    return reply(200, { available: true, last_checked_at: '2026-10-23T12:00:00', last_error: null })
  if (pathname === '/hcp-mad/tasks' && method === 'POST') {
    assert.deepEqual(Object.keys(body).sort(), ['question', 'use_rag'])
    const id = 'qa-system-' + (questions.size + 1)
    const task = qaTask(id, who, body)
    questions.set(id, task)
    if (delayQaCreate) {
      delayQaCreate = false
      await new Promise((resolve) => {
        releaseQaCreate = resolve
      })
    }
    return reply(200, { task: structuredClone(task) })
  }
  if (pathname.startsWith('/users/tasks/')) {
    if (
      failQaPoll &&
      new URL(request.headers().referer || base).pathname.startsWith('/qa/tasks/')
    ) {
      failQaPoll = false
      return reply(503, { detail: 'test-only-private-stack' })
    }
    const task = questions.get(pathname.split('/').at(-1))
    if (!task) return reply(404, { detail: 'Task does not exist' })
    if (task.user_id !== who.id) return reply(403, { detail: '无权限访问该任务' })
    if (['completed', 'failed'].includes(task.status))
      return reply(200, { task: structuredClone(task) })
    if (pauseQa) {
      task.status = 'answering'
      task.metadata.hcp_mad.debate = { phase: 'review_failed' }
      return reply(200, { task: structuredClone(task) })
    }
    delete task.metadata.hcp_mad.debate
    const count = (qaReads.get(task.id) || 0) + 1
    qaReads.set(task.id, count)
    if (count < 3) {
      task.status = 'answering'
      return reply(200, { task: structuredClone(task) })
    }
    task.status = 'completed'
    task.final_result = task.metadata.hcp_mad.use_rag
      ? '# 知识增强正式回答\n\n**传统木构**注重榫卯连接。\n\n- 记录结构\n- 核对来源\n\n```txt\n<script>unsafe()</script>\n```'
      : '系统普通问答正式结果：保护木构建筑需要持续记录。'
    return reply(200, { task: structuredClone(task) })
  }
  if (
    method === 'GET' &&
    ['/rag-tasks', '/rag-tasks/mine/published', '/rag-tasks/mine/accepted'].includes(pathname)
  ) {
    let items = [...tasks.values()]
      .filter((value) =>
        pathname.endsWith('/published')
          ? value.publisher_id === who.id
          : pathname.endsWith('/accepted')
            ? participants.get(value.task_id)?.has(who.id)
            : true,
      )
      .map((value) => snapshot(value.task_id, who))
    if (url.searchParams.get('status'))
      items = items.filter((value) => value.status === url.searchParams.get('status'))
    if (url.searchParams.get('available_only') === 'true')
      items = items.filter((value) => value.can_accept)
    if (url.searchParams.get('participation_status'))
      items = items.filter(
        (value) => value.my_participation?.status === url.searchParams.get('participation_status'),
      )
    const page = Number(url.searchParams.get('page') || 1),
      pageSize = Number(url.searchParams.get('page_size') || 20)
    return reply(200, {
      items: items.slice((page - 1) * pageSize, page * pageSize),
      total: items.length,
      page,
      page_size: pageSize,
    })
  }
  if (pathname === '/rag-tasks' && method === 'POST') {
    assert.deepEqual(Object.keys(body).sort(), [
      'answer_format',
      'question',
      'title',
      'worker_count',
    ])
    const id = 'rag-task-browser'
    tasks.set(id, task(id, who.id, body))
    participants.set(id, new Set())
    return reply(201, snapshot(id, who))
  }
  const match = /^\/rag-tasks\/([^/]+)(?:\/(accept|answers|status|result|retry))?$/.exec(pathname)
  if (!match) throw new Error(`Frontend called an unsupported endpoint: ${method} ${pathname}`)
  const [, id, operation] = match,
    current = tasks.get(id)
  if (!current) return reply(404, { detail: 'Crowd knowledge task does not exist' })
  const members = participants.get(id)
  if (operation === 'accept') {
    if (current.status !== 'collecting')
      return reply(409, { detail: 'Task is no longer collecting' })
    if (members.has(who.id)) return reply(409, { detail: 'Worker has already accepted this task' })
    if (members.size >= current.required_worker_count)
      return reply(409, { detail: 'Worker capacity reached' })
    members.add(who.id)
    current.accepted_worker_count = members.size
    return reply(200, snapshot(id, who))
  }
  if (operation === 'answers') {
    if (!members.has(who.id)) return reply(403, { detail: 'Worker must accept this task first' })
    const key = id + ':' + who.id,
      previous = answers.get(key)
    if (previous && previous !== body.content)
      return reply(409, { detail: 'A submitted answer is immutable' })
    if (!previous) {
      if (current.status !== 'collecting')
        return reply(409, { detail: 'Task is no longer accepting answers' })
      answers.set(key, body.content)
      current.valid_worker_count++
      if (current.valid_worker_count === current.required_worker_count) {
        current.status = 'aggregating'
        current.jobs = [
          {
            phase: 'aggregate',
            status: 'pending',
            attempts: 0,
            next_run_at: null,
            lease_expires_at: null,
            last_error: null,
          },
        ]
      }
    }
    if (firstAnswerUnknown) {
      firstAnswerUnknown = false
      return route.abort('failed')
    }
    return reply(200, { ...snapshot(id, who), submission_reused: !!previous })
  }
  if (operation === 'retry') {
    assert.equal(who.id, current.publisher_id)
    assert.equal(body.acknowledge_unknown, true)
    current.status = 'aggregating'
    return reply(200, snapshot(id, who))
  }
  return reply(200, snapshot(id, who))
})
const see = (text) => page.getByText(text, { exact: true }).first().waitFor({ timeout: 10000 })
async function login(name, target = page) {
  await target.goto(base + '/login')
  await target.getByLabel('用户名', { exact: true }).fill(name)
  await target.getByLabel('密码', { exact: true }).fill('test-only-password')
  await target.getByRole('button', { name: '登录', exact: true }).click()
  await target.waitForURL('**/dashboard')
}
function seedCrowd(id, publisher, members, count = 1) {
  const record = task(id, publisher, {
    question: id,
    answer_format: '事实与来源',
    worker_count: count,
  })
  record.accepted_worker_count = members.length
  tasks.set(id, record)
  participants.set(id, new Set(members))
  return record
}
async function noOverflow() {
  assert.ok(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
    'Page overflowed viewport',
  )
}
try {
  await page.goto(base + '/rag-tasks')
  await page.waitForURL((url) => url.pathname === '/login' && url.searchParams.has('redirect'))
  checks.push('Protected routes redirect to login')
  await login('publisher')
  await see('工作台')
  await page.locator('img').evaluateAll((images) => {
    if (!images.length || images.some((image) => !image.complete || !image.naturalWidth))
      throw new Error('Dashboard image did not render')
  })
  await page.screenshot({ path: path.join(artifacts, 'dashboard-desktop.png'), fullPage: true })
  await noOverflow()
  await page.goto(base + '/rag-tasks/new')
  await page.getByLabel('任务标题（选填）').fill('木构建筑资料征集')
  await page.getByLabel(/任务问题/).fill('如何记录和保护传统木构建筑？')
  await page.getByLabel('所需工人数').fill('1')
  await page.getByRole('button', { name: '发布任务', exact: true }).click()
  await page.waitForURL('**/rag-tasks/rag-task-browser')
  await see('木构建筑资料征集')
  assert.equal(
    calls.filter((call) => call.pathname === '/rag-tasks' && call.method === 'POST').length,
    1,
  )
  checks.push('Crowd publish uses actual contract; server task ID is used')
  await page.goto(base + '/rag-tasks?view=published')
  await see('木构建筑资料征集')
  await page.getByRole('link', { name: '我接取的任务', exact: true }).click()
  await see('暂无符合条件的任务')
  await page.getByRole('link', { name: '任务广场', exact: true }).click()
  await see('木构建筑资料征集')
  assert.equal(await page.locator('.task-item').count(), 1)
  await page.screenshot({ path: path.join(artifacts, 'public-square-desktop.png'), fullPage: true })
  checks.push(
    'Published and accepted lists use separate server endpoints; public discovery returns publisher tasks',
  )
  await page.getByRole('button', { name: '退出登录', exact: true }).click()
  await page.waitForURL((url) => url.pathname === '/login')
  await login('worker')
  await page.goto(base + '/rag-tasks')
  await see('木构建筑资料征集')
  await page.getByRole('link', { name: '查看详情', exact: true }).click()
  await page.getByRole('button', { name: '接取任务', exact: true }).click()
  await see('接取成功。')
  await page.getByLabel('答案正文').fill('人工提交的原始答案，必须与正式聚合结果分开。')
  await page.getByRole('button', { name: '提交答案', exact: true }).click()
  await see('服务器已确认你提交的回答。')
  await page.reload()
  await see('服务器已确认你提交的回答。')
  assert.equal(await page.getByLabel('答案正文').getAttribute('readonly'), '')
  const submissions = calls.filter((call) => call.pathname.endsWith('/answers'))
  assert.equal(submissions.length, 1)
  assert.equal(await page.getByRole('button', { name: '提交答案', exact: true }).count(), 0)
  checks.push(
    'Unknown POST outcome is reconciled by GET without another submission; server status survives reload',
  )
  const current = tasks.get('rag-task-browser')
  current.result = {
    id: 'result-test',
    content: '# 正式聚合结果\n\n这是经过聚合的正式答案。',
    aggregator_ai_id: 'ai-judge',
    execution_id: 'execution-test',
    answer_count: 1,
    version: 1,
    source_answer_ids: ['answer-test'],
    created_at: '2026-10-23T12:00:00',
  }
  current.status = 'ingesting'
  current.aggregation_status = 'succeeded'
  current.ingestion = {
    status: 'running',
    document_id: null,
    source_id: 'rag-task://test/results/test',
    chunk_count: null,
    attempt_count: 1,
    last_error: null,
  }
  await page.getByRole('button', { name: '刷新状态', exact: true }).click()
  await see('这是经过聚合的正式答案。')
  await see('正式结果已生成，知识入库以独立回执为准。')
  await page.screenshot({ path: path.join(artifacts, 'task-detail-desktop.png'), fullPage: true })
  current.status = 'completed'
  current.ingestion.status = 'succeeded'
  current.ingestion.chunk_count = 3
  await page.getByRole('button', { name: '刷新状态', exact: true }).click()
  await page.getByText('已完成知识入库，切片数量 3。').waitFor()
  checks.push('Official aggregation and ingestion remain independent stages')
  await page.goto(base + '/rag-tasks?view=accepted')
  await see('木构建筑资料征集')
  await see('已提交回答')
  await page.reload()
  await see('木构建筑资料征集')
  assert.ok(
    calls.some((call) => call.pathname === '/rag-tasks/mine/accepted' && call.method === 'GET'),
  )
  await page.getByRole('link', { name: '查看正式结果', exact: true }).click()
  await page.waitForURL('**/rag-tasks/rag-task-browser')
  await page.goBack()
  await page.waitForURL((url) => url.pathname === '/rag-tasks')
  await see('木构建筑资料征集')
  checks.push('Refresh and browser back requery database-backed personal lists')
  await page.getByRole('link', { name: '我发布的任务', exact: true }).click()
  await see('暂无符合条件的任务')
  checks.push('Worker accepted view contains publisher task; worker published view does not')
  const immutable = seedCrowd('rag-task-immutable', 'account-publisher', ['account-worker'], 2)
  immutable.valid_worker_count = 1
  answers.set(immutable.task_id + ':account-worker', '已在其他设备提交的原文')
  await page.goto(base + '/rag-tasks/' + immutable.task_id)
  await see('服务器已确认你提交的回答。')
  assert.equal(await page.getByLabel('答案正文').inputValue(), '已在其他设备提交的原文')
  assert.equal(await page.getByRole('button', { name: '提交答案', exact: true }).count(), 0)
  assert.equal(answers.get(immutable.task_id + ':account-worker'), '已在其他设备提交的原文')
  checks.push('Immutable cross-device answer is not overwritten or misrepresented as the draft')
  const closing = seedCrowd('rag-task-closing', 'account-publisher', ['account-worker'])
  await page.goto(base + '/rag-tasks/' + closing.task_id)
  await page.getByLabel('答案正文').fill('任务关闭前输入的草稿')
  closing.status = 'aggregating'
  const answersBeforeClose = calls.filter((call) => call.pathname.endsWith('/answers')).length
  await page.getByRole('button', { name: '提交答案', exact: true }).click()
  await see('任务已不允许提交，请核对当前状态。')
  assert.equal(
    calls.filter((call) => call.pathname.endsWith('/answers')).length,
    answersBeforeClose,
  )
  checks.push('Pre-submit status refresh blocks a new answer when collection has closed')
  seedCrowd('rag-task-full', 'account-publisher', ['another-test-worker'])
  await page.goto(base + '/rag-tasks/rag-task-full')
  await page.getByText('任务问题', { exact: true }).waitFor()
  assert.equal(await page.getByRole('button', { name: '接取任务', exact: true }).count(), 0)
  await page.goto(base + '/rag-tasks/does-not-exist')
  await see('找不到该众包任务。')
  assert.equal(await page.getByRole('button', { name: '接取任务', exact: true }).count(), 0)
  checks.push('Capacity conflicts and missing tasks are not presented as successful participation')
  const aiCallsBeforeQa = calls.filter((call) => call.pathname === '/ais').length
  await page.goto(base + '/qa')
  assert.equal(await page.locator('select').count(), 0)
  assert.equal(await page.getByRole('button', { name: '刷新 Agent' }).count(), 0)
  await page.getByLabel('输入问题').fill('木构建筑如何保护？')
  await page.getByLabel('输入问题').press('Enter')
  await page.waitForURL('**/qa/tasks/qa-system-1')
  await see('执行中')
  await see('系统普通问答正式结果：保护木构建筑需要持续记录。')
  const firstQaPosts = calls.filter(
    (call) => call.pathname === '/hcp-mad/tasks' && call.method === 'POST',
  ).length
  assert.equal(firstQaPosts, 1)
  assert.deepEqual(calls.find((call) => call.pathname === '/hcp-mad/tasks').body, {
    question: '木构建筑如何保护？',
    use_rag: false,
  })
  const terminalReads = calls.filter((call) => call.pathname === '/users/tasks/qa-system-1').length
  await page.waitForTimeout(2800)
  assert.equal(
    calls.filter((call) => call.pathname === '/users/tasks/qa-system-1').length,
    terminalReads,
  )
  checks.push(
    'Server-issued QA task ID becomes a detail route; polling automatically displays final_result and stops',
  )
  await page.goto(base + '/profile')
  await page.getByRole('link', { name: '查看问答', exact: true }).first().click()
  await page.waitForURL('**/qa/tasks/qa-system-1')
  await see('系统普通问答正式结果：保护木构建筑需要持续记录。')
  await page.reload()
  await see('系统普通问答正式结果：保护木构建筑需要持续记录。')
  assert.equal(calls.filter((call) => call.pathname === '/hcp-mad/tasks').length, firstQaPosts)
  const persisted = await page.evaluate(() =>
    JSON.parse(localStorage.getItem('heritage:qa:ids:v1:account-worker')),
  )
  assert.deepEqual(persisted, ['qa-system-1'])
  checks.push(
    'Personal center, route re-entry and refresh restore server content from an ID-only account index',
  )
  await page.goto(base + '/qa/knowledge')
  await page.getByLabel('输入问题').fill('检索木构建筑的参考资料')
  await page.getByRole('button', { name: '创建问答任务', exact: true }).click()
  await page.waitForURL('**/qa/tasks/qa-system-2')
  await see('知识增强正式回答')
  await see('知识检索命中 2 条资料。当前问答接口未返回引用明细。')
  assert.equal(calls.filter((call) => call.pathname === '/hcp-mad/tasks').at(-1).body.use_rag, true)
  assert.equal(calls.filter((call) => call.pathname === '/ais').length, aiCallsBeforeQa)
  assert.ok(
    !calls.some(
      (call) => call.pathname.startsWith('/rag-tasks/legacy/') || call.pathname === '/rag/answer',
    ),
  )
  assert.equal(await page.locator('main script').count(), 0)
  await page.screenshot({ path: path.join(artifacts, 'qa-desktop.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await noOverflow()
  await page.screenshot({ path: path.join(artifacts, 'qa-mobile.png'), fullPage: true })
  await page.goto(base + '/profile/questions')
  await page.getByText('检索木构建筑的参考资料', { exact: true }).waitFor()
  await noOverflow()
  await page.screenshot({ path: path.join(artifacts, 'qa-history-mobile.png'), fullPage: true })
  checks.push(
    'QA modes remain server-owned after navigation; knowledge and ordinary requests differ by use_rag',
  )
  ragOutcome = 'failed'
  await page.goto(base + '/qa/knowledge')
  await page.getByLabel('输入问题').fill('检索失败时应如何提示？')
  await page.getByRole('button', { name: '创建问答任务', exact: true }).click()
  await see('知识检索失败，本次已按服务端策略降级为无检索回答。')
  await see('知识增强正式回答')
  await page.goto(base + '/qa')
  await page.getByLabel('输入问题').fill('查询失败后自动查询已有结果')
  failQaPoll = true
  const beforeResume = calls.filter((call) => call.pathname === '/hcp-mad/tasks').length
  await page.getByRole('button', { name: '创建问答任务', exact: true }).click()
  await page.getByText(/正在退避重试，网络失败不代表任务失败/).waitFor()
  await see('系统普通问答正式结果：保护木构建筑需要持续记录。')
  assert.equal(calls.filter((call) => call.pathname === '/hcp-mad/tasks').length, beforeResume + 1)
  checks.push(
    'RAG failure is visible; transient read failure backs off and auto-recovers without a second POST',
  )
  await page.goto(base + '/qa')
  await page.getByLabel('输入问题').fill('评审暂停时不要假装继续运行')
  pauseQa = true
  await page.getByRole('button', { name: '创建问答任务', exact: true }).click()
  await see('系统评审阶段已暂停，请联系管理员处理。可稍后查询原问答，不会重新发问。')
  pauseQa = false
  await page.getByRole('button', { name: '核对最新状态', exact: true }).click()
  await see('系统普通问答正式结果：保护木构建筑需要持续记录。')
  checks.push('Paused HCP review is visible; manual refresh queries the existing persisted task')
  const failedQa = qaTask('qa-execution-failed', accounts.get('worker'), {
    question: '失败问答不应永久等待',
    use_rag: false,
  })
  failedQa.status = 'failed'
  failedQa.metadata.hcp_mad.error = 'Traceback: test-only-private-stack'
  questions.set(failedQa.id, failedQa)
  await page.goto(base + '/qa/tasks/' + failedQa.id)
  await see('问答任务执行失败，请联系管理员核对问答服务。')
  assert.ok(!(await page.locator('body').innerText()).includes('private-stack'))
  const failedReads = calls.filter((call) => call.pathname === '/users/tasks/' + failedQa.id).length
  await page.waitForTimeout(2800)
  assert.equal(
    calls.filter((call) => call.pathname === '/users/tasks/' + failedQa.id).length,
    failedReads,
  )
  checks.push('Failed task stops automatic waiting without leaking execution internals')
  const emptyQa = qaTask('qa-empty-result', accounts.get('worker'), {
    question: '已完成但缺正式结果',
    use_rag: false,
  })
  emptyQa.status = 'completed'
  emptyQa.answers = [{ id: 'raw-answer', content: '不可冒充正式结果的原始答案', source: 'ai' }]
  questions.set(emptyQa.id, emptyQa)
  await page.goto(base + '/qa/tasks/' + emptyQa.id)
  await see('任务已完成，但服务端未返回有效的正式结果。请稍后核对或联系管理员。')
  assert.ok(!(await page.locator('body').innerText()).includes('不可冒充正式结果的原始答案'))
  checks.push(
    'Completed without final_result is rechecked and reported, never replaced with a Worker answer',
  )
  await page.goto(base + '/qa')
  await page.getByLabel('输入问题').fill('创建返回前离开页面，任务编号仍须保存')
  delayQaCreate = true
  const delayedRequest = page.waitForRequest((request) =>
    request.url().endsWith('/api/hcp-mad/tasks'),
  )
  await page.getByRole('button', { name: '创建问答任务', exact: true }).click()
  await delayedRequest
  await page.getByRole('link', { name: '个人中心', exact: true }).click()
  await page.waitForURL('**/profile')
  releaseQaCreate()
  await page
    .getByRole('link', { name: '创建返回前离开页面，任务编号仍须保存', exact: true })
    .waitFor()
  assert.equal(new URL(page.url()).pathname, '/profile')
  checks.push(
    'Leaving during creation still indexes the returned server task without redirecting the new page',
  )
  for (const target of [
    '/dashboard',
    '/rag-tasks',
    '/rag-tasks/new',
    '/rag-tasks/rag-task-browser',
    '/ai',
    '/profile',
  ]) {
    await page.goto(base + target)
    await page.locator('h1').first().waitFor()
    await noOverflow()
    if (target === '/ai')
      await page.screenshot({ path: path.join(artifacts, 'agents-mobile.png'), fullPage: true })
  }
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(base + '/ai')
  await page.getByRole('button', { name: '注册 Agent', exact: true }).click()
  await page.getByLabel('显示名称').fill('新增验收 Agent')
  assert.equal(await page.getByLabel('模型接口地址（选填）').getAttribute('required'), null)
  await page.getByRole('button', { name: '确认注册', exact: true }).click()
  await see('新增验收 Agent')
  await page.getByRole('button', { name: '检查连接', exact: true }).last().click()
  checks.push('Agent registration retains canonical roles and health checks')
  failStatus = 503
  await page.getByRole('button', { name: '刷新', exact: true }).click()
  await see('服务暂不可用，请稍后刷新状态。')
  assert.ok(!(await page.locator('body').innerText()).includes('private-stack'))
  failStatus = 401
  await page.getByRole('button', { name: '刷新', exact: true }).click()
  await page.waitForURL((url) => url.pathname === '/login' && url.searchParams.has('redirect'))
  assert.equal(await page.evaluate(() => localStorage.getItem('token')), null)
  checks.push('503 safe messaging and 401 token cleanup/redirect')
  await login('publisher')
  await page.goto(base + '/profile/questions')
  await see('本浏览器暂无此账号的问答任务编号。')
  assert.equal(await page.getByText('木构建筑如何保护？', { exact: true }).count(), 0)
  await page.goto(base + '/qa/tasks/qa-system-1')
  await see('无权限访问该任务')
  assert.ok(!(await page.locator('body').innerText()).includes('系统普通问答正式结果'))
  checks.push(
    'Publisher cannot discover worker QA IDs in personal center or read their private task',
  )
  const failed = task('rag-task-failed', 'account-publisher', {
    question: '失败任务',
    answer_format: '文本',
    worker_count: 1,
  })
  failed.status = 'failed'
  failed.jobs = [
    {
      phase: 'aggregate',
      status: 'uncertain',
      attempts: 1,
      next_run_at: null,
      lease_expires_at: null,
      last_error: 'Traceback: test-only-private-stack',
    },
  ]
  tasks.set(failed.task_id, failed)
  participants.set(failed.task_id, new Set())
  await page.goto(base + '/rag-tasks/rag-task-failed')
  const retry = page.getByRole('button', { name: '重试当前失败阶段' })
  await retry.waitFor()
  await page.getByText('处理记录与来源', { exact: true }).click()
  assert.ok(!(await page.locator('body').innerText()).includes('private-stack'))
  assert.equal(await retry.isDisabled(), true)
  await page.getByRole('checkbox').check()
  await retry.click()
  await page.getByText('聚合处理中', { exact: true }).first().waitFor()
  checks.push('Publisher retry requires explicit unknown-outcome acknowledgement')
  seedCrowd('rag-task-owner-accepted', 'account-publisher', ['account-publisher'])
  await page.goto(base + '/rag-tasks/rag-task-owner-accepted')
  await page.getByLabel('答案正文').waitFor()
  await page.reload()
  await page.getByLabel('答案正文').waitFor()
  await page.goto(base + '/rag-tasks?view=accepted')
  await see('rag-task-owner-accepted')
  assert.equal(await page.getByText('木构建筑资料征集', { exact: true }).count(), 0)
  checks.push('Publisher participation is recovered directly from server state at full capacity')
  const otherTab = await context.newPage()
  otherTab.on('pageerror', (error) => consoleErrors.push(error.message))
  await otherTab.goto(base + '/profile')
  await otherTab.getByRole('button', { name: '退出当前账号', exact: true }).click()
  await page.waitForURL((url) => url.pathname === '/login')
  await login('worker', otherTab)
  await page.waitForURL((url) => url.pathname === '/dashboard')
  await page.getByText('worker，欢迎回来', { exact: true }).waitFor()
  await page.goto(base + '/profile/questions')
  await page.getByRole('link', { name: '木构建筑如何保护？', exact: true }).waitFor()
  checks.push('Re-login restores the same account QA index and re-queries persisted server tasks')
  await page.goto(base + '/rag-tasks?view=accepted')
  await see('木构建筑资料征集')
  assert.equal(await page.getByText('rag-task-owner-accepted', { exact: true }).count(), 0)
  await otherTab.close()
  checks.push(
    'Cross-tab logout and re-login reload the identity and preserve account-isolated records',
  )
  assert.deepEqual(consoleErrors, [])
  await writeFile(
    path.join(artifacts, 'browser-report.json'),
    JSON.stringify(
      {
        checks,
        requests: calls.map(({ method, pathname }) => ({ method, pathname })),
        consoleErrors,
        scope: 'Browser HTTP fixtures only; no live backend or database E2E',
        blockedRequirements: ['Full cross-device QA history: no authenticated HCP list endpoint'],
      },
      null,
      2,
    ),
  )
  console.log(JSON.stringify({ passed: checks.length, checks, consoleErrors }, null, 2))
} catch (error) {
  console.error(
    JSON.stringify(
      {
        url: page.url(),
        consoleErrors,
        body: (await page.locator('body').innerText()).slice(0, 3000),
      },
      null,
      2,
    ),
  )
  await page.screenshot({ path: path.join(artifacts, 'failure.png'), fullPage: true })
  throw error
} finally {
  await browser.close()
}
