import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi, getErrorMessage } from '@/api'

export interface UserInfo {
  id: string
  username: string
  score: number
}

export interface LoginParams {
  username: string
  password: string
}

export interface LoginResult {
  success: boolean
  message?: string //可选
  data?: UserInfo
}

export const useUserStore = defineStore('user', () => {
  const userInfo = ref<UserInfo | null>(null)
  const token = ref<string | null>(null)
  const account = computed(() => userInfo.value)

  // 这里只表示本地持有 token；签名、有效期和 Redis 会话由后端每次请求时验证。
  const isLoggedIn = computed(() => !!token.value)
  const userId = computed(() => userInfo.value?.id || '')

  const login = async (params: LoginParams): Promise<LoginResult> => {
    try {
      const response = await authApi.login({
        username: params.username,
        password: params.password,
      })

      localStorage.setItem('token', response.access_token)
      token.value = response.access_token

      const userInfoData: UserInfo = {
        id: response.account.id,
        username: response.account.username,
        score: response.account.score,
      }

      setUserInfo(userInfoData)
      return { success: true, data: userInfoData }
    } catch (error) {
      clearUserData()
      return { success: false, message: getErrorMessage(error, '登录失败，请重试') }
    }
  }

  const logout = async () => {
    try {
      await authApi.logout()
    } catch (error) {
      console.error('退出登录接口调用失败:', error)
    } finally {
      clearUserData()
    }
  }

  const setUserInfo = (info: UserInfo) => {
    userInfo.value = info
    localStorage.setItem('userInfo', JSON.stringify(info))
  }

  const clearUserData = () => {
    userInfo.value = null
    token.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('userInfo')
  }

  const initUserInfo = () => {
    // 刷新后恢复界面状态，并只保留 id/username/score；这里不会发送登录验证请求。
    const savedToken = localStorage.getItem('token')
    const savedUserInfo = localStorage.getItem('userInfo')
    if (savedToken && savedUserInfo) {
      try {
        const parsedUserInfo = JSON.parse(savedUserInfo) as Partial<UserInfo>
        if (
          !savedToken.includes('.') ||
          typeof parsedUserInfo.id !== 'string' ||
          typeof parsedUserInfo.username !== 'string' ||
          typeof parsedUserInfo.score !== 'number'
        ) {
          clearUserData()
          return
        }
        token.value = savedToken
        userInfo.value = {
          id: parsedUserInfo.id,
          username: parsedUserInfo.username,
          score: parsedUserInfo.score,
        }
      } catch {
        clearUserData()
      }
    } else {
      clearUserData()
    }
  }

  return {
    userInfo,
    account,
    token,
    isLoggedIn,
    userId,
    login,
    logout,
    setUserInfo,
    clearUserData,
    initUserInfo,
  }
})
