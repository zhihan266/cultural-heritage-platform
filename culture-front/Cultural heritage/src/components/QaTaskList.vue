<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, ArrowRight, Refresh, Search } from '@element-plus/icons-vue'
import { qaApi, type QaTask } from '@/api/qa'
import { getErrorMessage } from '@/api/request'
import { useUserStore } from '@/stores/user'
import {
  QA_INDEX_EVENT,
  qaIndexKey,
  readQaIds,
  ownsQaTask,
  qaMode,
  qaStatusLabels,
} from '@/utils/qaTasks'
const props = withDefaults(defineProps<{ pageSize?: number }>(), { pageSize: 10 })
const user = useUserStore(),
  router = useRouter()
const tasks = ref<QaTask[]>([]),
  errors = ref<string[]>([]),
  loading = ref(false),
  page = ref(0),
  total = ref(0),
  lookup = ref('')
let generation = 0,
  controller: AbortController | undefined
async function load() {
  const current = ++generation,
    account = user.userId
  controller?.abort()
  controller = new AbortController()
  const signal = controller.signal,
    ids = readQaIds(account)
  total.value = ids.length
  page.value = Math.min(page.value, Math.max(0, Math.ceil(ids.length / props.pageSize) - 1))
  const selected = ids.slice(page.value * props.pageSize, (page.value + 1) * props.pageSize)
  tasks.value = []
  errors.value = []
  loading.value = true
  const results = await Promise.allSettled(
    selected.map(async (id) => {
      const { task } = await qaApi.status(id, signal)
      if (!ownsQaTask(task, account, id)) throw new Error('Unexpected QA resource')
      return task
    }),
  )
  if (generation !== current || user.userId !== account) return
  results.forEach((result, index) => {
    if (result.status === 'fulfilled') tasks.value.push(result.value)
    else
      errors.value.push(
        `${selected[index]}：${getErrorMessage(result.reason, '无法读取该账号的问答任务。')}`,
      )
  })
  loading.value = false
}
function storage(event: StorageEvent) {
  if (event.key === qaIndexKey(user.userId)) void load()
}
function open() {
  if (lookup.value.trim()) void router.push('/qa/tasks/' + encodeURIComponent(lookup.value.trim()))
}
watch([() => user.userId, page], () => void load(), { immediate: true })
onMounted(() => {
  window.addEventListener(QA_INDEX_EVENT, load)
  window.addEventListener('storage', storage)
})
onBeforeUnmount(() => {
  generation++
  controller?.abort()
  window.removeEventListener(QA_INDEX_EVENT, load)
  window.removeEventListener('storage', storage)
})
</script>
<template>
  <div class="row">
    <h2>我的问答</h2>
    <button :disabled="loading" @click="load">
      <el-icon><Refresh /></el-icon>刷新记录
    </button>
  </div>
  <p class="notice">
    仅恢复当前账号在本浏览器保存的任务编号，内容与状态均重新从服务器读取；不包含其他设备或清除浏览器数据后的完整历史。
  </p>
  <form class="toolbar" @submit.prevent="open">
    <label class="qa-lookup"
      >已有问答编号<input
        v-model="lookup"
        maxlength="128"
        placeholder="输入已保存的问答任务编号"
        required /></label
    ><button>
      <el-icon><Search /></el-icon>打开问答
    </button>
  </form>
  <p v-if="loading" role="status" class="empty">正在查询服务器问答记录…</p>
  <p v-else-if="!total" class="empty">本浏览器暂无此账号的问答任务编号。</p>
  <div v-else>
    <p class="muted">本浏览器索引 {{ total }} 项 · 记录顺序不代表服务器创建时间</p>
    <details v-if="errors.length">
      <summary>{{ errors.length }} 项记录读取失败</summary>
      <p v-for="error in errors" :key="error" class="notice error">{{ error }}</p>
    </details>
    <p v-if="!tasks.length && errors.length" class="notice error">
      无法读取这一页记录，不代表服务器没有问答任务。
    </p>
    <article v-for="task in tasks" :key="task.id" class="task-item qa-history-item">
      <div>
        <h3>
          <RouterLink :to="'/qa/tasks/' + encodeURIComponent(task.id)">{{
            task.question
          }}</RouterLink>
        </h3>
        <p class="muted">{{ qaMode(task) }} · 创建时间未提供</p>
      </div>
      <div class="actions">
        <span class="status" :class="task.status">{{
          qaStatusLabels[task.status] || '未识别状态'
        }}</span
        ><RouterLink class="button" :to="'/qa/tasks/' + encodeURIComponent(task.id)"
          >查看问答</RouterLink
        >
      </div>
    </article>
    <div v-if="total > pageSize" class="qa-pages">
      <button :disabled="!page || loading" @click="page--">
        <el-icon><ArrowLeft /></el-icon>上一页</button
      ><span>{{ page + 1 }} / {{ Math.ceil(total / pageSize) }}</span
      ><button :disabled="(page + 1) * pageSize >= total || loading" @click="page++">
        下一页<el-icon><ArrowRight /></el-icon>
      </button>
    </div>
  </div>
</template>
<style scoped>
.qa-lookup {
  flex: 1;
  min-width: 0;
}
.qa-history-item h3 {
  overflow: hidden;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.qa-pages {
  display: flex;
  gap: 14px;
  align-items: center;
  margin-top: 20px;
}
</style>
