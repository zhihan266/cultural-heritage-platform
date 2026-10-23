import request, { LONG_REQUEST_TIMEOUT } from './request'
import type {
  RagRetrieveRequest,
  RagRetrieveResponse,
  RagAnswerRequest,
  RagAnswerResponse,
  RagDocumentIndexResponse,
} from './types'

export const ragApi = {
  retrieve: (data: RagRetrieveRequest): Promise<RagRetrieveResponse> =>
    request.post('/rag/retrieve', data, { timeout: LONG_REQUEST_TIMEOUT }),
  answer: (data: RagAnswerRequest, signal?: AbortSignal): Promise<RagAnswerResponse> =>
    request.post('/rag/answer', data, { timeout: LONG_REQUEST_TIMEOUT, signal }),
  indexDocument: (file: File): Promise<RagDocumentIndexResponse> => {
    const body = new FormData()
    body.append('file', file)
    return request.post('/admin/rag/documents', body, { timeout: LONG_REQUEST_TIMEOUT })
  },
}
