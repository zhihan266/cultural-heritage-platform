import test from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import { AxiosError } from 'axios'
import { h } from 'vue'
import { renderToString } from '@vue/server-renderer'

const root = fileURLToPath(new URL('..', import.meta.url))
const backend = fileURLToPath(new URL('../../../Cultural heritage ai', import.meta.url))
const python = process.env.PYTHON || backend + '/.venv/Scripts/python.exe'
const source =
  'import sys,json;sys.path.insert(0,sys.argv[1]);from app.main import app;print(json.dumps(app.openapi()))'
const result = spawnSync(python, ['-B', '-c', source, backend], {
  cwd: backend,
  encoding: 'utf8',
  windowsHide: true,
  timeout: 30000,
  env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' },
})
assert.equal(result.status, 0, result.stderr)
const schema = JSON.parse(result.stdout.trim())
const storage = new Map()
globalThis.localStorage = {
  getItem: (key) => storage.get(key) || null,
  setItem: (key, value) => storage.set(key, value),
  removeItem: (key) => storage.delete(key),
}
const redirects = []
globalThis.window = {
  location: { pathname: '/login', search: '', assign: (url) => redirects.push(url) },
  dispatchEvent() {},
}
const server = await createServer({ root, server: { middlewareMode: true }, logLevel: 'error' })
const {
  default: request,
  getErrorMessage,
  hasErrorDetail,
} = await server.ssrLoadModule('/src/api/request.ts')
const { ragTasksApi } = await server.ssrLoadModule('/src/api/ragTasks.ts')
const { qaApi, finishedAnswer, retrievalNotice, qaExecutionIssue } =
  await server.ssrLoadModule('/src/api/qa.ts')
const { aiApi } = await server.ssrLoadModule('/src/api/ai.ts')
const { authApi } = await server.ssrLoadModule('/src/api/auth.ts')
const { createCrowdPoller } = await server.ssrLoadModule('/src/utils/crowdPolling.ts')
const receipts = await server.ssrLoadModule('/src/utils/crowdTasks.ts')
const { markdownNodes } = await server.ssrLoadModule('/src/utils/markdown.ts')
const qaIndex = await server.ssrLoadModule('/src/utils/qaTasks.ts')
const { createQaPoller } = await server.ssrLoadModule('/src/utils/qaPolling.ts')
const calls = []
let adapter
request.defaults.adapter = async (config) => {
  const path = Object.keys(schema.paths).find((path) =>
    new RegExp('^' + path.replace(/\{[^}]+\}/g, '[^/]+') + '$').test(config.url),
  )
  assert.ok(path, 'Unknown real backend route: ' + config.url)
  assert.ok(schema.paths[path][config.method], config.method + ' ' + path)
  calls.push({
    url: config.url,
    method: config.method,
    headers: config.headers,
    body: config.data ? JSON.parse(config.data) : undefined,
    params: config.params,
  })
  return { status: 200, statusText: 'OK', headers: {}, config, data: await adapter(config) }
}
const task = {
  task_id: 'rag-task-test',
  is_publisher: false,
  can_accept: true,
  can_submit: false,
  created_at: '2026-10-23T12:00:00Z',
  my_participation: null,
  title: '保护资料',
  question: '如何保护木构建筑？',
  answer_format: '事实和来源',
  status: 'collecting',
  required_worker_count: 2,
  accepted_worker_count: 0,
  valid_worker_count: 0,
  aggregation_status: 'pending',
  jobs: [],
  result: null,
  ingestion: null,
}

