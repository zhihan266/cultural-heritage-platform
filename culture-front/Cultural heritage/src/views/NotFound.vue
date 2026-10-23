<script setup lang="ts">
import Layout from '@/layouts/index.vue'
</script>
<template>
  <Layout
    ><div class="empty">
      <h1>页面不存在</h1>
      <p>此链接可能已失效。</p>
      <RouterLink to="/dashboard">返回工作台</RouterLink>
    </div></Layout
  >
</template>
