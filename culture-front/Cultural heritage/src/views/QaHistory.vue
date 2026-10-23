<script setup lang="ts">
import Layout from '@/layouts/index.vue'
import QaTaskList from '@/components/QaTaskList.vue'
import { Plus } from '@element-plus/icons-vue'
</script>
<template>
  <Layout
    ><div class="page-heading">
      <div>
        <h1>个人中心 · 我的问答</h1>
        <p class="muted">已保存的问答编号与服务器最新回执</p>
      </div>
      <RouterLink class="button primary" to="/qa"
        ><el-icon><Plus /></el-icon>新建问答</RouterLink
      >
    </div>
    <QaTaskList
  /></Layout>
</template>
