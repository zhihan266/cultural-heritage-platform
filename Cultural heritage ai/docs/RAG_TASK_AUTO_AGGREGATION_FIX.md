# RAG Task 自动聚合故障与恢复

2026-10-23。结论基于当前源码和开发库 `aijiu_dev` 的只读查询。本轮不更新真实任务、不执行 DDL、不调用真实模型、不写 Milvus；恢复能力通过隔离数据库验证。

## 1. 精确故障分类

目标任务 `rag-task-91649bfcbb87405086150ba678320464` 的人数、状态和 Job 登记均正确。实际停在“**聚合 Job 已创建，但当前配置没有启动消费者**”；不是提交计数错误、租约竞争、模型调用后漏记 Execution，或成功结果保存失败。

还存在第二个阻塞：任务 metadata 和 aggregate Job 的聚合 AI 绑定为空。旧代码只读该冻结 ID，即使开启消费者也会记录 `rag_aggregator_not_configured`。用户随后明确授权未配置时读取 type=judge 的 Agent，并要求复用公共查询；本轮按此接入。

### 只读数据库快照

| 项目 | 核查结果 |
| --- | --- |
| 当前环境文件 | `.env.development`，由 get_env_file() 优先选择；修改 `.env` 不会覆盖此文件，进程环境变量优先 |
| 消费者开关 | RAG_TASK_CONSUMER_ENABLED=false |
| 显式聚合配置 | RAG_TASK_AGGREGATOR_AI_ID 未配置 |
| 任务 | 上述 task_id，mode=crowd，status=aggregating，required_worker_count=3，发布者 account-8 |
| 有效参与者 | account-10、account-9、user-1，三者均 submitted，分别对应自己的 worker Answer/participant_id |
| 有效 Answer | 三条、source=worker、responder 与 Participant.worker_id 一致；未将 AI 回答计入 |
| aggregate Job | job-68b9c01d9fe3472892f92daa12bf98ca，pending，attempts=0，generation=0，retry_limit=3 |
| 可执行时间 | next_run_at=2026-10-23 04:25:38；检查时数据库 UTC=04:37:47，已经到期，不是时区延后 |
| 租约与调用 | lease_owner / lease_expires_at / execution_id / last_error 均空，external_started=0，prepared_text 空 |
| 冻结输入 | answers 数量=3，aggregator_ai_id 为 JSON null |
| 聚合结果 / 入库记录 | 均无 |
| 此任务的 Execution | 无；全表另有旧普通问答 task-0ca737ad 的一条失败 Execution，不能误认为本任务已经执行 |

没有获取运行中服务的历史控制台日志；当前日志配置只输出控制台，没有可用于此任务的应用日志文件。因此不伪造“日志显示消费者启动/模型失败”。当前配置使 `app/main.py::lifespan` 根本不创建消费者，结合已经到期但从未领取的 Job，足以定位当前阻塞；不声称读取了所有其他实例的运行环境。

## 2. 真实调用链与事务

```text
POST /rag-tasks/{id}/answers
  → crowd_router.submit：身份只取登录账号
  → CrowdTaskFacade.submit → CrowdService.submit
  → crowd_repository.transaction：先锁父任务，再锁 Participant
  → 保存 Answer，Participant=submitted，flush
  → worker_answers：关联参与记录、source=worker、作者一致、已提交
  → 第三份达到 required_worker_count
  → 同事务 Task=aggregating + 唯一 aggregate Job(pending，冻结三份输入)
  → commit，HTTP 返回；不等待模型

FastAPI lifespan（开关 true）
  → CrowdTaskFacade.consumer → CrowdConsumer.start / _run
  → CrowdProcessor.process_one → ProcessingRepository.claim
  → 扫描 pending/retry 到期、running 租约到期 Job
  → 短事务重锁 Task→Job，复核状态/时间/owner/generation/重试上限
  → 已绑定 AI：创建真实 RAG Context/Execution，running
  → 空绑定 AI：先授予 Job 租约，不伪造 Execution
  → RAG Task select_aggregation_agent_id
  → AgentFacade.ranked_agents → repository.list_ai_rows_by_score → 既有 Mapper
  → 选择 Judge 配置，不调用 HCP Selector/JudgeExecutor
  → ProcessingRepository.bind_aggregation_agent：同租约围栏下原子冻结 AI、创建真实 Execution
  → load_aggregation_agent / AgentFacade.get_agent
  → mark_external_started（短事务）
  → AgentFacade.run_agent_with_usage → RegisteredAgentRunner.run_with_usage
  → 原 Agent Loop / Provider，空工具目录，max_steps=1
  → RAG PersistentAgentObserver 记录请求事件
  → prepare_aggregation：持久化 prepared_text（P）
  → commit_aggregation：Result + Ingestion + 唯一 ingest Job + Execution completed（T）
  → Task=ingesting
  → 消费 ingest Job，复用 RagFacade.prepare_text_ingestion / index_prepared_text
  → 原 Parser/Cleaner/Splitter/Embedding/VectorStore.upsert
  → 保存 document_id / chunk_count，Task=completed
```

