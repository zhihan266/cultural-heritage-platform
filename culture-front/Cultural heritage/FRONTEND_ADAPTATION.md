# 前端适配交付说明

> 2026-10-23：众包公共/个人GET列表已补齐，legacy HTTP API已删除。当前契约和测试以 [RAG众包交付报告](../../Cultural%20heritage%20ai/docs/RAG_CROWD_DELIVERY.md) 为准；本文以下为历史记录。

> 历史记录：这是上一轮适配时的说明。本轮已替换问答窗口的执行策略，并彻底分开公共广场与个人记录。当前行为、接口与验收结论以 [前端业务逻辑审计](FRONTEND_LOGIC_AUDIT.md) 为准；下文 legacy QA、手选 Agent 和广场展示本地任务的描述不再适用。

验收日期：2026-10-23。

## 1. 范围与业务结构

前端：`C:\Code\culture-project\culture-front\Cultural heritage`。

后端：`C:\Code\culture-project\Cultural heritage ai`，本次仅作只读检查。本次编辑限于前端目录，没有编辑后端代码、配置、SQL、迁移或后端测试，没有执行 SQL、启动后端生命周期或操作数据库。

沿用 Vue 3、TypeScript、Vue Router、Element Plus、Pinia、Axios、Vite。未新增依赖、未升级框架、未改动锁文件。认证仍使用现有登录回执与 JWT 拦截器。

| 入口 | 前端路径 | 职责 |
| --- | --- | --- |
| 工作台 | `/dashboard` | 当前账号、登录时积分、本浏览器任务数量、真实 Agent 数量、业务导航 |
| 任务广场 | `/rag-tasks` | 编号查找、刷新已知任务，不冒充公开全站列表 |
| 我发布的任务 | `/rag-tasks?view=published` | 根据服务端发布者过滤已知任务 |
| 我接取的任务 | `/rag-tasks?view=accepted` | 根据本账号接取/提交回执过滤已知任务 |
| 发布众包任务 | `/rag-tasks/new` | 实际众包模型，不再使用旧 multipart 任务发布 |
| 众包详情 | `/rag-tasks/:id` | 接取、人工答案、正式聚合、独立入库状态、失败阶段重试 |
| 普通问答 | `/qa` | 独立 legacy 普通 QA 资源，异步查询正式回答 |
| 知识库问答 | `/qa/knowledge` | 独立 RAG 问答接口与引用来源 |
| Agent 管理 | `/ai` | 列表、注册、健康检查，实际角色 `worker` / `judge` |
| 个人中心 | `/profile` | 登录回执账号信息、退出，不虚构资料更新与积分刷新 |
| 登录 / 注册 | `/login`、`/register` | 现有账号体系，不添加账号角色选择 |

旧 `/user`、`/user/publish-task`、`/user/task-results`、`/worker` 分别重定向至工作台、新众包发布、已发布、已接取。未知地址有未找到页面。受保护入口检查登录，登录后恢复合法站内跳转。

原工程没有独立 HCP-MAD 前端页面；本次没有删除后端 HCP-MAD 功能，也没有将其兼容 `/users/tasks/{id}` 误作普通 QA 或众包接口。

## 2. 文件清单

路径均相对前端目录。源码和测试对比开始时的文件快照；构建产物、截图、日志不列为源文件。

### 修改

| 文件 | 变化 |
| --- | --- |
| `src/api/ai.ts` | 当前 Agent 列表、注册、健康检查 |
| `src/api/index.ts` | 导出新众包、QA 模块，移除失效入口 |
| `src/api/request.ts` | JWT、401 清理跳转、FastAPI 校验、状态冲突与服务错误 |
| `src/api/types.ts` | 当前 Agent 角色与配置；普通 QA 所需 legacy 类型保留，众包类型独立 |
| `src/api/rag.ts` | 保留有效 RAG 封装，问答增加可取消请求参数；QA 复用同一封装 |
| `src/layouts/index.vue` | 五类业务菜单、账号与退出 |
| `src/router/index.ts` | 新业务路由、认证守卫、旧地址重定向 |
| `src/main.ts` | 加载统一样式 |
| `src/stores/user.ts` | 保留认证，清理无效残留用户状态 |
| `src/views/Login.vue` | 真实登录、错误和安全站内跳转 |
| `src/views/Register.vue` | 真实注册、长度与密码 UTF-8 字节校验 |
| `src/views/Ai.vue` | 当前 Agent 模型、注册配置、连接检查、敏感错误隐藏 |
| `src/views/UserPublishTask.vue` | 改为众包发布；未知结果暂停重复发布 |
| `tests/integration.mjs` | 当前 OpenAPI 只读契约校验与前端回归测试 |
| `package.json` | 仅增加 `test:browser` 脚本，依赖不变 |
| `.gitignore` | 忽略浏览器验收输出 |
| `README.md` | 更新已失效的接口、测试和启动描述 |