test('Frontend APIs use current FastAPI contracts and JWT', async (t) => {
  adapter = async () => ({
    access_token: 'signed.jwt.token',
    account: { id: 'account-a', username: '甲', score: 1 },
  })
  const login = await authApi.login({ username: '甲', password: 'test-only-password' })
  storage.set('token', login.access_token)
  adapter = async (config) => {
    if (config.url === '/hcp-mad/tasks' || config.url.startsWith('/users/tasks/'))
      return {
        task: {
          id: 'qa-test',
          user_id: 'account-a',
          question: '木构建筑',
          answer_format: '普通文本',
          ai_ids: [],
          required_worker_count: 0,
          accepted_worker_ids: [],
          answers: [],
          final_result: null,
          mode: 'hcp_mad',
          status: 'waiting',
          metadata: {},
        },
      }
    if (config.url.startsWith('/ais/'))
      return { available: true, last_checked_at: '2026-10-23T12:00:00', last_error: null }
    if (config.url === '/ais')
      return {
        ai: {
          id: 'ai-test',
          name: 'test',
          api_url: 'http://model.test',
          type: 'worker',
          score: 1,
          enabled: true,
          available: false,
          metadata: {},
        },
      }
    return task
  }
  const body = {
    question: task.question,
    answer_format: task.answer_format,
    worker_count: 2,
    title: task.title,
  }
  assert.equal((await ragTasksApi.publish(body)).task_id, task.task_id)
  assert.deepEqual(calls.at(-1).body, body)
  assert.equal(calls.at(-1).headers.Authorization, 'Bearer signed.jwt.token')
  await ragTasksApi.detail(task.task_id)
  await ragTasksApi.status(task.task_id)
  await ragTasksApi.accept(task.task_id)
  assert.equal(calls.at(-1).body, undefined)
  await ragTasksApi.submit(task.task_id, '  原答案  ')
  assert.deepEqual(calls.at(-1).body, { content: '  原答案  ' })
  await ragTasksApi.result(task.task_id)
  await ragTasksApi.retry(task.task_id, true)
  assert.deepEqual(calls.at(-1).body, { acknowledge_unknown: true })
  assert.ok(!calls.some((call) => /\/workers\/|^\/users\/tasks/.test(call.url)))
  const requestBody = schema.paths['/rag-tasks'].post.requestBody.content['application/json'].schema
  const definition = schema.components.schemas[requestBody.$ref.split('/').at(-1)]
  assert.deepEqual(
    Object.keys(definition.properties).sort(),
    ['question', 'answer_format', 'worker_count', 'title'].sort(),
  )
  assert.equal(definition.additionalProperties, false)
  await t.test('QA remains separate from crowds', async () => {
    const body = {
      question: '木构建筑',
      use_rag: false,
    }
    await qaApi.ask(body)
    assert.equal(calls.at(-1).url, '/hcp-mad/tasks')
    assert.deepEqual(calls.at(-1).body, body)
    await qaApi.status('qa-test')
    assert.equal(calls.at(-1).url, '/users/tasks/qa-test')
    await qaApi.ask({ ...body, use_rag: true })
    assert.equal(calls.at(-1).url, '/hcp-mad/tasks')
    assert.equal(calls.at(-1).body.use_rag, true)
    assert.deepEqual(Object.keys(calls.at(-1).body).sort(), ['question', 'use_rag'])
    const hcp = schema.components.schemas.HcpTaskCreateRequest
    assert.deepEqual(hcp.required, ['question'])
    assert.equal(hcp.properties.use_rag.type, 'boolean')
    assert.equal(hcp.additionalProperties, false)
    assert.ok(!('ai_ids' in hcp.properties) && !('user_id' in hcp.properties))
    assert.ok(schema.components.schemas.RagAnswerRequest.required.includes('ai_id'))
    assert.ok(!definition.properties.ai_ids)
    assert.ok(schema.paths['/rag-tasks'].get)
    assert.ok(schema.paths['/rag-tasks/mine/published'].get)
    assert.ok(schema.paths['/rag-tasks/mine/accepted'].get)
    assert.ok(!Object.keys(schema.paths).some((path) => path.startsWith('/rag-tasks/legacy')))
    assert.ok(!('get' in schema.paths['/hcp-mad/tasks']))
    assert.ok(!schema.paths['/users/tasks'])
  })
  await t.test('Agent registration uses canonical roles', async () => {
    await aiApi.registerAi({
      name: 'test',
      type: 'worker',
      api_url: 'http://model.test',
      initial_score: 1,
    })
    assert.equal(calls.at(-1).body.type, 'worker')
    const types = schema.components.schemas.AiType.enum
    assert.deepEqual(types, ['worker', 'judge'])
    await aiApi.healthCheck('ai-test')
    assert.equal(calls.at(-1).url, '/ais/ai-test/health-check')
  })
})

