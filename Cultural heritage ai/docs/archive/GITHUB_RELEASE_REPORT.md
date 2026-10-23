# GitHub 发布前最终重构与清理报告

核对日期：2026-10-08；分支：main；Python 3.11.8。

**结论：代码重构、配置模板、脚本和文档清理已完成，离线验收通过。当前工作区及暂存区的高置信敏感信息扫描通过；已有 Git 历史仍含 4 个带私有地址的 .env.production blob，公开原历史前必须处理。未提交、未推送、未改写历史，也未执行真实数据库或 Milvus 删除操作。**

开始时仓库已有大量 staged / unstaged / untracked 重构内容。本报告按本轮开始时的文件哈希区分变更，不将之前的业务模块迁移重复记为本轮成果。除取消跟踪 .env.production、同步已匿名化的 DOCX 暂存副本外，保留原有暂存安排；最终提交前需要审阅并暂存完整的当前改动，不能直接提交旧暂存快照。

## 1. Architecture changes

- 默认公共检索统一到 `composition.build_rag_retriever`：Hybrid(Dense + Milvus native BM25) → RRF → RerankedRetriever → VllmReranker → final TopK。Dense-only builder 仅保留为显式评估基线。
- HTTP dependencies 和 Task 使用同一装配策略；dependencies 管理请求资源，task_context 作为任务适配器管理资源。业务上下文编排放入 RagContextService。
- RagAnswerService 仅依赖 Retrieval 和 AnswerGenerator。AgentAnswerGenerator 在 infrastructure 中连接现有业务 Agent 配置及 LLM 调用，不额外建立平行 LLM 配置。
- PreprocessingService 注入 Parser resolver，不再绑定 infrastructure；IndexingService 通过抽象完成单次预处理、向量化、records 和 upsert。
- RAG domain 保持 stdlib / ABC；application 不引入 FastAPI、SQLAlchemy、httpx 或 Milvus SDK。Milvus/HTTP 由 infrastructure 实现，composition 只组装对象。
- HTTP / Task / 手动脚本明确关闭客户端；同步 Milvus、解析和入库转交工作线程。应用启动/关闭改用 lifespan 管理 DB 初始化、健康检查任务及 engine.dispose。

保持公开 27 个 API 操作、请求响应字段、JWT / Redis token 语义、五张业务表定义。切分 800/120/100、HNSW M16/efConstruction200/ef64、RRF60、默认候选20/最终5和内容 ID 算法保持原值。

有附件 Task 仍取公共3 + 临时2；公共部分改用正式检索策略，临时部分继续内存余弦。两部分分别请求 query embedding，未承诺跨检索器复用一次向量。

## 2. Bugs fixed

| 问题 | 修复 |
| --- | --- |
| HTTP 问答工厂把 Retriever 当作 AsyncClient | 统一工厂签名和依赖注入，增加真实依赖图及 MockTransport 测试 |
| HTTP 与 Task 使用不同公共检索策略 | 默认策略只有一处，附件公共检索也复用同一实例 |
| candidate / final 数量混淆 | 召回不少于候选默认值和最终请求数；最终截断且不超过 top_k |
| 精排 index 映射、重复、越界和非有限分数 | 显式校验，按 relevance 排序，复制原 hit，保留 retrieval score |
| 精排 HTTP 参数不符合 vLLM 数量协议 | 外部请求使用 top_n，公开 API 继续使用 top_k |
| 生产 IndexingService 重复解析和 debug print | 只 process 一次，稳定性测试迁入 tests |
| Chunk 与向量数量或顺序不一致 | 校验对应关系，复制 metadata，构造正确 VectorRecord |
| Milvus 旧 Dense-only schema 无清晰错误 | 验证 embedding维度 / sparse / BM25 function；不自动删除旧库 |
| document_id filter 拼接 | JSON 转义引号和反斜杠，两路 ANN 使用相同 filter |
| tool-only 模型响应 content=None 类型问题 | 内部 LlmToolResponse 使用可空 content |
| Tool 参数 validator 未完成 | 实现 JSON Schema 校验并接入 executor，非法参数不会到达工具 |
| Tool 脚本建两套 Registry / 固定业务AI-ID / 预设答案断言 | Registry 共享，ID 和查询由 CLI 提供，只输出真实结果 |
| 独立 init_db 遗漏模型注册 | 注册 task / heritage 模型；未增加或改动业务表 |
| SQLAlchemy rowcount 类型问题 | 检查真实 CursorResult；领取状态条件更新保持不变 |
| 评分重复/无效数据未完整进入失败处理 | 校验移入异常处理范围，拒绝非有限分数，校验覆盖后按 ID 取值 |
| urllib 模块名、pyexpat / sqlalchemy.testing 历史引用 | 修正实际调用和删除无效依赖 |
| vLLM 超时、配置数值未校验 | 集中 Settings，正数/有限值检查，candidate >= final |
| 缺少上传 / JSON Schema 依赖及 UTF-16 清单 | 补 python-multipart / jsonschema / pymilvus，清单改为 UTF-8 |
| Evaluation 覆盖 Dense 历史报告、客户端不关闭 | 使用独立编号报告，明确 Dense / Hybrid / Hybrid+Rerank 策略和 finally 清理 |

