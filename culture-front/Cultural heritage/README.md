# Cultural Heritage 前端

Vue3 / TypeScript / Pinia / Axios / Element Plus / Vite。完整部署见[教程](../../docs/DEPLOYMENT.md)。

```powershell
npm ci
Copy-Item .env.example .env
npm run dev
npm test
npm run type-check
npx eslint .
npx oxlint .
npm run build
```

VITE_API_URL默认/api，BACKEND_URL为开发/预览代理目标，本机8000。Vite开发5173、preview4173。生产静态服务器须自行配置/api反向代理与SPA回退。

浏览器验收依赖Playwright（已锁定）和Chrome：

```powershell
$env:PLAYWRIGHT_CHANNEL='chrome'
npm run test:crowd-http
```

Linux另设PYTHON=后端虚拟环境/bin/python；测试默认Windows路径。也可手动npx playwright install chromium，需下载浏览器；本次使用已有Chrome，没有下载浏览器。测试使用临时SQLite和独立FastAPI，不调用真实模型或消费者。

npm audit的19项风险及devtools传递依赖版本告警已记录，未强制升级。仅用于受控本地演示。

## 问答任务工作台

问答创建后进入 `/qa/tasks/{id}`，自动读取服务器状态和正式 `final_result`。个人中心的“我的问答”及 `/profile/questions` 可重新打开任务；普通与知识库模式分别发送真实的 `use_rag`。

HCP 尚无当前用户历史列表接口，本浏览器只按账号保存已知任务编号，问题、状态与答案均重新从服务器获取，不保存聊天文本。刷新、切换页面和同浏览器重新登录可恢复；换设备或清理编号索引后不能保证恢复完整历史。

轮询按 2.5 秒串行读取，有 180 秒预算和网络退避，终态停止，卸载只取消本地读取、不取消后台任务。完整设计、文件清单、测试结果与限制见 [问答持久化交付说明](QA_TASK_PERSISTENCE.md)。