test('Errors distinguish validation, authorization and service failure without exposing stacks', async (t) => {
  for (const status of [401, 403, 404, 409, 422, 500, 503]) {
    await t.test(String(status), async () => {
      adapter = async (config) => {
        const response = {
          status,
          data: {
            detail:
              status === 422
                ? [{ loc: ['body', 'question'], msg: 'Field required' }]
                : status >= 500
                  ? 'Traceback: Bearer secret-key'
                  : 'Unknown task',
          },
          headers: {},
          config,
        }
        throw new AxiosError('Request failed', 'ERR_BAD_RESPONSE', config, null, response)
      }
      let error
      try {
        await ragTasksApi.detail('test')
      } catch (e) {
        error = e
      }
      assert.ok(error)
      const message = getErrorMessage(error)
      assert.ok(message)
      assert.ok(!message.includes('Traceback') && !message.includes('secret-key'))
      if (status === 422) assert.match(message, /Field required/)
    })
  }
  assert.equal(storage.has('token'), false)
  const timeout = new AxiosError('timeout', 'ECONNABORTED')
  assert.match(getErrorMessage(timeout), /未确认/)
  globalThis.window.location.pathname = '/rag-tasks/test'
  adapter = async (config) => {
    throw new AxiosError('Unauthorized', 'ERR_BAD_RESPONSE', config, null, {
      status: 401,
      data: {},
      headers: {},
      config,
    })
  }
  await assert.rejects(ragTasksApi.detail('test'))
  assert.equal(redirects.at(-1), '/login?redirect=%2Frag-tasks%2Ftest')
  globalThis.window.location.pathname = '/login'
})

test('Crowd lists use database API endpoints and preserve server pagination', async () => {
  adapter = async () => ({ items: [task], total: 41, page: 2, page_size: 12 })
  for (const [scope, url] of [
    ['square', '/rag-tasks'],
    ['published', '/rag-tasks/mine/published'],
    ['accepted', '/rag-tasks/mine/accepted'],
  ]) {
    const response = await ragTasksApi.list(scope, { page: 2, page_size: 12, status: 'collecting' })
    assert.equal(calls.at(-1).url, url)
    assert.equal(calls.at(-1).method, 'get')
    assert.deepEqual(calls.at(-1).params, { page: 2, page_size: 12, status: 'collecting' })
    assert.equal(response.total, 41)
  }
})

test('Crowd receipts isolate accounts and preserve exact pending submissions', () => {
  storage.clear()
  receipts.saveReceipt('a', { id: 'real-server-id', relation: 'published' })
  receipts.saveReceipt('a', { id: 'real-server-id', relation: 'accepted' })
  let saved = receipts.readReceipts('a')[0]
  assert.equal(saved.relation, 'published')
  assert.equal(saved.participated, true)
  receipts.saveReceipt('a', {
    id: 'real-server-id',
    relation: 'accepted',
    pendingAnswer: '原始正文',
  })
  saved = receipts.readReceipts('a')[0]
  assert.equal(saved.pendingAnswer, '原始正文')
  assert.deepEqual(receipts.readReceipts('b'), [])
  assert.equal(receipts.canAccept(task), true)
  assert.equal(receipts.canAccept({ ...task, can_accept: false }), false)
  assert.equal(receipts.canAccept({ ...task, my_participation: { status: 'accepted' } }), false)
  for (const status of ['completed', 'aggregating', 'ingesting', 'failed'])
    assert.equal(receipts.canAccept({ ...task, status }), false)
  assert.equal(receipts.participationLabel(task), '尚未接取')
  assert.equal(
    receipts.participationLabel({ ...task, my_participation: { status: 'accepted' } }),
    '已接取，待提交',
  )
  assert.equal(
    receipts.participationLabel({ ...task, my_participation: { status: 'submitted' } }),
    '已提交回答',
  )
  assert.equal(receipts.canAccept({ ...task, accepted_worker_count: 2 }), false)
  assert.equal(receipts.validateAnswer('   '), '请输入答案正文')
  assert.equal(receipts.answerByteLength('中'), 3)
  assert.ok(receipts.validateAnswer('中'.repeat(21846)))
  assert.equal(receipts.validateAnswer('中'.repeat(21845)), '')
})