## 3. Deleted legacy code

- `rag/infrastructure/reranking/repository.py`、`models.py`：没有真实业务调用，依赖不存在的 RerankerConfig / mapper；不恢复死代码，不更改物理数据库。
- `rag/application/bm25_retriever.py`、`domain/bm25_models.py`：未接入公共链，删除与 Milvus-native BM25 平行的 Python 实现，同时删除仅它使用的 rank-bm25 / jieba3k 依赖。
- `rag/infrastructure/openai_compatible_generator.py`：未使用的直接生成适配器；统一走真实注册业务 AI。
- `scripts/test_qwen3_reranker.py`：依赖缺失的 Ollama 类；`legacy_memory.py`：旧内存原型。
- `test_milvus.py`、`test_milvus_store.py`：导入时外部副作用及重复写实验；保留只读连接检查和正式入库入口。
- `test_tool_execution.py`、`test_web_agent_loop.py`、`test_tool_executor.py`：重复或临时 Fake Tool demo；正式工具循环/参数验证迁入 pytest。
- `test_heritage_repository.py`、`inspect_rag_document_ids.py`：冗余固定数据实验；保留可配置工具查询、检索和目录导出。
- 三个 parser demo 合并为 `scripts/inspect_document.py`，支持本地文件检查。

本轮共删除 17 个实际存在的历史源文件；之前已经删除的 app/api、app/crud、app/services 等迁移内容不计入本轮。测试、历史文档、评估集、小型 fixture 及本地运行数据保留。

## 4. GitHub safety

- .env.example 按真实 Settings 补齐 DB / JWT / Redis password / Milvus token / Embedding / Reranker / 模型超时 / 可选 Tavily；没有凭空新增 LLM_BASE_URL / LLM_MODEL。
- Settings 保留原有配置键；增加标准 .env fallback，兼容既有环境专用文件。生产环境拒绝已知占位 JWT 密钥，连接密码按 URL 编码。
- 本地 .env.development 和 .env.production 哈希与修改前一致。只取消 .env.production Git 跟踪，文件仍在本机；索引中只保留 .env.example。
- .gitignore 覆盖环境文件、虚拟环境、缓存、IDE、日志、临时附件、数据库 dump、模型文件和服务运行卷。
- DOCX 示例作者 / 最后编辑者已匿名化，正文 ZIP entries 保留且能正常解析。文件字节变化会自然改变该示例的 document_id；ID 算法未改变，30 份评估文档及所有标注 ID 未变。
- 扫描当前可提交工作区（227文件）与暂存区（180文件）：0个高置信发现。扫描包含 credential-shaped values、私有/CGNAT地址、个人路径、环境文件、运行文件及暂存 DOCX 作者。
- 历史扫描271个文本blob：4个发现，均为历史 .env.production 私有地址；对象前缀为 `6a6a6dac72`、`a4bd3b23da`、`a9d17a5a5e`、`82e5e3f477`，实际值不回显。该扫描明确返回非零，不能描述为历史已通过。
- 扫描只是高置信规则检查，不是完整秘密检测证明；现存 staged 内容还包含此前的 AD/AM 项，正式提交前应审阅完整 diff 和更新暂存快照。

**公开旧历史前的具体处理范围：备份仓库与未提交改动后，在可恢复副本中从所有待发布历史移除 .env.production，再运行 --history 验证。历史重写会改变提交 ID，需明确确认；也可发布审核后的当前快照到不含旧历史的新仓库。若进一步审计发现曾提交的有效凭据，应轮换。**

自动审批曾拒绝新增“把全局 LLM_API_KEY 发给任意注册 AI URL”的补丁，理由是目的地未被证明可信。该额外能力已撤回，继续使用现有无密钥本地模型调用，没有绕过拒绝或扩大密钥外发范围。

## 5. Verification

最终实际执行：

