# Package by Feature 重构报告

> 历史记录：本文描述当时状态，可能包含已删除文件和已修复缺陷。2026-10-08 的当前架构、测试及发布风险以 [GitHub 发布前报告](GITHUB_RELEASE_REPORT.md) 和 [根 README](../README.md) 为准。

本次重构以开始执行时的工作区为基准，包括已经修改、暂存和尚未跟踪的代码。没有回退既有修改，没有修改暂存区，也没有执行 git commit。

## 原始结构与依赖

原有 Account、Agent、Task 和 Answer 的领域模型共用一个文件，请求和响应模型也共用一个文件。ORM 表及转换函数集中在 db 中，业务代码则分散在 api、crud、services。用户路由混合了认证和任务生命周期，AI 路由混合了配置管理和任务回答。

```text
app/
  api/dependencies/auth.py
  api/endpoints/{user,worker,ai,tasks}.py
  core/{security,redis}.py
  crud/{accounts,ai_agents,tasks}.py
  db/{session,tables,mapper,memory}.py
  models/{domain,rag}.py
  schemas/{domain,embedding,rag}.py
  services/
    auth_service.py
    ai_health.py
    local_ai.py
    ai_answers.py
    aggregation.py
    orchestration/task_orchestrator.py
    tasks/{management,answers,queries,rankings,review,task_utils}.py
    rag/{ingestion,cleaning,splitting,embedding}/
    rag/preprocessing_service.py
  scripts/__init__.py
  main.py
config/{settings,logging}.py
tests/  自动化测试、手动解析演示和 HTTP 示例
```

原有调用关系：

- 认证路由与鉴权依赖 → auth_service → accounts CRUD、账号转换、密码工具、Redis、配置。
- 用户/worker 路由 → tasks 服务 → tasks CRUD → 全局 ORM 表及转换函数。
- 任务编排 → ai_answers → local_ai 与任务服务；任务编排 → aggregation。
- aggregation 同时处理聚合、评分、完成状态，依赖 Task、Answer、AI 配置及通用 AI 请求。
- AI 路由 → AI CRUD、ai_health、ai_answers、任务排行榜。
- RAG 预处理 → ParserFactory → TXT/PDF/DOCX Parser → Cleaner → Splitter。
- EmbeddingService → EmbeddingProvider → OpenAICompatibleEmbeddingProvider；RAG 不依赖账号、任务或 Agent。
- db.session 在 init_db 内导入全局 tables 注册 ORM；main 注册路由、CORS 和两个 startup 钩子。

## 最终目录树

下方省略空的 __init__.py；必要的 Python package 均保留它。未创建空的 shared、retrieval 或业务占位文件。

```text
app/
  main.py
  core/
    config.py
    logging.py
    redis.py
  db/
    session.py
  modules/
    account/
      router.py
      schemas.py
      models.py
      mapper.py
      repository.py
      service.py
      dependencies.py
      security.py
    agent/
      router.py
      schemas.py
      models.py
      mapper.py
      repository.py
      service.py
      health.py
    task/
      router.py
      user_router.py
      worker_router.py
      ai_router.py
      schemas.py
      models.py
      mapper.py
      repository.py
      service.py
      management.py
      queries.py
      answers.py
      ai_answers.py
      orchestration.py
      aggregation.py
      rankings.py
      review.py
      task_utils.py
    rag/
      models.py
      service.py
      ingestion/
        parser.py
        parser_factory.py
        text_parser.py
        pdf_parser.py
        docx_parser.py
      preprocessing/
        cleaner.py
        cleaner_config.py
        splitter.py
        splitter_config.py
      embedding/
        base.py
        embedding_service.py
        openai_compatible_provider.py
        schemas.py
scripts/
  demo_parser_factory.py
  demo_docx_parser.py
  demo_pdf_parser.py
  legacy_memory.py
tests/
  account/test_account_permissions.py
  task/test_task_orchestration.py
  rag/
    test_clean_pipeline.py
    test_document_splitter.py
    test_preprocessing_service.py
  test_application_startup.py
  test_main.http
docs/
  package-by-feature-refactor.md
data/          原样保留
migrations/    原样保留
README.md
requirements.txt
```