test('QA only presents formal HCP results and reports real retrieval outcomes', () => {
  const result = {
    mode: 'hcp_mad',
    status: 'completed',
    final_result: '正式结果',
    answers: [{ content: '原始工人回答' }],
    metadata: { hcp_mad: { rag_status: 'retrieved', rag_hit_count: 3 } },
  }
  assert.equal(finishedAnswer(result), '正式结果')
  assert.equal(finishedAnswer({ ...result, status: 'answering' }), null)
  assert.equal(finishedAnswer({ ...result, mode: 'rag' }), null)
  assert.equal(finishedAnswer({ ...result, final_result: '  ' }), null)
  assert.match(retrievalNotice(result), /3 条资料/)
  assert.match(retrievalNotice(result), /未返回引用明细/)
  for (const [status, pattern] of [
    ['failed', /降级/],
    ['empty', /未命中/],
    ['disabled', /未启用/],
  ])
    assert.match(
      retrievalNotice({ ...result, metadata: { hcp_mad: { rag_status: status } } }),
      pattern,
    )
  assert.equal(retrievalNotice({ ...result, metadata: {} }), '')
  assert.match(
    qaExecutionIssue({
      ...result,
      status: 'answering',
      metadata: { hcp_mad: { debate: { phase: 'review_failed' } } },
    }),
    /已暂停/,
  )
  assert.equal(qaExecutionIssue({ ...result, metadata: {} }), '')
})

test('Business conflict checks use server details, not translated UI messages', () => {
  const error = new AxiosError('conflict', 'ERR_BAD_RESPONSE', undefined, null, {
    status: 409,
    data: { detail: 'A submitted answer is immutable' },
  })
  assert.equal(hasErrorDetail(error, 409, 'A submitted answer is immutable'), true)
  assert.equal(hasErrorDetail(error, 409, 'Worker has already accepted this task'), false)
  assert.equal(hasErrorDetail(error, 403, 'A submitted answer is immutable'), false)
})

test('Markdown produces headings, lists, Chinese and code while escaping HTML', async () => {
  const html = await renderToString(
    h(
      'div',
      markdownNodes(
        '# 保护\n\n**事实**与 `code`\n\n- 第一项\n- 第二项\n\n> 引用\n\n```js\n<script>alert(1)</script>\n```\n\n<img src=x onerror=alert(1)>\n[jump](javascript:alert)\n[来源](https://example.org)',
      ),
    ),
  )
  assert.match(html, /<h1>保护<\/h1>/)
  assert.match(html, /<ul>/)
  assert.match(html, /<blockquote>/)
  assert.match(html, /&lt;script&gt;/)
  assert.ok(!html.includes('<img') && !html.includes('href="javascript:'))
  assert.match(html, /rel="noopener noreferrer"/)
})

