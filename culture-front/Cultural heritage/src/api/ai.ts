import request, { LONG_REQUEST_TIMEOUT } from './request'
import type { AiRegisterRequest, AiRegisterResponse, AiAgent, AiHealthResponse } from './types'

export const aiApi = {
  registerAi: (data: AiRegisterRequest): Promise<AiRegisterResponse> => request.post('/ais', data),
  listAis: (): Promise<AiAgent[]> => request.get('/ais'),
  healthCheck: (id: string): Promise<AiHealthResponse> =>
    request.post(`/ais/${encodeURIComponent(id)}/health-check`, undefined, {
      timeout: LONG_REQUEST_TIMEOUT,
    }),
}
