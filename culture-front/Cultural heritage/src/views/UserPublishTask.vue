<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, Promotion } from '@element-plus/icons-vue'
import Layout from '@/layouts/index.vue'
import { ragTasksApi } from '@/api/ragTasks'
import { getErrorMessage, isUncertainRequest } from '@/api/request'
import { useUserStore } from '@/stores/user'
const user = useUserStore(),
  router = useRouter()
const form = reactive({
  title: '',
  question: '',
  answer_format: '主题、知识正文、冲突与不确定点、来源',
  worker_count: 3,
})
const saving = ref(false),
  error = ref(''),
  storageKey = 'heritage:unconfirmed-publish:' + user.userId
const uncertain = ref(sessionStorage.getItem(storageKey) === '1'),
  acknowledged = ref(false)
function unlock() {
  if (acknowledged.value) {
    sessionStorage.removeItem(storageKey)
    uncertain.value = false
    acknowledged.value = false
  }
}
async function submit() {
  if (saving.value || uncertain.value) return
  error.value = ''
  if (
    !form.question.trim() ||
    !form.answer_format.trim() ||
    !Number.isInteger(form.worker_count) ||
    form.worker_count < 1 ||
    form.worker_count > 1000
  ) {
    error.value = '请填写完整的问题、答案要求和 1–1000 的整数工人数。'
    return
  }
  saving.value = true
  try {
    sessionStorage.setItem(storageKey, '1')
    const task = await ragTasksApi.publish({
      question: form.question.trim(),
      answer_format: form.answer_format.trim(),
      worker_count: form.worker_count,
      ...(form.title.trim() ? { title: form.title.trim() } : {}),
    })
    sessionStorage.removeItem(storageKey)
    await router.push('/rag-tasks/' + encodeURIComponent(task.task_id))
  } catch (e) {
    error.value = getErrorMessage(e, '任务发布失败')
    uncertain.value = isUncertainRequest(e)
    if (!uncertain.value) sessionStorage.removeItem(storageKey)
  } finally {
    saving.value = false
  }
}
</script>
<template>
  <Layout>
    <RouterLink to="/rag-tasks" class="back-link"
      ><el-icon><ArrowLeft /></el-icon>返回众包任务</RouterLink
    >
    <div class="page-heading">
      <div>
        <h1>发布众包任务</h1>
        <p class="muted">明确问题与答案要求，征集可用于知识聚合的资料</p>
        <p class="muted">
          发布后，任务问题及正式聚合知识对登录用户公开；其他工人的原始回答不公开。
        </p>
      </div>
    </div>
    <div v-if="uncertain" class="notice error" role="alert">
      <p>上次发布结果未确认，已暂停重复发布。请先到“我发布的任务”核对。</p>
      <RouterLink to="/rag-tasks?view=published">核对我发布的任务</RouterLink>
      <label class="check"
        ><input
          v-model="acknowledged"
          type="checkbox"
        />我已核实此前未创建任务，确认允许再次发布</label
      ><button :disabled="!acknowledged" @click="unlock">解除暂停</button>
    </div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <form class="band form-shell" @submit.prevent="submit">
      <div class="form-grid">
        <label class="span-2"
          >任务标题（选填）<input
            v-model="form.title"
            maxlength="200"
            placeholder="例如：整理苏州园林的造园手法"
        /></label>
        <label class="span-2"
          >任务问题 <span class="muted">{{ form.question.length }} / 10000</span
          ><textarea
            v-model="form.question"
            required
            maxlength="10000"
            rows="7"
            placeholder="描述需要收集、考证或比较的问题"
          />
        </label>
        <label class="span-2"
          >答案格式要求<textarea v-model="form.answer_format" required maxlength="2000" rows="3" />
        </label>
        <label
          >所需工人数<input
            v-model.number="form.worker_count"
            required
            type="number"
            min="1"
            max="1000"
            step="1"
        /></label>
      </div>
      <div class="form-actions">
        <button class="primary" :disabled="saving || uncertain">
          <el-icon><Promotion /></el-icon>{{ saving ? '正在发布…' : '发布任务' }}</button
        ><RouterLink to="/rag-tasks">取消</RouterLink>
      </div>
    </form>
  </Layout>
</template>
<style scoped>
.back-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 18px;
}
.check {
  display: flex;
  gap: 8px;
  align-items: center;
  margin: 12px 0;
}
.check input {
  width: auto;
}
</style>