## 模块职责与边界

| 模块 | 职责 |
| --- | --- |
| Account | 注册、登录、登出、JWT 生成和解析、Redis 会话和黑名单、当前用户校验、密码哈希、账号模型及数据访问 |
| Agent | AI 注册和配置、业务/聚合 AI 类型、健康检查、通用 OpenAI 兼容请求、AI 模型及数据访问 |
| Task | 用户问题、任务大厅、接单、人工/AI 回答、后台并发编排、聚合与评分、最终审核、排行榜、TaskStatus、Task 和 Answer 的模型与数据访问 |
| RAG | 文档及切片模型、TXT/PDF/DOCX 解析、清理、切片、预处理编排、向量生成及响应校验 |

同一 feature 的 models.py 保留原有 Pydantic 领域模型与独立 SQLAlchemy ORM 类，不将两种模型合并为一个类。mapper.py 保留原有转换函数。所有类名、校验约束、字段、默认值和数据库表名保持原样。

Task 继续使用原有的细分服务实现，service.py 只是原公开入口的迁移，没有重写为一个大型服务。Answer 没有另建业务模块。聚合与评分继续共存于 aggregation.py，避免拆散原有事务、错误处理和状态流转。

/users 的认证接口归 Account，其任务接口归 Task；/ais 的注册、查询、健康检查归 Agent，其任务调用、答案提交和排行榜归 Task。模块归属改变，URL、HTTP 方法、标签、operationId、请求和响应格式均保持一致。

通用 AI 请求进入 agent/service.py；根据 Task 构造提示词并保存回答的逻辑进入 task/ai_answers.py。Task 可以依赖 Account 和 Agent，Account、Agent 的实现不反向依赖 Task。RAG 保持独立。

## 保留的基础设施

- core/config.py：来自原 config/settings.py，保留现有配置字段、默认值、环境文件选择和缓存行为。
- core/logging.py：来自原 config/logging.py，保留日志配置行为。
- core/redis.py：保留通用 Redis 连接、开发环境内存回退和过期处理；业务 key 及会话语义留在 Account。
- db/session.py：保留 Base、Engine、SessionLocal、get_db、init_db 及既有数据库兼容初始化。init_db 改为局部导入三个 feature 的 ORM 模型，确保注册完整。
- 原有 _ensure_ai_agent_columns 属于启动时的数据库兼容初始化，本次保留其 SQL 和调用顺序，未执行真实数据库建表或 ALTER。
- 密码工具只服务 Account，因此迁入 account/security.py；JWT 编解码仍与原认证服务一起保留在 account/service.py。
- 未创建 shared。旧 MemoryStore 没有生产调用者，原实现迁入 scripts/legacy_memory.py，保留原型功能。

## 文件迁移清单

共 67 项整文件迁移或按定义拆分。多行源文件表示同一文件的不同职责被分别迁移；不表示重复实现。

