<script setup lang="ts">
import { Collection, Grid, ChatDotRound, Cpu, User, SwitchButton } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { useRouter } from 'vue-router'
defineProps<{ showScore?: boolean }>()
const user = useUserStore()
const router = useRouter()
const links = [
  { to: '/dashboard', name: '工作台', icon: Grid },
  { to: '/rag-tasks', name: 'RAG 众包任务', icon: Collection },
  { to: '/qa', name: 'AI 问答', icon: ChatDotRound },
  { to: '/ai', name: 'Agent 管理', icon: Cpu },
  { to: '/profile', name: '个人中心', icon: User },
]
async function logout() {
  await user.logout()
  await router.push('/login')
}
</script>
<template>
  <div class="app-shell">
    <header class="app-header">
      <RouterLink to="/dashboard" class="brand"
        ><el-icon :size="24"><Collection /></el-icon
        ><span>文化遗产 <b>AI 协作平台</b></span></RouterLink
      >
      <div class="header-account">
        <span>{{ user.account?.username }}</span
        ><el-tooltip content="退出登录"
          ><button class="icon-button" aria-label="退出登录" @click="logout">
            <el-icon><SwitchButton /></el-icon></button
        ></el-tooltip>
      </div>
    </header>
    <nav class="app-nav" aria-label="主导航">
      <RouterLink v-for="link in links" :key="link.to" :to="link.to"
        ><el-icon><component :is="link.icon" /></el-icon>{{ link.name }}</RouterLink
      >
    </nav>
    <main class="page"><slot /></main>
    <footer class="app-footer">文化遗产知识协作 · {{ new Date().getFullYear() }}</footer>
  </div>
</template>
