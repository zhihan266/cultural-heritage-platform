import type { QaTask } from '@/api/qa'

export const QA_INDEX_EVENT = 'qa-index-changed'
export const qaIndexKey = (account: string) => `heritage:qa:ids:v1:${account}`
export const qaPendingKey = (account: string) => `heritage:qa:pending:${account}`
export function readQaIds(account: string): string[] {
  if (!account) return []
  try {
    const value: unknown = JSON.parse(localStorage.getItem(qaIndexKey(account)) || '[]')
    return Array.isArray(value)
      ? [
          ...new Set(
            value.filter(
              (id): id is string => typeof id === 'string' && !!id.trim() && id.length <= 128,
            ),
          ),
        ]
      : []
  } catch {
    return []
  }
}
// Persist only server-issued IDs. Never cache private answers or authoritative status here.
export function rememberQaId(account: string, id: string) {
  if (!account || !id.trim() || id.length > 128) throw new Error('Invalid QA identity')
  const ids = readQaIds(account)
  if (ids.includes(id)) return
  localStorage.setItem(qaIndexKey(account), JSON.stringify([id, ...ids]))
  window.dispatchEvent(new Event(QA_INDEX_EVENT))
}
export function qaMode(task: QaTask) {
  const progress = task.metadata.hcp_mad
  if (!progress || typeof progress !== 'object') return '模式未提供'
  const useRag: unknown = Reflect.get(progress, 'use_rag')
  return useRag === true ? '知识库问答' : useRag === false ? '普通问答' : '模式未提供'
}
export const qaStatusLabels: Record<QaTask['status'], string> = {
  waiting: '等待执行',
  answering: '执行中',
  aggregating: '生成正式结果',
  completed: '已完成',
  failed: '执行失败',
}
export function ownsQaTask(task: QaTask, account: string, id = task.id) {
  return task.mode === 'hcp_mad' && task.user_id === account && task.id === id
}