| 原文件 | 新文件 | 迁移内容 |
| --- | --- | --- |
| `app/models/domain.py` | `app/modules/account/models.py` | `Account` |
| `app/db/tables.py` | `app/modules/account/models.py` | `AccountTable` |
| `app/schemas/domain.py` | `app/modules/account/schemas.py` | `RegisterRequest`、`RegisterResponse`、`LoginRequest`、`LoginResponse`、`CurrentUser` |
| `app/db/mapper.py` | `app/modules/account/mapper.py` | `account_to_domain` |
| `app/crud/accounts.py` | `app/modules/account/repository.py` | 整文件迁移，更新 import |
| `app/services/auth_service.py` | `app/modules/account/service.py` | 整文件迁移，更新 import |
| `app/api/dependencies/auth.py` | `app/modules/account/dependencies.py` | 整文件迁移，更新 import |
| `app/core/security.py` | `app/modules/account/security.py` | 整文件迁移，更新 import |
| `app/crud/tasks.py` | `app/modules/account/repository.py` | `get_account_row`、`list_account_rows_by_score` |
| `app/api/endpoints/user.py` | `app/modules/account/router.py` | `register_api`、`login`、`logout` |
| `app/models/domain.py` | `app/modules/agent/models.py` | `AiType`、`AiAgent` |
| `app/db/tables.py` | `app/modules/agent/models.py` | `AiAgentTable` |
| `app/schemas/domain.py` | `app/modules/agent/schemas.py` | `AiRegisterRequest`、`AiRegisterResponse`、`AiHealthResponse` |
| `app/db/mapper.py` | `app/modules/agent/mapper.py` | `ai_to_domain` |
| `app/crud/ai_agents.py` | `app/modules/agent/repository.py` | 整文件迁移，更新 import |
| `app/crud/tasks.py` | `app/modules/agent/repository.py` | `get_ai_row`、`find_missing_ai_ids`、`list_ai_rows_by_score` |
| `app/services/ai_health.py` | `app/modules/agent/health.py` | 整文件迁移，更新 import |
| `app/services/local_ai.py` | `app/modules/agent/service.py` | `request_local_ai_raw`、`_post_json`、`_extract_answer` |
| `app/api/endpoints/ai.py` | `app/modules/agent/router.py` | `register_ai`、`list_ais`、`health_check` |
| `app/models/domain.py` | `app/modules/task/models.py` | `TaskStatus`、`AnswerSource`、`Answer`、`Task` |
| `app/db/tables.py` | `app/modules/task/models.py` | `TaskTable`、`AnswerTable` |
| `app/schemas/domain.py` | `app/modules/task/schemas.py` | `TaskCreateRequest`、`TaskCreateResponse`、`TaskAcceptRequest`、`TaskAnswerRequest`、`FinalReviewRequest`、`TaskSummary`、`TaskDetailResponse`、`HallResponse`、`MessageInboxResponse`、`AnswerResponse`、`ScoreboardResponse`、`TaskProcessData`、`TaskProcessResponse` |
| `app/db/mapper.py` | `app/modules/task/mapper.py` | `answer_to_domain`、`task_to_domain` |
| `app/crud/tasks.py` | `app/modules/task/repository.py` | 整文件迁移，更新 import |
| `app/services/tasks/answers.py` | `app/modules/task/answers.py` | 整文件迁移，更新 import |
| `app/services/tasks/management.py` | `app/modules/task/management.py` | 整文件迁移，更新 import |
| `app/services/tasks/queries.py` | `app/modules/task/queries.py` | 整文件迁移，更新 import |
| `app/services/tasks/rankings.py` | `app/modules/task/rankings.py` | 整文件迁移，更新 import |
| `app/services/tasks/review.py` | `app/modules/task/review.py` | 整文件迁移，更新 import |
| `app/services/tasks/task_utils.py` | `app/modules/task/task_utils.py` | 整文件迁移，更新 import |
| `app/services/tasks/__init__.py` | `app/modules/task/service.py` | 整文件迁移，更新 import |
| `app/services/aggregation.py` | `app/modules/task/aggregation.py` | 整文件迁移，更新 import |
| `app/services/ai_answers.py` | `app/modules/task/ai_answers.py` | 整文件迁移，更新 import |
| `app/services/orchestration/task_orchestrator.py` | `app/modules/task/orchestration.py` | 整文件迁移，更新 import |
| `app/services/local_ai.py` | `app/modules/task/ai_answers.py` | `request_local_ai`、`_build_task_prompts` |
| `app/api/endpoints/user.py` | `app/modules/task/user_router.py` | 整文件迁移，更新 import |
| `app/api/endpoints/worker.py` | `app/modules/task/worker_router.py` | 整文件迁移，更新 import |
| `app/api/endpoints/ai.py` | `app/modules/task/ai_router.py` | 整文件迁移，更新 import |
| `app/api/endpoints/tasks.py` | `app/modules/task/router.py` | 整文件迁移，更新 import |
| `app/models/rag.py` | `app/modules/rag/models.py` | 整文件迁移，更新 import |
| `app/services/rag/preprocessing_service.py` | `app/modules/rag/service.py` | 整文件迁移，更新 import |
| `app/services/rag/ingestion/__init__.py` | `app/modules/rag/ingestion/__init__.py` | 整文件迁移，更新 import |
| `app/services/rag/ingestion/docx_parser.py` | `app/modules/rag/ingestion/docx_parser.py` | 整文件迁移，更新 import |
| `app/services/rag/ingestion/parser.py` | `app/modules/rag/ingestion/parser.py` | 整文件迁移，更新 import |
| `app/services/rag/ingestion/parser_factory.py` | `app/modules/rag/ingestion/parser_factory.py` | 整文件迁移，更新 import |
| `app/services/rag/ingestion/pdf_parser.py` | `app/modules/rag/ingestion/pdf_parser.py` | 整文件迁移，更新 import |
| `app/services/rag/ingestion/text_parser.py` | `app/modules/rag/ingestion/text_parser.py` | 整文件迁移，更新 import |
| `app/services/rag/embedding/__init__.py` | `app/modules/rag/embedding/__init__.py` | 整文件迁移，更新 import |
| `app/services/rag/embedding/base.py` | `app/modules/rag/embedding/base.py` | 整文件迁移，更新 import |
| `app/services/rag/embedding/embedding_service.py` | `app/modules/rag/embedding/embedding_service.py` | 整文件迁移，更新 import |
| `app/services/rag/embedding/openai_compatible_provider.py` | `app/modules/rag/embedding/openai_compatible_provider.py` | 整文件迁移，更新 import |
| `app/services/rag/cleaning/document_cleaner.py` | `app/modules/rag/preprocessing/cleaner.py` | 整文件迁移，更新 import |
| `app/services/rag/cleaning/config.py` | `app/modules/rag/preprocessing/cleaner_config.py` | 整文件迁移，更新 import |
| `app/services/rag/splitting/document_splitter.py` | `app/modules/rag/preprocessing/splitter.py` | 整文件迁移，更新 import |
| `app/services/rag/splitting/config.py` | `app/modules/rag/preprocessing/splitter_config.py` | 整文件迁移，更新 import |
| `app/schemas/embedding.py` | `app/modules/rag/embedding/schemas.py` | 整文件迁移，更新 import |
| `config/settings.py` | `app/core/config.py` | 整文件迁移，更新 import |
| `config/logging.py` | `app/core/logging.py` | 整文件迁移，更新 import |
| `app/db/memory.py` | `scripts/legacy_memory.py` | 整文件迁移，更新 import |
| `tests/test_pdf_parser.py` | `scripts/demo_pdf_parser.py` | 整文件迁移，更新 import |
| `tests/test_docx_parser.py` | `scripts/demo_docx_parser.py` | 整文件迁移，更新 import |
| `tests/test_parser_factory.py` | `scripts/demo_parser_factory.py` | 整文件迁移，更新 import |
| `tests/test_account_permissions.py` | `tests/account/test_account_permissions.py` | 整文件迁移，更新 import |
| `tests/test_task_orchestration.py` | `tests/task/test_task_orchestration.py` | 整文件迁移，更新 import |
| `tests/test_preprocessing_service.py` | `tests/rag/test_preprocessing_service.py` | 整文件迁移，更新 import |
| `tests/test_document_splitter.py` | `tests/rag/test_document_splitter.py` | 整文件迁移，更新 import |
| `tests/test_clean_pipeline.py` | `tests/rag/test_clean_pipeline.py` | 整文件迁移，更新 import |