### 新增

| 文件 | 职责 |
| --- | --- |
| `src/api/ragTasks.ts` | 众包独立请求、响应、状态类型及接口 |
| `src/api/qa.ts` | 普通 QA 创建/状态，已有知识问答封装的别名 |
| `src/utils/crowdTasks.ts` | 账号隔离的编号索引、回执、原文重放、答案字节校验 |
| `src/utils/markdown.ts` | 安全 Markdown 到 Vue VNode，不注入原始 HTML |
| `src/components/SafeMarkdown.vue` | 统一安全回答渲染 |
| `src/styles.css` | 统一布局、表单、状态、响应式和长文本规则 |
| `src/views/Dashboard.vue` | 工作台 |
| `src/views/RagTasks.vue` | 三个众包页签、编号查找、真实状态筛选与刷新 |
| `src/views/RagTaskDetail.vue` | 众包操作与处理阶段 |
| `src/views/Qa.vue` | 普通 / 知识问答窗口 |
| `src/views/Profile.vue` | 个人中心 |
| `src/views/NotFound.vue` | 未找到页面 |
| `tests/browser.mjs` | 隔离 HTTP 的浏览器验收，不属于生产代码 |
| `public/heritage-wall.jpg` | 工作台本地长城实景图片，来源链接见 README |
| `FRONTEND_ADAPTATION.md` | 本说明 |

### 删除

- `src/views/User.vue`、`src/views/Worker.vue`、`src/views/UserTaskResults.vue`：旧通用任务页面，由新众包页面替代。
- `src/api/user.ts`、`src/api/worker.ts`、`src/api/task.ts`：旧任务入口封装，避免普通流程承担众包业务。
- `src/utils/userTasks.ts`、`src/utils/publishTask.ts`：旧任务索引与发布组装。
- `tests/backend_fixture.py`、`tests/live_workflow.py`：前端目录中的过期测试辅助程序，依赖已迁移的模块/路由；未删除或编辑后端测试。

## 3. 真实后端接口

检查依据：当前 `app/main.py` / `app/api` 路由装配、众包路由/应用服务、账号/Agent 路由、RAG 模型、legacy 用户路由、众包工作流文档及实际 OpenAPI。不是根据表名推测 HTTP 能力。

下表为去掉 `/api` 代理前缀后的后端地址。所有前端业务页面要求登录，Axios 有 Token 时统一发送 Bearer；这不代表每个后端路由都实现了服务端鉴权。

| 页面 / 用途 | 方法和地址 | 请求、响应、权限 |
| --- | --- | --- |
| 注册 | `POST /users/register` | `{username,password}` -> `{user_id,username}`；无需 JWT |
| 登录 | `POST /users/login` | `{username,password}` -> `{access_token,token_type,account}`；无需 JWT |
| 退出 | `POST /users/logout` | Bearer；成功后清理登录状态 |
| 工作台、Agent、问答模型选择 | `GET /ais` | `AiAgent[]`；当前后端未声明账号鉴权依赖 |
| Agent 注册 | `POST /ais` | 返回 `{ai}`；当前后端未声明账号鉴权依赖 |
| Agent 健康检查 | `POST /ais/{id}/health-check` | `{available,last_checked_at,last_error}`；当前后端未声明账号鉴权依赖 |
| 众包发布 | `POST /rag-tasks` | `{question,answer_format,worker_count,title?}`；201 返回完整快照；JWT 发布者由服务端确定 |
| 已知列表、详情 | `GET /rag-tasks/{id}` | 完整快照；仅 JWT 发布者或已接取/提交的参与者，否则 403 |
| 进度轮询 | `GET /rag-tasks/{id}/status` | 同一快照、相同权限，没有猜测百分比 |
| 接取众包 | `POST /rag-tasks/{id}/accept` | 无请求体，JWT 身份参与；返回快照 |
| 提交人工答案 | `POST /rag-tasks/{id}/answers` | `{content}`，须已接取；快照附 `submission_reused` |
| 正式结果查询 | `GET /rag-tasks/{id}/result` | `{task_id,status,result,ingestion}`；已封装，详情直接使用快照内同一正式数据避免重复请求 |
| 发布者重试 | `POST /rag-tasks/{id}/retry` | `{acknowledge_unknown:boolean}`；发布者、失败任务、可重试阶段 |
| 普通 QA 发送 | `POST /rag-tasks/legacy/users/tasks` | `{user_id,question,answer_format,ai_ids:[workerId],worker_count:0}` -> `{task}`；JWT 覆盖提交者 |
| 普通 QA 查询 | `GET /rag-tasks/legacy/users/tasks/{id}` | `{task}`；发布者 JWT；只认 completed 的 `final_result` |
| 知识库 QA | `POST /rag/answer` | `{query,ai_id,top_k:5}` -> `{query,ai_id,answer,sources}`；当前后端未声明账号鉴权依赖 |
| 保留有效封装，无新增页面入口 | `POST /rag/retrieve`、`POST /admin/rag/documents` | 不自动拼入众包发布或问答流程 |

