<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Plus, Refresh, Collection } from '@element-plus/icons-vue'
import Layout from '@/layouts/index.vue'
import {
  ragTasksApi,
  crowdStatusLabels,
  jobStatusLabels,
  type CrowdSummary,
  type CrowdStatus,
} from '@/api/ragTasks'
import { getErrorMessage } from '@/api/request'
import { canAccept, participationLabel, formatTime } from '@/utils/crowdTasks'
import { useUserStore } from '@/stores/user'
const route = useRoute(),
  router = useRouter(),
  user = useUserStore()
const view = computed(() =>
  route.query.view === 'published' || route.query.view === 'accepted' ? route.query.view : 'square',
)
const tabs = [
  { key: 'square', label: '任务广场' },
  { key: 'published', label: '我发布的任务' },
  { key: 'accepted', label: '我接取的任务' },
]
const tasks = ref<CrowdSummary[]>([]),
  total = ref(0),
  page = ref(1),
  pageSize = 12
const filter = ref<CrowdStatus | ''>(''),
  participation = ref<'' | 'accepted' | 'submitted'>(''),
  available = ref(false)
const loading = ref(false),
  accepting = ref(''),
  error = ref('')
let generation = 0,
  controller: AbortController | undefined
async function load() {
  const current = ++generation
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  error.value = ''
  tasks.value = []
  try {
    const result = await ragTasksApi.list(
      view.value,
      {
        page: page.value,
        page_size: pageSize,
        ...(filter.value ? { status: filter.value } : {}),
        ...(view.value === 'square' ? { available_only: available.value } : {}),
        ...(view.value === 'accepted' && participation.value
          ? { participation_status: participation.value }
          : {}),
      },
      controller.signal,
    )
    if (current !== generation) return
    tasks.value = result.items
    total.value = result.total
    if (page.value > 1 && !result.items.length && result.total)
      page.value = Math.ceil(result.total / pageSize)
  } catch (e) {
    if (current === generation) error.value = getErrorMessage(e, '任务列表读取失败')
  } finally {
    if (current === generation) loading.value = false
  }
}
async function accept(id: string) {
  if (accepting.value) return
  const account = user.userId
  accepting.value = id
  try {
    await ragTasksApi.accept(id)
    if (account === user.userId) await router.push('/rag-tasks/' + encodeURIComponent(id))
  } catch (e) {
    if (account !== user.userId) return
    await load()
    error.value = getErrorMessage(e)
  } finally {
    accepting.value = ''
  }
}
watch(
  [view, filter, participation, available, () => user.userId],
  () => {
    if (page.value === 1) void load()
    else page.value = 1
  },
  { immediate: true },
)
watch(page, () => void load())
onBeforeUnmount(() => {
  generation++
  controller?.abort()
})
</script>
<template>
  <Layout>
    <div class="page-heading">
      <div>
        <h1>RAG 众包任务</h1>
        <p class="muted">征集人工资料，生成正式知识与入库回执</p>
      </div>
      <RouterLink class="button primary" to="/rag-tasks/new"
        ><el-icon><Plus /></el-icon>发布众包任务</RouterLink
      >
    </div>
    <div class="tabs">
      <RouterLink
        v-for="tab in tabs"
        :key="tab.key"
        :class="{ active: view === tab.key }"
        :to="{ path: '/rag-tasks', query: { view: tab.key } }"
        >{{ tab.label }}</RouterLink
      >
    </div>
    <div class="toolbar">
      <label class="status-filter"
        >任务状态<select v-model="filter" aria-label="任务状态">
          <option value="">全部状态</option>
          <option v-for="(label, key) in crowdStatusLabels" :key="key" :value="key">
            {{ label }}
          </option>
        </select></label
      >
      <label v-if="view === 'square'" class="check"
        ><input v-model="available" type="checkbox" />仅看可接取</label
      >
      <label v-if="view === 'accepted'" class="status-filter"
        >我的参与<select v-model="participation" aria-label="我的参与">
          <option value="">全部参与</option>
          <option value="accepted">待提交</option>
          <option value="submitted">已提交</option>
        </select></label
      >
      <button :disabled="loading" @click="load">
        <el-icon><Refresh /></el-icon>刷新
      </button>
    </div>
    <p v-if="error" class="notice error" role="alert">
      {{ error }} <button :disabled="loading" @click="load">重新获取</button>
    </p>
    <p v-if="loading" class="empty" role="status">正在读取任务列表…</p>
    <div v-else-if="!error && !tasks.length" class="empty">
      <el-icon><Collection /></el-icon>
      <p>暂无符合条件的任务</p>
      <RouterLink to="/rag-tasks/new">发布一个众包任务</RouterLink>
    </div>
    <div v-else class="cards">
      <article v-for="task in tasks" :key="task.task_id" class="task-item">
        <div class="row">
          <span :class="['status', task.status]">{{ crowdStatusLabels[task.status] }}</span
          ><span class="muted">{{ task.is_publisher ? '我发布的任务' : '公开众包任务' }}</span>
        </div>
        <h3>
          <RouterLink :to="'/rag-tasks/' + encodeURIComponent(task.task_id)">{{
            task.title || task.question.slice(0, 80)
          }}</RouterLink>
        </h3>
        <p>{{ task.question.slice(0, 160) }}</p>
        <div class="task-meta">
          <span>接取 {{ task.accepted_worker_count }} / {{ task.required_worker_count }}</span
          ><span>已提交 {{ task.valid_worker_count }}</span
          ><span>{{ participationLabel(task) }}</span>
        </div>
        <p class="muted">发布于 {{ formatTime(task.created_at) }}</p>
        <p class="muted">
          聚合：{{
            task.threshold_reached ? jobStatusLabels[task.aggregation_status] : '等待回答达标'
          }}
          · 入库：{{ task.ingestion_status ? jobStatusLabels[task.ingestion_status] : '尚未开始' }}
        </p>
        <div class="actions">
          <button
            v-if="canAccept(task)"
            :disabled="!!accepting"
            class="primary"
            @click="accept(task.task_id)"
          >
            {{ accepting === task.task_id ? '正在接取…' : '接取任务' }}</button
          ><RouterLink class="button" :to="'/rag-tasks/' + encodeURIComponent(task.task_id)">{{
            task.result_available ? '查看正式结果' : task.can_submit ? '查看并提交回答' : '查看详情'
          }}</RouterLink>
        </div>
      </article>
    </div>
    <div v-if="!error" class="pagination">
      <button :disabled="loading || page <= 1" @click="page--">上一页</button
      ><span
        >第 {{ page }} / {{ Math.max(1, Math.ceil(total / pageSize)) }} 页 · 共 {{ total }} 项</span
      ><button :disabled="loading || page * pageSize >= total" @click="page++">下一页</button>
    </div>
  </Layout>
</template>
<style scoped>
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 340px), 1fr));
  gap: 18px;
}
.task-item {
  display: block;
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: white;
}
.task-item h3 {
  margin-top: 16px;
}
.status-filter,
.check {
  display: flex;
  align-items: center;
  gap: 10px;
}
.status-filter select {
  width: 160px;
}
.check input {
  width: auto;
}
.pagination {
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: center;
  margin: 24px 0;
  flex-wrap: wrap;
}
</style>
