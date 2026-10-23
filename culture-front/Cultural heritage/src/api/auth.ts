import request from './request'
import type { LoginRequest, LoginResponse, RegisterRequest, RegisterResponse } from './types'

export const authApi = {
  register: (data: RegisterRequest): Promise<RegisterResponse> => {
    return request.post('/users/register', data)
  },
  login: (data: LoginRequest): Promise<LoginResponse> => {
    return request.post('/users/login', data)
  },
  logout: (): Promise<void> => {
    return request.post('/users/logout')
  },
}
