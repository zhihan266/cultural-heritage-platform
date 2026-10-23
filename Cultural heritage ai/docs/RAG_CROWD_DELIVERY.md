# RAG 众包接口与前端交付报告（2026-10-23）

## 1. 交付范围与可见性

正式前端在 `../culture-front/Cultural heritage`。后端先完成155项定向验证，再按真实OpenAPI更新现有Vue工程。没有修改HCP-MAD状态机、辩论、执行持久化、Agent Loop、LLM Provider、Embedding或Milvus检索算法。

公共任务须同时满足 `rag_tasks.mode=crowd`、`metadata_json.crowd.visibility=public`。新发布接口设置此标记，前端明确告知公开范围；历史未标记数据不会自动进入公共池，仅发布者和已有参与者能读取。无权访问的私人ID和不存在ID均返回404。GET只读，不触发队列、模型或状态更新。

Task没有创建时间列。新任务在已有JSON的 `crowd.published_at` 保存数据库UTC时间，对外DTO称 `created_at`；历史缺失为null，不虚构时间。公共/发布列表按该时间倒序、ID倒序，接取列表按participants.accepted_at倒序、ID倒序。

账号无user/worker角色。发布、参与身份使用可信JWT sub，对应accounts.id；participants.worker_id是同一个账号ID。同一账号可发布和接取，发布不会自动成为参与者。不存在可通过请求体或user_id查询参数越权的个人列表。

## 2. 后端接口清单

全部接口需要有效Bearer JWT，并核对签名、黑名单、当前Redis会话和账号存在性。公共表示登录用户可见，不是匿名开放。分页默认page=1、page_size=20，上限100，返回 `{items,total,page,page_size}`。

| 变更 | Method / URL | 请求 | 响应 | 权限/页面 |
| --- | --- | --- | --- | --- |
| 新增 | GET /rag-tasks | page≥1、page_size=1..100、status、available_only=false | CrowdPage | 所有登录账号；公共大厅，不过滤发布者 |
| 新增 | GET /rag-tasks/mine/published | page、page_size、status | CrowdPage | JWT本人发布的crowd；我发布的任务 |
| 新增 | GET /rag-tasks/mine/accepted | page、page_size、任务status、participation_status=accepted/submitted | CrowdPage | JWT本人参与；我接取的任务 |
| 修改响应 | POST /rag-tasks | question、worker_count=1..1000、answer_format（可省略）、title（可选） | 201 CrowdDetail | JWT本人为发布者；发布页 |
| 修改响应 | POST /rag-tasks/{id}/accept | 无身份参数 | CrowdDetail | 可见且征集中、未接取、有容量 |
| 修改响应 | POST /rag-tasks/{id}/answers | content | CrowdDetail、submission_reused | 已接取的本人；相同正文幂等，不同正文409 |
| 修改可见性/响应 | GET /rag-tasks/{id} | task ID | CrowdDetail | 明确公开，或本人发布/参与；详情 |
| 修改响应 | GET /rag-tasks/{id}/status | task ID | CrowdDetail | 同详情；轮询 |
| 修改响应 | GET /rag-tasks/{id}/result | task ID | task_id/status/result/ingestion | 同详情；正式结果 |
| 修改响应 | POST /rag-tasks/{id}/retry | acknowledge_unknown=false | CrowdDetail | 仅发布者、失败阶段；未知模型结果须明确true |

`/mine/*`注册在动态ID路由前。任务status接受collecting、aggregating、ingesting、completed、failed；非法分页/状态422。个人列表不会使用客户端user_id。

**CrowdSummary**：task_id、title、question、answer_format、status、created_at、is_publisher、required_worker_count、accepted_worker_count、valid_worker_count、can_accept、can_submit、threshold_reached、aggregation_status、ingestion_status、result_available、my_participation。

my_participation为null或 `{status,accepted_at,submitted_at}`。CrowdDetail另含my_answer（仅本人原文）、jobs（仅phase/status）、retry（allowed/requires_acknowledgement/phase）、result、ingestion和可选submission_reused。

result为null或 `{content,answer_count,version,created_at}`；ingestion为null或 `{status,document_id,source_id,chunk_count}`。正式结果存在与入库成功是独立事实。

不返回publisher_id、工人身份列表、其他工人原文、metadata_json、Agent执行/工具历史、错误堆栈、租约与调度信息、冻结输入或准备文本。列表不加载完整个人答案和聚合正文；SQL执行COUNT及一次联合分页查询，无逐条N+1。有效答案计数沿用参与者/回答一致性约束。

## 3. 旧接口清理

已删除下列16个旧API，表中路径均以 `/rag-tasks/legacy` 为前缀。没有301/302、隐藏代理或旧API包装。

