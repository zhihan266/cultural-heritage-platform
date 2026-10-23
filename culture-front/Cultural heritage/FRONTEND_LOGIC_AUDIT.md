# 前端业务逻辑审计与修正

> 2026-10-23：众包公共/个人GET列表已补齐，legacy HTTP API已删除。当前契约和测试以 [RAG众包交付报告](../../Cultural%20heritage%20ai/docs/RAG_CROWD_DELIVERY.md) 为准；本文以下为历史记录。

日期：2026-10-23。本说明记录上一轮业务逻辑审计；当前问答已从临时窗口改为可恢复任务工作台，最新说明见 [问答任务持久化交付](QA_TASK_PERSISTENCE.md)。下文关于问答仅在当前页面保留的描述不再代表当前版本。

范围：仅修改 `C:\Code\culture-project\culture-front\Cultural heritage` 中的前端源码、测试和文档。后端只读，未启动其生命周期，未修改配置、数据库、SQL、迁移、后端测试或任何后端业务实现。没有新增依赖、升级 UI 框架或创建 Git 提交。

结束时对开始快照中的 322 个后端源码、测试、文档和 SQL 文件进行 SHA-256 核对，数量和内容均一致，无新增或删除。前端快照对比为 16 个文件修改、1 个报告新增、0 个文件删除；生成的构建产物和测试证据不计入源码文件清单。

## 1. 审计结论与真实 API

依据为当前实际路由注册、Pydantic 模型与服务校验，不按现有前端反推后端。重点检查了 `app/main.py`、`app/api/task_routes.py`，账号与 Agent 路由/模型/服务、RAG 路由/模型，众包路由/服务/Facade，以及 HCP-MAD 提交、选择器、编排、结果查询。实际只读导出 OpenAPI 再与前端测试对照。

下列地址是后端完整资源路径，前端开发环境通过 `/api` 代理转发，默认后端 `http://127.0.0.1:8000`。所有业务页面保留前端登录门禁；不能据此声称后端所有接口都已鉴权。

| 页面 / 能力 | Method、URL | 请求 | 响应与权限 |
| --- | --- | --- | --- |
| 注册 | `POST /users/register` | `{username,password}` | `{user_id,username}`；无需 JWT |
| 登录 | `POST /users/login` | `{username,password}` | `{access_token,token_type,account:{id,username,score}}`；无需 JWT |
| 退出 | `POST /users/logout` | 无业务请求体 | Bearer；退出后清理会话 |
| 当前普通问答 | `POST /hcp-mad/tasks` | `{question,use_rag:false}` | `{task:QaTask}`；JWT，发布者取自服务端 |
| 当前知识增强问答 | `POST /hcp-mad/tasks` | `{question,use_rag:true}` | 同一正式 HCP-MAD 工作流，但真实开启共享知识检索 |
| 问答查询 | `GET /users/tasks/{task_id}` | 路径编号 | `{task:QaTask}`；JWT，仅本账号任务；本路径现为 HCP 查询，不是旧普通任务查询 |
| 众包发布 | `POST /rag-tasks` | `{question,answer_format,worker_count,title?}` | 201，完整 `CrowdTask`；JWT，发布者与聚合配置由服务端确定 |
| 众包详情 / 两个个人视图的已知记录刷新 | `GET /rag-tasks/{task_id}` | 路径编号 | `CrowdTask`；JWT，仅发布者或已参与者，否则 403 |
| 众包接取 | `POST /rag-tasks/{task_id}/accept` | 无请求体 | `CrowdTask`；JWT 当前账号参与 |
| 人工提交 | `POST /rag-tasks/{task_id}/answers` | `{content}` | `CrowdTask` 加可选 `submission_reused`；JWT，须已接取 |
| 众包状态 | `GET /rag-tasks/{task_id}/status` | 路径编号 | 同一快照、同一权限，包含实际作业和入库记录 |
| 正式结果 | `GET /rag-tasks/{task_id}/result` | 路径编号 | `{task_id,status,result,ingestion}`；同一权限。已有封装，详情直接使用快照中的同一正式结果以避免重复请求 |
| 众包失败重试 | `POST /rag-tasks/{task_id}/retry` | `{acknowledge_unknown:boolean}` | `CrowdTask`；JWT，仅发布者、失败任务、有可重试阶段 |
| 公共广场 | 无众包集合接口 | 无 | 不存在可对接的公共列表、筛选或分页能力 |
| 我的发布 / 我的接取全量查询 | 无众包个人集合接口 | 无 | 当前只允许按已知编号取得各自有权访问的快照 |
| 工作台 / Agent 列表 | `GET /ais` | 无 | `AiAgent[]`；当前后端未声明账号鉴权依赖 |
| Agent 注册 | `POST /ais` | 见表单契约 | `{ai:AiAgent}`；当前后端未声明账号鉴权依赖 |
| Agent 健康检查 | `POST /ais/{ai_id}/health-check` | 路径编号，无请求体 | `{available,last_checked_at,last_error}`；当前后端未声明账号鉴权依赖 |
| 个人中心 | 没有 `/me` / 资料修改 / 积分刷新接口 | 无 | 使用登录回执，不伪造实时刷新 |

