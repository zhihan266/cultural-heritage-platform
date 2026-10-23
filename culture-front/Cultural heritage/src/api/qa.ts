import request, { LONG_REQUEST_TIMEOUT } from './request'

export interface SystemQuestion {
  question: string
  use_rag: boolean
}
export interface QaTask {
  id: string
  user_id: string
  question: string
  answer_format: string
  ai_ids: string[]
  required_worker_count: number
  accepted_worker_ids: string[]
  mode: 'normal' | 'rag' | 'hcp_mad'
  status: 'waiting' | 'answering' | 'aggregating' | 'completed' | 'failed'
  final_result: string | null
  answers: {
    id: string
    task_id: string
    responder_id: string
    source: 'worker' | 'ai'
    content: string
    score_snapshot: number
    weight: number
    execution_id: string | null
  }[]
  metadata: Record<string, unknown>
}
export interface QaResponse {
  task: QaTask
}
// Only this formal workflow selects its execution Agents on the server.
export const qaApi = {
  ask: (body: SystemQuestion, signal?: AbortSignal): Promise<QaResponse> =>
    request.post('/hcp-mad/tasks', body, { timeout: LONG_REQUEST_TIMEOUT, signal }),
  status: (id: string, signal?: AbortSignal): Promise<QaResponse> =>
    request.get(`/users/tasks/${encodeURIComponent(id)}`, { signal }),
}
export function finishedAnswer(task: QaTask): string | null {
  if (task.mode !== 'hcp_mad' || task.status !== 'completed') return null
  return task.final_result?.trim() ? task.final_result : null
}
export function retrievalNotice(task: QaTask): string {
  const progress = task.metadata.hcp_mad
  if (!progress || typeof progress !== 'object' || Array.isArray(progress)) return ''
  const status = Reflect.get(progress, 'rag_status')
  if (status === 'failed') return '知识检索失败，本次已按服务端策略降级为无检索回答。'
  if (status === 'empty') return '本次知识检索未命中资料，回答没有检索证据支持。'
  if (status === 'retrieved') {
    const count = Reflect.get(progress, 'rag_hit_count')
    return (
      (Number.isInteger(count) && count >= 0
        ? `知识检索命中 ${count} 条资料。`
        : '知识检索已完成。') + '当前问答接口未返回引用明细。'
    )
  }
  if (status === 'disabled') return '本次服务端未启用知识检索。'
  return ''
}
export function qaExecutionIssue(task: QaTask): string {
  const progress = task.metadata.hcp_mad
  if (!progress || typeof progress !== 'object') return ''
  const debate = Reflect.get(progress, 'debate')
  if (debate && typeof debate === 'object' && Reflect.get(debate, 'phase') === 'review_failed')
    return '系统评审阶段已暂停，请联系管理员处理。可稍后查询原问答，不会重新发问。'
  return ''
}
