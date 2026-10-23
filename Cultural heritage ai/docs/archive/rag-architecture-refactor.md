# RAG 项目结构与职责整理报告

> 历史记录：本文描述当时状态，可能包含已删除文件和已修复缺陷。2026-10-08 的当前架构、测试及发布风险以 [GitHub 发布前报告](GITHUB_RELEASE_REPORT.md) 和 [根 README](../README.md) 为准。

日期：2026-09-17。基准是本次开始时的真实工作区，包含原有未提交、已暂存及未跟踪修改，不是 Git HEAD。没有回退既有修改，没有提交或重写暂存区。

## 1. 检查结论

检查了 app、scripts、tests、core、db、当前模块目录、配置入口、样本与迁移文件目录，以及现有项目说明。当前后端已经按 account、agent、task、rag 组织，不再存在实际使用中的全局 api/services/models 目录。工作区同级的前端和 infra/Milvus 部署目录不需要迁移。

RAG 已经包含索引、检索、问答和任务检索接入，旧 README 的“未实现检索”“索引服务为空”等描述已过时。主要结构问题是同级目录混放应用编排、抽象、第三方实现；dependencies 同时承担普通对象工厂与 FastAPI 请求生命周期；agent/service.py 实际是模型 HTTP adapter。

本次采用模块内部轻量分层，不改账号、任务名称，不添加 RetrievalService 同义包装类，不引入新框架或 interface/impl 成对结构。

## 2. 重构前主要结构

以下是本次开始时的结构，不是更早的技术分层版本。省略缓存及 __init__.py。

```text
app/
  main.py
  core/{config,logging,redis}.py
  db/session.py
  modules/
    account/{router,schemas,dependencies,service,security,models,mapper,repository}.py
    agent/{router,schemas,service,health,models,mapper,repository}.py
    task/{router,user_router,worker_router,ai_router,schemas,service,models,
          mapper,repository,management,queries,answers,ai_answers,
          orchestration,aggregation,review,rankings,task_utils}.py
    rag/
      router.py, schemas.py, dependencies.py
      models.py, context_builder.py, task_context.py
      preprocessing/{service,cleaner,cleaner_config,splitter,splitter_config}.py
      ingestion/{parser,parser_factory,text_parser,docx_parser,pdf_parser}.py
      embedding/{base,embedding_service,openai_compatible_provider,schemas}.py
      vectorstore/{base,config,models,milvus_store}.py
      indexing/indexing_service.py
      retrieval/{base,config,models,dense_retriever}.py
      answering/{base,openai_compatible_generator,rag_answer_service}.py
scripts/   解析、Milvus、索引、检索、问答实验和历史内存原型
tests/     account、task、rag、应用启动
docs/      之前的重构报告
data/      文档样本与检索评估材料
migrations/
```

## 3. 重构后目录树

```text
app/
  main.py
  core/{config,logging,redis}.py
  db/session.py
  modules/
    README.md
    account/                         原结构保留
    task/                            原结构保留
    agent/
      router.py, schemas.py, models.py, mapper.py, repository.py, health.py
      llm_provider.py
    rag/
      composition.py
      task_context.py
      api/
        router.py
        schemas.py
        dependencies.py
      application/
        preprocessing_service.py
        embedding_service.py
        indexing_service.py
        dense_retriever.py
        retrieval_config.py
        rag_answer_service.py
        context_builder.py
        preprocessing/
          cleaner.py, cleaner_config.py, splitter.py, splitter_config.py
      domain/
        models.py
        vector_models.py
        answer_models.py
        parser.py
        embedding_provider.py
        vector_store.py
        retriever.py
        answer_generator.py
        retrieval_models.py          原空占位文件，未新增业务
      infrastructure/
        openai_compatible_generator.py
        embedding/
          openai_compatible_provider.py
          schemas.py
        ingestion/
          parser_factory.py
          text_parser.py
          docx_parser.py
          pdf_parser.py
        vectorstore/
          config.py
          milvus_vector_store.py
scripts/                             保留原脚本位置
tests/
  account/
  task/
  rag/
    test_architecture.py             新增离线结构及流程回归检查
    test_clean_pipeline.py
    test_document_splitter.py
    test_preprocessing_service.py
  test_application_startup.py
  test_main.http
docs/
  package-by-feature-refactor.md      历史记录保留
  rag-architecture-refactor.md        本报告
pytest.ini                           自动测试只收集 tests/
requirements.txt                     未修改
```