Execution 在模型调用**之前**真实创建并提交，Observer 的 llm_request 在网络发送前记录。原来“配置有效的聚合调用没有 Execution”的路径并不存在，本轮没有伪造一条历史执行或修复不存在的漏记。新增的是空绑定 Job 在选定真实 AI 后的原子绑定路径。

## 3. Judge 查询复用和隔离

- 使用现有 `AgentFacade.ranked_agents(session_factory=...)`。唯一 Judge 专用选择器位于 HCP 内部，不跨业务模块导入它；排名查询及配置 Mapper 完全复用 Agent 公共入口，没有新增 SQL 或 Agent Repository。
- RAG Task 自己从返回领域对象中筛选 `AiType.JUDGE`、enabled、有非空 API URL、available 或尚未健康检查的配置，按原排名顺序选择。与现有健康语义一致：未检测不是已知失败。
- 沿用既有 Mapper 的历史 aggregator→judge 兼容。未找到合格 Judge 则记录明确配置失败，不使用任意 Worker。
- 对当前开发库通过上述真实公共入口只读选出 `ai-963456c4`；未调用该模型。
- `RAG_TASK_AGGREGATOR_AI_ID` 仍可作为显式覆盖；已冻结的有效 Job 绑定不会随配置或排名改变。空绑定才在首次实际执行前选择，失败重试沿用已冻结 ID。
- 模型的 type=judge 只用于选择配置；执行的是 RAG 知识整理，数据只进 rag_task_*。没有调用 HCP Judge、Debate、Finalization，也没有改公共 Runner 或 HCP 代码。

## 4. 已有任务的安全恢复

### 当前 pending Job：通常只需开启消费者并重启

在实际读取的 `.env.development` 设置：

```dotenv
RAG_TASK_CONSUMER_ENABLED=true
# 留空即可按公共 Agent 排名选择可用 Judge；不需要再指定通用 agent_id。
RAG_TASK_AGGREGATOR_AI_ID=
RAG_TASK_POLL_SECONDS=2
RAG_TASK_LEASE_SECONDS=120
RAG_TASK_MAX_ATTEMPTS=3
RAG_TASK_RETRY_SECONDS=10
```

然后正常重启 FastAPI：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

不需要独立后台进程；每个启用实例由 lifespan 管理一个消费者，reload 的工作进程启动/关闭也走同一生命周期。若使用多实例，仍通过已有父锁、唯一 Job、租约、generation 和心跳竞争领取。

现有 aggregate Job 已到期，消费者会读取它、绑定 Judge、创建 Execution 并继续。**不要重新发布、不要重接取、不要重提三份回答，也不要 INSERT 一个新 Job。** 本轮没有修改实际 `.env.development` 或重启当前服务，以遵守只读真实数据库、不执行真实模型和入库的要求；修改开关后的重启会开始处理真实待办任务。

### 可选：发布者显式补绑定

现有 `POST /rag-tasks/{id}/retry` 新增了受限 pending 恢复：只允许发布者，对从未领取的聚合 Job（attempts/generation=0、无 Execution、无外部调用/准备结果/租约），重新核对有效回答与原冻结输入后补空绑定。复用原 Job ID、原 answers、原次数和可执行时间，不调用模型，不更换已有有效 AI。

```http
POST /rag-tasks/rag-task-91649bfcbb87405086150ba678320464/retry
Authorization: Bearer <任务发布者登录 Token>
Content-Type: application/json

{}
```

无需此请求也能在开启消费者后自动处理当前空绑定 pending Job。调用后还必须有消费者；该接口本身不承担耗时聚合。此受限 pending 恢复目前可直接通过 API/Swagger 使用，原查询 DTO 的 retry.allowed 仍主要表示失败阶段重试，未为此修改前端页面。

