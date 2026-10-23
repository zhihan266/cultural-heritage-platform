<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowLeft, Plus, Refresh } from '@element-plus/icons-vue'
import Layout from '@/layouts/index.vue'
import SafeMarkdown from '@/components/SafeMarkdown.vue'
import { finishedAnswer, retrievalNotice, type QaTask } from '@/api/qa'
import { useUserStore } from '@/stores/user'
import { createQaPoller, type QaMonitorState } from '@/utils/qaPolling'
import { rememberQaId, ownsQaTask, qaMode, qaStatusLabels } from '@/utils/qaTasks'
const route = useRoute(),
  user = useUserStore()
const id = computed(() => String(route.params.id || ''))
const task = ref<QaTask | null>(null),
  state = ref<QaMonitorState>('idle'),
  message = ref(''),
  warning = ref('')
const answer = computed(() => (task.value ? finishedAnswer(task.value) : null))
const poller = createQaPoller({
  onTask(value) {
    if (!ownsQaTask(value, user.userId, id.value)) return
    task.value = value
    try {
      rememberQaId(user.userId, value.id)
    } catch {
      warning.value = '任务已保存在服务器，但本浏览器未能保存编号索引；请保留当前链接。'
    }
  },
  onState(value, detail) {
    state.value = value
    message.value = detail
  },
  onAccessError() {
    task.value = null
  },
})
function refresh() {
  poller.start(id.value, user.userId)
}
function visible() {
  if (
    document.visibilityState === 'visible' &&
    ['waiting', 'retrying', 'exhausted'].includes(state.value)
  )
    refresh()
}
watch(
  [id, () => user.userId],
  () => {
    task.value = null
    warning.value = ''
    refresh()
  },
  { immediate: true },
)
onMounted(() => document.addEventListener('visibilitychange', visible))
onBeforeUnmount(() => {
  poller.stop()
  document.removeEventListener('visibilitychange', visible)
})
</script>
<template>
  <Layout>
    <RouterLink class="back-link" to="/profile/questions"
      ><el-icon><ArrowLeft /></el-icon>我的问答</RouterLink
    >
    <div class="page-heading">
      <div>
        <h1>问答任务详情</h1>
        <p class="muted">{{ task ? qaMode(task) : '从服务器查询任务' }} · HCP-MAD</p>
      </div>
      <div class="actions">
        <button :disabled="state === 'checking'" @click="refresh">
          <el-icon><Refresh /></el-icon>核对最新状态</button
        ><RouterLink class="button primary" to="/qa"
          ><el-icon><Plus /></el-icon>新建问答</RouterLink
        >
      </div>
    </div>
    <p v-if="warning" class="notice" role="status">{{ warning }}</p>
    <p
      v-if="message"
      :class="[
        'notice',
        ['error', 'unknown', 'inconsistent', 'failed'].includes(state) ? 'error' : '',
      ]"
      role="status"
    >
      {{ message }}
    </p>
    <p v-if="!task && state === 'checking'" class="empty" role="status">正在读取问答任务…</p>
    <template v-if="task">
      <section class="band">
        <h2>任务问题</h2>
        <p class="long-text">{{ task.question }}</p>
      </section>
      <section class="band">
        <div class="row">
          <h2>运行状态</h2>
          <span class="status" :class="task.status">{{
            qaStatusLabels[task.status] || '未识别状态'
          }}</span>
        </div>
        <p v-if="state === 'checking'" role="status">正在核对服务器状态…</p>
        <p v-else-if="state === 'waiting'">任务尚未结束，页面将自动核对最新结果。</p>
        <p v-if="retrievalNotice(task)" class="notice">{{ retrievalNotice(task) }}</p>
      </section>
      <section class="band">
        <h2>最终答案</h2>
        <SafeMarkdown v-if="answer" :text="answer" />
        <p v-else class="muted">尚未取得有效的正式结果。原始 Worker 回答不作为最终答案展示。</p>
      </section>
    </template>
  </Layout>
</template>
<style scoped>
.back-link {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 20px;
}
</style>