| 验证 | 结果 |
| --- | --- |
| python -m compileall app | exit 0 |
| from app.main import app | app import ok；27个API操作，5张业务表注册 |
| python -m pytest --basetemp tmp/pytest-release-20261008-final | **90 passed in 4.04s** |
| Ruff：app tests scripts evaluation | All checks passed |
| Mypy：app scripts evaluation | Success: no issues found in 159 source files |
| 30个 scripts / evaluation Python 模块导入 | 全部通过，无导入时网络调用 |
| pip check | No broken requirements found |
| pip install --dry-run -r requirements-dev.txt | exit 0；在现有环境解析通过，不是独立全新环境安装验收 |
| git diff --check | exit 0；只存在本机 autocrlf 换行提示 |
| 已删除 Reranker / BM25 / Ollama / 测试依赖源码引用 | app/tests/scripts/evaluation 无残留 |
| 当前工作区 / 暂存区发布扫描 | 通过 |
| 旧历史扫描 | **未通过：4个私有地址历史blob** |
| 本地DOCX / PDF解析 | 通过，分别1 / 4个chunk |

测试覆盖 Rerank 候选/最终数、顺序及索引错误、分数保留、单次 indexing、稳定 ID、真实 composition + HTTP MockTransport + SDK schema/request 对象、资源成功/失败/取消时关闭、Task 共享/降级/领取、Tool 多调用与轮数限制、Config 模板与覆盖、报告不覆盖历史。

首次 compileall 因沙箱缓存写权限失败，授权重跑通过；原样 pytest 在当前 Windows 临时目录遇到9个 fixture 权限错误，另一次指定目录尚缺父目录。建立项目内被忽略的 tmp 父目录后，完整90项通过，没有跳过失败测试或改变测试断言。此前禁用缓存的完整离线运行也已90项通过。

保护检查：本地环境文件、30份评估TXT、evaluation datasets、历史 Dense 报告、cleaner/splitter 算法文件和业务表定义保留；DOCX仅清理个人元数据。未连接真实外部服务跑评估，没有编造 Hybrid 或 Rerank 实验数字。

## 6. Remaining external requirements

| 服务 | 需要它的功能 |
| --- | --- |
| MySQL | 应用启动建表、账号、AI配置、任务、答案、信誉、heritage工具 |
| Redis | JWT当前token/退出黑名单；开发内存降级只适合本机 |
| Milvus（native BM25 / jieba） | 公共入库、Hybrid搜索、RRF；旧schema须新collection重索引 |
| Embedding server（/v1/embeddings，维度匹配） | 公共入库、查询和附件向量 |
| Reranker server（/v1/rerank） | 当前所有默认公共检索，使用index/relevance_score响应 |
| LLM server（本地无密钥chat completions，工具兼容） | 业务回答、工具循环、聚合与评分；需登记business/aggregator并健康检查 |
| Tavily（可选） | web_search / fetch_webpage；密钥为空时不启用 |

完整启动、模型示例、接口操作和评估命令见 [README](../README.md)。离线验收不替代这些服务的联调。

## 7. Files changed

主要改动位于 RAG composition / dependencies / task_context、检索与入库 service、Milvus/vLLM adapters、Settings、应用生命周期、评分和工具执行、脚本、测试与文档。

以下按本轮开始时文件哈希列出，不等同于整个既有 Git diff；暂存 .env.production 的删除单独记录，文件内容保留。

### 本轮修改