仍存在但不用于当前问答页面的正式接口：

| 接口 | 实际约束 | 本次判断 |
| --- | --- | --- |
| `POST /rag-tasks/legacy/users/tasks` | `user_id,question,answer_format` 必填；`ai_ids` 可省略 | 业务创建不会补选模型；`TaskOrchestrator._claim_task` 对空 `ai_ids` 直接跳过。不能把“Schema 可选”当成有默认执行策略 |
| `POST /rag/answer` | `query,ai_id` 必填，`top_k` 可选 1..20，`document_id` 可空 | 必须指定执行 AI，不符合普通用户无需选模型的入口。低层有效 `ragApi.answer` 封装保留，不在问答页面调用 |
| `GET /rag-tasks/legacy/workers/hall` | JWT，旧 `HallResponse` | `available_hall_tasks` 明确排除 `mode == crowd`，不能作为众包公共池 |
| `GET /rag-tasks/legacy/users/{user_id}/messages` | JWT 本人，旧普通任务模型 | `tasks_for_user` / `completed_tasks_for_user` 同样排除众包，不能用于众包个人列表 |

### 类型、状态与字段归属

`QaTask` 对齐 HCP 查询返回：`id,user_id,question,answer_format,ai_ids,required_worker_count,accepted_worker_ids,status,answers,final_result,metadata,mode`。答案记录的 `execution_id` 可空，`final_result` 可空。后端 Task 模式枚举有 `normal/rag/hcp_mad`，当前问答只接受 `mode=hcp_mad`，避免把另一个资源当问答结果。

HCP 状态：`waiting,answering,aggregating,completed,failed`。只有 `completed` 且有非空 `final_result` 时才显示正式答案，不能取 `answers[0]` 代替。

`CrowdTask`：`task_id,publisher_id,question,answer_format,title,status,required_worker_count,accepted_worker_count,valid_worker_count,aggregation_status,jobs,result,ingestion`。`title/result/ingestion` 可空，提交响应可附布尔 `submission_reused`。

- 众包状态：`collecting,aggregating,ingesting,completed,failed`。
- 作业阶段：`aggregate/ingest`；状态 `pending,running,retry,uncertain,succeeded,failed`。
- 入库状态：`pending,running,succeeded,failed`，也可能尚无记录。
- 正式结果包括 `content,version,answer_count,created_at` 等实际字段。任务本身未返回创建时间，不在列表虚构时间。

JWT 依次校验 Bearer、黑名单、签名和有效期、Redis 当前会话、账号存在。当前认证模型没有工人/发布者/管理员角色字段：普通账号均可按任务规则参与，发布者/参与者是任务关系，不是注册角色。不能前端自行新增管理员角色或声称 Agent 注册已受管理员权限保护。

## 2. 问答 Agent 字段处理

已删除问答页的 Agent 下拉框、刷新按钮、可用模型前置校验及“请选择 Agent”空状态；页面不读取 `/ais`，不选首项、不硬编码模型 ID。

检查到现有 `POST /hcp-mad/tasks` 是可满足目标的另一条正式问答策略：Facade 调用 WorkerSelector，服务端按既有排名、角色、启用及健康规则选择两个 Worker 和一个 Judge。未经检测不等于检测失败，前端不再用自己的 available 筛选阻断服务端选择。参与者在服务端提交时固定，前端不做备用模型选择或回退调用。

