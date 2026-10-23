import request from './request'

export const systemApi = {
  info: (): Promise<{ message: string; docs: string; health: string }> => request.get('/'),
  health: (): Promise<{ status: string }> => request.get('/health'),
}