### 字段与状态

发布字段：`question` 非空、最多 10000 字符；`answer_format` 非空、最多 2000 字符；可选 `title` 非空、最多 200 字符；`worker_count` 是 1 至 1000 的严格整数。请求禁止额外字段。实际发送 `worker_count`，不是响应中的 `required_worker_count`；不发送 `user_id`、`metadata`、附件、预算、截止时间或 `ai_ids`。

快照：`task_id`、`publisher_id`、`question`、`answer_format`、`title`、`status`、`required_worker_count`、`accepted_worker_count`、`valid_worker_count`、`aggregation_status`、`jobs`、`result`、`ingestion`。任务本身没有创建时间，列表不显示假时间。

- 众包状态：`collecting`、`aggregating`、`ingesting`、`completed`、`failed`。
- 作业状态：`pending`、`running`、`retry`、`uncertain`、`succeeded`、`failed`；阶段 `aggregate` / `ingest`。
- 入库状态：`pending`、`running`、`succeeded`、`failed`，也可能没有入库记录。
- 普通 QA 状态：`waiting`、`answering`、`aggregating`、`completed`、`failed`；不与众包枚举混用。

正式 `result.content` 与工人输入分区；版本、答案数、生成时间来自结果回执。知识入库成功单独依据 `ingestion.status`，不把答案提交或聚合完成当成已入库。

Agent 注册对齐 `name`、`api_url`、`type`、`initial_score`、`provider`、`model_name`、`auth_type`、`credential_ref`、`generation_config`。角色为 `worker` / `judge`。Bearer 凭据只引用后端当前支持的 `DEEPSEEK_API_KEY`，前端不接收、保存或展示真实密钥。

### 错误与权限

- 401：清理 Token / 用户回执，登录后可回到当前站内地址。
- 403：权限提示；未参与者按编号查看被拒后，可显式请求接取，服务器最终判定。
- 404：记录不存在，不生成占位任务冒充成功。
- 409：重复接取、满额、关闭征集、答案不可修改、无重试阶段等分别提示并刷新。
- 422：FastAPI 参数校验消息；答案另按 65535 UTF-8 字节校验。
- 500 / 503：安全服务错误，不展示堆栈/密钥；可能写入的操作不假定“失败等于未写入”。
- 超时 / 断网：明确结果未知，不自动重试发布、不变更答案重复写入。
- 任务执行失败：读取真实阶段状态，与 HTTP 失败和校验错误分开。

后端允许发布者接取参与，前端未擅自禁止。前端可见性仅是交互控制，不能替代服务端鉴权。

## 4. RAG 众包完整操作流程

1. 登录，发布标题（选填）、问题、格式与人工数量。
2. 等待服务端 201，保存真实编号并进详情。发布超时/5xx 时暂停重复发布，须核实此前未创建后显式解除。后端没有发布幂等键或恢复编号接口，前端不编造。
3. 分享链接/编号。另一账号打开，未参与时看到权限提示，可接取；成功后重新读详情，不提前显示成功。
4. 答案去掉首尾空白，校验非空及 UTF-8 字节数。在账号隔离存储中先保存待确认原文，再核对当前快照、发送答案。
5. 后端已有同账号同正文时返回 `submission_reused=true`，后续阶段也能核对原提交；不同正文返回 409、不可修改。没有虚构幂等头或键。
6. 断网/超时/5xx 后锁定原文，刷新仍保留。再次操作只核对状态并重放相同原文。成功后保存确认回执、不提供无限提交；明确校验/权限/状态拒绝则刷新并依返回处理。
7. 收齐有效答案后由后端创建聚合作业。详情仅在 `aggregating` / `ingesting` 每 5 秒读状态；页面隐藏暂停请求，离开清理定时器，失败停止自动请求。编号切换隔离请求序号，旧响应不覆盖新任务。
8. 正式答案与入库独立展示；入库成功以回执和切片数为准。折叠处理阶段与尝试次数，不显示任意原始错误文本。
9. 失败时仅发布者看到重试。作业 `uncertain` 必须勾选确认可能重复计费，再传 `acknowledge_unknown=true`。

