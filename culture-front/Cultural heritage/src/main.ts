import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './styles.css'

import App from './App.vue'
import router from './router'
import { useUserStore } from './stores/user'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)
app.use(ElementPlus)

app.mount('#app')

// 初始化用户信息
const userStore = useUserStore()
userStore.initUserInfo()

// Another tab can replace the shared JWT. Reload before attributing work to the old account.
let reloadingSession = false
window.addEventListener('storage', (event) => {
  if (
    !reloadingSession &&
    event.storageArea === localStorage &&
    (event.key === null || ['token', 'userInfo'].includes(event.key))
  ) {
    reloadingSession = true
    window.location.reload()
  }
})