包入口只作简短职责说明，不增加集中 re-export。旧 embedding/__init__.py 的三个跨层导出随旧包移除；仓库内没有调用该包级入口的代码，现有使用方均更新为明确模块导入。旧目录迁完后仅剩 Python 缓存，检查内容后清理，不删除业务代码。

## 4. 目录职责和依赖方向

| 位置 | 职责 |
| --- | --- |
| rag/api | 路由、HTTP 参数校验、响应转换、请求级 Depends；不直接创建 SDK 或执行底层操作 |
| rag/application | 索引、预处理、Embedding 批次、检索、问答流程；保留原有算法和提示词 |
| rag/application/preprocessing | 纯文本清理/切片实现及参数，作为预处理服务的内聚子目录 |
| rag/domain | 稳定数据类与抽象接口；仅依赖标准库和本目录 |
| rag/infrastructure | HTTP 模型适配器、外部响应模型、文件格式库调用、Milvus SDK |
| rag/composition.py | 普通工厂：组装具体实现和服务；不运行 embed/search/index/answer |
| rag/task_context.py | Task 与 RAG 的接入适配，拥有后台检索 HTTP Client 生命周期 |
| agent/llm_provider.py | 普通 AI 回答、聚合、评分、RAG 回答共用的通用模型 HTTP 调用 |
| core | 全局环境配置、日志和 Redis 连接/回退 |
| db | SQLAlchemy Base、引擎、SessionLocal、请求会话和原有初始化 |
| scripts | 手动集成验证、开发工具和历史原型，不是应用服务 |
| tests | 离线自动化回归、结构及装配检查 |

实际依赖方向为 API → Application → Domain，Infrastructure 实现并依赖 Domain；Composition 同时认识 Application 与 Infrastructure，选择具体实现。Domain 不反向导入 Infrastructure。

两处有意保留的务实边界：
- PreprocessingService 仍使用具体 ParserFactory 选择解析器，保持原有无参构造和延迟选择行为；真正文件库操作封装在 Infrastructure。没有为迁移增加一个无实际收益的工厂接口。
- RagAnswerService 继续调用 Agent 仓储和通用 LLM 函数，并保留原有 HTTPException。没有在此次整理中重写跨模块错误模型或改变 AI 的选择方式。

## 5. 主要文件迁移清单