本地仅存账号隔离的编号、操作回执、待确认原文，最多 200 项，不是服务器任务数据库。列表仍逐项读取实时详情，不使用假任务补空，不声称全部历史。存储不可用时不发送无法安全保留原文的答案；已收到服务器成功而本地回执保存失败时说明限制。

## 5. AI 问答完整操作流程

1. 进入普通问答，读取真实 Agent，选择可用 `worker`。
2. Enter 发送、Shift + Enter 换行，中文输入法组合输入不误发；发送期间禁止重复操作，用户与 AI 消息分开。
3. 普通问答创建独立 legacy QA 资源，人工数量 0，指定一个回答 Agent。按返回编号查进度，只展示 completed 的 `final_result`；仍依赖已有后端聚合/评分配置，不绕过后端直接调模型。
4. 查询失败或等待超过当前轮次，可继续 GET 已创建问答，不自动再 POST。若创建结果未知且无编号，说明未重复发送，不虚构恢复。
5. 知识库问答使用另一入口和 `/rag/answer`，不是虚构 `use_rag`。显示真实回答、来源文件、片段序号、分数；没有来源则如实显示，不编造未返回的片段正文。
6. Markdown 支持中文、标题、粗体、行内代码、代码块、列表、引用和基础表格。Vue VNode 渲染并转义 HTML，仅 HTTP/HTTPS 链接，附安全链接属性。跟随新消息，用户向上阅读时不强拉到底部。
7. 新建对话仅清空当前页面，不假称创建服务端会话。离开取消本地请求/等待，不等于取消服务端任务。刷新不恢复对话，后端没有会话历史 API。

## 6. 接口缺口与限制

| 能力 | 后端情况 | 前端处理 |
| --- | --- | --- |
| 公开、我的发布、我的接取全量列表与分页 | 没有集合接口 | 明示限制，按本浏览器编号读实时详情，支持分享；不调用不存在的 `GET /rag-tasks` |
| 本人参与阶段、答案正文、提交时间查询 | 无专用 API，快照不含 | 本地确认回执与远端未知区分；跨设备已提交无法预知，服务器冲突为准 |
| 工人原始答案、Agent 执行记录 | 表存在但无众包公开查询 | 不伪造折叠数据 |
| 取消众包任务 | 无接口 | 不提供取消已发布任务；表单取消只离开未提交页面 |
| 发布幂等 / 未知后恢复编号 | 无协议 | 暂停重复并要求核实，不能保证未知网络结果下恰好一次 |
| 任务创建时间 | 快照不返回 | 不显示假时间 |
| 普通 QA 的 RAG 开关、流式、服务端会话历史 | 不支持 | 不伪造；独立知识问答调用真实 RAG API |
| `/rag/answer` 来源正文 | 仅元数据 | 展示实际来源，不编造引文 |
| 个人资料修改、积分实时刷新 | 无接口 | 显示登录回执信息，不提供假更新 |
| Agent 编辑 / 删除 | 无接口 | 只保留真实注册、列表和健康检查 |
| Agent / RAG 服务端账号鉴权 | 当前相关路由未声明鉴权依赖 | 登录门禁和 JWT 注入不等于服务端安全保障；本次不改后端 |

**当前 API 能支持的前端流程已实现；全站公开任务广场、完整跨设备历史、个人已提交答案预查询仍受后端缺口限制，没有伪称全部可用。**

## 7. 实际测试与结果

命令在本前端目录实际运行。浏览器为本机 Chrome，桌面 1440 x 1000、移动端 390 x 844。

| 命令 | 结果 |
| --- | --- |
| `npm.cmd ls --depth=0` | 成功，无缺失依赖；Node v22.13.0、Vue 3.5.34、Element Plus 2.14.1、Vite 8.0.14 |
| `npm.cmd run type-check` | 成功；最终 build 再次包含并通过 |
| `npm.cmd exec eslint .` | 成功，退出码 0 |
| `npm.cmd exec oxlint .` | 成功，33 文件、0 警告、0 错误 |
| `npm.cmd test` | 13 项通过、0 失败 |
| `node tests/browser.mjs`（第 8 节环境变量） | 9 组场景通过、0 页面 JavaScript 异常 |
| `npm.cmd run build` | 类型检查、生产构建成功，输出 dist |

