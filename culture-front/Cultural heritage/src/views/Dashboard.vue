<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Collection, ChatDotRound, Cpu, ArrowRight } from '@element-plus/icons-vue'
import Layout from '@/layouts/index.vue'
import { useUserStore } from '@/stores/user'
import { aiApi, getErrorMessage } from '@/api'
import { ragTasksApi } from '@/api/ragTasks'
const user = useUserStore(),
  agentCount = ref<number | null>(null),
  error = ref('')
const publishedCount = ref<number | null>(null)
onMounted(async () => {
  try {
    const [agents, published] = await Promise.all([
      aiApi.listAis(),
      ragTasksApi.list('published', { page_size: 1 }),
    ])
    agentCount.value = agents.length
    publishedCount.value = published.total
  } catch (e) {
    error.value = getErrorMessage(e)
  }
})
</script>
<template>
  <Layout>
    <div class="workspace-banner">
      <div>
        <span class="eyebrow">HERITAGE · KNOWLEDGE · COLLABORATION</span>
        <h1>工作台</h1>
        <p>{{ user.account?.username }}，欢迎回来</p>
      </div>
      <img src="/heritage-wall.jpg" alt="中国长城的山地建筑景观" />
    </div>
    <div class="overview">
      <div>
        <span>我发布的众包任务</span><strong>{{ publishedCount ?? '—' }}</strong>
      </div>
      <div>
        <span>已注册 Agent</span><strong>{{ agentCount ?? '—' }}</strong>
      </div>
      <div>
        <span>账号积分</span><strong>{{ user.account?.score.toFixed(2) ?? '—' }}</strong>
      </div>
    </div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <h2>开始协作</h2>
    <div class="workspace-links">
      <RouterLink to="/rag-tasks"
        ><el-icon><Collection /></el-icon>
        <div>
          <h3>RAG 众包任务</h3>
          <span>征集回答与正式聚合结果</span>
        </div>
        <el-icon><ArrowRight /></el-icon></RouterLink
      ><RouterLink to="/qa"
        ><el-icon><ChatDotRound /></el-icon>
        <div>
          <h3>AI 问答</h3>
          <span>普通问答与独立知识库问答</span>
        </div>
        <el-icon><ArrowRight /></el-icon></RouterLink
      ><RouterLink to="/ai"
        ><el-icon><Cpu /></el-icon>
        <div>
          <h3>Agent 管理</h3>
          <span>查看模型与服务连接状态</span>
        </div>
        <el-icon><ArrowRight /></el-icon
      ></RouterLink>
    </div>
    <div class="quick-links">
      <RouterLink class="button primary" to="/rag-tasks/new">发布众包任务</RouterLink
      ><RouterLink class="button" to="/rag-tasks?view=accepted">我接取的任务</RouterLink>
    </div>
  </Layout>
</template>
<style scoped>
.workspace-banner {
  display: grid;
  grid-template-columns: 1fr 40%;
  min-height: 190px;
  background: white;
  border-bottom: 1px solid var(--line);
  margin-bottom: 24px;
}
.workspace-banner > div {
  padding: 28px;
}
.workspace-banner img {
  width: 100%;
  height: 190px;
  object-fit: cover;
}
.eyebrow {
  font-size: 11px;
  color: #7b899e;
}
.workspace-banner h1 {
  margin-top: 12px;
}
.workspace-banner p {
  color: var(--muted);
  margin-bottom: 0;
}
.overview {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  padding: 24px 0 32px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 28px;
}
.overview > div {
  border-right: 1px solid var(--line);
  padding-left: 22px;
}
.overview > div:first-child {
  padding-left: 0;
}
.overview > div:last-child {
  border: 0;
}
.overview span {
  display: block;
  color: var(--muted);
}
.overview strong {
  font-size: 28px;
  font-weight: 500;
}
.workspace-links {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.workspace-links > a {
  display: flex;
  align-items: center;
  gap: 14px;
  background: white;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 20px;
  color: inherit;
}
.workspace-links > a > .el-icon {
  font-size: 22px;
  color: var(--blue);
}
.workspace-links > a > div {
  flex: 1;
  min-width: 0;
}
.workspace-links h3 {
  margin-bottom: 5px;
}
.workspace-links span {
  color: var(--muted);
  font-size: 12px;
}
.quick-links {
  display: flex;
  gap: 12px;
  margin-top: 28px;
}
@media (max-width: 800px) {
  .workspace-links {
    grid-template-columns: 1fr;
  }
  .workspace-banner {
    grid-template-columns: 1fr 35%;
  }
  .workspace-banner > div {
    padding: 20px;
  }
  .overview > div {
    padding-left: 12px;
  }
  .overview span {
    font-size: 12px;
  }
  .overview strong {
    font-size: 23px;
  }
}
@media (max-width: 420px) {
  .eyebrow {
    display: none;
  }
}
</style>