| Method | 删除的路径 | 当前处理 |
| --- | --- | --- |
| POST | /users/tasks | 系统问答POST /hcp-mad/tasks；众包POST /rag-tasks |
| POST | /users/tasks/rag | HCP的use_rag用于公共知识增强；旧附件契约退役，见限制 |
| GET | /users/{user_id}/messages | 众包个人发布GET /rag-tasks/mine/published |
| GET | /users/tasks/{id} | 众包GET /rag-tasks/{id}；HCP仍GET /users/tasks/{id} |
| POST | /users/tasks/{id}/final-review | 旧人工修改/评分调试入口退役，无当前前端调用 |
| GET | /workers/hall | GET /rag-tasks |
| GET | /workers/scoreboard | 旧排行榜入口退役，无当前前端页面 |
| POST | /workers/tasks/{id}/accept | POST /rag-tasks/{id}/accept |
| POST | /workers/tasks/{id}/answers | POST /rag-tasks/{id}/answers |
| GET | /workers/tasks/{id} | GET /rag-tasks/{id} |
| GET | /ais/scoreboard | 旧排行榜入口退役 |
| POST | /ais/{ai}/tasks/{id}/invoke | 旧QA调试入口退役；独立HCP调用契约保留 |
| POST | /ais/tasks/{id}/answers | 旧手工写入AI答案入口退役 |
| POST | /tasks/{id}/aggregate | 众包人数达标自动排队 |
| POST | /tasks/{id}/score | 旧QA评分调试入口退役 |
| POST | /tasks/{id}/finish | 众包持久消费者聚合/入库 |

删除rag_task/api下user_router.py、worker_router.py、ai_router.py；router.py只组合crowd_router。当前前端本来已采用正式HCP入口，无legacy调用需要保留；本次删除失效注释、本地列表依赖和旧测试预期。app生产代码和前端src无 `/rag-tasks/legacy` 引用；测试保留旧路径不存在的负向断言，文档保留删除映射和历史记录。

保留 `/hcp-mad/tasks`、`/users/tasks/{id}`、`/ais/{ai_id}/tasks/{task_id}/invoke`：仍承担HCP独立业务或拒绝非法执行的契约。账号、公共Agent/Tool、RAG检索/管理入库继续保留。TaskFacade、Observer、Context和内部应用服务仍有公开契约或测试引用，本次没有扩大删除到执行基础设施及历史ORM。

## 4. 文件变更

### 后端代码

- 新增 infrastructure/crowd_queries.py：SQL可见性、计数、资格、个人过滤、排序、分页及安全投影。
- 新增 api/crowd_schemas.py：摘要/详情/结果/分页的OpenAPI响应模型。
- 修改 api/crowd_router.py：三个GET集合入口、参数验证及全部响应模型。
- 修改 application/crowd_service.py：新任务显式公开/时间、当前调用者安全快照、接取私人任务的可见性检查；保留原锁、短事务、幂等、唯一约束和恢复机制。
- 修改 application/facade.py：复用CrowdService新增list_tasks。
- 修改 api/router.py；删除上述三个旧Router。
- 新增 scripts/check_crowd_environment.py：仅允许本机回环的只读检查，无DDL、推理或消费。
- 新增 tests/rag_task/test_crowd_queries.py：14项真实JWT/SQL/HTTP查询与权限、分页、安全字段测试。
- 修改 tests/account/test_account_permissions.py、test_account_identity_api.py、tests/rag_task/test_crowd_workflow.py、test_task_orchestration.py、tests/test_task_route_composition.py：迁移有效权限/调度测试，保留内部错误持久化断言并验证HTTP不泄漏，断言旧路由不存在。

以上代码路径除scripts/tests外，均位于app/modules/rag_task。

### 前端全部变更

| 文件 | 内容 |
| --- | --- |
| src/api/ragTasks.ts | 新DTO、统一列表封装、筛选、可取消状态查询 |
| src/views/RagTasks.vue | 真正公共/个人列表、卡片、数据库分页筛选、加载/空/失败/重试；删除编号主入口和本地过滤 |
| src/views/RagTaskDetail.vue | 本人原答案/参与跨设备恢复、资格按钮、提交前核对、未知结果恢复、正式结果与入库分离 |
| src/utils/crowdPolling.ts（新增） | 单轮请求、取消和代次校验、隐藏暂停、3分钟预算、最多3次连续读取失败、终态停止 |
| src/utils/crowdTasks.ts | 本地回执仅辅助待确认正文，不作为列表/权限/提交状态；移除recordSnapshot |
| src/views/UserPublishTask.vue | 公开范围提示、真实ID跳转；未知发布可先核对个人列表 |
| src/views/Dashboard.vue | 发布任务总数改读服务端 |
| src/api/qa.ts | 清理过时legacy注释；HCP请求契约保持 |
| tests/integration.mjs | 列表、轮询与真实OpenAPI契约；保留既有HCP/鉴权/Markdown测试 |
| tests/browser.mjs | 迁移新DTO及列表；保留25个完整浏览器场景 |
| tests/crowd_server.py（新增） | 真实路由/JWT的临时SQLite HTTP测试宿主，不运行消费者 |
| tests/crowd-http-browser.mjs（新增） | 不替换HTTP响应的双用户、分页和筛选验收 |
| tests/crowd-http.mjs（新增） | 每次启动独立端口/临时数据库并停止自身测试进程 |
| package.json | 新增test:crowd-http |
| README.md、FRONTEND_LOGIC_AUDIT.md、FRONTEND_ADAPTATION.md | 当前契约与历史记录边界 |