如果旧程序已经消费并报配置失败：修复配置/启用 Judge 后，发布者调用同一 retry。已经 running 的 Job 不允许这种补绑定；已开始但结果未知的 uncertain 仍需原有 `acknowledge_unknown=true`，不能静默重复模型。已完成任务继续拒绝重开，入库失败只恢复 ingest，不再聚合。

### 幂等与崩溃边界

- 没有新增第二套调度：持久化 Job 是权威来源，HTTP 不同步调用模型，没有 Job 时只查数据库。
- 新绑定事务使用同一 Task→Job 锁顺序和 owner/generation/到期围栏，同时写真实 Context/Execution；失去租约不能补绑定。
- 补绑定提交后、发送前崩溃：按已有“未开始外部调用”恢复策略关闭旧运行 Execution、创建新尝试，保留 Agent ID。
- 调用开始后无可靠 P：保留现有 uncertain/人工确认策略，不宣称远端恰好一次。
- P 已持久化后：只重试原子发布 T，不重复模型；入库保留原计划、document/chunk 主键重放，不重算聚合。

## 5. 日志与排查 SQL

新增启动 disabled 提示、consumer started/stopped、Job claimed/committed、Agent starting（含真实 execution_id）、配置错误码。日志不包含三份回答正文、Prompt、密钥或隐藏推理。

启动应看到 `RAG Task consumer started`。调用前会出现 `Crowd aggregation Agent starting`；若一直没有 claimed，则先核对开关和队列，不调整 Prompt、检索策略或 Milvus。

以下只读 SQL 可在 HeidiSQL 核对，不需要执行任何修复 DDL/UPDATE：

```sql
SELECT id,mode,status,required_worker_count,
 JSON_EXTRACT(metadata_json,'$.crowd.aggregator_ai_id') AS ai_id
FROM rag_tasks WHERE id='rag-task-91649bfcbb87405086150ba678320464';
SELECT COUNT(DISTINCT p.worker_id) AS valid_worker_count
FROM rag_task_participants p JOIN rag_task_answers a ON a.participant_id=p.id
WHERE p.task_id='rag-task-91649bfcbb87405086150ba678320464'
 AND a.task_id=p.task_id AND a.responder_id=p.worker_id
 AND p.status='submitted' AND a.source='worker';
SELECT UTC_TIMESTAMP(),id,phase,status,attempts,next_run_at,lease_owner,
 lease_expires_at,generation,external_started,execution_id,last_error,
 JSON_EXTRACT(payload_json,'$.aggregator_ai_id') AS ai_id
FROM rag_task_processing_jobs
WHERE task_id='rag-task-91649bfcbb87405086150ba678320464';
SELECT id,ai_id,status,llm_request_count,error_message FROM rag_task_agent_executions
WHERE task_id='rag-task-91649bfcbb87405086150ba678320464';
SELECT id,aggregator_ai_id,execution_id,answer_count,version FROM rag_task_results
WHERE task_id='rag-task-91649bfcbb87405086150ba678320464';
SELECT status,document_id,chunk_count,attempt_count,last_error
FROM rag_task_knowledge_ingestions
WHERE task_id='rag-task-91649bfcbb87405086150ba678320464';
```

预期恢复后的顺序是：原 aggregate Job running、真实 Execution running → 单条 Result/Execution completed/ingest Job pending → 入库回执 succeeded/Task completed。正式知识保存在 Result，原三份 Answer 保留，不写 HCP answers 或 rag_tasks.final_result。

## 6. 修改文件及验证

| 文件 | 目的 |
| --- | --- |
| app/main.py | 开关关闭时明确警告，启用时说明空绑定 Judge 选择策略 |
| app/modules/rag_task/application/aggregation_agent.py | 复用公共 Agent 查询选择 Judge，仅本业务筛选 |
| app/modules/rag_task/application/crowd_processing.py | 空绑定 Job 选择/冻结 Agent 后走原 Runner；补充安全阶段日志 |
| app/modules/rag_task/infrastructure/processing_repository.py | 同租约围栏下原子绑定与创建真实 Execution；共用原 Execution 创建能力 |
| app/modules/rag_task/application/crowd_service.py | 受限 pending 原 Job 恢复，保留原失败重试语义 |
| app/modules/rag_task/application/facade.py | 恢复时复用同一 Judge 选择；不要求客户端传 AI ID |
| tests/rag_task/test_crowd_auto_recovery.py（新增） | 三人真实 API/Runner/Provider/Observer、lifespan 自动消费、重启恢复、Judge 查询和边界测试 |
| .env.example | 说明消费者开关与可选 Agent 覆盖 |
| docs/MODULE_PUBLIC_API.md | 更新选择归属与公开恢复契约 |
| docs/RAG_TASK_CROWD_WORKFLOW.md | 同步实际调用链与恢复约束 |
| docs/RAG_TASK_AUTO_AGGREGATION_FIX.md（新增） | 本证据、结果与操作手册 |

