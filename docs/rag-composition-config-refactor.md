# RAG 依赖装配与配置收口报告

> 历史记录：本文描述当时状态，可能包含已删除文件和已修复缺陷。2026-10-08 的当前架构、测试及发布风险以 [GitHub 发布前报告](GITHUB_RELEASE_REPORT.md) 和 [根 README](../README.md) 为准。

日期：2026-09-17。本次以开始执行时的真实工作区为基准，没有移动目录、改名业务模块、回退既有修改或提交 Git commit。

## 一、检查结论

RAG 已按 API/Application/Domain/Infrastructure 分层，无须再次搬迁。

- 原 composition.build_dense_retriever 同时写死 Embedding 参数、创建 EmbeddingService 和 MilvusVectorStore，再组装检索器。
- HTTP get_dense_retriever 原来拥有 HTTP Client 生命周期；task_context 自行创建另一任务级 Client。两者已经共用检索工厂，但各自硬编码了 60 秒超时。
- IndexingService 已有预处理、向量化和写入编排，但缺少统一的正式装配入口；实验脚本各自手动组装。
- RagAnswerService 已依赖真实抽象 Retrieval，并通过 Agent 仓储及通用 LLM 调用生成答案，本次不改变它。
- TaskOrchestrator 原本只检索一次，将同一上下文传给多个 AI、聚合和评分；异常降级、任务状态和提交逻辑不需要改动。
- Settings 已使用 APP_ENV、get_env_file、get_settings 缓存、.env.development/.env.production。复用这套机制，没有第二套设置加载器。

## 二、修改文件及原因

| 文件 | 改动原因 |
| --- | --- |
| app/core/config.py | 在原 Settings 内增加 13 个 RAG 字段；默认值保持原实际值，校验正数配置，API key 使用 SecretStr 隐藏常规输出 |
| app/modules/rag/composition.py | 成为统一 Composition Root；拆出 Provider、EmbeddingService、VectorStore 工厂，新增 IndexingService 装配，集中问答对象装配 |
| app/modules/rag/api/dependencies.py | 拆出 get_rag_http_client 管理请求级资源；其他 Depends 只委托工厂，检索器类型改为 Retrieval |
| app/modules/rag/api/router.py | 仅将具体 DenseRetriever 注解改成已有 Retrieval 抽象，路由和处理逻辑不变 |
| app/modules/rag/task_context.py | 使用 Settings 超时及任务默认 top_k，通过 composition 模块调用与 HTTP 相同的工厂；保留任务级 try/finally 关闭 |
| tests/rag/test_architecture.py | 更新拆分后的依赖生命周期测试，保留原断言，补充 Composition 不执行业务和 API 不引用具体基础设施的检查 |
| tests/rag/test_composition.py | 新增环境加载、配置校验、密钥隐藏、非默认配置一致性、HTTP 实际依赖图、后台异常/取消关闭检查 |
| tests/task/test_task_orchestration.py | 更新工厂替身位置；验证单次检索及上下文共享，强化真实聚合/评分流程测试 |
| README.md | 添加本次配置说明入口 |
| app/modules/README.md | 更新当前装配与生命周期导读 |
| docs/rag-composition-config-refactor.md | 本报告及配置参考 |

所有 Application、Domain、Infrastructure 实现文件保持原样；Task/Agent 生产代码、schemas、切片/清理、Prompt、SQL/ORM、Redis/JWT、requirements、迁移和前端没有修改。scripts 和 .env.* 未修改。

## 三、最终依赖关系

```text
APP_ENV + environment + existing .env file
  -> core.Settings / get_settings / settings
       -> composition
            -> build_embedding_provider(client) -> EmbeddingProvider implementation
            -> build_embedding_service(client)  -> EmbeddingService
            -> build_vector_store()             -> VectorStore implementation
            -> build_dense_retriever(client)    -> DenseRetriever
            -> build_indexing_service(client)   -> IndexingService
            -> build_rag_answer_service(retriever) -> RagAnswerService

HTTP:
get_rag_http_client -> Depends client
  -> get_dense_retriever -> composition
  -> get_rag_answer_service -> composition
  -> API -> Application -> Domain abstractions
                           ^ Infrastructure implementations

TaskOrchestrator:
retrieve_task_rag_context -> task-owned client
  -> composition.build_dense_retriever(client)
  -> retrieve once -> build_rag_context -> close client
  -> same context for all AI, aggregator and scoring
```

composition 不导入 FastAPI、Depends 或 Router，不创建或关闭 HTTP Client，不执行 embed、retrieve/search、index_document、upsert 或 answer。