| 原路径 | 新路径 | 说明 |
| --- | --- | --- |
| `app/modules/rag/router.py` | `app/modules/rag/api/router.py` | 整文件迁移，更新 import |
| `app/modules/rag/schemas.py` | `app/modules/rag/api/schemas.py` | 整文件迁移，更新 import |
| `app/modules/rag/dependencies.py` | `app/modules/rag/api/dependencies.py` | 仅 FastAPI 依赖；普通工厂另行拆出 |
| `app/modules/rag/context_builder.py` | `app/modules/rag/application/context_builder.py` | 整文件迁移，更新 import |
| `app/modules/rag/preprocessing/service.py` | `app/modules/rag/application/preprocessing_service.py` | 整文件迁移，更新 import |
| `app/modules/rag/preprocessing/cleaner.py` | `app/modules/rag/application/preprocessing/cleaner.py` | 整文件迁移，更新 import |
| `app/modules/rag/preprocessing/cleaner_config.py` | `app/modules/rag/application/preprocessing/cleaner_config.py` | 整文件迁移，更新 import |
| `app/modules/rag/preprocessing/splitter.py` | `app/modules/rag/application/preprocessing/splitter.py` | 整文件迁移，更新 import |
| `app/modules/rag/preprocessing/splitter_config.py` | `app/modules/rag/application/preprocessing/splitter_config.py` | 整文件迁移，更新 import |
| `app/modules/rag/embedding/base.py` | `app/modules/rag/domain/embedding_provider.py` | 整文件迁移，更新 import |
| `app/modules/rag/embedding/embedding_service.py` | `app/modules/rag/application/embedding_service.py` | 整文件迁移，更新 import |
| `app/modules/rag/embedding/openai_compatible_provider.py` | `app/modules/rag/infrastructure/embedding/openai_compatible_provider.py` | 整文件迁移，更新 import |
| `app/modules/rag/embedding/schemas.py` | `app/modules/rag/infrastructure/embedding/schemas.py` | 整文件迁移，更新 import |
| `app/modules/rag/ingestion/parser.py` | `app/modules/rag/domain/parser.py` | 整文件迁移，更新 import |
| `app/modules/rag/ingestion/parser_factory.py` | `app/modules/rag/infrastructure/ingestion/parser_factory.py` | 整文件迁移，更新 import |
| `app/modules/rag/ingestion/text_parser.py` | `app/modules/rag/infrastructure/ingestion/text_parser.py` | 整文件迁移，更新 import |
| `app/modules/rag/ingestion/pdf_parser.py` | `app/modules/rag/infrastructure/ingestion/pdf_parser.py` | 整文件迁移，更新 import |
| `app/modules/rag/ingestion/docx_parser.py` | `app/modules/rag/infrastructure/ingestion/docx_parser.py` | 整文件迁移，更新 import |
| `app/modules/rag/models.py` | `app/modules/rag/domain/models.py` | 整文件迁移，更新 import |
| `app/modules/rag/vectorstore/base.py` | `app/modules/rag/domain/vector_store.py` | 整文件迁移，更新 import |
| `app/modules/rag/vectorstore/models.py` | `app/modules/rag/domain/vector_models.py` | 整文件迁移，更新 import |
| `app/modules/rag/vectorstore/config.py` | `app/modules/rag/infrastructure/vectorstore/config.py` | 整文件迁移，更新 import |
| `app/modules/rag/vectorstore/milvus_store.py` | `app/modules/rag/infrastructure/vectorstore/milvus_vector_store.py` | 整文件迁移，更新 import |
| `app/modules/rag/retrieval/base.py` | `app/modules/rag/domain/retriever.py` | 整文件迁移，更新 import |
| `app/modules/rag/retrieval/config.py` | `app/modules/rag/application/retrieval_config.py` | 整文件迁移，更新 import |
| `app/modules/rag/retrieval/models.py` | `app/modules/rag/domain/retrieval_models.py` | 保留原有空占位文件，未新增抽象 |
| `app/modules/rag/retrieval/dense_retriever.py` | `app/modules/rag/application/dense_retriever.py` | 整文件迁移，更新 import |
| `app/modules/rag/answering/base.py` | `app/modules/rag/domain/answer_generator.py` | 整文件迁移，更新 import |
| `app/modules/rag/answering/openai_compatible_generator.py` | `app/modules/rag/infrastructure/openai_compatible_generator.py` | 整文件迁移，更新 import |
| `app/modules/rag/answering/rag_answer_service.py` | `app/modules/rag/application/rag_answer_service.py` | 问答服务；结果模型另行拆出 |
| `app/modules/rag/indexing/indexing_service.py` | `app/modules/rag/application/indexing_service.py` | 整文件迁移，更新 import |
| `app/modules/agent/service.py` | `app/modules/agent/llm_provider.py` | 整文件迁移，更新 import |
| `app/modules/rag/dependencies.py:build_dense_retriever` | `app/modules/rag/composition.py:build_dense_retriever` | 原工厂原样拆出，配置与创建顺序不变 |
| `app/modules/rag/answering/rag_answer_service.py:RagAnswerResult` | `app/modules/rag/domain/answer_models.py:RagAnswerResult` | 数据类脱离应用服务，字段和类型不变 |

