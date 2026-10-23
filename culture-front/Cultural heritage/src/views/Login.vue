<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Collection } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
const route = useRoute(),
  router = useRouter(),
  user = useUserStore()
const form = reactive({ username: '', password: '' }),
  loading = ref(false),
  error = ref('')
async function login() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const result = await user.login(form)
    if (!result.success) {
      error.value = result.message || '登录失败'
      return
    }
    const redirect = route.query.redirect
    await router.push(
      typeof redirect === 'string' &&
        redirect.startsWith('/') &&
        !redirect.startsWith('//') &&
        !redirect.startsWith('/login')
        ? redirect
        : '/dashboard',
    )
  } finally {
    loading.value = false
  }
}
</script>
<template>
  <main class="auth-page">
    <div class="auth-brand">
      <el-icon :size="30"><Collection /></el-icon>
      <h1>文化遗产 AI 协作平台</h1>
      <p>登录账号</p>
    </div>
    <form class="auth-form" @submit.prevent="login">
      <p v-if="error" class="notice error" role="alert">{{ error }}</p>
      <label
        >用户名<input
          v-model.trim="form.username"
          required
          maxlength="50"
          autocomplete="username" /></label
      ><label
        >密码<input
          v-model="form.password"
          required
          type="password"
          maxlength="72"
          autocomplete="current-password" /></label
      ><button class="primary" :disabled="loading">{{ loading ? '正在登录…' : '登录' }}</button>
      <p>还没有账号？<RouterLink to="/register">注册账号</RouterLink></p>
    </form>
  </main>
</template>