保留 MilvusVectorStore 原有构造时 ensure_collection 行为：创建该对象可能检查并创建不存在的集合。这不是新增索引/写入流程，本次验证全部用替身隔离，未连接真实 Milvus，也未修改任何集合。

没有新增 RetrievalService 包装类，也没有单独的 build_preprocessing_service：目前只需要在索引工厂中创建一次 PreprocessingService，独立包装没有额外价值。

## 四、Settings 配置参考

字段均在现有 Settings 内；环境变量使用大写 alias。未设置新字段时与重构前有效运行值一致。

| Settings 字段 | 环境变量 | 默认值/用途 |
| --- | --- | --- |
| rag_embedding_base_url | RAG_EMBEDDING_BASE_URL | http://localhost:11434 |
| rag_embedding_api_key | RAG_EMBEDDING_API_KEY | 保留原本地 Ollama 占位值；SecretStr，不是新增真实 secret |
| rag_embedding_model | RAG_EMBEDDING_MODEL | qwen3-embedding:0.6b |
| rag_embedding_dimension | RAG_EMBEDDING_DIMENSION | 1024；同时传给 Provider 和 MilvusConfig |
| rag_embedding_normalized | RAG_EMBEDDING_NORMALIZED | false；原语义不变，不新增归一化计算 |
| rag_embedding_batch_size | RAG_EMBEDDING_BATCH_SIZE | 16 |
| rag_embedding_timeout_seconds | RAG_EMBEDDING_TIMEOUT_SECONDS | 60.0；请求级和任务级 Client 共用 |
| rag_embedding_query_instruction | RAG_EMBEDDING_QUERY_INSTRUCTION | 根据用户问题检索最相关的文化遗产资料 |
| rag_milvus_uri | RAG_MILVUS_URI | http://localhost:19530 |
| rag_milvus_collection_name | RAG_MILVUS_COLLECTION_NAME | heritage_chunks |
| rag_milvus_metric_type | RAG_MILVUS_METRIC_TYPE | COSINE |
| rag_retrieval_top_k | RAG_RETRIEVAL_TOP_K | 3；检索工厂的缺省值 |
| rag_task_retrieval_top_k | RAG_TASK_RETRIEVAL_TOP_K | 4；任务调用的默认覆盖值 |

维度、批大小、top_k 必须大于零；超时必须为有限正数。配置仍按原机制在进程启动时读取并缓存，修改环境后需要重新启动服务，不提供热更新。

### 旧配置项与兼容性

当前 .env.development 中已有 EMBEDDING_* 实验项，但原 Settings 未定义这些字段，因 extra=ignore 而不影响当前正式 RAG。特别是 EMBEDDING_TIMEOUT_SECONDS=120，原实际 HTTP Client 超时仍为 60 秒。

因此本次选择明确的 RAG_* aliases，不自动激活那些过去无效的项，也没有改写环境文件。直接沿用旧超时变量会把实际行为从 60 改为 120 秒，违背本次要求。未来正式配置请使用上表名称；生产密钥通过部署环境提供，不提交到版本库。

### 保留的默认值边界

HTTP 请求 schema 的 top_k=5 是原有公开接口契约，原样保留。它是调用级覆盖值，不会被工厂的缺省 3 覆盖。任务缺省 4 也单独保留，可通过其专属配置调整，显式传入 top_k 仍优先。

MilvusConfig()、EmbeddingService(provider) 和 RetrievalConfig() 原有的独立构造默认值继续保留，兼容现有脚本和直接调用者。它们不是正式装配的配置来源：composition 对 MilvusConfig 和 EmbeddingService 显式传入全部相关 Settings 值，对 RetrievalConfig 显式传入检索默认值。没有通过删除构造默认值来破坏已有用法，也没有让 Application 自己加载环境。

非默认配置测试验证：即使这些组件保留旧独立默认值，正式构造的索引与查询也都使用 Settings 中的新值，不会回落到组件默认值。

HNSW M=16、efConstruction=200、搜索 ef=64、chunk_size=800、chunk_overlap=120、min_chunk_chars=100 全部原样保留，不借配置收口改动算法或集合。文件读取缓冲区、字段长度等非运行装配数值也不机械迁移。

### 实验脚本

index_rag_eval_documents.py、test_rag_indexing.py、test_dense_retriever.py、test_rag_answer.py 和各 Milvus 实验仍保留其手动装配及独立参数。RAG_CHAT_* 实验项也保留。没有执行集合 reset 脚本。

