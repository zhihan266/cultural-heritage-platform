export enum AiType {
  WORKER = 'worker',
  JUDGE = 'judge',
}

// Users 相关
export interface Account {
  id: string
  username: string
  score: number
}

export interface LoginRequest {
  username: string
  password: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  account: Account
}

export interface RegisterRequest {
  username: string
  password: string
}

export interface RegisterResponse {
  user_id: string
  username: string
}

export interface RagRetrieveRequest {
  query: string
  top_k?: number
  document_id?: string | null
}

export interface RagAnswerSource {
  chunk_id: string
  document_id: string
  source: string
  chunk_index: number
  score: number
}

export interface RagRetrieveHit extends RagAnswerSource {
  content: string
}

export interface RagRetrieveResponse {
  query: string
  hits: RagRetrieveHit[]
}

export interface RagAnswerRequest extends RagRetrieveRequest {
  ai_id: string
}

export interface RagAnswerResponse {
  query: string
  ai_id: string
  answer: string
  sources: RagAnswerSource[]
}

export interface RagDocumentIndexResponse {
  filename: string
  document_id: string
  message: string
}

// AIs 相关
export interface AiAgent {
  id: string
  name: string
  api_url?: string | null
  type: AiType
  score: number
  enabled: boolean
  available: boolean
  last_checked_at?: string | null
  last_error?: string | null
  metadata: Record<string, unknown>
  provider?: string | null
  model_name?: string | null
  auth_type?: string
  credential_ref?: string | null
  generation_config?: Record<string, unknown> | null
}

export interface AiRegisterRequest {
  name: string
  api_url?: string | null
  type?: AiType
  initial_score?: number
  provider?: string | null
  model_name?: string | null
  auth_type?: string
  credential_ref?: string | null
  generation_config?: Record<string, unknown> | null
}

export interface AiRegisterResponse {
  ai: AiAgent
}

export interface AiHealthResponse {
  available: boolean
  last_checked_at: string
  last_error?: string | null
}
