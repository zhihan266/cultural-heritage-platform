<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Collection } from '@element-plus/icons-vue'
import { authApi, getErrorMessage } from '@/api'
const router = useRouter(),
  loading = ref(false),
  error = ref('')
const form = reactive({ username: '', password: '', confirm: '' })
async function register() {
  if (loading.value) return
  error.value = ''
  if (form.password !== form.confirm) {
    error.value = '两次输入的密码不一致'
    return
  }
  if (new TextEncoder().encode(form.password).length > 72) {
    error.value = '密码不得超过 72 个 UTF-8 字节'
    return
  }
  loading.value = true
  try {
    await authApi.register({ username: form.username, password: form.password })
    await router.push('/login')
  } catch (e) {
    error.value = getErrorMessage(e, '注册失败')
  } finally {
    loading.value = false
  }
}
</script>
<template>
  <main class="auth-page">
    <div class="auth-brand">
      <el-icon :size="30"><Collection /></el-icon>
      <h1>注册账号</h1>
      <p>文化遗产 AI 协作平台</p>
    </div>
    <form class="auth-form" @submit.prevent="register">
      <p v-if="error" class="notice error" role="alert">{{ error }}</p>
      <label
        >用户名<input
          v-model.trim="form.username"
          required
          minlength="3"
          maxlength="50"
          autocomplete="username" /></label
      ><label
        >密码<input
          v-model="form.password"
          required
          type="password"
          minlength="6"
          maxlength="72"
          autocomplete="new-password" /></label
      ><label
        >确认密码<input
          v-model="form.confirm"
          required
          type="password"
          autocomplete="new-password" /></label
      ><button class="primary" :disabled="loading">{{ loading ? '正在注册…' : '注册' }}</button
      ><RouterLink to="/login">返回登录</RouterLink>
    </form>
  </main>
</template>