function systemTask(overrides = {}) {
  return {
    id: 'qa-a',
    user_id: 'a',
    mode: 'hcp_mad',
    question: '问题',
    status: 'answering',
    final_result: null,
    metadata: { hcp_mad: { use_rag: false } },
    answers: [{ content: '非正式原始答案' }],
    ...overrides,
  }
}
const flush = () => new Promise((resolve) => setImmediate(resolve))
function monitor(fetchTask, options = {}) {
  let time = 0,
    serial = 0
  const timers = new Map(),
    states = [],
    values = [],
    accesses = []
  const clock = {
    now: () => time,
    set: (callback, delay) => {
      const id = ++serial
      timers.set(id, { at: time + delay, callback })
      return id
    },
    clear: (id) => timers.delete(id),
  }
  const poller = createQaPoller({
    fetchTask,
    clock,
    onTask: (task) => values.push(task),
    onState: (state, message) => states.push({ state, message }),
    onAccessError: () => accesses.push(true),
    ...options,
  })
  async function advance(ms) {
    const end = time + ms
    for (;;) {
      const next = [...timers]
        .sort((a, b) => a[1].at - b[1].at)
        .find(([, timer]) => timer.at <= end)
      if (!next) break
      timers.delete(next[0])
      time = next[1].at
      next[1].callback()
      await flush()
    }
    time = end
    await flush()
  }
  return { ...poller, advance, timers, states, values, accesses, state: () => states.at(-1)?.state }
}
test('QA index stores only account-isolated server IDs, never text, results or status', () => {
  storage.clear()
  qaIndex.rememberQaId('a', 'qa-a')
  qaIndex.rememberQaId('a', 'qa-a')
  qaIndex.rememberQaId('b', 'qa-b')
  assert.deepEqual(qaIndex.readQaIds('a'), ['qa-a'])
  assert.deepEqual(qaIndex.readQaIds('b'), ['qa-b'])
  assert.deepEqual(JSON.parse(storage.get(qaIndex.qaIndexKey('a'))), ['qa-a'])
  assert.equal(qaIndex.qaMode(systemTask()), '普通问答')
  assert.equal(
    qaIndex.qaMode(systemTask({ metadata: { hcp_mad: { use_rag: true } } })),
    '知识库问答',
  )
  assert.equal(qaIndex.ownsQaTask(systemTask(), 'b'), false)
})
test('QA monitor follows server progress to the formal result, then stops', async () => {
  let calls = 0
  const m = monitor(async () =>
    systemTask(++calls < 3 ? {} : { status: 'completed', final_result: '正式结果' }),
  )
  m.start('qa-a', 'a')
  await flush()
  await m.advance(5000)
  assert.equal(m.state(), 'completed')
  assert.equal(m.values.at(-1).final_result, '正式结果')
  await m.advance(60000)
  assert.equal(calls, 3)
  assert.equal(m.timers.size, 0)
})
test('QA monitor handles failed, paused, unknown and inconsistent states without endless waiting', async (t) => {
  for (const [task, expected] of [
    [systemTask({ status: 'failed' }), 'failed'],
    [systemTask({ metadata: { hcp_mad: { debate: { phase: 'review_failed' } } } }), 'paused'],
    [systemTask({ status: 'new-server-state' }), 'unknown'],
    [systemTask({ status: 'toString' }), 'unknown'],
    [systemTask({ status: 'completed', final_result: ' ' }), 'inconsistent'],
  ])
    await t.test(expected + ':' + task.status, async () => {
      let calls = 0
      const m = monitor(async () => {
        calls++
        return task
      })
      m.start('qa-a', 'a')
      await flush()
      await m.advance(60000)
      assert.equal(m.state(), expected)
      assert.ok(calls <= 3)
      assert.equal(m.timers.size, 0)
    })
})
test('Completed without result is rechecked for consistency before presenting an error', async () => {
  let calls = 0
  const m = monitor(async () =>
    systemTask({ status: 'completed', final_result: ++calls === 1 ? null : '迟到的正式结果' }),
  )
  m.start('qa-a', 'a')
  await flush()
  await m.advance(2500)
  assert.equal(m.state(), 'completed')
  assert.equal(calls, 2)
})
test('Temporary query failure backs off and recovers without failing or recreating the task', async () => {
  let calls = 0
  const m = monitor(async () => {
    if (++calls === 1) throw new AxiosError('timeout', 'ECONNABORTED')
    return systemTask({ status: 'completed', final_result: '恢复后的正式结果' })
  })
  m.start('qa-a', 'a')
  await flush()
  assert.equal(m.state(), 'retrying')
  await m.advance(2500)
  assert.equal(m.state(), 'completed')
  assert.equal(calls, 2)
})
test('QA monitor budget and repeated transport failures stop with query, not task, errors', async () => {
  const m = monitor(async () => systemTask(), { budgetMs: 3000 })
  m.start('qa-a', 'a')
  await flush()
  await m.advance(3000)
  assert.equal(m.state(), 'exhausted')
  assert.equal(m.timers.size, 0)
  const n = monitor(async () => {
    throw new AxiosError('network')
  })
  n.start('qa-a', 'a')
  await flush()
  await n.advance(90000)
  assert.equal(n.state(), 'error')
  assert.equal(n.timers.size, 0)
  assert.ok(!n.states.some((state) => state.state === 'failed'))
})
test('QA monitor never overlaps requests and stop cancels in-flight reads and scheduled timers', async () => {
  let resolve,
    signal,
    calls = 0
  const m = monitor(async (_id, _account, value) => {
    signal = value
    calls++
    return new Promise((done) => {
      resolve = done
    })
  })
  m.start('qa-a', 'a')
  await m.advance(20000)
  assert.equal(calls, 1)
  m.stop()
  assert.equal(signal.aborted, true)
  resolve(systemTask())
  await flush()
  assert.deepEqual(m.values, [])
  assert.equal(m.timers.size, 0)
  const n = monitor(async () => systemTask())
  n.start('qa-a', 'a')
  await flush()
  n.stop()
  await n.advance(10000)
  assert.equal(n.values.length, 1)
})
test('Switching task or account rejects stale responses and mismatched ownership', async () => {
  let resolve
  const m = monitor(async (id) =>
    id === 'qa-a'
      ? new Promise((done) => {
          resolve = done
        })
      : systemTask({ id: 'qa-b', user_id: 'b', status: 'completed', final_result: 'B 正式结果' }),
  )
  m.start('qa-a', 'a')
  m.start('qa-b', 'b')
  await flush()
  resolve(systemTask({ status: 'completed', final_result: '旧结果' }))
  await flush()
  assert.equal(m.values.length, 1)
  assert.equal(m.values[0].id, 'qa-b')
  const n = monitor(async () => systemTask())
  n.start('qa-a', 'b')
  await flush()
  assert.equal(n.state(), 'error')
  assert.deepEqual(n.values, [])
  assert.equal(n.accesses.length, 1)
})
test('Hidden views pause reads, and private access failures stop without leaking data', async () => {
  let visible = false,
    calls = 0
  const m = monitor(
    async () => {
      calls++
      return systemTask({ status: 'completed', final_result: '结果' })
    },
    { visible: () => visible },
  )
  m.start('qa-a', 'a')
  await m.advance(2500)
  assert.equal(calls, 0)
  visible = true
  await m.advance(2500)
  assert.equal(calls, 1)
  const n = monitor(async () => {
    throw new AxiosError('Forbidden', 'ERR_BAD_RESPONSE', undefined, null, {
      status: 403,
      data: {},
    })
  })
  n.start('qa-a', 'a')
  await flush()
  assert.equal(n.state(), 'error')
  assert.equal(n.accesses.length, 1)
  assert.equal(n.timers.size, 0)
})

