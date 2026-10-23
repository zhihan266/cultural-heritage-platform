<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowLeft, Refresh, Link, Promotion } from '@element-plus/icons-vue'
import Layout from '@/layouts/index.vue'
import SafeMarkdown from '@/components/SafeMarkdown.vue'
import { ragTasksApi, crowdStatusLabels, jobStatusLabels, type CrowdTask } from '@/api/ragTasks'
import { getErrorMessage, isUncertainRequest } from '@/api/request'
import {
  answerByteLength,
  validateAnswer,
  readReceipts,
  saveReceipt,
  formatTime,
} from '@/utils/crowdTasks'
import { createCrowdPoller } from '@/utils/crowdPolling'
import { useUserStore } from '@/stores/user'
const route = useRoute(),
  user = useUserStore()
const id = computed(() => String(route.params.id || ''))
const task = ref<CrowdTask | null>(null),
  loading = ref(false),
  busy = ref(false)
const error = ref(''),
  actionError = ref(''),
  notice = ref(''),
  monitor = ref('')
const answer = ref(''),
  pending = ref(''),
  acknowledgeUnknown = ref(false),
  copied = ref(false)
const owner = computed(() => !!task.value?.is_publisher)
const submitted = computed(() => task.value?.my_participation?.status === 'submitted')
const participant = computed(() => !!task.value?.my_participation)
const uncertain = computed(() => !!pending.value && !submitted.value)
const canSubmit = computed(() => !!task.value?.can_submit && !loading.value && !error.value)
const canJoin = computed(() => !!task.value?.can_accept)
const unknownJob = computed(() => !!task.value?.retry.requires_acknowledgement)
let generation = 0
function receive(response: CrowdTask) {
  task.value = response
  loading.value = false
  error.value = ''
  if (response.my_participation?.status === 'submitted') {
    answer.value = response.my_answer || ''
    pending.value = ''
    try {
      saveReceipt(user.userId, {
        id: response.task_id,
        relation: 'submitted',
        pendingAnswer: undefined,
        submittedAnswer: undefined,
      })
    } catch {
      /* Server remains authoritative. */
    }
  }
}
const poller = createCrowdPoller({
  onTask: receive,
  onNotice: (message) => {
    monitor.value = message
    loading.value = false
  },
})
function load() {
  if (busy.value) return
  loading.value = true
  monitor.value = ''
  poller.start(id.value)
}
function resume() {
  loading.value = true
  poller.start(id.value)
}
async function join() {
  if (busy.value || !canJoin.value) return
  const current = generation,
    taskId = id.value
  poller.stop()
  busy.value = true
  actionError.value = ''
  try {
    const response = await ragTasksApi.accept(taskId)
    if (current !== generation) return
    receive(response)
    notice.value = '接取成功。'
  } catch (e) {
    if (current === generation) actionError.value = getErrorMessage(e)
  } finally {
    if (current === generation) {
      busy.value = false
      resume()
    }
  }
}
async function submitAnswer() {
  if (busy.value || submitted.value || !canSubmit.value) return
  const current = generation,
    taskId = id.value,
    accountId = user.userId
  const body = pending.value || answer.value.trim()
  actionError.value = validateAnswer(body)
  if (actionError.value) return
  poller.stop()
  busy.value = true
  try {
    // Always reconcile before sending. An uncertain outcome can only replay this exact body.
    const latest = await ragTasksApi.detail(taskId)
    if (current !== generation) return
    receive(latest)
    if (latest.my_participation?.status === 'submitted') {
      notice.value = '服务器已确认提交，未重复发送。'
      return
    }
    if (!latest.can_submit) {
      actionError.value = '任务已不允许提交，请核对当前状态。'
      return
    }
    saveReceipt(accountId, { id: taskId, relation: 'accepted', pendingAnswer: body })
    pending.value = body
    const response = await ragTasksApi.submit(taskId, body)
    if (current !== generation) return
    receive(response)
    notice.value = response.submission_reused
      ? '服务器已确认原答案。'
      : '答案已提交，后续聚合及入库状态见下方。'
  } catch (e) {
    if (current !== generation) return
    actionError.value = getErrorMessage(e, '提交失败，请核对服务器状态。')
    if (!isUncertainRequest(e)) {
      pending.value = ''
      try {
        saveReceipt(accountId, { id: taskId, relation: 'accepted', pendingAnswer: undefined })
      } catch {
        /* Keep server truth. */
      }
    }
  } finally {
    if (current === generation) {
      busy.value = false
      resume()
    }
  }
}
async function retry() {
  if (busy.value || !task.value?.retry.allowed || (unknownJob.value && !acknowledgeUnknown.value))
    return
  const current = generation,
    taskId = id.value
  poller.stop()
  busy.value = true
  actionError.value = ''
  try {
    const response = await ragTasksApi.retry(taskId, acknowledgeUnknown.value)
    if (current !== generation) return
    receive(response)
    acknowledgeUnknown.value = false
  } catch (e) {
    if (current === generation) actionError.value = getErrorMessage(e)
  } finally {
    if (current === generation) {
      busy.value = false
      resume()
    }
  }
}
async function copy() {
  try {
    await navigator.clipboard.writeText(window.location.href)
    copied.value = true
  } catch {
    notice.value = '无法复制链接，可复制浏览器地址栏分享。'
  }
}
watch(
  [id, () => user.userId],
  () => {
    generation++
    poller.stop()
    busy.value = false
    task.value = null
    answer.value = ''
    actionError.value = ''
    notice.value = ''
    copied.value = false
    acknowledgeUnknown.value = false
    pending.value =
      readReceipts(user.userId).find((receipt) => receipt.id === id.value)?.pendingAnswer || ''
    if (pending.value) answer.value = pending.value
    load()
  },
  { immediate: true },
)
onBeforeUnmount(() => {
  generation++
  poller.stop()
})
</script>
<template>
  <Layout>
    <RouterLink to="/rag-tasks" class="back-link"
      ><el-icon><ArrowLeft /></el-icon>返回众包任务</RouterLink
    >
    <div class="page-heading">
      <div>
        <h1>{{ task?.title || '众包任务详情' }}</h1>
        <p class="muted">{{ task ? crowdStatusLabels[task.status] : '任务状态以服务器为准' }}</p>
      </div>
      <div class="actions">
        <button @click="copy">
          <el-icon><Link /></el-icon>{{ copied ? '链接已复制' : '分享链接' }}</button
        ><button :disabled="loading || busy" @click="load()">
          <el-icon><Refresh /></el-icon>刷新状态
        </button>
      </div>
    </div>
    <p v-if="monitor" class="notice" role="status">{{ monitor }}</p>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-if="actionError" class="notice error" role="alert">{{ actionError }}</p>
    <p v-if="notice" class="notice success" role="status">{{ notice }}</p>
    <p v-if="loading && !task" class="empty" role="status">正在读取任务…</p>
    <template v-if="task">
      <section class="band">
        <div class="row">
          <h2>任务问题</h2>
          <span :class="['status', task.status]">{{
            crowdStatusLabels[task.status] || task.status
          }}</span>
        </div>
        <p class="long-text">{{ task.question }}</p>
        <h3>答案格式要求</h3>
        <p class="long-text muted">{{ task.answer_format }}</p>
        <p class="muted">发布于 {{ formatTime(task.created_at) }}</p>
        <ol class="workflow">
          <li :class="{ done: task.threshold_reached }">回答达标</li>
          <li :class="{ done: task.result }">
            {{
              task.aggregation_status === 'running'
                ? 'AI 聚合中'
                : task.result
                  ? '聚合完成'
                  : '等待聚合'
            }}
          </li>
          <li :class="{ done: task.ingestion?.status === 'succeeded' }">
            {{
              task.ingestion?.status === 'succeeded'
                ? '入库完成'
                : task.ingestion?.status === 'failed'
                  ? '入库失败'
                  : '等待知识入库'
            }}
          </li>
        </ol>
        <dl class="facts">
          <div>
            <dt>发布者</dt>
            <dd>{{ owner ? '我' : '其他用户' }}</dd>
          </div>
          <div>
            <dt>已接取 / 所需工人</dt>
            <dd>{{ task.accepted_worker_count }} / {{ task.required_worker_count }}</dd>
          </div>
          <div>
            <dt>有效人工答案</dt>
            <dd>{{ task.valid_worker_count }}</dd>
          </div>
        </dl>
        <button v-if="canJoin" :disabled="busy || loading || !!error" class="primary" @click="join">
          接取任务
        </button>
      </section>
      <section class="band">
        <h2>我的回答</h2>
        <p v-if="submitted" class="notice success">服务器已确认你提交的回答。</p>
        <p v-else-if="participant" class="muted">已接取此任务，提交状态由服务器确认。</p>
        <p v-else class="muted">
          {{
            owner
              ? '你是任务发布者。如需参与回答，可按服务器规则接取任务。'
              : '接取任务后可以提交答案。'
          }}
        </p>
        <p v-if="uncertain" class="notice">
          原答案提交结果待核对，正文已锁定。系统先查询提交状态；确认尚未提交时，可手动重放同一份正文。
        </p>
        <form v-if="participant || submitted" @submit.prevent="submitAnswer">
          <label
            >答案正文<textarea
              v-model="answer"
              :rows="submitted ? 3 : 8"
              :readonly="submitted || uncertain"
              :disabled="!canSubmit && !uncertain && !submitted"
              placeholder="围绕问题提供事实、来源与不确定点"
            />
          </label>
          <div class="row">
            <small>{{ answerByteLength(answer) }} / 65535 UTF-8 字节</small
            ><button
              v-if="!submitted"
              class="primary"
              :disabled="busy || loading || !!error || (!canSubmit && !uncertain)"
              type="submit"
            >
              <el-icon><Promotion /></el-icon
              >{{ busy ? '正在核对并提交…' : uncertain ? '核对并重放原答案' : '提交答案' }}
            </button>
          </div>
        </form>
        <p v-if="task.status !== 'collecting' && !submitted && !uncertain" class="muted">
          当前任务已关闭新答案提交。
        </p>
      </section>
      <section class="band">
        <div class="row">
          <h2>正式聚合结果</h2>
          <span class="status" :class="task.aggregation_status">{{
            task.status === 'collecting'
              ? '等待答案收集完成'
              : jobStatusLabels[task.aggregation_status]
          }}</span>
        </div>
        <template v-if="task.result"
          ><SafeMarkdown :text="task.result.content" />
          <p class="muted">
            版本 {{ task.result.version }} · 基于 {{ task.result.answer_count }} 条回答 ·
            {{ formatTime(task.result.created_at) }}
          </p></template
        >
        <p v-else class="muted">尚未生成正式聚合结果。工人提交回执不代表聚合已完成。</p>
      </section>
      <section class="band">
        <div class="row">
          <h2>知识入库</h2>
          <span class="status" :class="task.ingestion?.status">{{
            task.ingestion ? jobStatusLabels[task.ingestion.status] : '尚未开始'
          }}</span>
        </div>
        <p v-if="task.ingestion?.status === 'succeeded'">
          已完成知识入库，切片数量 {{ task.ingestion.chunk_count ?? '未提供' }}。
        </p>
        <p v-else-if="task.result" class="muted">正式结果已生成，知识入库以独立回执为准。</p>
        <p v-else class="muted">正式结果生成后进入知识入库阶段。</p>
        <p v-if="task.ingestion?.status === 'failed'" class="notice error">
          知识入库失败，请展开处理记录核对阶段错误。
        </p>
      </section>
      <section v-if="owner && task.retry.allowed" class="band">
        <h2>重试失败阶段</h2>
        <label v-if="unknownJob" class="check"
          ><input
            v-model="acknowledgeUnknown"
            type="checkbox"
          />模型此前执行结果未知，重复调用可能再次计费，我确认允许重试。</label
        ><button
          :disabled="busy || loading || !!error || (unknownJob && !acknowledgeUnknown)"
          @click="retry"
        >
          重试当前失败阶段
        </button>
      </section>
      <details>
        <summary>处理记录与来源</summary>
        <div v-for="job in task.jobs" :key="job.phase" class="job">
          <strong>{{ job.phase === 'aggregate' ? '答案聚合' : '知识入库' }}</strong> ·
          {{ jobStatusLabels[job.status] }}
          <p v-if="job.status === 'failed' || job.status === 'uncertain'" class="muted">
            该阶段未成功完成，请联系管理员核对服务配置与执行日志。
          </p>
        </div>
        <p v-if="!task.jobs.length" class="muted">尚无处理记录</p>
        <p class="muted">这里展示正式知识和处理阶段；上方“我的回答”为本人的原始提交内容。</p>
      </details>
    </template>
  </Layout>
</template>
<style scoped>
.workflow {
  display: flex;
  gap: 22px;
  flex-wrap: wrap;
  padding-left: 20px;
  color: var(--muted);
}
.workflow .done {
  color: var(--green);
}
.back-link {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 18px;
}
.check {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 16px;
}
.check input {
  width: auto;
  margin-top: 6px;
}
.job {
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
}
form .row {
  margin-top: 12px;
}
</style>