本次没有修复旧 test_rag_answer.py 与当前 RagAnswerService 构造/调用签名不匹配等已记录实验问题；离线测试成功不表示这些真实外部集成实验已通过。

## 五、资源生命周期与共用配置

HTTP：get_rag_http_client 通过 async generator 创建请求级 AsyncClient，在 finally 中关闭。FastAPI 默认按请求依赖图缓存，同一请求复用这项依赖；不同请求不共享全局 Client。get_dense_retriever 只调用工厂，get_rag_answer_service 只调用问答工厂。

后台任务：task_context 自行创建任务级 AsyncClient，并在 finally 中关闭，不使用 FastAPI Depends。它和 HTTP 使用同一个 composition 模块函数、同一个 Settings 超时来源。

两条服务装配链：

```text
build_dense_retriever(client)
  -> build_embedding_service(client)
       -> build_embedding_provider(client)
  -> build_vector_store()

build_indexing_service(client)
  -> PreprocessingService()
  -> build_embedding_service(client)
       -> build_embedding_provider(client)
  -> build_vector_store()
```

共用的是创建逻辑和配置，不是跨请求单例。各次构造对象独立，使用调用方传入的 Client；同一个 dimension 字段同时决定 Provider 维度和 MilvusConfig 维度，避免装配时分别配置。

已有集合与新模型/维度的兼容检查仍未新增。部署人员未来更换模型、维度或集合时需单独评估数据兼容与迁移，不能把修改 Settings 当成自动迁移知识库。

## 六、下一步管理员上传

应复用 app.modules.rag.composition.build_indexing_service(client)。

未来 POST /admin/rag/documents 可新增一个 Depends 装配索引服务，其 Client 复用现有 api.dependencies.get_rag_http_client；Router 只处理管理员鉴权、文件与临时 Path，再调用 IndexingService.index_document(path)。Provider/Milvus 不应在 Router 重建。

此次仅准备索引工厂，没有新增上传 URL、权限模型、文件存储或普通用户临时 RAG。普通用户未来的临时资料不应复用永久写入 Milvus 的索引流程。

## 七、验证

| 验证 | 结果 |
| --- | --- |
| 修改前基线 | 31 passed |
| python -m pytest tests/rag -q | 40 passed |
| python -m pytest tests -q | 54 passed |
| 原有断言 | 保留，生命周期检查适配拆分后的依赖，新增而非删除业务断言 |
| 环境配置 | development/production 环境文件读取与环境变量优先级通过；旧无效超时不被误激活 |
| 配置一致性 | 非默认模型、维度、批大小、归一化、instruction、集合及度量均同时作用于索引和检索 |
| HTTP 依赖图 | /rag/retrieve、/rag/answer 正常、装配失败、检索失败时均关闭 Client |
| 后台生命周期 | 正常、装配失败、检索失败、取消时关闭 Client；检索至多一次 |
| Task 流程 | 一次检索上下文传递给全部 AI 和 finisher；真实聚合/评分测试也检查收到同一资料，最终 completed |
| 新进程检查 | 环境变量覆盖任务 top_k 和 HTTP 超时确实生效 |
| 结构/导入 | 全部 app 模块可导入，Domain 独立，未发现顶层循环导入 |
| OpenAPI | 与本次开始时完整一致；24 个路径、25 个操作，无管理员上传接口 |
| 文件保护 | .env.*、requirements、data、migrations 和 Git 暂存区的 SHA-256 与本次开始时一致 |
| 语法/文档 | 109 个 Python 文件语法检查通过，当前 README/导读无断链；git diff --check 通过 |
| 启动 | 现有 TestClient/独立 SQLite 启动测试通过；Uvicorn lifespan=on 启动成功，健康接口与 OpenAPI 返回 200，随后关闭 |

实际命令使用现有 .venv/Scripts/python.exe，附加 -B 和 -p no:cacheprovider 避免生成缓存。5 个既有 FastAPI/Redis 弃用警告未改动。

以上是离线单元测试和隔离启动检查。数据库、Redis、模型健康检查或 Milvus 使用替身，不代表真实 Ollama/Milvus/MySQL/Redis 集成已经验证。没有调用真实模型，没有写入、重建或清空 Milvus，没有执行数据库 migration。

## 结论

本次仅完成依赖装配与配置收口。默认运行值、现有 HTTP 契约、Task 单次检索及后续状态流程、Prompt、数据库和集合行为保持不变；新增的是可复用索引装配入口、可通过现有 Settings 机制设置的 RAG 参数和相应离线回归覆盖。