- `app/main.py`
- `app/core/config.py`
- `app/core/redis.py`
- `app/db/session.py`
- `app/modules/README.md`
- `app/modules/agent/health.py`
- `app/modules/agent/llm_provider.py`
- `app/modules/agent/router.py`
- `app/modules/agent/tool_calling.py`
- `app/modules/agent/tool_models.py`
- `app/modules/heritage/models.py`
- `app/modules/heritage/repository.py`
- `app/modules/rag/composition.py`
- `app/modules/rag/task_context.py`
- `app/modules/rag/api/admin_router.py`
- `app/modules/rag/api/dependencies.py`
- `app/modules/rag/api/router.py`
- `app/modules/rag/api/schemas.py`
- `app/modules/rag/application/combined_retriever.py`
- `app/modules/rag/application/dense_retriever.py`
- `app/modules/rag/application/hybrid_retriever.py`
- `app/modules/rag/application/indexing_service.py`
- `app/modules/rag/application/preprocessing_service.py`
- `app/modules/rag/application/rag_answer_service.py`
- `app/modules/rag/application/reranked_retriever.py`
- `app/modules/rag/application/temporary_rag_service.py`
- `app/modules/rag/application/temporary_retriever.py`
- `app/modules/rag/domain/answer_generator.py`
- `app/modules/rag/domain/vector_store.py`
- `app/modules/rag/infrastructure/temporary_storage.py`
- `app/modules/rag/infrastructure/embedding/openai_compatible_provider.py`
- `app/modules/rag/infrastructure/ingestion/docx_parser.py`
- `app/modules/rag/infrastructure/reranking/vllm_reranker.py`
- `app/modules/rag/infrastructure/vectorstore/config.py`
- `app/modules/rag/infrastructure/vectorstore/milvus_vector_store.py`
- `app/modules/task/application/aggregation.py`
- `app/modules/task/application/orchestration.py`
- `app/modules/task/infrastructure/repository.py`
- `app/modules/tool/composition.py`
- `app/modules/tool/application/argument_validator.py`
- `app/modules/tool/application/executor.py`
- `app/modules/tool/domain/errors.py`
- `app/modules/tool/infrastructure/web/tavily_web_provider.py`
- `app/modules/tool/infrastructure/web/webpage_fetch_tool.py`
- `tests/test_application_startup.py`
- `tests/rag/test_architecture.py`
- `tests/rag/test_composition.py`
- `tests/rag/test_preprocessing_service.py`
- `tests/task/test_task_orchestration.py`
- `scripts/export_rag_document_catalog.py`
- `scripts/index_rag_eval_documents.py`
- `scripts/reset_rag_collection.py`
- `scripts/test_combined_retriever.py`
- `scripts/test_dense_retriever.py`
- `scripts/test_heritage_detail_tool.py`
- `scripts/test_llm_tool_call.py`
- `scripts/test_milvus_connection.py`
- `scripts/test_rag_answer.py`
- `scripts/test_rag_indexing.py`
- `scripts/test_reranked_retriever.py`
- `scripts/test_temporary_rag_embedding.py`
- `scripts/test_temporary_retriever.py`
- `scripts/test_tool_agent_loop.py`
- `scripts/test_tool_schema.py`
- `scripts/test_vllm_embedding.py`
- `scripts/test_vllm_rerank.py`
- `scripts/test_web_tools.py`
- `evaluation/metrics/retrieval_metrics.py`
- `evaluation/reports/report_writer.py`
- `evaluation/runners/rag_retrieval_runner.py`
- `docs/INTERVIEW_PROJECT_REVIEW.md`
- `docs/package-by-feature-refactor.md`
- `docs/rag-architecture-refactor.md`
- `docs/rag-composition-config-refactor.md`
- `data/test.docx`
- `.env.example`
- `.gitignore`
- `README.md`
- `requirements.txt`

### 本轮新增

- `app/modules/rag/application/rag_context_service.py`
- `app/modules/rag/infrastructure/agent_answer_generator.py`
- `tests/conftest.py`
- `tests/test_tool_contracts.py`
- `tests/evaluation/test_report_writer.py`
- `tests/rag/test_infrastructure_contracts.py`
- `tests/rag/test_pipeline_contracts.py`
- `scripts/check_publication_safety.py`
- `scripts/inspect_document.py`
- `docs/GITHUB_RELEASE_REPORT.md`
- `mypy.ini`
- `requirements-dev.txt`
- `ruff.toml`

### 本轮删除

- `app/modules/rag/application/bm25_retriever.py`
- `app/modules/rag/domain/bm25_models.py`
- `app/modules/rag/infrastructure/openai_compatible_generator.py`
- `app/modules/rag/infrastructure/reranking/models.py`
- `app/modules/rag/infrastructure/reranking/repository.py`
- `scripts/demo_docx_parser.py`
- `scripts/demo_parser_factory.py`
- `scripts/demo_pdf_parser.py`
- `scripts/inspect_rag_document_ids.py`
- `scripts/legacy_memory.py`
- `scripts/test_heritage_repository.py`
- `scripts/test_milvus.py`
- `scripts/test_milvus_store.py`
- `scripts/test_qwen3_reranker.py`
- `scripts/test_tool_execution.py`
- `scripts/test_tool_executor.py`
- `scripts/test_web_agent_loop.py`

## 8. Remaining TODO

- **发布前阻碍：处理旧 Git 历史的 .env.production 私有地址，并审阅/更新完整暂存快照。**
- 在配置好外部服务后完成真实端到端流程，以及 Dense / Hybrid / Hybrid+Rerank 对照。历史 Dense 数字仍只代表原有小数据实验。
- 部署公网前统一管理员/AI/任务处理接口权限和资源所有权检查。
- 持久任务队列、重启恢复、结构化失败原因，人工与AI完成条件、完成/审核幂等事务。
- 附件 TTL、文档更新/删除/版本、Milvus完整来源metadata和引用核验。
- Tool调用持久trace/预算、答案质量评估。
- HCP-MAD 动态选择与讨论：Planned / In Progress。