13 项测试加载真实前端模块，对照当前后端 OpenAPI，检查 JWT、路径/众包发布字段/角色、401/403/404/409/422/500/503/超时、账号回执隔离、原文幂等与字节限制、Markdown 安全。

浏览器 9 组场景：登录门禁；众包发布/真实编号；接取、未知提交刷新后原文重放；正式聚合与入库独立；已知列表刷新/浏览器返回；普通和知识 QA 独立接口、Markdown、来源和窄屏；Agent 注册/健康检查；503 安全文案及 401 清理跳转；发布者未知结果确认重试。另检查主要页面无横向溢出、图片加载、任意处理错误文本隐藏。

故意测试的 403、断网、503、401 会产生资源错误日志，但没有未处理页面异常。测试替身只在 tests 中，生产页面没有测试账号、模拟任务或成功兜底响应。

构建保留第三方 `@vueuse/core` PURE 注释位置警告、Element Plus 主包大于 500 kB 的体积提示，不阻断构建；没有为消除提示升级依赖或修改构建架构。

证据：`tests/artifacts/browser-report.json`、`dashboard-desktop.png`、`task-detail-desktop.png`、`qa-desktop.png`、`qa-mobile.png`、`agents-mobile.png`。已查看截图，未发现文字互相覆盖。

**验证边界：没有启动真实后端，没有通过真实数据库完成发布、接取、模型聚合或向量入库，不声称真实前后端 E2E 已通过。** 契约测试仅导入 OpenAPI、不触发 lifespan；浏览器用隔离 HTTP 响应驱动真实 Vue 页面。没有 SQL 操作。

后端指纹核对期间观察到其他并行改动：开始检查的 320 个源码/文档/SQL/测试文件中，12 个既有文件变化、另新增 2 个文件，涉及 Agent、HCP-MAD、RAG 实现与测试。本次未写入或回退这些文件，不将整个工作区原有/并行差异归为本次修改。所用众包路由/服务与模型契约未变，最终测试读取的是当前后端。

## 8. 启动与人工验收

当前保留前端开发服务：<http://127.0.0.1:5175/>。页面只请求配置的真实后端；后端不可用时显示连接错误，不填充示例任务。

重新启动：

```powershell
Set-Location 'C:\Code\culture-project\culture-front\Cultural heritage'
npm.cmd run dev -- --host 127.0.0.1 --port 5175
```

5175 已被本次服务占用时不重复启动；选其他空闲端口时同步调整浏览器测试 FRONTEND_URL。默认后端 `http://127.0.0.1:8000`；本目录 `.env.local` 的 BACKEND_URL 可替换代理目标。依赖已安装并检查通过，不需要为此次验收重新安装。

隔离浏览器验收需保持前端运行，在另一终端：

```powershell
Set-Location 'C:\Code\culture-project\culture-front\Cultural heritage'
$env:PLAYWRIGHT_NODE_MODULES='C:\Users\17708\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
$env:PLAYWRIGHT_CHANNEL='chrome'
$env:FRONTEND_URL='http://127.0.0.1:5175'
npm.cmd run test:browser
```

Playwright 使用本机已有运行时，不是项目新增依赖；其他机器可指向自己的模块目录。

真实人工验收在已有可运行后端环境中执行，不要求重建表：

1. 真实账号登录，核对工作台、Agent、账号信息、退出与路由权限。
2. 发布需要 1 名工人的任务，复制服务端真实链接。
3. 另一真实账号打开、接取、提交，核对答案不可修改以及真实后台聚合、入库。
4. 发布者核对正式结果与独立入库回执；仅在真实失败阶段验收重试。
5. 普通问答选可用 Agent，核对正式答案；知识库问答核对引用，服务不可用时核对真实错误。
6. 刷新、返回、切换账号，核对编号索引隔离和缺口提示；窄窗口核对长回答和表单。

上述真实数据验收步骤未执行，不属于已通过的隔离测试。

## 9. 完成情况

前端代码、业务路由、独立 API/类型、认证、众包可支持操作、问答、Agent、个人中心、安全渲染、测试和生产构建已落地，没有待接入的模拟接口或占位实现。

未完成业务能力限于第 6 节明确的后端缺口；真实模型、数据库、队列和向量库端到端验收未执行。未为迁就前端修改后端，未创建 Git 提交。