未新增/删除前端业务页面，保留布局及HCP页面。后端README、MODULE_PUBLIC_API、ACCOUNT_IDENTITY和众包实施记录更新到当前入口；根目录README更新启动/演示指引。

## 5. 数据库影响与部署检查

复用现有九表：rag_tasks、rag_task_participants、rag_task_answers、rag_task_results、rag_task_processing_jobs、rag_task_knowledge_ingestions、rag_task_agent_executions、rag_task_contexts、rag_task_context_items。

没有新增SQL、表、列或索引，未执行DDL、清空/删除或历史数据更新。本机配置实际使用 `127.0.0.1:3306/aijiu_dev`。只读检查确认九表和所需列齐全，均为InnoDB；新公共分页SELECT在真实MySQL执行成功，显式公共任务总数0。只能确认所需结构存在，不能证明SQL文件的历史执行记录。

Embedding模型列表返回200且包含qwen3-embedding。Milvus集合存在、embedding维度1024。聚合Agent未配置，消费者关闭；本次没有代为启用或执行真实模型。数据库连接、API密钥和Token未写入报告。

## 6. 实际测试与命令

### 后端（项目后端目录）

```powershell
.\.venv\Scripts\python.exe -u -m pytest tests/rag_task/test_crowd_queries.py tests/rag_task/test_crowd_workflow.py tests/account/test_account_identity_api.py tests/account/test_account_permissions.py tests/test_task_route_composition.py tests/rag_task/test_task_orchestration.py -q --tb=short -p no:cacheprovider --basetemp=.pytest_tmp/crowd-api-verified-20261023
.\.venv\Scripts\python.exe -u -m pytest tests -q --tb=short -p no:cacheprovider --basetemp=.pytest_tmp/crowd-full-20261023
.\.venv\Scripts\python.exe -u -m pytest tests/rag_task/test_crowd_queries.py -q --tb=short -p no:cacheprovider --basetemp=.pytest_tmp/crowd-query-final-20261023
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy app/modules/agent app/modules/hcp_mad app/modules/rag_task app/api
.\.venv\Scripts\python.exe -u -m scripts.check_crowd_environment
```

- 定向155 passed；列表正文投影优化后的新增查询回归14 passed。
- 全量1160 passed、3 failed，504.83秒。
- 失败是 test_new_sql_matches_all_four_orm_tables_and_existing_answer_alter、test_published_stage_one_migration_is_unchanged、test_incremental_sql_matches_all_new_orm_columns_indexes_constraints_and_defaults：均涉及本次未改动的SQL/ORM建表文本或已发布摘要不一致。保留原测试，未通过跳过/放宽/更新哈希来掩盖差异。
- Ruff全项目通过；相关mypy 140个源文件通过；app/tests/scripts共320个Python文件AST语法通过。
- Windows受限沙箱的socketpair会阻塞TestClient初始化，允许本机连接后完成测试，未为此改写业务逻辑。

### 前端（现有Vue目录）

```powershell
npm test
npm run type-check
npx eslint .
npx oxlint .
npm run build
$env:PLAYWRIGHT_NODE_MODULES='C:\Users\17708\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
$env:PLAYWRIGHT_CHANNEL='msedge'
# test:browser使用独立终端的Vite；本次运行于5187
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5187 --strictPort
$env:FRONTEND_URL='http://127.0.0.1:5187'
npm run test:browser
# 以下自动启动/停止自己的临时前后端，不依赖5187
npm run test:crowd-http
```

- 前端31项测试、类型、ESLint、Oxlint、生产构建通过。构建有现有依赖注释与大分包提示。
- HTTP fixture浏览器25个场景通过，无pageerror，不称为真实后端验收。
- 真实浏览器→Vite→FastAPI→JWT→临时SQLite流程通过：A发布、B公共发现、个人列表隔离、B接取提交、刷新恢复、A查看门槛与真实pending Job；14条实际API创建记录12/2分页、状态/可接取筛选；清空本地众包回执后恢复参与状态。
- 前端tests/artifacts/browser-report.json、crowd-http-report.json保存验收边界与步骤。

