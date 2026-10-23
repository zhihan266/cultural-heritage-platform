import { qaApi, finishedAnswer, qaExecutionIssue, type QaTask } from '@/api/qa'
import { getErrorMessage, getHttpStatus } from '@/api/request'
import { ownsQaTask, qaStatusLabels } from './qaTasks'

export const QA_POLL_INTERVAL_MS = 2500
export const QA_POLL_BUDGET_MS = 180000
export type QaMonitorState =
  | 'idle'
  | 'checking'
  | 'waiting'
  | 'retrying'
  | 'completed'
  | 'failed'
  | 'paused'
  | 'unknown'
  | 'inconsistent'
  | 'exhausted'
  | 'error'
interface Clock {
  now: () => number
  set: (callback: () => void, delay: number) => ReturnType<typeof setTimeout>
  clear: (timer: ReturnType<typeof setTimeout>) => void
}
interface PollOptions {
  onTask: (task: QaTask) => void
  onState: (state: QaMonitorState, message: string) => void
  onAccessError?: () => void
  fetchTask?: (id: string, account: string, signal: AbortSignal) => Promise<QaTask | null>
  visible?: () => boolean
  clock?: Clock
  budgetMs?: number
}
async function query(id: string, account: string, signal: AbortSignal): Promise<QaTask | null> {
  const read = async () => (await qaApi.status(id, signal)).task
  if (typeof navigator !== 'undefined' && navigator.locks)
    return navigator.locks.request(
      `heritage:qa:read:${account}:${id}`,
      { ifAvailable: true },
      async (lock) => (lock && !signal.aborted ? read() : null),
    )
  return read()
}
export function createQaPoller(options: PollOptions) {
  const clock = options.clock || {
    now: () => Date.now(),
    set: (callback, delay) => setTimeout(callback, delay),
    clear: (timer) => clearTimeout(timer),
  }
  const visible =
    options.visible ||
    (() => typeof document === 'undefined' || document.visibilityState === 'visible')
  let generation = 0,
    timer: ReturnType<typeof setTimeout> | undefined,
    controller: AbortController | undefined
  function stop() {
    // Invalidate first: a late response must not reach another task or account.
    generation++
    if (timer !== undefined) clock.clear(timer)
    timer = undefined
    controller?.abort()
  }
  function start(id: string, account: string) {
    stop()
    const current = generation,
      started = clock.now(),
      budget = options.budgetMs ?? QA_POLL_BUDGET_MS
    let failures = 0,
      inconsistent = 0
    const active = () => current === generation
    function state(value: QaMonitorState, message = '') {
      if (active()) options.onState(value, message)
    }
    function schedule(delay: number) {
      if (active())
        timer = clock.set(
          () => void tick(),
          Math.min(delay, Math.max(0, budget - (clock.now() - started))),
        )
    }
    async function tick() {
      if (!active()) return
      timer = undefined
      if (clock.now() - started >= budget) {
        state('exhausted', '本次自动查询已暂停，任务仍保留在服务器，可稍后继续查询。')
        return
      }
      if (!visible()) {
        state('waiting', '页面隐藏，已暂停查询。返回后将继续核对。')
        schedule(QA_POLL_INTERVAL_MS)
        return
      }
      controller = new AbortController()
      state('checking')
      try {
        const task = await (options.fetchTask || query)(id, account, controller.signal)
        if (!active()) return
        if (!task) {
          state('waiting', '另一页面正在读取此任务。')
          schedule(QA_POLL_INTERVAL_MS)
          return
        }
        if (!ownsQaTask(task, account, id)) {
          options.onAccessError?.()
          state('error', '返回的任务不属于当前账号的此问答，已停止读取。')
          return
        }
        options.onTask(task)
        failures = 0
        if (!Object.hasOwn(qaStatusLabels, task.status)) {
          state('unknown', '服务端返回了未识别的任务状态，已停止自动查询。')
          return
        }
        if (task.status === 'completed') {
          if (finishedAnswer(task)) {
            state('completed')
            return
          }
          if (++inconsistent >= 3) {
            state(
              'inconsistent',
              '任务已完成，但服务端未返回有效的正式结果。请稍后核对或联系管理员。',
            )
            return
          }
          state('waiting', '任务已完成，正在重新核对正式结果。')
        } else if (task.status === 'failed') {
          state('failed', '问答任务执行失败，请联系管理员核对问答服务。')
          return
        } else if (qaExecutionIssue(task)) {
          state('paused', qaExecutionIssue(task))
          return
        } else {
          inconsistent = 0
          state('waiting')
        }
        schedule(QA_POLL_INTERVAL_MS)
      } catch (error) {
        if (!active()) return
        const status = getHttpStatus(error)
        if (status === 403 || status === 404 || status === 401) options.onAccessError?.()
        if (status && status < 500) {
          state('error', getErrorMessage(error))
          return
        }
        if (++failures >= 6) {
          state('error', '连续查询失败，无法确认任务的最新状态。任务未被取消，可稍后继续查询。')
          return
        }
        state('retrying', getErrorMessage(error) + ' 正在退避重试，网络失败不代表任务失败。')
        schedule(Math.min(QA_POLL_INTERVAL_MS * 2 ** (failures - 1), 15000))
      }
    }
    void tick()
  }
  return { start, stop }
}