另新增分层包 __init__.py、tests/rag/test_architecture.py、pytest.ini 和本报告。README、模块导读、main、受影响脚本/测试、任务调用方更新引用或说明。

内部 Python import 路径有意更新，不维护所有旧路径的兼容壳；HTTP 对外契约不变。仓库外若有直接导入旧模块的脚本，需按本清单更新。

## 6. 未移动的位置及原因

- account 全部文件：注册、认证、数据访问已按业务内聚，规模小，本次无须机械拆四层。
- task 全部文件：任务状态、事务、回答、聚合和审核已有明确划分；只更新 ai_answers/aggregation 中的 LLM import，不重写业务。
- agent 除 service.py 外的 router、schemas、models、mapper、repository、health：已有明确职责；健康检查含状态持久化编排，不等同于通用 LLM adapter，保留。
- rag/task_context.py：是后台任务接入适配而非 FastAPI Depends，也不是纯检索算法；保留模块边界入口，只改为直接依赖普通 composition。
- core/config.py、logging.py、redis.py 和 db/session.py：跨业务共用基础设施已有稳定入口，不再为目录整齐迁移。
- scripts 全部原位置：包含实验配置、输出和 SDK 检查。正式索引/检索/问答已经在应用内，不把实验校验提升为业务功能。
- tests 既有测试文件：只更新 RAG import；任务测试替身补上已有的 rag_context 参数并隔离外部检索，所有原断言保留。
- requirements、环境文件、data、migrations：全部保留；没有运行真实数据库迁移或集合重置。
- 前端、infra/Milvus 部署配置及数据卷：不属于本次代码职责迁移，不修改。

## 7. 当前核心调用链

### 文档入库

```text
scripts/index_rag_eval_documents.main（手动装配）
  -> IndexingService.index_document
  -> PreprocessingService.process
  -> ParserFactory -> TextParser / DocxParser / PdfParser
  -> RagDocument
  -> 文件 SHA-256 统一 document_id
  -> DocumentCleaner -> DocumentSplitter -> RagChunk[]
  -> EmbeddingService.embed_chunks
  -> OpenAICompatibleEmbeddingProvider.embed_documents
  -> POST /v1/embeddings
  -> EmbeddedChunk[] -> VectorRecord[]
  -> VectorStore.upsert -> MilvusVectorStore -> Milvus upsert + flush
  -> 返回 document_id
```

IndexingService 现有逻辑包含两次额外预处理以打印稳定 ID，总计三次 process 调用，全部保留。没有新增上传或索引 HTTP API。

### RAG 查询与问答

```text
POST /rag/retrieve
  -> API request schema + Depends
  -> DenseRetriever.retrieve
  -> EmbeddingService.embed_query
  -> EmbeddingProvider（查询 instruction 保留）
  -> MilvusVectorStore.search
  -> VectorSearchHit[] -> API response

POST /rag/answer
  -> RagAnswerService.answer
  -> agent.repository.get_ai_agent + available 检查
  -> Retrieval.retrieve（当前实例为 DenseRetriever）
  -> 无命中：返回原有资料不足回答，不请求 LLM
  -> 有命中：_build_context + 原 system/user prompt
  -> agent.llm_provider.request_local_ai_raw
  -> Agent 配置的 OpenAI-compatible chat endpoint
  -> RagAnswerResult -> answer + sources 响应
```

项目中没有叫 RetrievalService 的类；DenseRetriever 就是现有检索服务，不添加冗余包装。

### 任务接入

```text
POST /users/tasks -> 创建并提交 Task -> BackgroundTasks
  -> TaskOrchestrator.process_task -> 原子领取 waiting 任务
  -> retrieve_task_rag_context（每任务一次）
  -> DenseRetriever -> build_rag_context
  -> 并发调用业务 AI，传入同一份 rag_context
  -> 保存答案 -> finish_task
  -> 聚合 -> 评分 -> completed
```