页面明确标注 HCP-MAD，不假称这是旧单模型接口的默认 Agent。两种模式发送同一正式接口，但 `use_rag` 分别为 false/true；编排代码确实据此决定是否检索，不是只改标签。

请求只含用户问题和实际模式布尔值。不发送 `user_id,ai_id,ai_ids,agent_id,model_name,worker_count,metadata`，不显示内部执行 ID。`answer_format` 使用后端默认值；服务器确定账号、执行 Agent、任务编号与状态。

该策略依赖后端可选择的两个 Worker 与一个 Judge；不足/配置错误返回实际 409/422/503，页面显示真实错误，不引导用户手动挑选，也不改用其他接口伪装成功。

HCP 返回的检索回执为 `metadata.hcp_mad.rag_status/rag_hit_count`。前端分别展示 retrieved、empty、failed、disabled 的真实含义。后端检索失败会降级为无检索回答，页面明确提示降级，不假称 RAG 成功。此接口没有引用文件/片段明细，不复用 `/rag/answer` 的 sources，也不自行重复检索补造引用。

没有流式接口或服务端会话列表；聊天仅在当前页面保留，刷新不伪恢复。查询失败时继续 GET 已创建资源，不重新 POST，不将清空当前页面称为持久化会话操作。实际 `review_failed` 阶段会说明系统评审已暂停，停止当前自动等待；可查询原问答，但不会把查询冒充执行重试。

## 3. 公共广场与权限检查

当前后端 **不支持众包跨用户公共发现，也没有匿名公共浏览接口**。公共广场不能在仅改前端的限制下真正完成；没有通过移除 Token、复用旧大厅、遍历数据库 ID 或本地数组制造公共池。

修正后广场只显示“公共任务池暂不可用”和按分享编号打开入口，不加载本浏览器个人记录，不显示误导性的“暂无公开任务”，不发送不存在的 `GET /rag-tasks`。

“我发布的任务”和“我接取的任务”是独立视图。前者按真实快照发布者过滤，后者只按本账号接取/提交回执过滤；发布成功只记发布关系，不自动当接取。两者仍只是本浏览器已知编号的受权详情，不等于完整个人服务端历史。查询失败和确实没有本机记录分开显示。

按编号详情仅发布者或已参与者可看。用户 B 未接取时得到 403，可显式调用接取，由服务端决定。没有公开摘要接口，前端无法在接取前绕过权限显示问题详情。

| 情形 | 真实规则与修正 |
| --- | --- |
| 发布者接取自己的任务 | 后端允许；不在前端禁止 |
| 已接取再接取 | 409 `Worker has already accepted this task`；据此恢复确认参与回执，不靠中文提示匹配 |
| 发布者其他设备已接取、本机未知且人数已满 | 提供核对已有接取操作，调用真实接取接口；服务端先查重复再查容量，可用重复回执证明参与。不是凭发布身份推断接取 |
| 无工人角色 | 当前无独立工人角色限制，不虚构角色按钮规则 |
| 满员或非 collecting | 接取 409；能读取快照时禁用新接取，不把失败当成功 |
| 首次人工提交 | 必须已接取、collecting、参与者状态 accepted；答案非空且不超 65535 UTF-8 字节 |
| 相同原文重复提交 | 返回原回执，`submission_reused=true`，即使任务已进入后续阶段 |
| 已提交后不同正文 | 409 immutable；保存“服务器已有答案”回执，不把本机草稿冒充远端原文 |
| 提交前任务结束征集 | 先刷新快照；若是新答案则不发 POST；待确认原文仍允许按后端幂等规则重放 |
| 完成或失败后首次提交 | 不提供新答案提交；不推断有取消/过期等未定义状态 |
| 后台失败重试 | 仅发布者、failed 且有可重试阶段；uncertain 另需确认重复计费风险 |

401 清理会话重登；403/404/409/422 分别提示权限、记录不存在、业务冲突、参数校验；500/503 不泄漏堆栈。前端交互控制不能替代服务端鉴权。

## 4. 实际文件变更

新增：`FRONTEND_LOGIC_AUDIT.md`。

修改：

