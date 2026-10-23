import type { CrowdSummary } from '@/api/ragTasks'

export interface TaskReceipt {
  id: string
  relation: 'visited' | 'published' | 'accepted' | 'submitted'
  pendingAnswer?: string
  submittedAnswer?: string
  participated?: boolean
}
const key = (user: string) => `heritage:crowd:v1:${user}`
export function readReceipts(user: string): TaskReceipt[] {
  try {
    const data: unknown = JSON.parse(localStorage.getItem(key(user)) || '[]')
    return Array.isArray(data)
      ? data.filter(
          (item): item is TaskReceipt =>
            !!item &&
            typeof item.id === 'string' &&
            ['visited', 'published', 'accepted', 'submitted'].includes(item.relation) &&
            (item.pendingAnswer === undefined || typeof item.pendingAnswer === 'string') &&
            (item.submittedAnswer === undefined || typeof item.submittedAnswer === 'string') &&
            (item.participated === undefined || typeof item.participated === 'boolean'),
        )
      : []
  } catch {
    return []
  }
}
export function saveReceipt(user: string, update: TaskReceipt) {
  const records = readReceipts(user)
  const current = records.find((item) => item.id === update.id)
  const rank = { visited: 0, accepted: 1, published: 2, submitted: 3 }
  const receipt = { ...current, ...update }
  if (['accepted', 'submitted'].includes(update.relation)) receipt.participated = true
  if (current && rank[current.relation] > rank[update.relation]) receipt.relation = current.relation
  localStorage.setItem(
    key(user),
    JSON.stringify([receipt, ...records.filter((item) => item.id !== update.id)].slice(0, 200)),
  )
  window.dispatchEvent(new Event('crowd-receipts-changed'))
}
export function canAccept(task: CrowdSummary) {
  return (
    task.can_accept &&
    task.status === 'collecting' &&
    !task.my_participation &&
    task.accepted_worker_count < task.required_worker_count
  )
}
export function participationLabel(task: CrowdSummary) {
  const status = task.my_participation?.status
  return status === 'submitted'
    ? '已提交回答'
    : status === 'accepted'
      ? '已接取，待提交'
      : status
        ? `参与状态：${status}`
        : '尚未接取'
}
export function answerByteLength(text: string) {
  return new TextEncoder().encode(text.trim()).length
}
export function validateAnswer(text: string) {
  const length = answerByteLength(text)
  return !length ? '请输入答案正文' : length > 65535 ? '答案不能超过 65535 个 UTF-8 字节' : ''
}
export function formatTime(value?: string | null) {
  if (!value) return '未提供'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '时间不可用'
    : date.toLocaleString('zh-CN', { hour12: false })
}