检索失败时原样降级为无资料回答；不等待全部人工回答，不要求每个 AI 成功。聚合与评分继续共享同一资料，不再次检索。所有提示词和提交事务顺序保持原样。

## 8. FastAPI 依赖装配

```text
api.dependencies.get_dense_retriever
  -> 请求级 httpx.AsyncClient(timeout=60.0)
  -> composition.build_dense_retriever(client)
       -> OpenAICompatibleEmbeddingProvider
       -> EmbeddingService(batch_size=16)
       -> MilvusVectorStore(MilvusConfig())
       -> DenseRetriever(RetrievalConfig(top_k=3))
  -> yield retriever
  -> finally client.aclose()

api.dependencies.get_rag_answer_service
  -> Depends(get_dense_retriever)
  -> RagAnswerService(retriever)
```

普通工厂不再导入 FastAPI；后台 task_context 直接复用工厂并自行关闭 HTTP Client。没有添加全局缓存或改变请求级生命周期。

保留现有 MilvusVectorStore 构造期间 ensure_collection 的行为，因此装配可能检查/创建集合；依赖函数本身没有新增 embed/search/index/生成回答操作。

其他 Depends 保留：账号 get_current_user 承担现有认证；db.get_db 创建并关闭请求级 Session；未将现有认证规则挪出或改写。

## 9. 抽象与实现

| 核心抽象 | 现有实现/使用方 |
| --- | --- |
| DocumentParser | TextParser、DocxParser、PdfParser；ParserFactory 选择 |
| EmbeddingProvider | OpenAICompatibleEmbeddingProvider；EmbeddingService 使用 |
| VectorStore | MilvusVectorStore；IndexingService 和 DenseRetriever 使用 |
| Retrieval | DenseRetriever；RagAnswerService 使用 |
| AnswerGenerator | OpenAICompatibleAnswerGenerator；保留规划中的抽象/实现，当前问答服务未使用 |

Embedding HTTP 响应模型放在 infrastructure/embedding/schemas.py，因为它描述第三方响应，而不是本项目的 HTTP 入参/出参。

## 10. 配置和业务不变量

环境入口仍为 core/config.py，根据 APP_ENV 选择既有 .env 文件。没有新增或重命名环境变量，也没有将全局配置改成另一套加载机制。模型配置的实际值不在报告中展开敏感信息。

保留原配置作用域，不把“统一入口”解释为强制覆盖所有脚本参数：
- MilvusConfig 在 Infrastructure，仍为原集合、1024 维、COSINE、HNSW M=16、efConstruction=200、搜索 ef=64。
- Embedding 模型、归一化设置、请求 instruction、batch_size 及 HTTP 超时均原样迁移。
- 切片仍为 800/120/100，其他清理和分隔符设置保留。
- RetrievalConfig 默认 5；普通装配工厂配置 3；HTTP 请求默认 5；任务检索默认 4。不同作用域均未合并。
- scripts 中不同的实验参数和 RAG_CHAT_* 读取保持原样。

未修改 URL、HTTP method、业务字段、数据库模型或 SQL、Redis key、JWT、登录策略、任务状态、评分、Prompt、模型名称、集合参数或依赖版本。新增 pytest 配置仅区分自动化测试与手动外部集成脚本。

## 11. 验证结果

