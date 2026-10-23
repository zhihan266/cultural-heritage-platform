import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useUserStore } from '@/stores/user'

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/dashboard' },
  { path: '/login', component: () => import('@/views/Login.vue') },
  { path: '/register', component: () => import('@/views/Register.vue') },
  {
    path: '/dashboard',
    component: () => import('@/views/Dashboard.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/rag-tasks',
    component: () => import('@/views/RagTasks.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/rag-tasks/new',
    component: () => import('@/views/UserPublishTask.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/rag-tasks/:id',
    component: () => import('@/views/RagTaskDetail.vue'),
    meta: { requiresAuth: true },
  },
  { path: '/qa', component: () => import('@/views/Qa.vue'), meta: { requiresAuth: true } },
  {
    path: '/qa/tasks/:id',
    component: () => import('@/views/QaTaskDetail.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/profile/questions',
    component: () => import('@/views/QaHistory.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/qa/knowledge',
    component: () => import('@/views/Qa.vue'),
    meta: { requiresAuth: true },
  },
  { path: '/ai', component: () => import('@/views/Ai.vue'), meta: { requiresAuth: true } },
  {
    path: '/profile',
    component: () => import('@/views/Profile.vue'),
    meta: { requiresAuth: true },
  },
  { path: '/user', redirect: '/dashboard' },
  { path: '/user/publish-task', redirect: '/rag-tasks/new' },
  { path: '/user/task-results', redirect: '/rag-tasks?view=published' },
  { path: '/worker', redirect: '/rag-tasks?view=accepted' },
  { path: '/:pathMatch(.*)*', component: () => import('@/views/NotFound.vue') },
]
const router = createRouter({ history: createWebHistory(import.meta.env.BASE_URL), routes })
router.beforeEach((to) => {
  const user = useUserStore()
  user.initUserInfo()
  if (to.meta.requiresAuth && !user.isLoggedIn)
    return { path: '/login', query: { redirect: to.fullPath } }
  if (['/login', '/register'].includes(to.path) && user.isLoggedIn) return '/dashboard'
  return true
})
export default router