test('Crowd polling has no overlapping reads, ignores stale responses, stops on completion and at budget', async () => {
  let now = 0,
    finish,
    calls = 0,
    current,
    notice = ''
  const timers = new Map()
  let timerId = 0
  const poll = createCrowdPoller({
    now: () => now,
    visible: () => true,
    budgetMs: 10000,
    set: (callback, delay) => {
      const id = ++timerId
      timers.set(id, { callback, delay })
      return id
    },
    clear: (id) => timers.delete(id),
    onTask: (task) => {
      current = task
    },
    onNotice: (message) => {
      notice = message
    },
    fetch: () => {
      calls++
      return new Promise((resolve) => {
        finish = resolve
      })
    },
  })
  poll.start('a')
  assert.equal(calls, 1)
  assert.equal(timers.size, 0)
  const old = finish
  poll.start('b')
  old({ ...task, task_id: 'a' })
  await flush()
  assert.equal(current, undefined)
  finish({ ...task, task_id: 'b', status: 'collecting' })
  await flush()
  assert.equal(current.task_id, 'b')
  assert.equal(timers.size, 1)
  const callback = [...timers.values()][0].callback
  timers.clear()
  now = 10000
  callback()
  assert.match(notice, /自动查询已暂停/)
  assert.equal(calls, 2)
  poll.start('c')
  finish({
    ...task,
    task_id: 'c',
    status: 'completed',
    result: { content: '正式知识' },
    ingestion: { status: 'succeeded' },
  })
  await flush()
  assert.equal(timers.size, 0)
  poll.stop()
})

test.after(async () => {
  await server.close()
  delete globalThis.localStorage
  delete globalThis.window
})
