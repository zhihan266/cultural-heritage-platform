<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Plus, Promotion } from '@element-plus/icons-vue'
import Layout from '@/layouts/index.vue'
import QaTaskList from '@/components/QaTaskList.vue'
import { qaApi } from '@/api/qa'
import { getErrorMessage, getHttpStatus, isUncertainRequest } from '@/api/request'
import { useUserStore } from '@/stores/user'
import { QA_INDEX_EVENT, ownsQaTask, qaPendingKey, rememberQaId } from '@/utils/qaTasks'
const route = useRoute(),
  router = useRouter(),
  user = useUserStore()
const knowledge = computed(() => route.path === '/qa/knowledge')
const query = ref(''),
  creating = ref(false),
  error = ref(''),
  uncertain = ref(false),
  acknowledged = ref(false)
let generation = 0
function readPending() {
  try {
    uncertain.value = sessionStorage.getItem(qaPendingKey(user.userId)) === '1'
  } catch {
    error.value = '浏览器暂不能保存创建请求回执，提交可能无法恢复。'
  }
}
function unlock() {
  if (!acknowledged.value) return
  sessionStorage.removeItem(qaPendingKey(user.userId))
  uncertain.value = false
  acknowledged.value = false
}
async function create() {
  if (creating.value || uncertain.value || !query.value.trim()) return
  const account = user.userId,
    current = generation
  creating.value = true
  error.value = ''
  let sent = false
  try {
    sessionStorage.setItem(qaPendingKey(account), '1')
    sent = true
    // Do not abort creation on navigation: a returned server ID must still enter its owner's index.
    const { task } = await qaApi.ask({ question: query.value.trim(), use_rag: knowledge.value })
    if (!ownsQaTask(task, account) || !task.id.trim()) throw new Error('Unexpected QA resource')
    sessionStorage.removeItem(qaPendingKey(account))
    try {
      rememberQaId(account, task.id)
    } catch {
      /* The detail URL and server task remain valid. */
    }
    if (generation === current && user.userId === account)
      await router.push('/qa/tasks/' + encodeURIComponent(task.id))
  } catch (failure) {
    const unknown = sent && (isUncertainRequest(failure) || !getHttpStatus(failure))
    if (!unknown) sessionStorage.removeItem(qaPendingKey(account))
    if (generation === current && user.userId === account) {
      uncertain.value = unknown
      error.value = getErrorMessage(failure, '无法创建问答，请确认浏览器存储可用。')
    }
  } finally {
    if (generation === current) creating.value = false
  }
}
function keydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    void create()
  }
}
watch(
  () => user.userId,
  () => {
    generation++
    creating.value = false
    query.value = ''
    error.value = ''
    acknowledged.value = false
    readPending()
  },
  { immediate: true },
)
onMounted(() => window.addEventListener(QA_INDEX_EVENT, readPending))
onBeforeUnmount(() => {
  generation++
  window.removeEventListener(QA_INDEX_EVENT, readPending)
})
</script>
<template>
  <Layout>
    <div class="page-heading">
      <div>
        <h1>AI 问答</h1>
        <p class="muted">HCP-MAD · 问答任务工作台</p>
      </div>
      <button :disabled="creating || uncertain" @click="query = ''">
        <el-icon><Plus /></el-icon>新建问答
      </button>
    </div>
    <div class="tabs">
      <RouterLink :class="{ active: !knowledge }" to="/qa">普通问答</RouterLink
      ><RouterLink :class="{ active: knowledge }" to="/qa/knowledge">知识库问答</RouterLink>
    </div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <div v-if="uncertain" class="notice" role="alert">
      <p>
        上次创建结果尚未确认，已暂停重复提交。若保留了任务编号，请先在我的问答中核对；未自动重新创建任务。
      </p>
      <label class="qa-check"
        ><input
          v-model="acknowledged"
          type="checkbox"
        />我已核实此前未创建任务，确认允许再次提交</label
      ><button :disabled="!acknowledged" @click="unlock">解除暂停</button>
    </div>
    <form class="band qa-create" @submit.prevent="create">
      <h2>{{ knowledge ? '新建知识库问答任务' : '新建普通问答任务' }}</h2>
      <label
        >任务问题<textarea
          v-model="query"
          aria-label="输入问题"
          rows="6"
          maxlength="10000"
          required
          :disabled="creating"
          placeholder="输入需要系统回答的文化遗产问题"
          @keydown="keydown"
        />
      </label>
      <div class="row">
        <small>{{ query.length }} / 10000</small
        ><button class="primary" :disabled="creating || uncertain || !query.trim()">
          <el-icon><Promotion /></el-icon>{{ creating ? '正在创建任务…' : '创建问答任务' }}
        </button>
      </div>
    </form>
    <section class="band"><QaTaskList /></section>
  </Layout>
</template>
<style scoped>
.qa-create textarea {
  margin-top: 10px;
}
.qa-create .row {
  margin-top: 14px;
}
.qa-check {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  margin: 14px 0;
}
.qa-check input {
  width: auto;
  margin-top: 5px;
}
</style>