实际验证：新增文件 22 项测试；最终相关集 **104 passed, 1 deselected，87.39 秒**。唯一未在此定向集运行的是已有 incremental SQL 文本契约测试，完整运行仍会执行它，未删除或放宽断言。

完整运行结果为 **1180 passed, 3 failed，897.45 秒**。这次完整运行收集了 1183 项；随后增补的租约失效拒绝绑定、绑定/Execution 一起回滚两项，已包含在最终 104 项通过中，没有冒充在该次完整运行中执行。完整运行的 HCP、公共执行、旧众包聚合/入库/恢复均通过。

3 项失败为：

- `tests/rag_task/test_crowd_schema.py::test_new_sql_matches_all_four_orm_tables_and_existing_answer_alter`
- `tests/rag_task/test_crowd_schema.py::test_published_stage_one_migration_is_unchanged`
- `tests/test_execution_architecture.py::test_incremental_sql_matches_all_new_orm_columns_indexes_constraints_and_defaults`

本次实际错误均为 FileNotFoundError：当前工作区缺少 `sql/20261022_add_rag_task_tables.sql`、`sql/20261022_add_rag_task_crowd_workflow.sql`。这两个文件在本轮开始快照中已不存在；本轮没有删除、重建 SQL，也没有更新迁移 hash 或放宽测试。与以前的报告相比，不能把本次缺文件错误写成仅仅 DDL 格式差异；完整测试不是全绿。

```powershell
.\.venv\Scripts\python.exe -B -u -m pytest -q -p no:cacheprovider --basetemp=.pytest_tmp/rag-auto-20261023/final-targeted tests/rag_task/test_crowd_auto_recovery.py tests/rag_task/test_crowd_workflow.py tests/test_application_startup.py tests/test_execution_architecture.py -k 'not incremental_sql'
.\.venv\Scripts\python.exe -B -u -m pytest -q -p no:cacheprovider --basetemp=.pytest_tmp/rag-auto-20261023/full
.\.venv\Scripts\python.exe -B -m ruff check app tests scripts evaluation
.\.venv\Scripts\python.exe -B -m mypy app/modules/rag_task app/modules/agent app/modules/hcp_mad app/api app/main.py
```

全目录 Ruff 通过；相关 mypy **141 个源文件通过**；app/tests/scripts/evaluation 的 **338 个 Python 文件内存语法编译通过**，没有写入 pyc。源码 AST 比较确认原租约验证、心跳、外部开始标记、准备 P、原子发布 T、入库读/计划/提交、失败路径共 10 个方法保持不变。

集成测试保留真实 API、lifespan、Facade、RegisteredAgentRunner、Agent Loop、Provider 解析、Observer 和 Repository，仅模拟模型网络与向量基础设施；断言 HTTP 不执行模型、模型发送前真实 Execution 已 running/记录请求、三份回答进入实际 payload、结果关联该 Execution，随后真实 RAG Facade/预处理与离线向量存储完成入库。HCP SQL 守卫检查此链不访问五张 HCP 表。没有伪造 Execution/Result。

真实数据库前后核对：本任务始终三份有效回答、同一个 pending Job、attempts/generation=0、目标 Execution/Result/Ingestion 仍为 0；本轮没有代消费真实任务。HCP 全表计数期间因并行使用发生变化，不能拿全库总计数假称环境静止；本轮代码与 SQL 操作均未写 HCP，HCP 模块及公共 Agent 源码与快照一致。

没有改模型 Prompt、RAG Pipeline/策略、Milvus 配置、HCP 代码/五表 ORM 或公共 Agent Runner。真实模型/向量入库、真实 MySQL 并发领取没有执行，不能把离线 completed 当作当前真实任务已经 completed。

本轮开始快照后检测到 `sql/createsql.sql` 等外部工作区变化；这些不属于本任务写入，原样保留，没有重置或覆盖。真实数据库只读快照与代码测试数据库严格区分；隔离 SQLite 并发测试不等于真实 MySQL 多实例并发验收。
