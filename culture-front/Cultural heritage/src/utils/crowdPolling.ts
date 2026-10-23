import { ragTasksApi, crowdStatusLabels, type CrowdTask } from '@/api/ragTasks'
import { getErrorMessage, getHttpStatus } from '@/api/request'

interface Options {
  onTask: (task: CrowdTask) => void
  onNotice: (message: string) => void
  fetch?: (id: string, signal: AbortSignal) => Promise<CrowdTask>
  now?: () => number
  set?: typeof setTimeout
  clear?: typeof clearTimeout
  visible?: () => boolean
  budgetMs?: number
}
export function createCrowdPoller(options: Options) {
  const now = options.now || Date.now,
    set = options.set || setTimeout,
    clear = options.clear || clearTimeout
  let generation = 0,
    timer: ReturnType<typeof setTimeout> | undefined,
    controller: AbortController | undefined
  function stop() {
    generation++
    if (timer !== undefined) clear(timer)
    timer = undefined
    controller?.abort()
  }
  function start(id: string) {
    stop()
    const current = generation,
      started = now(),
      budget = options.budgetMs ?? 180000
    let failures = 0
    const active = () => current === generation
    function schedule(delay = 5000) {
      if (active())
        timer = set(() => void tick(), Math.min(delay, Math.max(0, budget - (now() - started))))
    }
    async function tick() {
      if (!active()) return
      timer = undefined
      if (now() - started >= budget) {
        options.onNotice('本次自动查询已暂停，后台任务可能仍在处理。可点击刷新继续核对。')
        return
      }
      if (!(options.visible?.() ?? document.visibilityState === 'visible')) {
        schedule()
        return
      }
      controller = new AbortController()
      try {
        const task = await (options.fetch || ragTasksApi.status)(id, controller.signal)
        if (!active()) return
        if (task.task_id !== id || !Object.hasOwn(crowdStatusLabels, task.status)) {
          options.onNotice('服务端状态无法识别，已暂停自动查询，请联系管理员。')
          return
        }
        options.onTask(task)
        failures = 0
        options.onNotice('')
        if (task.status === 'completed') {
          if (!task.result || task.ingestion?.status !== 'succeeded')
            options.onNotice('任务标记已完成，但正式结果或入库回执不完整，请核对。')
          return
        }
        if (task.status === 'failed') {
          options.onNotice(
            task.result
              ? '正式聚合结果已保存，知识入库未完成，请核对入库阶段。'
              : '聚合处理已暂停，请发布者核对失败阶段。',
          )
          return
        }
        schedule()
      } catch (error) {
        if (!active()) return
        const status = getHttpStatus(error)
        if (status && status < 500) {
          options.onNotice(getErrorMessage(error))
          return
        }
        options.onNotice(getErrorMessage(error) + ' 当前无法确认最新状态。')
        // A failed read is not a failed business task. Manual refresh is always available.
        if (++failures < 3) schedule(5000 * failures)
      }
    }
    void tick()
  }
  return { start, stop }
}