| 文件 | 修正 |
| --- | --- |
| `src/api/qa.ts` | 系统 HCP 请求/查询及独立类型，正式结果与真实检索回执 |
| `src/views/Qa.vue` | 删除 Agent 前置操作，对接服务端策略与真实 use_rag，显示降级，不虚构来源 |
| `src/api/ragTasks.ts` | 详情读取支持取消信号 |
| `src/views/RagTasks.vue` | 公共池不混入个人记录，页签区分，读取失败/空状态分开，切换取消旧请求 |
| `src/views/RagTaskDetail.vue` | 发布者跨设备参与恢复、状态变化后阻止新答案、按服务错误标识处理 immutable、请求隔离、隐藏无业务用途的原始 ID |
| `src/utils/crowdTasks.ts` | 回执布尔字段校验，个人提交状态明确来源与未知边界 |
| `src/api/request.ts` | 增加精确业务错误标识比较及系统模型配置错误文案 |
| `src/api/types.ts` | 删除已无消费者的旧任务/工人请求类型，避免继续混用；保留有效账号、Agent、RAG 类型 |
| `src/stores/user.ts` | 登录失败清理部分写入状态；恢复会话不再重写共享存储触发跨页循环 |
| `src/main.ts` | 另一标签页登录/退出后刷新当前会话，防止旧账号界面配新 JWT |
| `src/views/Ai.vue` | 接口地址按真实 Schema 改为选填、空值为 null；刷新/注册互斥；失败不显示“尚未注册” |
| `src/views/Profile.vue` | 保留账号信息，移除无必要的原始账号 ID，退出使用对应图标 |
| `tests/integration.mjs` | 新策略、Schema、字段、结果、检索降级、精确冲突与状态回归 |
| `tests/browser.mjs` | 无客户端选 Agent、个人/公共隔离、跨账号、跨设备、跨标签页、冲突与降级回归 |
| `README.md` | 当前业务说明和实际入口 |
| `FRONTEND_ADAPTATION.md` | 显式标为上一轮历史说明，避免旧契约被当作当前契约 |

删除文件：无。删除的是页面内旧交互、旧 QA API 方法和无消费者类型，不是有效账号、Agent 或 HCP 后端功能。

## 5. 原有错误与修正摘要

- 把 legacy Schema 可省略 ai_ids 与真正可执行的默认策略混为一谈的风险，改为追踪编排并使用现有 HCP 自动选择入口；不发送空列表碰运气。
- 普通用户必须选择 Agent，且前端选首个 available Worker，改为完全由后端选择执行者；Agent 管理保留独立。
- 广场展示本地账号记录容易被理解为公共池，改为只说明真实缺口，个人记录仅进个人页签。
- 发布/接取/提交关系混淆与跨设备未知状态，改为回执区分，不从发布关系推断接取，不声称能查询未返回的个人提交明细。
- immutable 判断依赖中文提示子串，改为 HTTP 状态与后端原始 detail 精确判断。
- 状态核对后仍可能明知征集已结束而发新答案，改为阻止新提交，只保留原文幂等核对。
- 不同任务、页签、账号的慢响应可能覆盖新页面，改为请求序号、取消与身份隔离；跨标签页 JWT 改变时重载会话。
- 可空的 Agent api_url 被前端错误要求必填，改为选填并传 null；可选字段不因出现在 Schema 就都变成必填输入。

## 6. 当前完整流程与边界

问答：登录 -> 选择普通/知识增强 -> 输入问题 -> 只发送 question/use_rag -> 后端选两 Worker、一 Judge -> 查询 HCP 任务 -> 正式 final_result 与实际检索回执。Enter、Shift+Enter、中文输入法、防重复、Markdown、失败提示与继续查询保留。不依赖访问 Agent 管理。

众包：登录 -> 发布业务内容 -> 服务端返回编号 -> **公共发现受后端缺口阻碍** -> 可分享链接/编号给另一账号 -> 后端判定接取 -> 保存服务器参与回执 -> 输入答案 -> 刷新状态 -> 提交/同原文核对 -> 达到后端门槛 -> 持久聚合作业 -> 正式结果 -> 独立知识入库 -> 发布者查看/受限重试。

没有以手工分享代替声称公共池已实现。人工原文、问答原始 Agent 答案、众包正式聚合结果分开处理，正式展示只取对应正式结果。