额外新增的 tests/test_application_startup.py 包含两个有针对性的回归测试：在独立进程中验证 ORM 注册及外键，以及使用外部服务替身验证原启动钩子和 HTTP 存活接口。

## 清理结果

先完成迁移和 import 更新，再确认旧路径无运行时代码引用、旧文件无剩余业务定义，最后删除旧文件和空目录：

- app/api/
- app/crud/
- app/models/
- app/schemas/
- app/services/
- app/scripts/
- config/

app/db/tables.py 与 app/db/mapper.py 也在各定义迁入业务模块后删除。原 app/schemas/rag.py 与 app/scripts/__init__.py 本来为空，未迁移为空壳业务文件。原任务服务的 package export 迁入 task/service.py，原 orchestration export 在扁平化为 task/orchestration.py 后不再需要。

RAG embedding/__init__.py 保留已有的稳定 package export，其余新增业务 package 的 __init__.py 为空。手动解析演示移到 scripts 并调整样本路径，不再被 pytest 误收集；三个演示均实际运行通过，样本文件未改动。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 重构前测试基线 | 20 passed，1 error；错误是手动演示 test_file 缺少 file_path fixture |
| 最终 compileall app | 通过 |
| 最终 pytest | 22 passed；原有 20 项自动化测试和新增 2 项启动回归测试全部通过 |
| FastAPI app import | 通过 |
| Uvicorn 配置加载 | 通过 |
| Uvicorn 实际 HTTP 检查 | /health 和 /openapi.json 均返回 200；检查后进程已停止 |
| OpenAPI 前后比较 | 完整内容一致：22 个路径、23 个接口操作，含 schema、operationId 和安全声明 |
| 数据库定义前后比较 | accounts、ai_agents、tasks、answers 的 MySQL DDL、索引和默认值一致 |
| 业务实现比对 | 191 个原有顶层类/函数的 AST 在归一化 import、模块别名、路由变量名及文档字符串后保持一致；手动演示 main 仅调整数据路径 |
| 模块导入及循环依赖 | 所有 64 个 app 模块可导入，未发现顶层循环导入 |
| 旧 import 扫描 | 生产代码、测试、脚本中没有旧技术层 import；本报告仅为记录保留旧路径名称 |
| 未修改文件核对 | data、migrations、requirements、环境配置文件内容和 Git index 的 SHA-256 与开始时一致 |
| git diff --check | 通过 |