| 检查 | 结果 |
| --- | --- |
| 重构前基线 | 19 passed、3 failed；旧任务测试替身未接收新增的 rag_context 参数；验证时隔离 Milvus |
| 原有测试恢复 | 替身适配当前参数并隔离检索后，22 项原有测试通过；不修改生产逻辑 |
| 最终 pytest | 31 passed，5 个既有弃用警告 |
| 命令 | .venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider |
| 语法检查 | app/scripts/tests 共 108 个 Python 文件均可解析 |
| 导入检查 | 88 个 app 模块可导入；12 个脚本可导入，脚本检查替换 MilvusClient 防止连真实服务 |
| RAG 关键类 | Parser、EmbeddingProvider、VectorStore、MilvusVectorStore、PreprocessingService、EmbeddingService、IndexingService、DenseRetriever、RagAnswerService 全部可导入 |
| 配置和 Router | app/config 正常加载，路由注册成功 |
| OpenAPI | 24 个路径、25 个操作；重构前后完整文档一致，包括 schema、默认值和 operationId |
| 实现比对 | 188 个原有顶层类/函数，忽略 import 和文档字符串后 AST 一致 |
| 依赖生命周期 | 请求装配成功和失败时均关闭 HTTP Client；装配不执行检索 |
| 离线流程 | TXT 预处理→Embedding→入库调用、查询 API、问答命中/空结果路径通过 |
| 启动测试 | TestClient 验证原启动钩子；独立进程 SQLite 验证 ORM 注册和外键 |
| 实际服务启动 | Uvicorn lifespan=on，替换启动外部依赖；/health 和 /openapi.json 均 200，随后关闭 |
| 受保护文件 | .env.*、requirements、data、migrations、Git index 的 SHA-256 与开始时一致 |
| 旧 import | app/scripts/tests 无旧 RAG 或 agent.service 导入 |
| Git 检查 | git diff --check 通过；仅有既有 LF/CRLF 提示 |

没有真实 MySQL/Redis/Milvus/LLM 联调，也没有重置集合、向真实 Milvus 写入或调用付费模型。脚本可导入不代表其所有实验流程已经验证通过。

### 循环依赖

未发现顶层循环导入，自动测试对 app 内显式顶层导入图检查通过；全部模块可加载。Domain 不依赖 Infrastructure、API、SQLAlchemy、httpx 或 FastAPI。

db.session.init_db 内部对 ORM 模块的延迟导入仍保留。它与 models → db.session.Base 构成静态双向引用，但不构成导入期循环；独立进程的初始化测试覆盖了这一点。本次没有改写该既有注册机制。

## 12. 本次保留的问题

- PreprocessingService 默认 ParserFactory 仍是具体依赖；后续确有替换需求时可再引入显式注入。
- RagAnswerService 仍耦合 Agent 仓储、HTTPException 和通用 LLM 函数；没有强制改成 AnswerGenerator 流程。
- application 内问答的 _build_context 与任务 context_builder 存在相似逻辑，本次不合并，以免改动提示词。
- IndexingService 中额外的稳定 ID 验证/打印，以及 Milvus upsert 后的 flush 保留，没有“顺手优化”。
- VectorStore.upsert 的抽象注解与具体实现的列表参数不一致；delete_by_document 尚未实现，均保留。
- Milvus SDK 返回对象的不同版本兼容性未真实联调，本次不改变 search 的结果转换方式。
- scripts/test_rag_answer.py 仍使用旧 generator 构造参数且调用 answer 时没有 ai_id，与当前服务签名不一致。它是保留的历史实验，不以更改问答业务来适配；后续需先决定使用指定 Agent 还是独立生成器。
- scripts/test_rag_indexing.py 删除了未使用且无法解析的 import http_client；其主体实验逻辑、输出及资源关闭位置不变。
- scripts/test_dense_retriever.py 的准确率统计仍是实验状态；scripts/test_milvus.py 导入即连接的旧行为保留，通过 pytest testpaths 避免自动收集。
- 环境设置与脚本手动模型装配仍有重复。统一模型/检索参数可能改变实验行为，本次只明确位置，不合并默认值。
- 账号/Agent/任务的 models 仍同时容纳 Pydantic 模型与 ORM 类，部分 Repository 使用 HTTPException；这些模块当前足够小，不在本次强行扩大分层范围。
- 原有 FastAPI on_event 与 Redis retry_on_timeout 弃用提示仍在，不升级依赖或迁移生命周期 API。

## 最终确认

本次只是架构整理，没有修改业务逻辑。实际变更是文件迁移、职责拆分、引用更新、注释/文档同步和测试配套调整；原有业务实现、外部接口、数据结构与业务配置含义均保持不变。
