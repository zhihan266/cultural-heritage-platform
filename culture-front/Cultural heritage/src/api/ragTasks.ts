import request from './request'

export type CrowdStatus = 'collecting' | 'aggregating' | 'ingesting' | 'completed' | 'failed'
export type JobStatus = 'pending' | 'running' | 'retry' | 'uncertain' | 'succeeded' | 'failed'
export interface CrowdJob {
  phase: 'aggregate' | 'ingest'
  status: JobStatus
}
export interface CrowdResult {
  content: string
  answer_count: number
  version: number
  created_at: string
}
export interface CrowdIngestion {
  status: 'pending' | 'running' | 'succeeded' | 'failed'
  document_id: string | null
  source_id: string
  chunk_count: number | null
}
export interface CrowdSummary {
  task_id: string
  title: string | null
  question: string
  answer_format: string
  status: CrowdStatus
  created_at: string | null
  is_publisher: boolean
  required_worker_count: number
  accepted_worker_count: number
  valid_worker_count: number
  aggregation_status: JobStatus
  ingestion_status: CrowdIngestion['status'] | null
  result_available: boolean
  threshold_reached: boolean
  can_accept: boolean
  can_submit: boolean
  my_participation: {
    status: string
    accepted_at: string
    submitted_at: string | null
  } | null
}
export interface CrowdTask extends CrowdSummary {
  my_answer: string | null
  jobs: CrowdJob[]
  retry: {
    allowed: boolean
    requires_acknowledgement: boolean
    phase: 'aggregate' | 'ingest' | null
  }
  result: CrowdResult | null
  ingestion: CrowdIngestion | null
  submission_reused?: boolean | null
}
export interface CrowdPage {
  items: CrowdSummary[]
  total: number
  page: number
  page_size: number
}
export interface CrowdFilters {
  page?: number
  page_size?: number
  status?: CrowdStatus
  available_only?: boolean
  participation_status?: 'accepted' | 'submitted'
}
export interface PublishCrowdTask {
  question: string
  answer_format: string
  worker_count: number
  title?: string
}
export const crowdStatusLabels: Record<CrowdStatus, string> = {
  collecting: '征集回答',
  aggregating: '聚合处理中',
  ingesting: '知识入库中',
  completed: '已完成',
  failed: '处理失败',
}
export const jobStatusLabels: Record<JobStatus, string> = {
  pending: '等待处理',
  running: '处理中',
  retry: '等待重试',
  uncertain: '执行结果待确认',
  succeeded: '已成功',
  failed: '已失败',
}
const path = (id: string) => `/rag-tasks/${encodeURIComponent(id)}`
export const ragTasksApi = {
  list: (
    scope: 'square' | 'published' | 'accepted',
    params: CrowdFilters,
    signal?: AbortSignal,
  ): Promise<CrowdPage> =>
    request.get(scope === 'square' ? '/rag-tasks' : `/rag-tasks/mine/${scope}`, { params, signal }),
  publish: (body: PublishCrowdTask): Promise<CrowdTask> => request.post('/rag-tasks', body),
  detail: (id: string, signal?: AbortSignal): Promise<CrowdTask> =>
    request.get(path(id), { signal }),
  status: (id: string, signal?: AbortSignal): Promise<CrowdTask> =>
    request.get(`${path(id)}/status`, { signal }),
  accept: (id: string): Promise<CrowdTask> => request.post(`${path(id)}/accept`),
  submit: (id: string, content: string): Promise<CrowdTask> =>
    request.post(`${path(id)}/answers`, { content }),
  result: (id: string): Promise<Pick<CrowdTask, 'task_id' | 'status' | 'result' | 'ingestion'>> =>
    request.get(`${path(id)}/result`),
  retry: (id: string, acknowledgeUnknown = false): Promise<CrowdTask> =>
    request.post(`${path(id)}/retry`, { acknowledge_unknown: acknowledgeUnknown }),
}
