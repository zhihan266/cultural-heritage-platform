import axios from 'axios'

export const LONG_REQUEST_TIMEOUT = 180_000
export const getHttpStatus = (error: unknown): number | undefined =>
  axios.isAxiosError(error) ? error.response?.status : undefined
export const isUncertainRequest = (error: unknown): boolean =>
  axios.isAxiosError(error) && (!error.response || error.response.status >= 500)
export const hasErrorDetail = (error: unknown, status: number, detail: string): boolean =>
  axios.isAxiosError(error) &&
  error.response?.status === status &&
  error.response?.data?.detail === detail

const knownDetails: Record<string, string> = {
  'Task is no longer collecting': '任务已结束征集，请刷新状态。',
  'Worker has already accepted this task': '你已接取此任务，请打开详情。',
  'Worker capacity reached': '任务参与名额已满。',
  'Worker must accept this task first': '请先接取此任务。',
  'A submitted answer is immutable': '答案已提交，不能修改。请核对原提交记录。',
  'Task is no longer accepting answers': '任务已进入处理阶段，不再接收新答案。',
  'Only the publisher or a participant can access this task':
    '只有发布者或已参与的账号可以查看详情。',
  'Crowd knowledge task does not exist': '找不到该众包任务。',
  'Only the publisher can retry processing': '只有任务发布者可以重试。',
  'Only failed tasks can be retried': '只有处理失败的任务可以重试。',
  'No failed processing stage': '没有可重试的处理阶段。',
  'No enabled judge is configured': '系统问答尚未配置评审模型，请联系管理员。',
}
export function getErrorMessage(error: unknown, fallback = '请求失败，请稍后重试'): string {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status
    if (!status)
      return error.code === 'ECONNABORTED'
        ? '请求超时，操作结果尚未确认。请先核对服务器状态。'
        : '无法连接服务器，请检查网络后重试。'
    if (status === 401) return '登录已失效，请重新登录。'
    if (status >= 500)
      return status === 503
        ? '服务暂不可用，请稍后刷新状态。'
        : '服务器处理失败，请稍后核对状态或联系管理员。'
    const detail: unknown = error.response?.data?.detail
    if (typeof detail === 'string')
      return (
        knownDetails[detail] ||
        (detail.length <= 240 && !/Traceback|File "|Bearer |api[_-]?key/i.test(detail)
          ? detail
          : fallback)
      )
    if (status === 422 && Array.isArray(detail)) {
      return detail
        .map((item: unknown) => {
          if (!item || typeof item !== 'object') return '参数不正确'
          const msg = Reflect.get(item, 'msg')
          return typeof msg === 'string' ? msg.slice(0, 160) : '参数不正确'
        })
        .join('；')
    }
    if (status === 403) return '没有权限执行此操作。'
    if (status === 404) return '未找到对应记录。'
    if (status === 409) return '任务状态已变化，请刷新后核对。'
  }
  return fallback
}
const request = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api',
  timeout: 10000,
})
request.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = 'Bearer ' + token
  return config
})
request.interceptors.response.use(
  (response) => response.data,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('userInfo')
      if (window.location.pathname !== '/login') {
        window.location.assign(
          '/login?redirect=' +
            encodeURIComponent(window.location.pathname + window.location.search),
        )
      }
    }
    return Promise.reject(error)
  },
)
export default request
