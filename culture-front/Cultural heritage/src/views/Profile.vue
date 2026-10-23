<script setup lang="ts">
import { SwitchButton } from '@element-plus/icons-vue'
import Layout from '@/layouts/index.vue'
import QaTaskList from '@/components/QaTaskList.vue'
import { useUserStore } from '@/stores/user'
import { useRouter } from 'vue-router'
const user = useUserStore()
const router = useRouter()
async function logout() {
  await user.logout()
  await router.push('/login')
}
</script>
<template>
  <Layout
    ><div class="page-heading">
      <div>
        <h1>个人中心</h1>
        <p class="muted">当前登录账号</p>
      </div>
    </div>
    <section class="band">
      <h2>{{ user.account?.username }}</h2>
      <dl class="facts">
        <div>
          <dt>积分</dt>
          <dd>{{ user.account?.score.toFixed(2) }}</dd>
        </div>
        <div>
          <dt>信息来源</dt>
          <dd>本次登录回执</dd>
        </div>
      </dl>
      <p class="muted">当前服务未提供资料修改及账号信息刷新接口，最新积分可在重新登录后获取。</p>
      <button @click="logout">
        <el-icon><SwitchButton /></el-icon>退出当前账号
      </button>
    </section>
    <RouterLink class="button" to="/profile/questions">查看全部问答</RouterLink>
    <section class="band"><QaTaskList :page-size="5" /></section>
  </Layout>
</template>
