<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Plus, Refresh, Connection } from '@element-plus/icons-vue'
import Layout from '@/layouts/index.vue'
import { aiApi, getErrorMessage, type AiAgent, type AiRegisterRequest } from '@/api'
import { AiType } from '@/api/types'
import { formatTime } from '@/utils/crowdTasks'
const agents = ref<AiAgent[]>([]),
  loading = ref(false),
  saving = ref(false),
  expanded = ref(false)
const error = ref(''),
  notice = ref(''),
  checking = reactive(new Set<string>())
const generation = ref('')
const form = reactive<AiRegisterRequest>({
  name: '',
  api_url: '',
  type: AiType.WORKER,
  initial_score: 1,
  model_name: '',
  provider: '',
  auth_type: 'none',
  credential_ref: null,
})
async function load() {
  if (loading.value || saving.value) return
  loading.value = true
  error.value = ''
  try {
    agents.value = await aiApi.listAis()
  } catch (e) {
    error.value = getErrorMessage(e)
  } finally {
    loading.value = false
  }
}
async function check(id: string) {
  if (checking.has(id)) return
  checking.add(id)
  error.value = ''
  try {
    const result = await aiApi.healthCheck(id)
    const agent = agents.value.find((a) => a.id === id)
    if (agent) Object.assign(agent, result)
  } catch (e) {
    error.value = getErrorMessage(e)
  } finally {
    checking.delete(id)
  }
}
async function register() {
  if (saving.value || loading.value) return
  error.value = ''
  notice.value = ''
  let config: Record<string, unknown> | null = null
  if (generation.value.trim()) {
    try {
      const parsed: unknown = JSON.parse(generation.value)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error()
      config = parsed as Record<string, unknown>
    } catch {
      error.value = '生成参数必须为合法 JSON 对象。'
      return
    }
  }
  saving.value = true
  try {
    const { ai } = await aiApi.registerAi({
      ...form,
      api_url: form.api_url?.trim() || null,
      model_name: form.model_name?.trim() || null,
      provider: form.provider?.trim() || null,
      credential_ref: form.auth_type === 'bearer' ? 'DEEPSEEK_API_KEY' : null,
      generation_config: config,
    })
    agents.value.push(ai)
    notice.value = 'Agent 已注册，请检查连接后使用。'
    expanded.value = false
    form.name = ''
    form.api_url = ''
    form.model_name = ''
    generation.value = ''
  } catch (e) {
    error.value = getErrorMessage(e)
  } finally {
    saving.value = false
  }
}
onMounted(load)
</script>
<template>
  <Layout>
    <div class="page-heading">
      <div>
        <h1>Agent 管理</h1>
        <p class="muted">回答模型、聚合评审模型及连接状态</p>
      </div>
      <div class="actions">
        <button :disabled="loading || saving" @click="load">
          <el-icon><Refresh /></el-icon>刷新</button
        ><button class="primary" :disabled="saving || loading" @click="expanded = !expanded">
          <el-icon><Plus /></el-icon>注册 Agent
        </button>
      </div>
    </div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice success" role="status">{{ notice }}</p>
    <form v-if="expanded" class="band" @submit.prevent="register">
      <h2>注册 Agent</h2>
      <div class="form-grid">
        <label>显示名称<input v-model.trim="form.name" required maxlength="80" /></label
        ><label
          >用途<select v-model="form.type">
            <option value="worker">回答 Agent</option>
            <option value="judge">聚合 / 评审 Agent</option>
          </select></label
        >
        <label class="span-2"
          >模型接口地址（选填）<input
            v-model.trim="form.api_url"
            type="url"
            placeholder="http://127.0.0.1:11434/v1/chat/completions"
        /></label>
        <label>模型名称（选填）<input v-model.trim="form.model_name" maxlength="255" /></label
        ><label
          >提供方标识（选填）<input
            v-model.trim="form.provider"
            maxlength="50"
            placeholder="openai-compatible"
        /></label>
        <label
          >初始积分<input
            v-model.number="form.initial_score"
            type="number"
            required
            min="0.1"
            max="10"
            step="0.1" /></label
        ><label
          >鉴权方式<select v-model="form.auth_type">
            <option value="none">无需鉴权</option>
            <option value="bearer">服务端 DEEPSEEK_API_KEY 引用</option>
          </select></label
        >
        <label class="span-2"
          >生成参数 JSON（选填）<textarea
            v-model="generation"
            rows="3"
            placeholder='例如 {"max_tokens": 2048}'
          />
        </label>
      </div>
      <div class="form-actions">
        <button class="primary" :disabled="saving || loading">
          {{ saving ? '正在注册…' : '确认注册' }}</button
        ><button type="button" :disabled="saving" @click="expanded = false">取消</button>
      </div>
    </form>
    <p v-if="loading" class="empty" role="status">正在加载 Agent…</p>
    <p v-else-if="error && !agents.length" class="empty" role="alert">
      Agent 列表读取失败，请刷新重试。
    </p>
    <div v-else-if="!agents.length" class="empty">
      <p>尚未注册 Agent</p>
      <button @click="expanded = true">注册第一个 Agent</button>
    </div>
    <div v-else class="task-list">
      <article v-for="agent in agents" :key="agent.id" class="task-item">
        <div>
          <h3>
            {{ agent.name }}
            <span class="status">{{ agent.type === 'judge' ? '聚合 / 评审' : '回答 Agent' }}</span>
          </h3>
          <div class="task-meta">
            <span>{{ agent.model_name || agent.name }}</span
            ><span>积分 {{ agent.score.toFixed(2) }}</span
            ><span>最近检测 {{ formatTime(agent.last_checked_at) }}</span>
          </div>
          <p v-if="agent.last_error" class="muted">最近连接检查失败，请核对服务与配置。</p>
          <details>
            <summary>连接配置</summary>
            <p class="long-text muted">{{ agent.api_url || '未配置地址' }}</p>
          </details>
        </div>
        <div class="actions">
          <span :class="['status', agent.enabled && agent.available ? 'succeeded' : 'failed']">{{
            !agent.enabled ? '已停用' : agent.available ? '可用' : '不可用'
          }}</span
          ><button :disabled="checking.has(agent.id)" @click="check(agent.id)">
            <el-icon><Connection /></el-icon>{{ checking.has(agent.id) ? '检查中…' : '检查连接' }}
          </button>
        </div>
      </article>
    </div>
  </Layout>
</template>