发布、人工提交、聚合、入库为独立阶段；不会根据本地答案数量自行判定成功。未知答案结果先保存原文，重放同正文，无虚构幂等头；未知发布结果暂停重复并要求核实，因后端无发布幂等恢复不能保证恰好一次。

## 7. 实际测试与验收

| 命令 | 真实结果 |
| --- | --- |
| `npm.cmd ls --depth=0` | 成功，无缺失依赖 |
| `npm.cmd run type-check` | 成功；最终 build 再次包含类型检查 |
| `npm.cmd exec eslint .` | 成功，退出码 0 |
| `npm.cmd exec oxlint .` | 33 个文件，0 警告、0 错误 |
| `npm.cmd test` | 15 项通过，0 失败 |
| `node tests/browser.mjs`（下列环境） | 19 组场景通过，0 页面 JavaScript 异常 |
| `npm.cmd run build` | 类型检查与生产构建成功，输出 dist |

浏览器为本机 Chrome，桌面 1440 x 1000，移动端 390 x 844。覆盖无 Agent 选择/无 Agent 列表请求、真实 HCP 两种请求参数、RAG 失败降级、评审暂停、查询恢复不重复创建、公开缺口不伪装、A 发布与 B 接取视图隔离、重复接取、immutable、征集前置校验、满额、404、幂等未知结果、正式聚合/入库分离、401、503、跨标签页登录及 Agent/个人中心保留功能。

测试对“公共发现不可用且未伪造”通过，不是“A 发布后 B 已在公共池发现”通过。报告单独列出 `blockedRequirements`。普通及知识模式的模型选择发生在后端策略，本次测试检查其真实契约及前端请求，没有实际运行远程模型选择/推理。

构建仅有现有第三方 PURE 注释位置及大包体积提示，不阻断生产构建，未为消除提示改框架。

验证边界：OpenAPI 只读导入，不触发 lifespan/SQL；浏览器测试只在 tests 进程中提供符合契约的 HTTP 替身，生产源码没有测试数据或成功兜底。没有真实数据库、模型、队列、向量库的端到端业务验收，不声称真实联调成功。

## 8. 后端缺口、启动与交付状态

仍需后端支持的能力：

- 众包公共池、匿名浏览策略、服务端公共摘要、分页/筛选。当前不能实现跨用户自动发现。
- 众包“我的发布/我的接取”全量历史及当前账号参与/提交明细。当前仅本机已知编号回执，不是跨设备全量历史。
- 原始众包答案和 Agent 执行记录的受权查询、取消任务、创建时间字段、发布未知结果的幂等恢复。
- HCP 引用来源明细、服务端会话列表与流式接口。仅已有检索回执不能编造来源。
- 个人资料修改/实时积分 API，以及真正的 Agent 管理服务端角色授权。前端不能自己补出管理员身份。

这轮用户无需选 Agent 的系统问答可由现有 HCP 正式策略支持；若要求沿用 legacy 单模型 API，则它仍缺默认执行者策略，不假称已经补齐。

当前前端地址：<http://127.0.0.1:5175/>。生产请求只去实际配置的后端；后端未运行时显示真实连接错误。

```powershell
Set-Location 'C:\Code\culture-project\culture-front\Cultural heritage'
npm.cmd run dev -- --host 127.0.0.1 --port 5175
```

已在运行时无需重复启动。默认代理后端 8000；本地可配置 BACKEND_URL，不修改后端。

复现隔离浏览器验收（前端保持运行）：

```powershell
$env:PLAYWRIGHT_NODE_MODULES='C:\Users\17708\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
$env:PLAYWRIGHT_CHANNEL='chrome'
$env:FRONTEND_URL='http://127.0.0.1:5175'
npm.cmd run test:browser
```

Playwright 为本机已有运行时，其他机器可换自己的模块路径；不是本次新增生产依赖。截图与 browser-report.json 在 tests/artifacts，已忽略。

真实人工验收还需已有可运行后端环境：先直接问答，验证无需选模型、模式参数、服务缺模型时的真实错误；再用 A 发布、B 凭链接接取提交，观察真实聚合和入库；公共池步骤应停在明确缺口处，不能勾选已完成。没有要求重建表或执行 SQL。