ORM 模型依赖 db.session.Base；init_db 的模型注册使用局部 import，避免导入期循环，并由独立进程中的 SQLite 建表测试验证。没有通过 shared 或 core 转移业务代码来掩盖依赖循环。

Windows 执行环境对现有缓存和默认 pytest 临时目录存在写权限限制。compileall 在获得项目写入权限后通过；pytest 使用独立缓存和临时目录后通过。没有为处理权限问题修改业务代码或依赖。现有 Redis retry_on_timeout 和 FastAPI on_event 的弃用提示保留，本次没有改写这些行为。

真实 Uvicorn HTTP 检查使用 --lifespan off，启动钩子另用测试替身验证。因此验证覆盖代码导入、路由、数据库模型注册和启动流程，未进行真实 MySQL、Redis、vLLM 联调，也未触发真实数据库兼容 DDL。

## 提交与人工确认

没有需要先由人工决定才能完成的结构问题。部署环境的真实外部服务联调仍需在对应环境执行。

没有执行 git add、git reset 或 git commit，原暂存区保持不变。因此 Git 状态会同时显示旧文件删除和新路径未跟踪；原先暂存的被迁移文件可能显示 AD。普通 git diff --stat 不包含未跟踪的新文件，不能单独用其中的删除行数判断功能是否丢失。审查时请结合本迁移清单和 app/modules、scripts、tests 下的新文件。