验证层次：单元/隔离集成通过；真实MySQL结构及只读SELECT通过；没有真实MySQL写入/行锁并发验收；没有真实模型聚合或Milvus入库；真实浏览器HTTP覆盖到待聚合队列。已有消费者回归使用模型、Embedding网络和存储替身，不冒充真实服务成功。

## 7. 双用户演示手册

### 启动

```powershell
cd 'C:\Code\culture-project\Cultural heritage ai'
.\.venv\Scripts\python.exe -u -m scripts.check_crowd_environment
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另一个终端：

```powershell
cd 'C:\Code\culture-project\culture-front\Cultural heritage'
npm run dev
```

打开http://127.0.0.1:5173。前端VITE_API_URL=/api，BACKEND_URL=http://127.0.0.1:8000；开发代理移除/api。CORS已包含本机5173；跨域直连需核对BACKEND_CORS_ORIGINS。

本机九表已存在，不要重复建表。全新数据库由管理者先审查并按顺序应用sql/20261022_add_rag_task_tables.sql、20261022_add_rag_task_crowd_workflow.sql，核对账户外键和排序规则；本次未代执行。

### 发布、发现与提交

1. 使用两个独立浏览器配置文件/隐私窗口登录A、B，避免同一localStorage的普通标签页共享身份。
2. A发布文化遗产任务，演示可设置工人数1。发布后跳转真实task_id，“我发布的任务”可再次找到。
3. B在公共大厅发现A的任务；B的发布列表不含A任务。B查看问题和要求后接取。
4. B在“我接取的任务”找到任务，提交事实、来源与不确定点；刷新后服务端恢复本人原文，首次提交按钮消失。
5. A查看真实接取/提交数量。最后一份有效答案原子创建唯一aggregate Job，转为aggregating，HTTP不等待模型。

### 明确启用真实聚合与入库

当前RAG_TASK_AGGREGATOR_AI_ID未配置，RAG_TASK_CONSUMER_ENABLED=false。为避免擅自执行可能计费的调用，本任务未开启消费者。

由操作者在后端配置中填写：

```dotenv
RAG_TASK_AGGREGATOR_AI_ID=<已有且启用、有有效API地址/凭据的聚合Agent ID>
RAG_TASK_CONSUMER_ENABLED=true
RAG_TASK_POLL_SECONDS=2
RAG_TASK_LEASE_SECONDS=120
RAG_TASK_MAX_ATTEMPTS=3
RAG_TASK_RETRY_SECONDS=10
```

应在发布演示任务前配置并重启，发布时会绑定该专属Agent。此配置独立于HCP Judge选择策略。用户表单无需输入任何Agent ID。

确认EMBEDDING_BASE_URL、EMBEDDING_MODEL、EMBEDDING_DIMENSION=1024、RAG_MILVUS_URI、RAG_MILVUS_COLLECTION_NAME与实际服务一致。本机只读检查显示Embedding模型和1024维集合可用。

消费者先生成并保存rag_task_results，再创建入库任务，复用RAG预处理/Embedding/Milvus链路，最终写真实回执。前端5秒串行查询，单次最多3分钟；完成后停止，可手动刷新继续。只有ingestion.status=succeeded显示入库完成。

入库失败保留正式知识，由发布者重试失败阶段；uncertain需明确确认可能重复调用及计费。没有自动强制重试未知结果。配置失败任务可在修复配置、重启后由发布者重试；只有原有规则允许的未执行配置失败会重绑Agent，保留其他恢复机制。

### 独立检索新知识

从详情ingestion.document_id取得真实ID，在Swagger调用：

```http
POST /rag/retrieve
Content-Type: application/json

{"query":"本次整理的问题关键词","top_k":5,"document_id":"真实入库document_id"}
```

核对hits的document_id、正文、source，不用前端完成图标替代检索验证。

## 8. 当前限制

1. 聚合Agent未配置、消费者关闭，真实模型聚合及Milvus写入闭环未执行；按上述配置可继续验证，无需为测试改业务代码。
2. SQLite不能证明MySQL并发锁正确；真实MySQL本轮只读。三项既有迁移契约失败保留，未修改历史SQL或哈希。
3. 历史未标记crowd不公开；当前真实公共总数0是可见性规则结果。本人历史众包从个人入口访问。
4. legacy普通QA临时附件HTTP接口已退役。HCP正式Schema没有附件，前端本来无此调用；use_rag不等同附件问答。未来若需要附件，须新增有归属与生命周期的正式HCP上传/关联契约；RAG公共附件服务保留。
5. HCP仍无完整个人历史GET列表，当前问答ID索引边界保留，不用此次众包个人列表冒充HCP历史。
6. 既有公共Agent/管理入库接口的权限范围未扩大重构，本次众包接口均具备JWT和资源可见性规则。
